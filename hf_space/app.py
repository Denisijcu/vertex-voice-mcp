"""
Vertex-Core Voice + Vision — Space de Hugging Face (CPU Basic gratis).

Arranque:
  1. Descarga los GGUF: Vertex-Core (Qwen3-4B Q4_K_M, 2.5 GB) y los ojos SmolVLM2-500M (Q8 + mmproj, ~550 MB).
  2. Lanza dos llama-server en local (OpenAI-compatible):
       :8080 -> Vertex-Core (habla)     :8081 -> SmolVLM2 (mira)
  3. Carga faster-whisper tiny (int8) y levanta Gradio en :7860.

RAM aproximada: ~2.8 GB Vertex-Core + ~0.7 GB SmolVLM2 + ~0.2 GB Whisper + ~0.3 GB Gradio.
"""
import asyncio
import atexit
import logging
import os
import subprocess
import time

import gradio as gr
import httpx
from huggingface_hub import hf_hub_download

from pipeline import SYSTEM_PROMPT, TTS_SR, VoicePipeline, guess_lang, to_16k_mono

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("app")

LLAMA_BIN = os.getenv("LLAMA_SERVER_BIN", "/app/llama-server")

# Cerebro que habla
MODEL_REPO = os.getenv("MODEL_REPO", "Denisijcu/vertex-core-gguf")
MODEL_FILE = os.getenv("MODEL_FILE", "vertex-core-q4_k_m.gguf")
LLM_PORT = int(os.getenv("LLM_PORT", "8080"))

# Ojos
VISION_REPO = os.getenv("VISION_REPO", "ggml-org/SmolVLM2-500M-Video-Instruct-GGUF")
VISION_FILE = os.getenv("VISION_FILE", "SmolVLM2-500M-Video-Instruct-Q8_0.gguf")
VISION_MMPROJ = os.getenv("VISION_MMPROJ", "mmproj-SmolVLM2-500M-Video-Instruct-Q8_0.gguf")
VISION_PORT = int(os.getenv("VISION_PORT", "8081"))
VISION_ENABLED = os.getenv("VISION_ENABLED", "1") == "1"

CTX = os.getenv("LLAMA_CTX", "2048")              # contexto corto = menos RAM de KV cache
WHISPER_SIZE = os.getenv("WHISPER_SIZE", "tiny")  # multilingüe: entiende español e inglés
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "80"))
HISTORY_TURNS = 3


def cpu_limit() -> int:
    """CPUs REALES del contenedor. os.cpu_count() devuelve los del host (16, 32...),
    no los 2 vCPU del Space: con tantos hilos llama.cpp se pisa a sí mismo y va lentísimo."""
    try:
        quota, period = open("/sys/fs/cgroup/cpu.max").read().split()
        if quota != "max":
            return max(1, int(int(quota) / int(period)))
    except Exception:
        pass
    try:
        return len(os.sched_getaffinity(0))
    except Exception:
        return 2


# Los dos servidores nunca trabajan a la vez (primero mira, luego habla), así que ambos usan todos los hilos.
THREADS = os.getenv("LLAMA_THREADS", str(cpu_limit()))


def launch_server(name: str, port: int, model_path: str, extra: list[str]) -> subprocess.Popen:
    cmd = [
        LLAMA_BIN, "-m", model_path,
        "--host", "127.0.0.1", "--port", str(port),
        "-c", CTX, "-t", THREADS, "-np", "1", "--jinja",
        *extra,
    ]
    log.info("[%s] Lanzando: %s", name, " ".join(cmd))
    proc = subprocess.Popen(cmd)
    atexit.register(proc.terminate)
    return proc


def wait_ready(name: str, port: int, proc: subprocess.Popen, t0: float) -> None:
    deadline = time.time() + 600
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"[{name}] llama-server terminó con código {proc.returncode}")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                log.info("[%s] listo en %.1f s (%s hilos)", name, time.perf_counter() - t0, THREADS)
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError(f"[{name}] llama-server no respondió en 10 minutos")


def warm_up() -> None:
    """Deja el system prompt de Vertex-Core ya procesado en la caché KV del slot."""
    t0 = time.perf_counter()
    try:
        httpx.post(
            f"http://127.0.0.1:{LLM_PORT}/v1/chat/completions",
            json={"messages": [{"role": "system", "content": SYSTEM_PROMPT},
                               {"role": "user", "content": "Hi"}],
                  "max_tokens": 4, "cache_prompt": True},
            timeout=300,
        )
        log.info("Calentamiento listo en %.1f s", time.perf_counter() - t0)
    except httpx.HTTPError as e:
        log.warning("Calentamiento falló: %s", e)


def start_servers() -> None:
    t0 = time.perf_counter()
    llm_path = hf_hub_download(MODEL_REPO, MODEL_FILE)
    llm = launch_server("habla", LLM_PORT, llm_path, [])

    vision = None
    if VISION_ENABLED:
        vis_path = hf_hub_download(VISION_REPO, VISION_FILE)
        mmproj_path = hf_hub_download(VISION_REPO, VISION_MMPROJ)
        vision = launch_server("mira", VISION_PORT, vis_path, ["--mmproj", mmproj_path])

    wait_ready("habla", LLM_PORT, llm, t0)
    if vision is not None:
        wait_ready("mira", VISION_PORT, vision, t0)
    warm_up()


start_servers()
PIPE = VoicePipeline(
    f"http://127.0.0.1:{LLM_PORT}/v1", WHISPER_SIZE, MAX_TOKENS,
    vision_base_url=f"http://127.0.0.1:{VISION_PORT}/v1" if VISION_ENABLED else None,
)


