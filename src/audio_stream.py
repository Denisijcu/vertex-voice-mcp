from collections import deque
from src.config import settings

class UtteranceBuffer:
    def __init__(self):
        max_bytes = settings.SAMPLE_RATE * settings.BYTES_PER_SAMPLE * settings.MAX_UTTERANCE_SECONDS
        self.max_Bytes = max_bytes
        self.chunks = deque()
        self.current_bytes = 0
        self.silence_counter = 0
        self.has_active_speech = False

    def add_chunk(self, chunk: bytes, is_speech: bool) -> bool:
        """
        Agrega un chunk al buffer y devuelve True si se completó una frase (End-of-Turn).
        """
        if is_speech:
            self.has_active_speech = True
            self.silence_counter = 0
            self.chunks.append(chunk)
            self.current_bytes += len(chunk)
            
            # Si supera el límite de seguridad de 15s, forzamos fin de turno
            if self.current_bytes >= self.max_Bytes:
                return True
        else:
            if self.has_active_speech:
                self.silence_counter += 1
                self.chunks.append(chunk) # Guardamos la cola del audio para no cortar sílabas
                self.current_bytes += len(chunk)
                
                if self.silence_counter >= settings.SILENCE_FRAMES_TO_TRIGGER:
                    return True
        return False

    def pop_utterance(self) -> bytes:
        payload = b"".join(self.chunks)
        self.reset()
        return payload

    def reset(self):
        self.chunks.clear()
        self.current_bytes = 0
        self.silence_counter = 0
        self.has_active_speech = False