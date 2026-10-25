"""
Pipeline de voz del Space: ASR (faster-whisper int8, CPU) -> Vertex-Core (llama-server local,
OpenAI-compatible, streaming por cláusulas) -> Edge Neural TTS.

Misma lógica de limpieza/corte por cláusulas que src/inference.py de vertex-voice-mcp.
"""
import asyncio
import base64
import io
import json
import logging
import os
import re

import edge_tts
import httpx
import miniaudio
import numpy as np

log = logging.getLogger("pipeline")

ASR_SR = 16000
TTS_SR = 24000  # frecuencia nativa de Edge TTS: mejor calidad que 16 kHz

VOICES = {
    "es": os.getenv("TTS_VOICE_ES", "es-US-AlonsoNeural"),
    "en": os.getenv("TTS_VOICE_EN", "en-US-ChristopherNeural"),
}

SYSTEM_PROMPT = (
    "You are Vertex-Core, the proprietary AI voice agent architected by Denis Sanchez Leyva "
    "at Vertex Coders LLC in Miami, Florida. Never say you are Qwen or developed by Alibaba. "
    "Always reply in the same language the user speaks (Spanish or English), "
    "in 1 or 2 short, natural spoken sentences, without markdown, lists or emojis. "
    "You have a camera. When a message includes [CAMERA: ...], that is what you are seeing "
    "right now: talk about it naturally in first person, never mention the tag. "
    "The camera note is always in English, but you must answer in the language of the user's "
    "question that follows it."
)

VISION_PROMPT = (
    "Describe this camera image in one short sentence. "
    "Mention what the person holds or does and the main objects. "
    "Only describe what is clearly visible; do not guess."
)

# ---------------------------------------------------------------------------
# Limpieza y corte por cláusulas
# ---------------------------------------------------------------------------
_CONTROL_TOKENS = re.compile(
    r"<\|?/?\s*(?:start_of_turn|end_of_turn|im_start|im_end|eot_id|endoftext|turn)\s*\|?>"
    r"|\b(?:start_of_turn|end_of_turn|im_start|im_end)\b",
    re.IGNORECASE,
)
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_ANY_TAG = re.compile(r"<[^<>]*>")
_CLAUSE_END = re.compile(r"[.!?;:](?=\s)|\n")
_NOISE = {"you", "bye", "bye.", "thank you", "thank you.", "thanks for watching.", "okay.", ".", "..."}


