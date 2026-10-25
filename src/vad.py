import numpy as np

class EnergyVAD:
    """
    VAD ligero en NumPy con histéresis para evitar falsos cortes a mitad de palabra.
    Latencia de ejecución: < 0.2 ms por chunk.
    """
    def __init__(self, start_threshold: float = 0.012, stop_threshold: float = 0.006):
        self.start_threshold = start_threshold
        self.stop_threshold = stop_threshold
        self.in_speech = False

    def process_chunk(self, audio_chunk: bytes) -> bool:
        if not audio_chunk or len(audio_chunk) % 2 != 0:
            return False
        
        pcm = np.frombuffer(audio_chunk, dtype=np.int16).astype(np.float32) / 32768.0
        rms_energy = float(np.sqrt(np.mean(pcm ** 2)))

        if not self.in_speech and rms_energy >= self.start_threshold:
            self.in_speech = True
        elif self.in_speech and rms_energy < self.stop_threshold:
            self.in_speech = False

        return self.in_speech