import sys
import json
import base64
import numpy as np
import librosa
import structlog
import subprocess
import sqlite3  # NOVO: Para gerenciar a memória de longo prazo
from pydantic import BaseModel
from faster_whisper import WhisperModel
import openwakeword
import ollama
from openwakeword.model import Model

from .intent import CommandRegistry

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.processors.JSONRenderer()
    ]
)
log = structlog.get_logger()

# Inicializa IA do Whisper (Transcrição)
log.info("loading_whisper_model", model="small")
whisper_model = WhisperModel("small", device="cpu", compute_type="int8")

# Inicializa IA do WakeWord (Jarvis)
log.info("loading_wakeword_model", model="hey_jarvis")
openwakeword.utils.download_models()
oww_model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")

# Inicializa a Árvore de Comandos
registry = CommandRegistry()

class CoreMessage(BaseModel):
    type: str
    payload: dict

class PipelineResponse(BaseModel):
    type: str
    payload: dict

# Máquina de estados no Python
class AudioState:
    def __init__(self):
        self.is_listening_for_command = False
        self.speech_buffer = []
        self.db_path = None  # NOVO: Guarda o caminho do banco de dados
        
        # Memória de Curto Prazo (Sessão atual)
        self.chat_history = [
            {
                "role": "system", 
                "content": "Você é o Jarvis, um assistente virtual prestativo, muito inteligente e direto ao ponto. Responda sempre em português brasileiro e de forma natural, sem enrolação."
            }
        ]

state = AudioState()

