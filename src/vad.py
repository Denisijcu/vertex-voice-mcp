import numpy as np


class EnergyVAD:
    """
    VAD por energía con histéresis + filtro de ráfaga (debounce) que ignora
    el ventilador de la laptop, clics de teclado y ruidos cortos.
    """

    def __init__(
        self,
        start_threshold: float = 0.035,
        stop_threshold: float = 0.015,
        min_voice_frames: int = 3,
    ):
        self.start_threshold = start_threshold
        self.stop_threshold = stop_threshold
        self.min_voice_frames = min_voice_frames
        self.in_speech = False
        self.consecutive_voice_frames = 0

    def reset(self) -> None:
        self.in_speech = False
        self.consecutive_voice_frames = 0

    def process_chunk(self, audio_chunk: bytes) -> bool:
        if not audio_chunk or len(audio_chunk) % 2 != 0:
            return False

        pcm = np.frombuffer(audio_chunk, dtype=np.int16).astype(np.float32) / 32768.0
        rms_energy = float(np.sqrt(np.mean(pcm ** 2)))

        if rms_energy >= self.start_threshold:
            self.consecutive_voice_frames += 1
            # Exigimos N paquetes consecutivos (~190ms con chunks de 64ms)
            # antes de declarar voz activa o disparar Barge-In
            if self.consecutive_voice_frames >= self.min_voice_frames:
                self.in_speech = True
        elif rms_energy < self.stop_threshold:
            self.consecutive_voice_frames = 0
            self.in_speech = False
        else:
            # FIX: zona de histéresis. Si todavía NO estamos en voz, la racha se rompe.
            # Antes, ruido intermitente (alto, medio, alto, medio, alto) acumulaba
            # frames "consecutivos" sin serlo y colaba ruido como voz.
            if not self.in_speech:
                self.consecutive_voice_frames = 0

        return self.in_speech
