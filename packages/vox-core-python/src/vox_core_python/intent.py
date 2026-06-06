import sqlite3
import unicodedata
import re
import difflib
import urllib.parse
from pydantic import BaseModel
from typing import Optional, List, Dict

def normalize_text(text: str) -> str:
    if not text: return ""
    text = text.lower()
    text = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
    text = re.sub(r'[^\w\s]', '', text)
    return text.strip()

class IntentResult(BaseModel):
    command_id: Optional[str]
    action: Optional[str]
    confidence: float
    matched_phrase: str
    method: str
    variables: Dict[str, str] = {}

class TrieNode:
    def __init__(self):
        self.children = {}
        self.command_id = None

class CommandRegistry:
    def __init__(self):
        self.root = TrieNode()
        self.commands_data = {}
        self.flat_phrases = {}
        self.regex_phrases = []

    def load_from_db(self, db_path: str) -> int:
        self.root = TrieNode()
        self.commands_data = {}
        self.flat_phrases = {}
        self.regex_phrases = []
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT id, action, phrases FROM commands")
            rows = cursor.fetchall()
            for row in rows:
                cmd_id, action, phrases_str = row
                phrases = [p.strip() for p in phrases_str.split(",") if p.strip()]
                self.register(cmd_id, action, phrases)
            conn.close()
            return len(rows)
        except Exception as e:
            raise RuntimeError(f"Erro no SQLite: {str(e)}")

    def register(self, command_id: str, action: str, phrases: List[str]):
        self.commands_data[command_id] = {"action": action, "phrases": phrases}
        for phrase in phrases:
            
            # NOVO MODO: Normalização Segura para Regex
            if "{" in phrase and "}" in phrase:
                # 1. Tira acentos da regra cadastrada
                temp_pattern = phrase.lower()
                temp_pattern = ''.join(c for c in unicodedata.normalize('NFD', temp_pattern) if unicodedata.category(c) != 'Mn')
                # 2. Descobre quem são os slots {query}
                slots = re.findall(r'\{(\w+)\}', temp_pattern)
                # 3. Tira toda pontuação EXCETO as chaves {}
                temp_pattern = re.sub(r'[^\w\s\{\}]', '', temp_pattern)
                
                # 4. Monta a Expressão Regular
                pattern = re.escape(temp_pattern)
                pattern = pattern.replace(r'\ ', r'\s+')
                for slot in slots:
                    # Usando .+? (non-greedy) para capturar a variável exata sem engolir outras palavras
                    pattern = pattern.replace(f"\\{{{slot}\\}}", f"(?P<{slot}>.+?)")
                    
                regex = re.compile(f".*{pattern}.*", re.IGNORECASE)
                self.regex_phrases.append((regex, command_id, phrase))
                continue

            # Cadastro Normal
            norm_phrase = normalize_text(phrase)
            if not norm_phrase: continue
            
            self.flat_phrases[norm_phrase] = command_id
            words = norm_phrase.split()
            node = self.root
            for word in words:
                if word not in node.children:
                    node.children[word] = TrieNode()
                node = node.children[word]
            node.command_id = command_id

    def classify(self, transcript: str) -> IntentResult:
        norm_transcript = normalize_text(transcript)
        if not norm_transcript:
            return IntentResult(command_id=None, action=None, confidence=0.0, matched_phrase="", method="unmatched")

        # 1. Busca Dinâmica por Variáveis (Regex) primeiro!
        # Agora a busca ocorre na string limpa e sem acentos, garantindo que vai bater com o Regex.
        for regex, cmd_id, original_phrase in self.regex_phrases:
            match = regex.match(norm_transcript)
            if match:
                variables = match.groupdict()
                action_template = self.commands_data[cmd_id]["action"]
                
                final_action = action_template
                for key, val in variables.items():
                    # Mudamos para 'quote' (%20) para os links abrirem perfeitamente no Mac
                    encoded_val = urllib.parse.quote(val.strip())
                    final_action = final_action.replace(f"{{{key}}}", encoded_val)
                    
                return IntentResult(
                    command_id=cmd_id,
                    action=final_action,
                    confidence=1.0,
                    matched_phrase=original_phrase,
                    method="regex",
                    variables=variables
                )

        # 2. Busca Exata Rápida
        if not self.flat_phrases:
            return IntentResult(command_id=None, action=None, confidence=0.0, matched_phrase=norm_transcript, method="unmatched")

        words = norm_transcript.split()
        node = self.root
        matched_id = None
        matched_words = []
        
        for word in words:
            if word in node.children:
                node = node.children[word]
                matched_words.append(word)
                if node.command_id:
                    matched_id = node.command_id
                    break
            else:
                break

        if matched_id:
            return IntentResult(
                command_id=matched_id,
                action=self.commands_data[matched_id]["action"],
                confidence=1.0,
                matched_phrase=" ".join(matched_words),
                method="exact"
            )

        # 3. Busca por Aproximação (Fuzzy)
        keys = list(self.flat_phrases.keys())
        closest_matches = difflib.get_close_matches(norm_transcript, keys, n=1, cutoff=0.65)

        if closest_matches:
            best_match = closest_matches[0]
            fuzzy_id = self.flat_phrases[best_match]
            ratio = difflib.SequenceMatcher(None, norm_transcript, best_match).ratio()
            
            return IntentResult(
                command_id=fuzzy_id,
                action=self.commands_data[fuzzy_id]["action"],
                confidence=round(ratio, 2),
                matched_phrase=best_match,
                method="fuzzy"
            )

        # Se nada bater, repassa para o Llama 3!
        return IntentResult(command_id=None, action=None, confidence=0.0, matched_phrase=norm_transcript, method="unmatched")
