import sys
import json
import base64
import numpy as np
import librosa
import structlog
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
        
        # NOVO: Memória de Conversa e Personalidade do Jarvis!
        self.chat_history = [
            {
                "role": "system", 
                "content": "Você é o Jarvis, um assistente virtual prestativo, muito inteligente e direto ao ponto. Responda sempre em português brasileiro e de forma natural, sem enrolação."
            }
        ]

state = AudioState()

def handle_message(msg: CoreMessage) -> PipelineResponse | None:
    if msg.type == "config_reloaded":
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
        # Recebendo fluxo contínuo do microfone
        b64_data = msg.payload.get("audio_b64")
        if not b64_data: return None
        
        audio_bytes = base64.b64decode(b64_data)
        audio_array = np.frombuffer(audio_bytes, dtype=np.float32)
        
        # O Mac manda 48kHz, todas as IAs precisam de 16kHz
        audio_16k = librosa.resample(y=audio_array, orig_sr=48000, target_sr=16000)
        
        # 1. Se estiver esperando o Jarvis
        if not state.is_listening_for_command:
            # OpenWakeWord exige Int16
            audio_i16 = (audio_16k * 32767.0).astype(np.int16)
            prediction = oww_model.predict(audio_i16)
            
            # Checa o score (0 a 1) para o modelo "hey_jarvis"
            score = prediction.get("hey_jarvis", 0.0)
            if score > 0.4:  # Threshold seguro
                log.info("wakeword_detected", score=score)
                state.is_listening_for_command = True
                state.speech_buffer = []
                return PipelineResponse(type="wakeword_status", payload={"status": "listening"})
            return None
            
        # 2. Se o Jarvis acordou, ele está ouvindo e acumulando a frase
        else:
            state.speech_buffer.append(audio_16k)
            return None

    elif msg.type == "audio_silence":
        # O Rust avisa que o usuário parou de falar
        if state.is_listening_for_command and len(state.speech_buffer) > 0:
            log.info("processing_command")
            
            # Junta todos os bloquinhos do buffer
            full_audio = np.concatenate(state.speech_buffer)
            
            # Se foi só um barulho rápido, ignora
            if len(full_audio) < 16000: # menos de 1 segundo
                state.is_listening_for_command = False
                state.speech_buffer = []
                return PipelineResponse(type="wakeword_status", payload={"status": "idle"})

            # Manda pro Whisper
            segments, _ = whisper_model.transcribe(full_audio, beam_size=1, language="pt", condition_on_previous_text=False)
            text = " ".join([segment.text for segment in segments]).strip()
            
            # Manda pra Árvore de Intenções
            intent = registry.classify(text)
            
            # Reseta estado e manda resposta
            state.is_listening_for_command = False
            state.speech_buffer = []
            
            # NOVO CÉREBRO: Com memória de contexto!
            if intent.method == "unmatched":
                log.info("asking_ollama", prompt=text)
                try:
                    # 1. Adiciona o que você acabou de falar na memória
                    state.chat_history.append({"role": "user", "content": text})
                    
                    # 2. Evita que a memória cresça infinitamente (Mantém o System Prompt + últimas 10 mensagens)
                    if len(state.chat_history) > 11:
                        state.chat_history.pop(1)
                    
                    # 3. Manda a conversa INTEIRA para o Ollama analisar
                    response = ollama.chat(model='llama3', messages=state.chat_history)
                    llm_text = response['message']['content']
                    
                    # 4. Salva a resposta que ele deu para que ele lembre na próxima!
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
            
            # Se reconheceu, segue o fluxo normal de ação
            return PipelineResponse(
                type="intent_match", 
                payload={"transcript": text, "intent": intent.model_dump()}
            )
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
                # Só imprime (devolve pro Rust) se houver resposta
                if response:
                    print(response.model_dump_json(), flush=True)
            except Exception as e:
                log.error("pipeline_error", error=str(e))
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
