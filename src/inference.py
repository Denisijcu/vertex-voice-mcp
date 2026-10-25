import asyncio
import json
import logging
import re

import edge_tts
import httpx
import miniaudio
import numpy as np

from src.config import settings

logger = logging.getLogger("VertexVoiceInference")

# ---------------------------------------------------------------------------
# Limpieza de texto del LLM
# ---------------------------------------------------------------------------
# Tokens de control de plantillas de chat (Gemma, ChatML/Qwen, Llama)
_CONTROL_TOKENS = re.compile(
    r"<\|?/?\s*(?:start_of_turn|end_of_turn|im_start|im_end|eot_id|endoftext|turn)\s*\|?>"
    r"|\b(?:start_of_turn|end_of_turn|im_start|im_end)\b",
    re.IGNORECASE,
)
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_ANY_TAG = re.compile(r"<[^<>]*>")
# Fin de cláusula: puntuación SEGUIDA de espacio (evita partir "3.5" o "U.S.") o salto de línea
_CLAUSE_END = re.compile(r"[.!?;:](?=\s)|\n")

_NOISE_HALLUCINATIONS = {
    "you", "bye", "bye.", "thank you", "thank you.", "thanks for watching.",
    "thanks for watching!", "okay.", "okay", ".", "...",
}


def _sanitize(text: str) -> str:
    """Deja solo texto hablable: sin <think>, sin tokens de control, sin tags ni markdown."""
    text = _THINK_BLOCK.sub("", text)
    text = _CONTROL_TOKENS.sub("", text)
    text = _ANY_TAG.sub("", text)
    text = text.replace("<", "").replace(">", "")
    text = re.sub(r"[*#`_]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _pop_clause(buffer: str) -> tuple[str | None, str]:
    """
    Intenta sacar una cláusula completa del buffer crudo del stream.
    Devuelve (cláusula_limpia | None, resto_del_buffer).
    """
    lower = buffer.lower()

    # Qwen a veces emite solo el cierre "</think>" (la apertura va en la plantilla):
    # todo lo anterior es razonamiento y no se habla.
    close_idx = lower.find("</think>")
    if close_idx != -1 and lower.rfind("<think>", 0, close_idx) == -1:
        buffer = buffer[close_idx + len("</think>"):]
        lower = buffer.lower()

    # Bloque <think> abierto sin cerrar: esperar a que termine
    open_idx = lower.rfind("<think>")
    if open_idx != -1 and lower.find("</think>", open_idx) == -1:
        return None, buffer

    # Quitar bloques <think> ya completos
    buffer = _THINK_BLOCK.sub("", buffer)

    # No cortar en medio de un tag que todavía se está formando ("<start_of_")
    lt = buffer.rfind("<")
    safe = buffer[:lt] if lt != -1 and ">" not in buffer[lt:] else buffer

    matches = list(_CLAUSE_END.finditer(safe))
    if not matches:
        return None, buffer

    cut = matches[-1].end()
    clause = _sanitize(buffer[:cut])
    if len(clause) <= 10:
        # Muy corta ("Hi."): seguimos acumulando para no mandar micro-frases al TTS
        return None, buffer
    return clause, buffer[cut:]


class LocalVoicePipeline:
    """
    Pipeline modular ASR (CPU int8) -> LLM (LM Studio, GPU 6GB) -> Neural TTS (PCM 16kHz).
    """

    def __init__(self):
        self.whisper = None
        self._init_asr()
        self.http_client = httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SECONDS)

    async def aclose(self) -> None:
        await self.http_client.aclose()

    # ------------------------------------------------------------------ ASR
    def _init_asr(self) -> None:
        try:
            from faster_whisper import WhisperModel

            logger.info(
                f"Cargando faster-whisper ({settings.WHISPER_MODEL_SIZE}, "
                f"{settings.WHISPER_COMPUTE_TYPE}) en CPU..."
            )
            self.whisper = WhisperModel(
                settings.WHISPER_MODEL_SIZE,
                device="cpu",
                compute_type=settings.WHISPER_COMPUTE_TYPE,
                cpu_threads=4,
            )
            logger.info("Motor ASR faster-whisper listo.")
        except Exception as e:
            logger.warning(f"ASR no disponible, todas las frases se descartarán: {e}")

    def _run_stt_sync(self, pcm_bytes: bytes) -> str:
        if self.whisper is None:
            return ""

        # Ignorar audios < 0.45s (clics, suspiros)
        min_bytes = int(settings.SAMPLE_RATE * settings.BYTES_PER_SAMPLE * 0.45)
        if len(pcm_bytes) < min_bytes:
            return ""

        audio_np = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self.whisper.transcribe(
            audio_np,
            beam_size=1,
            language=settings.WHISPER_LANGUAGE,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()

        # Alucinaciones típicas de Whisper ante ruido de ventilador (solo en audios cortos)
        short_limit = int(settings.SAMPLE_RATE * settings.BYTES_PER_SAMPLE * 0.875)
        if text.lower() in _NOISE_HALLUCINATIONS and len(pcm_bytes) < short_limit:
            return ""
        return text

    async def transcribe(self, pcm_bytes: bytes) -> str:
        return await asyncio.to_thread(self._run_stt_sync, pcm_bytes)

    # ------------------------------------------------------------------ LLM
    async def stream_llm_response(self, prompt: str, tool=None):
        """tool: ToolResult del router (o None). Si viene, sus datos van como contexto
        y el modelo los cuenta con sus palabras."""
        user_content = prompt
        max_tokens = 90
        if tool is not None:
            user_content = (
                f"[TOOL {tool.tool} — real, up-to-date data]\n{tool.text}\n[/TOOL]\n"
                f"Answer the user's question below using ONLY the tool data above. "
                f"Answer in the same language as the question, in 2 or 3 short spoken sentences. "
                f"Never read URLs or mention the tool.\n\n{prompt}"
            )
            max_tokens = 160  # noticias y pronósticos necesitan algo más de espacio
        payload = {
            "model": settings.LLM_MODEL_NAME,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "/no_think\n"
                        "You are Vertex-Core, the proprietary AI voice agent architected by "
                        "Denis Sanchez Leyva at Vertex Coders LLC in Miami, Florida. "
                        "Never say you are Qwen or developed by Alibaba/Tongyi Lab. "
                        "Reply in 1 or 2 crisp, natural spoken sentences without markdown. "
                        "You ARE connected to live tools built by Vertex Coders (MCP servers): weather, "
                        "local time, news headlines and Wikipedia. When a message contains [TOOL ...] data, "
                        "that is real information you just looked up: use it confidently. "
                        "Never say you have no internet access or that you can't read Wikipedia. "
                        "If a question needs live data and no [TOOL] data came with it, invite the user "
                        "to ask it directly, like 'What's the weather in Miami?' or 'Who was Alan Turing?'."
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            "stream": True,
            "temperature": 0.25,
            "max_tokens": max_tokens,
        }

        yielded_any = False
        buffer = ""
        try:
            async with self.http_client.stream(
                "POST", f"{settings.LLM_BASE_URL}/chat/completions", json=payload
            ) as resp:
                resp.raise_for_status()

                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue  # chunks de "usage" vienen sin choices
                    delta = (choices[0].get("delta") or {}).get("content") or ""
                    if not delta:
                        continue

                    # FIX: se acumula el texto CRUDO y se limpia al emitir.
                    # Antes se borraban "<" y ">" de cada delta, así que "<think>"
                    # nunca se detectaba y el razonamiento se leía en voz alta.
                    buffer += delta

                    while True:
                        clause, buffer = _pop_clause(buffer)
                        if clause is None:
                            break
                        yielded_any = True
                        yield clause

            # Lo que quede al cerrar el stream
            rest = buffer
            if "<think>" in rest.lower() and "</think>" not in rest.lower():
                rest = ""  # el modelo se cortó pensando: no hay nada que decir
            rest = _sanitize(rest)
            if rest:
                yielded_any = True
                yield rest

        except Exception as e:
            # FIX: antes había dos `except Exception` seguidos (el segundo era
            # inalcanzable) y el fallback se emitía aunque ya se hubiera hablado.
            logger.warning(f"LLM local falló ({type(e).__name__}: {e}).")
            if not yielded_any:
                yield f"Vertex-Core local engine is offline. I heard you say: {prompt}"

    # ------------------------------------------------------------------ TTS
    async def synthesize_speech_chunk(self, text_clause: str) -> bytes:
        """
        Convierte texto en voz neuronal (PCM 16kHz 16-bit Mono).
        """
        text_clause = text_clause.strip()
        if not text_clause:
            return b""
        try:
            communicate = edge_tts.Communicate(text_clause, settings.TTS_VOICE, rate=settings.TTS_RATE)
            mp3_buffer = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3_buffer.extend(chunk["data"])

            if not mp3_buffer:
                raise RuntimeError("Buffer MP3 vacío")

            decoded = await asyncio.to_thread(
                miniaudio.decode,
                bytes(mp3_buffer),
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=1,
                sample_rate=settings.SAMPLE_RATE,
            )
            return decoded.samples.tobytes()

        except Exception as e:
            logger.warning(f"Fallo en TTS neuronal ({e}), enviando tono de respaldo.")
            duration_sec = min(max(len(text_clause) * 0.04, 0.25), 2.0)
            t = np.linspace(0, duration_sec, int(settings.SAMPLE_RATE * duration_sec), False)
            tone = (0.12 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
            return tone.tobytes()
