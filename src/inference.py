import io
import time
import asyncio
import logging
import numpy as np
import httpx
from src.config import settings

logger = logging.getLogger("VertexVoiceInference")

class LocalVoicePipeline:
    """
    Pipeline modular ASR -> LLM -> TTS optimizado para correr en 6GB de VRAM (ej. GTX 1660 Ti).
    """
    def __init__(self):
        self.whisper = None
        self._init_asr()
        self.http_client = httpx.AsyncClient(timeout=15.0)

    def _init_asr(self):
        try:
            from faster_whisper import WhisperModel
            logger.info(f"Cargando faster-whisper ({settings.WHISPER_MODEL_SIZE}, {settings.WHISPER_COMPUTE_TYPE})...")
            self.whisper = WhisperModel(
                settings.WHISPER_MODEL_SIZE,
                device="auto",
                compute_type=settings.WHISPER_COMPUTE_TYPE
            )
        except Exception as e:
            logger.warning(f"Modo fallback ASR activo (faster-whisper no inicializado): {e}")

    def _run_stt_sync(self, pcm_bytes: bytes) -> str:
        audio_np = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if self.whisper is None:
            return "[Audio recibido correctamente en el nodo local]"
        
        segments, _ = self.whisper.transcribe(audio_np, beam_size=1, language="en", vad_filter=False)
        return " ".join([seg.text.strip() for seg in segments]).strip()

    async def transcribe(self, pcm_bytes: bytes) -> str:
        return await asyncio.to_thread(self._run_stt_sync, pcm_bytes)

    async def stream_llm_response(self, prompt: str):
        """
        Consume el endpoint compatible con OpenAI (LM Studio / llama.cpp) en modo SSE streaming
        y emite fragmentos de texto tan pronto como se completa una cláusula.
        """
        payload = {
            "model": settings.LLM_MODEL_NAME,
            "messages": [
                {"role": "system", "content": "You are a realtime voice agent powered by Vertex Coders. Keep answers concise, natural, and under 2 sentences."},
                {"role": "user", "content": prompt}
            ],
            "stream": True,
            "temperature": 0.3,
            "max_tokens": 120
        }
        try:
            async with self.http_client.stream("POST", f"{settings.LLM_BASE_URL}/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                buffer = ""
                async for line in resp.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    import json
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    buffer += delta
                    if any(punct in buffer for punct in [".", "?", "!", "\n", ","]) and len(buffer.strip()) > 12:
                        yield buffer.strip()
                        buffer = ""
                if buffer.strip():
                    yield buffer.strip()
        except Exception as e:
            logger.warning(f"LLM local offline o sin conexión ({e}). Usando respuesta determinista de respaldo.")
            yield f"Heard you loud and clear: {prompt}"

    async def synthesize_speech_chunk(self, text_clause: str) -> bytes:
        """
        Sintetiza una cláusula de texto a PCM 16-bit 16kHz.
        (Listo para enlazar con Kokoro-82M / Piper ONNX; genera onda senoidal modulada de prueba si no hay binario TTS).
        """
        await asyncio.sleep(0.01) # Simula inferencia ONNX ultrarrápida
        duration_sec = min(max(len(text_clause) * 0.04, 0.25), 2.5)
        t = np.linspace(0, duration_sec, int(settings.SAMPLE_RATE * duration_sec), False)
        tone = (0.15 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
        return tone.tobytes()