async def run_turn(user_text, lang, history, t0, t_asr, look, frame):
    """Generador común: (mirar) -> texto del usuario -> cláusulas del modelo -> voz por cláusula."""
    parts = [f"ASR {t_asr:.0f} ms"] if t_asr is not None else []

    # La cámara es opcional: solo mira si el usuario activó "Mirar" Y hay un fotograma.
    # Sin cámara responde igual que siempre, solo con la voz/texto.
    seen = None
    if look and frame is not None:
        yield user_text, "", gr.skip(), " · ".join(parts + ["mirando…"]), gr.skip(), history
        t_v = time.perf_counter()
        seen = await PIPE.describe(frame)
        parts.append(f"vista {(time.perf_counter() - t_v) * 1000:.0f} ms")
    elif look:
        parts.append("sin cámara")

    yield user_text, "", gr.skip(), " · ".join(parts + ["pensando…"]), (seen or ""), history

    reply, first_audio_ms = "", None
    async for clause in PIPE.stream_reply(user_text, history, seen, lang):
        reply = f"{reply} {clause}".strip()
        yield gr.skip(), reply, gr.skip(), gr.skip(), gr.skip(), gr.skip()

        audio = await PIPE.tts(clause, lang)
        if audio is not None:
            if first_audio_ms is None:
                first_audio_ms = (time.perf_counter() - t0) * 1000
            yield gr.skip(), gr.skip(), (TTS_SR, audio), gr.skip(), gr.skip(), gr.skip()

    if first_audio_ms:
        parts.append(f"primer audio {first_audio_ms:.0f} ms")
    parts.append(f"total {(time.perf_counter() - t0) * 1000:.0f} ms")
    # Al historial va la pregunta con lo que vio, para que pueda volver a hablar de ello
    user_turn = f"[CAMERA: {seen}]\n{user_text}" if seen else user_text
    history = (history + [{"role": "user", "content": user_turn},
                          {"role": "assistant", "content": reply}])[-2 * HISTORY_TURNS:]
    yield gr.skip(), gr.skip(), gr.skip(), " · ".join(parts), gr.skip(), history


async def respond_voice(audio, look, frame, history):
    history = history or []
    if audio is None:
        yield "", "", gr.skip(), "No llegó audio.", gr.skip(), history
        return
    t0 = time.perf_counter()
    sr, data = audio
    text, lang = await asyncio.to_thread(PIPE.transcribe, to_16k_mono(sr, data))
    t_asr = (time.perf_counter() - t0) * 1000
    if not text:
        yield "", "", gr.skip(), f"ASR {t_asr:.0f} ms · no te escuché bien, prueba otra vez.", gr.skip(), history
        return
    async for out in run_turn(text, lang, history, t0, t_asr, look, frame):
        yield out


async def respond_text(text, look, frame, history):
    history = history or []
    text = (text or "").strip()
    if not text:
        yield "", "", gr.skip(), "Escribe algo primero.", gr.skip(), history
        return
    async for out in run_turn(text, guess_lang(text), history, time.perf_counter(), None, look, frame):
        yield out


with gr.Blocks(title="Vertex-Core Voice") as demo:
    gr.Markdown(
        "# 🎙️👁️ Vertex-Core Voice\n"
        "Agente de voz de **Vertex Coders LLC** que también **mira**. Cerebro: "
        "[Denisijcu/vertex-core-gguf](https://huggingface.co/Denisijcu/vertex-core-gguf) "
        "(Qwen3-4B afinado, Q4_K_M). Ojos: "
        "[SmolVLM2-500M](https://huggingface.co/ggml-org/SmolVLM2-500M-Video-Instruct-GGUF). "
        "Todo en **CPU** con llama.cpp. Habla o escribe en español o inglés."
    )
    with gr.Row():
        mic = gr.Audio(sources=["microphone"], type="numpy", label="Habla (se envía al parar)")
        with gr.Column():
            typed = gr.Textbox(label="…o escribe", lines=2, placeholder="Who built you?")
            send = gr.Button("Enviar", variant="primary")
    # Cámara OPCIONAL: cerrada y desactivada por defecto. El navegador no pide permiso
    # hasta que el usuario abre esto y enciende la webcam.
    with gr.Accordion("👁️ Cámara (opcional)", open=False):
        look = gr.Checkbox(value=False, label="👁️ Mirar al responder (una foto por pregunta)")
        cam = gr.Image(sources=["webcam"], type="numpy", streaming=True, label="Cámara", height=320)
        seen_box = gr.Textbox(label="Lo que vio", interactive=False)
    heard = gr.Textbox(label="Te entendí", interactive=False)
    reply = gr.Textbox(label="Vertex-Core", interactive=False, lines=3)
    voice = gr.Audio(label="Voz", streaming=True, autoplay=True, interactive=False, format="wav")
    metrics = gr.Markdown()
    state = gr.State([])
    frame = gr.State(None)  # último fotograma de la webcam
    clear = gr.Button("Nueva conversación")

    # La webcam solo guarda su último fotograma (1 por segundo); no se analiza hasta que preguntas.
    cam.stream(lambda f: f, cam, frame, stream_every=1.0, concurrency_limit=None, show_progress="hidden")
    # Si apagas o limpias la cámara, se olvida el último fotograma (para no describir una foto vieja)
    cam.clear(lambda: None, None, frame)

    outputs = [heard, reply, voice, metrics, seen_box, state]
    mic.stop_recording(respond_voice, [mic, look, frame, state], outputs)
    send.click(respond_text, [typed, look, frame, state], outputs)
    typed.submit(respond_text, [typed, look, frame, state], outputs)
    clear.click(lambda: ("", "", None, "", "", []), None, outputs)

demo.queue(default_concurrency_limit=1, max_size=8)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
