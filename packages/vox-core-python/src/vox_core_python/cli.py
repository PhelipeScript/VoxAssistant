import sys
import json
import base64
import numpy as np
import librosa
import structlog
import subprocess
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
openwakeword.utils.download_models() # Garante que os modelos oficiais estão baixados
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
        
        # Memória de Conversa e Personalidade do Jarvis!
        self.chat_history = [
            {
                "role": "system", 
                "content": "Você é o Jarvis, um assistente virtual prestativo, muito inteligente e direto ao ponto. Responda sempre em português brasileiro e de forma natural, sem enrolação."
            }
        ]

state = AudioState()

# ==========================================
# CÉREBRO CENTRAL (Texto e Voz passam por aqui)
# ==========================================
def process_intent_or_llm(text: str, intent) -> PipelineResponse:
    if intent.method != "unmatched":
        # 1. Intercepta a Área de Transferência
        if intent.action and intent.action.startswith("llm_clipboard:"):
            try:
                # Usa o comando nativo pbpaste do Mac
                clipboard_text = subprocess.check_output(['pbpaste'], text=True).strip()
                instrucao = intent.action.replace("llm_clipboard:", "").strip()
                
                texto_final = f"{instrucao}\n\nTexto copiado:\n{clipboard_text}"
                
                # Adiciona na memória e chama o Llama 3
                state.chat_history.append({"role": "user", "content": texto_final})
                if len(state.chat_history) > 11:
                    state.chat_history.pop(1)
                    
                response = ollama.chat(model='llama3', messages=state.chat_history)
                llm_text = response['message']['content']
                state.chat_history.append({"role": "assistant", "content": llm_text})
                
                return PipelineResponse(
                    type="llm_response", 
                    payload={"transcript": text, "response": llm_text}
                )
            except Exception as e:
                log.error("clipboard_error", error=str(e))
                return PipelineResponse(
                    type="llm_response", 
                    payload={"transcript": text, "response": "Houve um erro ao ler a área de transferência do Mac."}
                )

        # 2. Se for um comando normal (open:, sh:), segue para o Rust executar
        return PipelineResponse(
            type="intent_match", 
            payload={"transcript": text, "intent": intent.model_dump()}
        )
    else:
        # 3. Conversa livre com o Llama 3 (Com Memória)
        log.info("asking_ollama", prompt=text)
        try:
            state.chat_history.append({"role": "user", "content": text})
            if len(state.chat_history) > 11:
                state.chat_history.pop(1)
                
            response = ollama.chat(model='llama3', messages=state.chat_history)
            llm_text = response['message']['content']
            
            state.chat_history.append({"role": "assistant", "content": llm_text})
            
            return PipelineResponse(
                type="llm_response", 
                payload={"transcript": text, "response": llm_text}
            )
        except Exception as e:
            log.error("ollama_error", error=str(e))
            return PipelineResponse(
                type="llm_response", 
                payload={"transcript": text, "response": "Erro de conexão com o cérebro principal."}
            )

# ==========================================
# ROTEADOR DE MENSAGENS (Tauri -> Python)
# ==========================================
def handle_message(msg: CoreMessage) -> PipelineResponse | None:
    
    # Recebendo texto digitado pelo React
    if msg.type == "text_input":
        text = msg.payload.get("text", "")
        log.info("text_received", text=text)
        intent = registry.classify(text)
        return process_intent_or_llm(text, intent)

    elif msg.type == "config_reloaded":
        db_path = msg.payload.get("db_path")
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

            # Transcreve o áudio
            segments, _ = whisper_model.transcribe(full_audio, beam_size=1, language="pt", condition_on_previous_text=False)
            text = " ".join([segment.text for segment in segments]).strip()
            
            # Reseta estado do áudio
            state.is_listening_for_command = False
            state.speech_buffer = []
            
            # Repassa pro cérebro central
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
