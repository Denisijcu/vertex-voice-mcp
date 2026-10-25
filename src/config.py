from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configuración central. Todo se puede sobreescribir con variables de entorno
    o con un archivo .env en la raíz del proyecto (E:\\vertex-voice-mcp\\.env).

    FIX: antes se usaba os.getenv() como default, que NO lee el .env
    (os.getenv solo ve el entorno del proceso). Ahora pydantic-settings
    resuelve todo, incluido el alias VERTEX_VOICE_TOKEN.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # FIX seguridad: por defecto solo localhost. Para exponerlo en la LAN,
    # pon HOST=0.0.0.0 en el .env a conciencia.
    HOST: str = "127.0.0.1"
    PORT: int = 8765
    API_TOKEN: str = Field(
        default="vx-secret-dev-token-2026",
        validation_alias=AliasChoices("VERTEX_VOICE_TOKEN", "API_TOKEN"),
    )

    # Audio (16kHz, 16-bit Mono PCM = 32 bytes/ms)
    SAMPLE_RATE: int = 16000
    BYTES_PER_SAMPLE: int = 2
    MAX_CHUNK_BYTES: int = 8192
    MAX_UTTERANCE_SECONDS: int = 15
    SILENCE_FRAMES_TO_TRIGGER: int = 10
    # Chunks guardados ANTES de que el VAD confirme voz (evita cortar el inicio de la frase)
    PREROLL_CHUNKS: int = 4

    # Inferencia local (presupuesto 6GB VRAM)
    WHISPER_MODEL_SIZE: str = "tiny.en"
    WHISPER_COMPUTE_TYPE: str = "int8"
    WHISPER_LANGUAGE: str = "en"
    LLM_BASE_URL: str = "http://127.0.0.1:1234/v1"
    LLM_MODEL_NAME: str = "vertex-core-q4km-v8"
    LLM_TIMEOUT_SECONDS: float = 20.0

    # Voz neuronal (Microsoft Edge Neural TTS — OJO: requiere internet)
    TTS_VOICE: str = "en-US-ChristopherNeural"
    TTS_RATE: str = "+8%"


settings = Settings()
