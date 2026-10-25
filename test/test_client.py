import asyncio
import numpy as np
import websockets

async def test_voice_session():
    uri = "ws://127.0.0.1:8765/ws/voice?token=vx-secret-dev-token-2026"
    async with websockets.connect(uri) as ws:
        print("[+] Conectado al Gateway de Voz de Vertex Coders.")
        
        # 1. Enviar 1 segundo de voz simulada (onda de alta energía)
        sr = 16000
        t = np.linspace(0, 0.064, int(sr * 0.064), False) # Chunks de 64ms (2048 bytes)
        speech_chunk = (0.5 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16).tobytes()
        silence_chunk = np.zeros(int(sr * 0.064), dtype=np.int16).tobytes()

        print("[*] Enviando ráfaga de voz...")
        for _ in range(15):
            await ws.send(speech_chunk)
            await asyncio.sleep(0.02)

        print("[*] Enviando silencio para disparar fin de turno (VAD)...")
        for _ in range(15):
            await ws.send(silence_chunk)
            await asyncio.sleep(0.02)

        # Escuchar respuestas y telemetría
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                print(f"[AUDIO TTS RECIBIDO] {len(msg)} bytes de audio PCM")
            else:
                print(f"[EVENTO JSON] {msg}")
                if "turn_complete" in msg:
                    break

if __name__ == "__main__":
    asyncio.run(test_voice_session())