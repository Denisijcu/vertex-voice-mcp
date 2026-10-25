import asyncio
import contextlib
import logging
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, WebSocket, status
from starlette.websockets import WebSocketState

from src.audio_stream import UtteranceBuffer
from src.config import settings
from src.inference import LocalVoicePipeline
from src.router import route
from src.vad import EnergyVAD

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("VertexVoiceGateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # FIX: antes el pipeline (Whisper + httpx) se creaba al importar el módulo
    # y el cliente HTTP nunca se cerraba. Ahora vive con el ciclo de la app.
    app.state.pipeline = LocalVoicePipeline()
    yield
    await app.state.pipeline.aclose()


app = FastAPI(title="Vertex Voice Agent Gateway (hf-voice PoC)", version="1.2.0", lifespan=lifespan)


def _token_ok(provided: str) -> bool:
    # FIX seguridad: comparación en tiempo constante (evita timing attacks)
    return bool(provided) and secrets.compare_digest(provided.encode(), settings.API_TOKEN.encode())


async def _send_json(ws: WebSocket, data: dict) -> bool:
    if ws.client_state != WebSocketState.CONNECTED:
        return False
    try:
        await ws.send_json(data)
        return True
    except Exception:
        return False


async def _send_bytes(ws: WebSocket, data: bytes) -> bool:
    if not data or ws.client_state != WebSocketState.CONNECTED:
        return False
    try:
        await ws.send_bytes(data)
        return True
    except Exception:
        return False


async def _cancel_task(task: asyncio.Task | None) -> None:
    """Cancela un turno y ESPERA a que termine, para que no se solape con el siguiente."""
    if task and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


@app.get("/health")
async def health_check():
    pipeline: LocalVoicePipeline = app.state.pipeline
    return {
        "status": "ok",
        "engine": "vertex-voice-mcp",
        "sample_rate": settings.SAMPLE_RATE,
        "asr_ready": pipeline.whisper is not None,
        "llm_model": settings.LLM_MODEL_NAME,
    }


@app.websocket("/ws/voice")
async def voice_stream_endpoint(websocket: WebSocket, token: str = Query(default="")):
    # 1. Autenticación (header Bearer o ?token=)
    auth_header = websocket.headers.get("Authorization", "")
    provided_token = auth_header[7:] if auth_header.startswith("Bearer ") else token
    if not _token_ok(provided_token):
        logger.warning("Intento de conexión rechazado: Token inválido.")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    client_id = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
    logger.info(f"[{client_id}] Sesión de voz iniciada.")

    pipeline: LocalVoicePipeline = websocket.app.state.pipeline
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
                logger.info(f"[{client_id}] Ruido ambiental descartado por filtro ASR.")
                # FIX: antes no se avisaba nada y el cliente se quedaba esperando para siempre
                await _send_json(websocket, {"event": "turn_discarded", "reason": "no_speech"})
                return

            await _send_json(websocket, {
                "event": "asr_transcript",
                "text": transcript,
                "asr_latency_ms": round(t_asr, 1),
            })

            # Paso 1.5: Músculos (router de herramientas MCP). None = el modelo contesta solo.
            tool = await route(transcript)
            if tool is not None:
                await _send_json(websocket, {
                    "event": "tool_call",
                    "tool": tool.tool,
                    "args": tool.args,
                    "tool_latency_ms": round(tool.latency_ms, 1),
                    "ok": tool.ok,
                })

            # Paso 2 & 3: Streaming LLM -> Streaming TTS
            first_chunk = True
            async for clause in pipeline.stream_llm_response(transcript, tool):
                await _send_json(websocket, {"event": "agent_text_chunk", "text": clause})
                tts_pcm = await pipeline.synthesize_speech_chunk(clause)

                if first_chunk:
                    ttfa = (time.perf_counter() - t0) * 1000
                    await _send_json(websocket, {"event": "metrics", "ttfa_ms": round(ttfa, 1)})
                    first_chunk = False

                await _send_bytes(websocket, tts_pcm)

            await _send_json(websocket, {"event": "turn_complete"})

        except asyncio.CancelledError:
            logger.info(f"[{client_id}] Barge-in: turno del agente cancelado en vivo.")
            await _send_json(websocket, {"event": "barge_in_interrupted"})
            raise
        except Exception:
            # FIX: antes una excepción aquí moría en silencio ("Task exception was never retrieved")
            logger.exception(f"[{client_id}] Error en el turno del agente")
            await _send_json(websocket, {"event": "error", "message": "agent_turn_failed"})

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                logger.info(f"[{client_id}] Cliente desconectado limpiamente.")
                break

            audio_chunk = message.get("bytes")
            if audio_chunk is None:
                # FIX: antes un mensaje de texto rompía receive_bytes() y mataba la sesión
                await _send_json(websocket, {"event": "error", "message": "Only binary PCM frames are accepted"})
                continue

            # Protección DoS por tamaño de paquete
            if len(audio_chunk) > settings.MAX_CHUNK_BYTES:
                await _send_json(websocket, {"event": "error", "message": "Chunk exceeds MAX_CHUNK_BYTES limit"})
                continue

            is_speech = vad.process_chunk(audio_chunk)

            # Barge-In: si el usuario habla mientras el agente responde, abortamos al agente
            if is_speech and active_inference_task and not active_inference_task.done():
                await _cancel_task(active_inference_task)

            if utterance_buffer.add_chunk(audio_chunk, is_speech):
                pcm_payload = utterance_buffer.pop_utterance()
                # FIX: nunca dos turnos a la vez (antes podían solaparse dos respuestas)
                await _cancel_task(active_inference_task)
                active_inference_task = asyncio.create_task(run_agent_turn(pcm_payload))

    except Exception as e:
        logger.error(f"[{client_id}] Excepción en bucle en tiempo real: {e!r}")
    finally:
        await _cancel_task(active_inference_task)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.server:app", host=settings.HOST, port=settings.PORT)