# ==========================================
# CÉREBRO CENTRAL (Texto e Voz com RAG)
# ==========================================
def process_intent_or_llm(text: str, intent) -> PipelineResponse:
    if intent.method != "unmatched":
        # 1. Intercepta a Área de Transferência
        if intent.action and intent.action.startswith("llm_clipboard:"):
            try:
                clipboard_text = subprocess.check_output(['pbpaste'], text=True).strip()
                instrucao = intent.action.replace("llm_clipboard:", "").strip()
                texto_final = f"{instrucao}\n\nTexto copiado:\n{clipboard_text}"
                
                state.chat_history.append({"role": "user", "content": texto_final})
                if len(state.chat_history) > 11:
                    state.chat_history.pop(1)
                    
                response = ollama.chat(model='llama3', messages=state.chat_history)
                llm_text = response['message']['content']
                state.chat_history.append({"role": "assistant", "content": llm_text})
                
                return PipelineResponse(type="llm_response", payload={"transcript": text, "response": llm_text})
            except Exception as e:
                log.error("clipboard_error", error=str(e))
                return PipelineResponse(type="llm_response", payload={"transcript": text, "response": "Houve um erro ao ler o clipboard."})

        # 1.5 Telemetria de Hardware
        elif intent.action and intent.action.startswith("sys_info:"):
            try:
                comando_mac = "top -l 1 | awk '/CPU usage/ || /PhysMem/'"
                sys_data = subprocess.check_output(comando_mac, shell=True, text=True).strip()
                instrucao = intent.action.replace("sys_info:", "").strip()
                texto_final = f"{instrucao}\n\nDados Brutos do Kernel:\n{sys_data}"
                
                state.chat_history.append({"role": "user", "content": texto_final})
                if len(state.chat_history) > 11:
                    state.chat_history.pop(1)
                    
                response = ollama.chat(model='llama3', messages=state.chat_history)
                llm_text = response['message']['content']
                state.chat_history.append({"role": "assistant", "content": llm_text})
                
                return PipelineResponse(type="llm_response", payload={"transcript": text, "response": llm_text})
            except Exception as e:
                return PipelineResponse(type="llm_response", payload={"transcript": text, "response": "Erro nos sensores."})

        # 1.6 Sentinela (Segurança de Rede)
        elif intent.action and intent.action.startswith("net_audit:"):
            try:
                comando_mac = "lsof -iTCP -sTCP:LISTEN -P -n | head -n 30"
                net_data = subprocess.check_output(comando_mac, shell=True, text=True).strip()
                if not net_data: net_data = "Nenhuma porta em escuta."
                instrucao = intent.action.replace("net_audit:", "").strip()
                texto_final = f"{instrucao}\n\nLog de Portas Abertas:\n{net_data}"
                
                state.chat_history.append({"role": "user", "content": texto_final})
                if len(state.chat_history) > 11:
                    state.chat_history.pop(1)
                    
                response = ollama.chat(model='llama3', messages=state.chat_history)
                llm_text = response['message']['content']
                state.chat_history.append({"role": "assistant", "content": llm_text})
                
                return PipelineResponse(type="llm_response", payload={"transcript": text, "response": llm_text})
            except Exception as e:
                return PipelineResponse(type="llm_response", payload={"transcript": text, "response": "Erro no firewall."})

        # Comandos normais (open:, sh:)
        return PipelineResponse(type="intent_match", payload={"transcript": text, "intent": intent.model_dump()})
    
    else:
        # =======================================================
        # 3. CONVERSA LIVRE COM RAG (MEMÓRIA DE LONGO PRAZO)
        # =======================================================
        log.info("asking_ollama_with_rag", prompt=text)
        context_str = ""
        
        # SE TEMOS O BANCO DE DADOS, VAMOS PEGAR LEMBRANÇAS DO PASSADO!
        if state.db_path:
            try:
                # 1. Transforma a sua pergunta atual em um vetor matemático
                emb_res = ollama.embeddings(model='nomic-embed-text', prompt=text)
                query_vector = np.array(emb_res['embedding'], dtype=np.float32)
                
                # 2. Puxa todas as memórias antigas salvas no SQLite
                conn = sqlite3.connect(state.db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT text, embedding FROM memories")
                rows = cursor.fetchall()
                conn.close()
                
                # 3. Varre o banco calculando a Similaridade de Cosseno (com Numpy)
                similarities = []
                for row_text, row_emb_blob in rows:
                    row_vector = np.frombuffer(row_emb_blob, dtype=np.float32)
                    if query_vector.shape == row_vector.shape:
                        dot = np.dot(query_vector, row_vector)
                        norm1 = np.linalg.norm(query_vector)
                        norm2 = np.linalg.norm(row_vector)
                        sim = dot / (norm1 * norm2) if (norm1 > 0 and norm2 > 0) else 0.0
                        similarities.append((sim, row_text))
                
                # Organiza pelas memórias mais parecidas e pega as 2 melhores
                similarities.sort(key=lambda x: x[0], reverse=True)
                top_memories = [txt for sim, txt in similarities[:2] if sim > 0.6] # Filtro de relevância de 60%
                
                if top_memories:
                    context_str = "\n".join(top_memories)
                    log.info("rag_memories_retrieved", count=len(top_memories))
            except Exception as e:
                log.error("rag_retrieval_error", error=str(e))

        # Monta o pacote de envio para o Llama 3
        messages = list(state.chat_history)
        if context_str:
            # Injeta as lembranças do passado como uma instrução secreta do sistema!
            messages.append({
                "role": "system",
                "content": f"Fatos importantes que você lembrou de conversas passadas com o usuário:\n{context_str}\nUse esses fatos se ajudarem a responder naturalmente."
            })
        
        # Adiciona a pergunta atual no fluxo
        messages.append({"role": "user", "content": text})
        
        # Alimenta a memória de curto prazo local
        state.chat_history.append({"role": "user", "content": text})
        if len(state.chat_history) > 11:
            state.chat_history.pop(1)
            
        try:
            response = ollama.chat(model='llama3', messages=messages)
            llm_text = response['message']['content']
            state.chat_history.append({"role": "assistant", "content": llm_text})
            
            # GRAVA ESSA CONVERSA NA MEMÓRIA DE LONGO PRAZO DO SQLITE!
            if state.db_path:
                try:
                    memory_text = f"Usuário disse: {text} | Jarvis respondeu: {llm_text}"
                    emb_res2 = ollama.embeddings(model='nomic-embed-text', prompt=memory_text)
                    mem_vector = np.array(emb_res2['embedding'], dtype=np.float32)
                    
                    conn = sqlite3.connect(state.db_path)
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO memories (text, embedding) VALUES (?, ?)", (memory_text, mem_vector.tobytes()))
                    conn.commit()
                    conn.close()
                    log.info("new_memory_stored_in_db")
                except Exception as e:
                    log.error("rag_storage_error", error=str(e))
            
            return PipelineResponse(type="llm_response", payload={"transcript": text, "response": llm_text})
        except Exception as e:
            log.error("ollama_error", error=str(e))
            return PipelineResponse(type="llm_response", payload={"transcript": text, "response": "Erro de conexão com o cérebro principal."})

# ==========================================
# ROTEADOR DE MENSAGENS (Tauri -> Python)
# ==========================================
def handle_message(msg: CoreMessage) -> PipelineResponse | None:
    
    if msg.type == "text_input":
        text = msg.payload.get("text", "")
        log.info("text_received", text=text)
        intent = registry.classify(text)
        return process_intent_or_llm(text, intent)

    elif msg.type == "config_reloaded":
        db_path = msg.payload.get("db_path")
        state.db_path = db_path # Salva o caminho do banco de dados na maquina de estados
        
        # NOVO: Cria a tabela de memórias vetoriais se ela não existir
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT,
                    embedding BLOB
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            log.error("sqlite_memories_init_failed", error=str(e))

        try:
            count = registry.load_from_db(db_path)
            log.info("command_registry_reloaded", total_commands=count)
            return PipelineResponse(type="status", payload={"status": f"{count} comandos ativos"})
        except Exception as e:
            return PipelineResponse(type="error", payload={"message": str(e)})

    elif msg.type == "ping":
        return PipelineResponse(type="pong", payload={"status": "ok"})
        
    elif msg.type == "audio_stream":
        b64_data = msg.payload.get("audio_b64")
        if not b64_data: return None
        
        audio_bytes = base64.b64decode(b64_data)
        audio_array = np.frombuffer(audio_bytes, dtype=np.float32)
        audio_16k = librosa.resample(y=audio_array, orig_sr=48000, target_sr=16000)
        
        if not state.is_listening_for_command:
            audio_i16 = (audio_16k * 32767.0).astype(np.int16)
            prediction = oww_model.predict(audio_i16)
            
            score = prediction.get("hey_jarvis", 0.0)
            if score > 0.4:
                log.info("wakeword_detected", score=score)
                state.is_listening_for_command = True
                state.speech_buffer = []
                return PipelineResponse(type="wakeword_status", payload={"status": "listening"})
            return None
        else:
            state.speech_buffer.append(audio_16k)
            return None

    elif msg.type == "audio_silence":
        if state.is_listening_for_command and len(state.speech_buffer) > 0:
            log.info("processing_command")
            full_audio = np.concatenate(state.speech_buffer)
            
            if len(full_audio) < 16000:
                state.is_listening_for_command = False
                state.speech_buffer = []
                return PipelineResponse(type="wakeword_status", payload={"status": "idle"})

            segments, _ = whisper_model.transcribe(full_audio, beam_size=1, language="pt", condition_on_previous_text=False)
            text = " ".join([segment.text for segment in segments]).strip()
            
            state.is_listening_for_command = False
            state.speech_buffer = []
            
            intent = registry.classify(text)
            return process_intent_or_llm(text, intent)
        return None

def main():
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line: continue
            try:
                raw_msg = json.loads(line)
                msg = CoreMessage(**raw_msg)
                response = handle_message(msg)
                if response:
                    print(response.model_dump_json(), flush=True)
            except Exception as e:
                log.error("pipeline_error", error=str(e))
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
