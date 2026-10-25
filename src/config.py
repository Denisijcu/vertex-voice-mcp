import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    HOST: str = "0.0.0.0"
    PORT: int = 8765
    API_TOKEN: str = os.getenv("VERTEX_VOICE_TOKEN", "vx-secret-dev-token-2026")
    
    # Audio Settings (16kHz, 16-bit Mono PCM = 32 bytes/ms)
    SAMPLE_RATE: int = 16000
    BYTES_PER_SAMPLE: int = 2
    MAX_CHUNK_BYTES: int = 8192          # Hard limit contra DoS (256ms max por paquete)
    MAX_UTTERANCE_SECONDS: int = 15      # Corte automático a los 15s de habla continua
    SILENCE_FRAMES_TO_TRIGGER: int = 12  # ~380ms de silencio dispara el turno del agente
    
    # Local Inference Endpoints (GPU-Poor Optimized: 6GB VRAM budget)
    WHISPER_MODEL_SIZE: str = "tiny.en"  # CTranslate2 int8 (~150MB VRAM)
    WHISPER_COMPUTE_TYPE: str = "int8"
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1") # LM Studio / llama.cpp server
    LLM_MODEL_NAME: str = os.getenv("LLM_MODEL_NAME", "vertex-core-4b-q4_k_m")

    class Config:
        env_file = ".env"

settings = Settings()