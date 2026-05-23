import sys
import json
import base64
import numpy as np
import librosa
import structlog
from pydantic import BaseModel
from faster_whisper import WhisperModel

# Importa o nosso novo classificador
from .intent import CommandRegistry

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.processors.JSONRenderer()
    ]
)
log = structlog.get_logger()

log.info("loading_whisper_model", model="small", compute_type="int8")
model = WhisperModel("small", device="cpu", compute_type="int8")
log.info("whisper_model_loaded")

# Inicializa e popula os comandos (Fase 1: Mock de Banco de Dados)
registry = CommandRegistry()
registry.register("spotify_play", "play_music", ["toca música", "tocar música", "play", "solta o som"])
registry.register("system_time", "get_time", ["que horas são", "me diga as horas", "horas"])
registry.register("browser_open", "open_chrome", ["abrir chrome", "abre o navegador", "navegar na internet"])
log.info("command_registry_loaded", total_commands=len(registry.commands_data))

class CoreMessage(BaseModel):
    type: str
    payload: dict

class PipelineResponse(BaseModel):
    type: str
    payload: dict
    
def handle_message(msg: CoreMessage) -> PipelineResponse:
    if msg.type == "ping":
        return PipelineResponse(type="pong", payload={"status": "ok"})
        
    elif msg.type == "audio_chunk":
        b64_data = msg.payload.get("audio_b64")
        if b64_data:
            audio_bytes = base64.b64decode(b64_data)
            audio_array = np.frombuffer(audio_bytes, dtype=np.float32)
            
            audio_16k = librosa.resample(y=audio_array, orig_sr=48000, target_sr=16000)
            
            log.info("transcribing...")
            segments, info = model.transcribe(
                audio_16k, 
                beam_size=1, 
                language="pt",
                condition_on_previous_text=False
            )
            
            text = " ".join([segment.text for segment in segments]).strip()
            
            # --- MÁGICA DA INTENÇÃO AQUI ---
            intent = registry.classify(text)
            
            return PipelineResponse(
                type="intent_match", 
                payload={
                    "transcript": text,
                    "intent": intent.model_dump()
                }
            )
        
        return PipelineResponse(type="error", payload={"message": "Nenhum áudio recebido"})
        
    else:
        return PipelineResponse(type="error", payload={"message": f"Tipo desconhecido: {msg.type}"})

def main():
    log.info("pipeline_started", version="0.1.0")
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                raw_msg = json.loads(line)
                msg = CoreMessage(**raw_msg)
                response = handle_message(msg)
                print(response.model_dump_json(), flush=True)
            except Exception as e:
                log.error("pipeline_error", error=str(e))
                print(PipelineResponse(type="error", payload={"message": str(e)}).model_dump_json(), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        log.info("pipeline_stopped")

if __name__ == "__main__":
    main()
