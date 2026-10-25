"""
Cliente de voz en vivo (micrófono + altavoces) para el gateway.

Uso (desde E:\\vertex-voice-mcp, con el servidor corriendo):
    .venv\\Scripts\\python.exe test\\live_mic.py
"""
import asyncio
import json
import os
import queue
import sys
import threading
import time

import numpy as np
import sounddevice as sd
import websockets

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1024
TOKEN = os.getenv("VERTEX_VOICE_TOKEN", "vx-secret-dev-token-2026")
WS_URI = f"ws://127.0.0.1:8765/ws/voice?token={TOKEN}"

# Umbral RMS para dejar pasar tu voz MIENTRAS el agente habla (anti-eco)
ECHO_GATE_RMS = 0.12
# Mantiene el gate 0.5s extra tras terminar el audio del agente
ECHO_COOLDOWN_S = 0.5

mic_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
speaker_byte_queue: queue.Queue[bytes] = queue.Queue()
current_out_buffer = bytearray()
# FIX: current_out_buffer lo tocan el hilo de audio de salida, el de entrada
# y el loop de asyncio (clear). Sin lock había carreras al hacer barge-in.
out_lock = threading.Lock()

last_agent_speech_time = 0.0


last_agent_speech_time = 0.0

def is_agent_speaking() -> bool:
    global last_agent_speech_time
    speaking = not speaker_byte_queue.empty() or len(current_out_buffer) > 0
    if speaking:
        last_agent_speech_time = time.time()
        return True
    
    # COOLDOWN DE SEGURIDAD: Mantiene el mic silenciado 0.5s adicionales tras terminar el audio
    if time.time() - last_agent_speech_time < 0.5:
        return True
        
    return False


def clear_speaker_buffer():
    with out_lock:
        while True:
            try:
                speaker_byte_queue.get_nowait()
            except queue.Empty:
                break
        current_out_buffer.clear()


def audio_output_callback(outdata: np.ndarray, frames: int, time_info, status):
    bytes_needed = frames * 2
    with out_lock:
        while len(current_out_buffer) < bytes_needed:
            try:
                current_out_buffer.extend(speaker_byte_queue.get_nowait())
            except queue.Empty:
                break

        if len(current_out_buffer) >= bytes_needed:
            out_bytes = bytes(current_out_buffer[:bytes_needed])
            del current_out_buffer[:bytes_needed]
        elif current_out_buffer:
            out_bytes = bytes(current_out_buffer) + b"\x00" * (bytes_needed - len(current_out_buffer))
            current_out_buffer.clear()
        else:
            out_bytes = None

    if out_bytes is None:
        outdata.fill(0)
    else:
        outdata[:, 0] = np.frombuffer(out_bytes, dtype=np.int16)


async def run_live_voice_agent():
    loop = asyncio.get_running_loop()

    def enqueue(data: bytes):
        if not mic_queue.full():
            mic_queue.put_nowait(data)

    def audio_input_callback(indata: np.ndarray, frames: int, time_info, status):
        # ECHO SUPPRESSION GATE: mientras el agente habla, solo pasa voz muy fuerte
        if is_agent_speaking():
            pcm_float = indata.astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(pcm_float ** 2)))
            if rms < ECHO_GATE_RMS:
                loop.call_soon_threadsafe(enqueue, bytes(indata.nbytes))
                return
        loop.call_soon_threadsafe(enqueue, indata.copy().tobytes())

    print("=================================================================")
    print(" 🎙️  VERTEX CODERS LLC - LIVE VOICE AGENT (ECHO-GATE ACTIVE) ")
    print("=================================================================")
    async with websockets.connect(WS_URI, max_size=4 * 1024 * 1024) as ws:
        print("[+] ¡Conectado y calibrado! Habla claro en inglés.")
        print("[+] Filtro anti-eco y anti-ventilador activado.")
        print("-----------------------------------------------------------------\n")

        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=CHUNK_SAMPLES, callback=audio_input_callback,
        ), sd.OutputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=CHUNK_SAMPLES, callback=audio_output_callback,
        ):
            async def send_mic_stream():
                while True:
                    await ws.send(await mic_queue.get())

            async def receive_agent_stream():
                while True:
                    msg = await ws.recv()
                    if isinstance(msg, bytes):
                        speaker_byte_queue.put(msg)
                        continue

                    data = json.loads(msg)
                    ev = data.get("event")
                    if ev == "asr_transcript":
                        clear_speaker_buffer()
                        print(f"\n🧑 [TÚ ({data.get('asr_latency_ms')}ms)]: {data.get('text')}")
                    elif ev == "tool_call":
                        print(f"💪 [MÚSCULO]: {data.get('tool')}({data.get('args')}) — {data.get('tool_latency_ms')} ms")
                    elif ev == "agent_text_chunk":
                        print(f"🤖 [VERTEX-CORE]: {data.get('text')}")
                    elif ev == "metrics":
                        print(f"⚡ [TELEMETRÍA]: TTFA = {data.get('ttfa_ms')} ms")
                    elif ev == "barge_in_interrupted":
                        clear_speaker_buffer()
                        print("🛑 [BARGE-IN]: Interrumpiste al agente.")
                    elif ev == "turn_discarded":
                        print("· (ruido descartado)")
                    elif ev == "error":
                        print(f"❌ [SERVIDOR]: {data.get('message')}")

            await asyncio.gather(send_mic_stream(), receive_agent_stream())


if __name__ == "__main__":
    try:
        asyncio.run(run_live_voice_agent())
    except KeyboardInterrupt:
        print("\n[!] Sesión finalizada.")
        sys.exit(0)
