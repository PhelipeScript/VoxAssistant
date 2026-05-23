import unicodedata
import re
import difflib
from pydantic import BaseModel
from typing import Optional, List, Dict

def normalize_text(text: str) -> str:
    """
    Normaliza o texto para busca:
    'Abre o Crômê!' -> 'abre o crome'
    """
    text = text.lower()
    # Remove acentos
    text = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
    # Remove pontuação
    text = re.sub(r'[^\w\s]', '', text)
    return text.strip()

class IntentResult(BaseModel):
    command_id: Optional[str]
    action: Optional[str]
    confidence: float
    matched_phrase: str
    method: str  # 'exact', 'fuzzy', ou 'unmatched'

class TrieNode:
    def __init__(self):
        self.children = {}
        self.command_id = None

class CommandRegistry:
    def __init__(self):
        self.root = TrieNode()
        self.commands_data = {}
        # Mapa reverso para o fuzzy match: phrase_normalizada -> command_id
        self.flat_phrases = {}

    def register(self, command_id: str, action: str, phrases: List[str]):
        """Registra um comando na Trie para busca ultra-rápida"""
        self.commands_data[command_id] = {"action": action, "phrases": phrases}
        
        for phrase in phrases:
            norm_phrase = normalize_text(phrase)
            self.flat_phrases[norm_phrase] = command_id
            
            # Inserção na Trie (palavra por palavra)
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

        words = norm_transcript.split()
        
        # 1. Busca Exata via Trie O(k)
        node = self.root
        matched_id = None
        matched_words = []
        
        for word in words:
            if word in node.children:
                node = node.children[word]
                matched_words.append(word)
                if node.command_id:
                    # Se encontrou um nó final de comando, salva.
                    # Ex: Se falou "toca música no quarto", ele casa até "toca música".
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

        # 2. Fallback: Fuzzy Match (Distância de Edição/Levenshtein)
        # Útil para: "tocar musica" (falado) vs "toca musica" (registrado)
        closest_matches = difflib.get_close_matches(
            norm_transcript, 
            self.flat_phrases.keys(), 
            n=1, 
            cutoff=0.75 # Threshold de 75% de similaridade
        )

        if closest_matches:
            best_match = closest_matches[0]
            fuzzy_id = self.flat_phrases[best_match]
            
            # Calcula uma "confiança" baseada na similaridade
            ratio = difflib.SequenceMatcher(None, norm_transcript, best_match).ratio()
            
            return IntentResult(
                command_id=fuzzy_id,
                action=self.commands_data[fuzzy_id]["action"],
                confidence=round(ratio, 2),
                matched_phrase=best_match,
                method="fuzzy"
            )

        # 3. Sem correspondência
        return IntentResult(
            command_id=None,
            action=None,
            confidence=0.0,
            matched_phrase=norm_transcript,
            method="unmatched"
        )
