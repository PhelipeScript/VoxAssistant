import sqlite3
import unicodedata
import re
import difflib
from pydantic import BaseModel
from typing import Optional, List

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

class TrieNode:
    def __init__(self):
        self.children = {}
        self.command_id = None

class CommandRegistry:
    def __init__(self):
        self.root = TrieNode()
        self.commands_data = {}
        self.flat_phrases = {}

    def register(self, command_id: str, action: str, phrases: List[str]):
        self.commands_data[command_id] = {"action": action, "phrases": phrases}
        for phrase in phrases:
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

    def load_from_db(self, db_path: str) -> int:
        self.root = TrieNode()
        self.commands_data = {}
        self.flat_phrases = {}
        
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

    def classify(self, transcript: str) -> IntentResult:
        norm_transcript = normalize_text(transcript)
        if not norm_transcript or not self.flat_phrases:
            return IntentResult(command_id=None, action=None, confidence=0.0, matched_phrase=norm_transcript, method="unmatched")

        # 1. Busca Exata Rápida (O(k))
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

        # 2. Busca por Aproximação (Fuzzy)
        keys = list(self.flat_phrases.keys())
        closest_matches = difflib.get_close_matches(
            norm_transcript, 
            keys, 
            n=1, 
            cutoff=0.65 # Tolerância levemente aumentada para variações
        )

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

        return IntentResult(command_id=None, action=None, confidence=0.0, matched_phrase=norm_transcript, method="unmatched")
