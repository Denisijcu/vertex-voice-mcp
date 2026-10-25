import time
import asyncio
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, status
from src.config import settings
from src.vad import EnergyVAD
from src.audio_stream import UtteranceBuffer
from src.inference import LocalVoicePipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("VertexVoiceGateway")

app = FastAPI(title="Vertex Voice Agent Gateway (hf-voice PoC)", version="1.1.0")
pipeline = LocalVoicePipeline()

@app.get("/health")
async def health_check():
    return {"status": "ok", "engine": "vertex-voice-mcp", "sample_rate": settings.SAMPLE_RATE}

@app.websocket("/ws/voice")
async def voice_stream_endpoint(
    websocket: WebSocket,
    token: str = Query(default="")
):
    # 1. Validación de Seguridad (PoLP & Auth)
    auth_header = websocket.headers.get("Authorization", "")
    provided_token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else token
    if provided_token != settings.API_TOKEN:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        logger.warning("Intento de conexión rechazado: Token inválido.")
        return

    await websocket.accept()
    client_id = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
    logger.info(f"[{client_id}] Sesión de voz iniciada.")

    vad = EnergyVAD()
    utterance_buffer = UtteranceBuffer()
    active_inference_task: asyncio.Task | None = None

    async def run_agent_turn(raw_pcm: bytes):
        t0 = time.perf_counter()
        try:
            # Paso 1: ASR
            transcript = await pipeline.transcribe(raw_pcm)
            t_asr = (time.perf_counter() - t0) * 1000
            if not transcript:
                return

            await websocket.send_json({
                "event": "asr_transcript",
                "text": transcript,
                "asr_latency_ms": round(t_asr, 1)
            })

            # Paso 2 & 3: Streaming LLM -> Streaming TTS
            first_chunk = True
            async for clause in pipeline.stream_llm_response(transcript):
                await websocket.send_json({"event": "agent_text_chunk", "text": clause})
                tts_pcm = await pipeline.synthesize_speech_chunk(clause)
                
                if first_chunk:
                    ttft_total = (time.perf_counter() - t0) * 1000
                    await websocket.send_json({"event": "metrics", "ttfa_ms": round(ttft_total, 1)})
                    first_chunk = False
                
                await websocket.send_bytes(tts_pcm)

            await websocket.send_json({"event": "turn_complete"})

        except asyncio.CancelledError:
            logger.info(f"[{client_id}] Barge-in ejecutado: Turno del agente cancelado en vivo.")
            await websocket.send_json({"event": "barge_in_interrupted"})
            raise

    try:
        while True:
            audio_chunk = await websocket.receive_bytes()

            # Protección DoS por tamaño de paquete
            if len(audio_chunk) > settings.MAX_CHUNK_BYTES:
                await websocket.send_json({"error": "Chunk exceeds MAX_CHUNK_BYTES limit"})
                continue

            is_speech = vad.process_chunk(audio_chunk)

            # Manejo de Barge-In: Si el usuario habla mientras el agente está respondiendo, abortamos al agente
            if is_speech and active_inference_task and not active_inference_task.done():
                active_inference_task.cancel()

            turn_ready = utterance_buffer.add_chunk(audio_chunk, is_speech)

            if turn_ready:
                pcm_payload = utterance_buffer.pop_utterance()
                active_inference_task = asyncio.create_task(run_agent_turn(pcm_payload))

    except WebSocketDisconnect:
        logger.info(f"[{client_id}] Cliente desconectado limpiamente.")
    except Exception as e:
        logger.error(f"[{client_id}] Excepción en bucle en tiempo real: {e}")
    finally:
        if active_inference_task and not active_inference_task.done():
            active_inference_task.cancel()