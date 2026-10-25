"""
Prueba end-to-end del gateway SIN micrófono.

Genera una frase hablada real con edge-tts (requiere internet), la manda en
chunks de 64ms como si fuera un micrófono, y espera la respuesta del agente.
Si edge-tts falla, cae a un tono (que el servidor descartará con turn_discarded).

Uso (desde E:\\vertex-voice-mcp, con el servidor corriendo):
    .venv\\Scripts\\python.exe test\\test_client.py
    .venv\\Scripts\\python.exe test\\test_client.py "Who built you?"
"""
import asyncio
import json
import os
import sys

import numpy as np
import websockets

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1024  # 64ms -> 2048 bytes
TOKEN = os.getenv("VERTEX_VOICE_TOKEN", "vx-secret-dev-token-2026")
URI = f"ws://127.0.0.1:8765/ws/voice?token={TOKEN}"
RECV_TIMEOUT = 60


async def synth_question(text: str) -> bytes:
    try:
        import edge_tts
        import miniaudio

        mp3 = bytearray()
        async for chunk in edge_tts.Communicate(text, "en-US-AriaNeural").stream():
            if chunk["type"] == "audio":
                mp3.extend(chunk["data"])
        decoded = miniaudio.decode(
            bytes(mp3),
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=SAMPLE_RATE,
        )
        pcm = np.frombuffer(decoded.samples.tobytes(), dtype=np.int16).astype(np.float32)
        # Normalizar para pasar el umbral del VAD de energía
        peak = float(np.max(np.abs(pcm))) or 1.0
        pcm = (pcm / peak * 0.6 * 32767).astype(np.int16)
        print(f"[*] Frase sintetizada: '{text}' ({len(pcm) / SAMPLE_RATE:.2f}s)")
        return pcm.tobytes()
    except Exception as e:
        print(f"[!] edge-tts no disponible ({e}). Usando tono (se espera turn_discarded).")
        t = np.linspace(0, 1.0, SAMPLE_RATE, False)
        return (0.5 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16).tobytes()


async def test_voice_session(question: str):
    speech = await synth_question(question)
    chunk_bytes = CHUNK_SAMPLES * 2
    silence_chunk = b"\x00" * chunk_bytes

    async with websockets.connect(URI, max_size=4 * 1024 * 1024) as ws:
        print("[+] Conectado al Gateway de Voz de Vertex Coders.")

        print("[*] Enviando voz...")
        for i in range(0, len(speech), chunk_bytes):
            chunk = speech[i:i + chunk_bytes]
            if len(chunk) < chunk_bytes:
                chunk += b"\x00" * (chunk_bytes - len(chunk))
            await ws.send(chunk)
            await asyncio.sleep(0.064)  # tiempo real

        print("[*] Enviando silencio para disparar fin de turno (VAD)...")
        for _ in range(20):
            await ws.send(silence_chunk)
            await asyncio.sleep(0.064)

        total_audio = 0
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
            except asyncio.TimeoutError:
                print(f"[X] Sin respuesta en {RECV_TIMEOUT}s. ¿Está corriendo el servidor / LM Studio?")
                return 1

            if isinstance(msg, bytes):
                total_audio += len(msg)
                print(f"[AUDIO TTS] {len(msg)} bytes ({len(msg) / 32000:.2f}s)")
                continue

            data = json.loads(msg)
            print(f"[EVENTO] {data}")
            ev = data.get("event")
            if ev == "turn_complete":
                print(f"[OK] Turno completo. Audio total: {total_audio / 32000:.2f}s")
                return 0
            if ev in ("turn_discarded", "error"):
                print(f"[!] Turno terminado sin respuesta: {ev}")
                return 2


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "Hello, who are you and who built you?"
    sys.exit(asyncio.run(test_voice_session(q)))
