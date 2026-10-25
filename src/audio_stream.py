from collections import deque

from src.config import settings


class UtteranceBuffer:
    """
    Acumula el audio de una frase y detecta el fin de turno.

    FIX: el VAD exige 3 chunks seguidos de energía antes de decir "voz", así que
    los primeros ~130ms de cada frase se tiraban (Whisper recibía "...at is the
    weather" en vez de "what is the weather"). Ahora hay un pre-roll: los últimos
    chunks previos a la voz se guardan y se anteponen cuando arranca la frase.
    """

    def __init__(self, preroll_chunks: int = settings.PREROLL_CHUNKS):
        self.max_bytes = settings.SAMPLE_RATE * settings.BYTES_PER_SAMPLE * settings.MAX_UTTERANCE_SECONDS
        self.chunks: list[bytes] = []
        self.preroll: deque[bytes] = deque(maxlen=max(preroll_chunks, 0))
        self.current_bytes = 0
        self.silence_counter = 0
        self.has_active_speech = False

    def _append(self, chunk: bytes) -> None:
        self.chunks.append(chunk)
        self.current_bytes += len(chunk)

    def add_chunk(self, chunk: bytes, is_speech: bool) -> bool:
        """
        Agrega un chunk y devuelve True si se completó una frase (End-of-Turn).
        """
        if is_speech:
            if not self.has_active_speech:
                self.has_active_speech = True
                for prev in self.preroll:
                    self._append(prev)
                self.preroll.clear()

            self.silence_counter = 0
            self._append(chunk)
            # Límite de seguridad (15s): forzamos fin de turno
            return self.current_bytes >= self.max_bytes

        if not self.has_active_speech:
            # Todavía no hay frase: solo alimentamos el pre-roll
            if self.preroll.maxlen:
                self.preroll.append(chunk)
            return False

        # Silencio después de voz: guardamos la cola para no cortar sílabas
        self.silence_counter += 1
        self._append(chunk)
        # FIX: el límite de 15s también aplica durante la cola de silencio
        return (
            self.silence_counter >= settings.SILENCE_FRAMES_TO_TRIGGER
            or self.current_bytes >= self.max_bytes
        )

    def pop_utterance(self) -> bytes:
        payload = b"".join(self.chunks)
        self.reset()
        return payload

    def reset(self) -> None:
        self.chunks.clear()
        self.preroll.clear()
        self.current_bytes = 0
        self.silence_counter = 0
        self.has_active_speech = False