def sanitize(text: str) -> str:
    text = _THINK_BLOCK.sub("", text)
    text = _CONTROL_TOKENS.sub("", text)
    text = _ANY_TAG.sub("", text)
    text = text.replace("<", "").replace(">", "")
    text = re.sub(r"[*#`_]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def pop_clause(buffer: str) -> tuple[str | None, str]:
    lower = buffer.lower()
    close_idx = lower.find("</think>")
    if close_idx != -1 and lower.rfind("<think>", 0, close_idx) == -1:
        buffer = buffer[close_idx + len("</think>"):]
        lower = buffer.lower()
    open_idx = lower.rfind("<think>")
    if open_idx != -1 and lower.find("</think>", open_idx) == -1:
        return None, buffer
    buffer = _THINK_BLOCK.sub("", buffer)
    lt = buffer.rfind("<")
    safe = buffer[:lt] if lt != -1 and ">" not in buffer[lt:] else buffer
    matches = list(_CLAUSE_END.finditer(safe))
    if not matches:
        # Frase larga sin punto: en CPU tarda mucho en llegar. Cortamos en la última coma
        # para que la voz empiece antes (sin esperar a que termine la oración entera).
        comma = safe.rfind(", ")
        if comma != -1 and len(sanitize(safe[:comma])) >= 45:
            return sanitize(buffer[:comma + 1]), buffer[comma + 2:]
        return None, buffer
    cut = matches[-1].end()
    clause = sanitize(buffer[:cut])
    if len(clause) <= 10:
        return None, buffer
    return clause, buffer[cut:]


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
def to_16k_mono(sr: int, data: np.ndarray) -> np.ndarray:
    """Audio del micrófono de Gradio (int16/float, mono o estéreo, 44.1/48 kHz) -> float32 16 kHz mono."""
    data = np.asarray(data)
    if data.dtype.kind in "iu":
        data = data.astype(np.float32) / float(np.iinfo(data.dtype).max + 1)
    else:
        data = data.astype(np.float32)
    if data.ndim == 2:
        data = data.mean(axis=1)
    if sr != ASR_SR and len(data) > 1:
        n_out = int(len(data) * ASR_SR / sr)
        data = np.interp(np.linspace(0, len(data) - 1, n_out), np.arange(len(data)), data).astype(np.float32)
    return data


_ES_WORDS = {
    "que", "qué", "de", "la", "el", "los", "las", "en", "es", "y", "un", "una", "por", "para",
    "con", "como", "cómo", "quien", "quién", "donde", "dónde", "hola", "tengo", "tienes", "mi",
    "tu", "viste", "ves", "eres", "hay", "esto", "eso", "no", "si", "sí", "mano", "imagen",
}


def guess_lang(text: str) -> str:
    """Idioma del texto escrito. FIX: antes solo miraba tildes, y "que viste en la imagen" salia ingles."""
    low = text.lower()
    if re.search(r"[áéíóúñ¿¡]", low):
        return "es"
    words = re.findall(r"[a-z]+", low)
    hits = sum(w in _ES_WORDS for w in words)
    return "es" if words and hits / len(words) >= 0.3 else "en"


def frame_to_data_uri(frame: np.ndarray, max_side: int = 384) -> str:
    """Fotograma de la webcam (RGB numpy) -> JPEG pequeño en base64.
    384 px: SmolVLM procesa la foto en un solo bloque (a 512+ la parte en varios y tarda el triple)."""
    from PIL import Image

    img = Image.fromarray(np.asarray(frame).astype(np.uint8)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


class VoicePipeline:
    def __init__(self, llm_base_url: str, whisper_size: str = "tiny", max_tokens: int = 80,
                 vision_base_url: str | None = None):
        from faster_whisper import WhisperModel

        log.info("Cargando faster-whisper %s (int8, CPU)...", whisper_size)
        self.whisper = WhisperModel(whisper_size, device="cpu", compute_type="int8", cpu_threads=2)
        self.llm_base_url = llm_base_url.rstrip("/")
        self.vision_base_url = vision_base_url.rstrip("/") if vision_base_url else None
        self.max_tokens = max_tokens
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0))

    # ------------------------------------------------------------- ASR
    def transcribe(self, audio16k: np.ndarray) -> tuple[str, str]:
        if len(audio16k) < int(0.4 * ASR_SR):
            return "", "en"
        # FIX: tiny multilingüe adivinaba idiomas raros (salió griego). Solo dejamos elegir
        # entre español e inglés: primero se miran las probabilidades y luego se transcribe
        # forzando el ganador.
        _, info = self.whisper.transcribe(audio16k, beam_size=1, vad_filter=True)
        probs = dict(info.all_language_probs or [])
        lang = "es" if probs.get("es", 0.0) >= probs.get("en", 0.0) else "en"
        segments, _ = self.whisper.transcribe(
            audio16k, beam_size=1, language=lang, vad_filter=True, condition_on_previous_text=False
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        if text.lower() in _NOISE and len(audio16k) < int(0.9 * ASR_SR):
            return "", lang
        return text, lang

    # ------------------------------------------------------------- Visión
    async def describe(self, frame: np.ndarray) -> str | None:
        """Los ojos: SmolVLM2 describe el fotograma en 1-2 frases."""
        if self.vision_base_url is None or frame is None:
            return None
        try:
            uri = await asyncio.to_thread(frame_to_data_uri, frame)
            resp = await self.http.post(f"{self.vision_base_url}/chat/completions", json={
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": uri}},
                    {"type": "text", "text": VISION_PROMPT},
                ]}],
                "max_tokens": 40,
                "temperature": 0.0,
            })
            resp.raise_for_status()
            text = sanitize(resp.json()["choices"][0]["message"]["content"] or "")
            return text or None
        except Exception as e:
            log.warning("Visión falló: %s: %s", type(e).__name__, e)
            return None

    # ------------------------------------------------------------- LLM
    async def stream_reply(self, user_text: str, history: list[dict], seen: str | None = None,
                           lang: str = "en"):
        content = f"[CAMERA: {seen}]\n{user_text}" if seen else user_text
        if seen:
            content += "\n(Responde en español.)" if lang == "es" else "\n(Answer in English.)"
        payload = {
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *history, {"role": "user", "content": content}],
            "stream": True,
            "temperature": 0.3,
            "max_tokens": self.max_tokens,
        }
        buffer = ""
        yielded = False
        try:
            async with self.http.stream("POST", f"{self.llm_base_url}/chat/completions", json=payload) as resp:
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
                        continue
                    delta = (choices[0].get("delta") or {}).get("content") or ""
                    if not delta:
                        continue
                    buffer += delta
                    while True:
                        clause, buffer = pop_clause(buffer)
                        if clause is None:
                            break
                        yielded = True
                        yield clause
            if "<think>" in buffer.lower() and "</think>" not in buffer.lower():
                buffer = ""
            rest = sanitize(buffer)
            if rest:
                yielded = True
                yield rest
        except Exception as e:
            log.warning("LLM falló: %s: %s", type(e).__name__, e)
            if not yielded:
                yield "Sorry, my local engine is not responding right now."

    # ------------------------------------------------------------- TTS
    async def tts(self, text: str, lang: str) -> np.ndarray | None:
        text = text.strip()
        if not text:
            return None
        try:
            mp3 = bytearray()
            async for chunk in edge_tts.Communicate(text, VOICES.get(lang, VOICES["en"]), rate="+8%").stream():
                if chunk["type"] == "audio":
                    mp3.extend(chunk["data"])
            if not mp3:
                return None
            decoded = await asyncio.to_thread(
                miniaudio.decode, bytes(mp3),
                output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=TTS_SR,
            )
            return np.frombuffer(decoded.samples.tobytes(), dtype=np.int16)
        except Exception as e:
            log.warning("TTS falló (%s); la respuesta queda solo en texto.", e)
            return None
