# vertex-voice-mcp

Gateway de voz en tiempo real de Vertex Coders LLC:
**micrófono → VAD → faster-whisper (CPU) → Vertex-Core en LM Studio (streaming) → Edge Neural TTS → altavoz**, sobre WebSocket con barge-in.

## Requisitos
- Python con el venv en `.venv`
- LM Studio sirviendo en `http://127.0.0.1:1234/v1` con el modelo `vertex-core-q4km-v8` cargado
- Internet para el TTS (Edge Neural TTS es un servicio de Microsoft, no es local)

## Instalación (desde `E:\vertex-voice-mcp`)
```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Arrancar el servidor (desde `E:\vertex-voice-mcp`)
```powershell
.venv\Scripts\python.exe -m uvicorn src.server:app --host 127.0.0.1 --port 8765
```
Salud: `http://127.0.0.1:8765/health` (`asr_ready` debe ser `true`).

## Probar (otra terminal, desde `E:\vertex-voice-mcp`)
```powershell
# Sin micrófono: manda una frase sintetizada y muestra la respuesta
.venv\Scripts\python.exe test\test_client.py "Who built you?"

# Con micrófono y altavoces
.venv\Scripts\python.exe test\live_mic.py
```

## Configuración (`.env` en la raíz)
| Variable | Default |
|---|---|
| `VERTEX_VOICE_TOKEN` | `vx-secret-dev-token-2026` (cámbialo) |
| `HOST` / `PORT` | `127.0.0.1` / `8765` |
| `LLM_BASE_URL` | `http://127.0.0.1:1234/v1` |
| `LLM_MODEL_NAME` | `vertex-core-q4km-v8` |
| `TTS_VOICE` | `en-US-ChristopherNeural` |
| `WHISPER_MODEL_SIZE` | `tiny.en` |

## Músculos: router de herramientas MCP (`src/router.py`)
Antes de pensar, cada frase pasa por un router ligero, con reglas en español e inglés, que decide si hace falta un músculo de `mcp_servers/`:

| Pregunta | Herramienta |
|---|---|
| "¿Qué tiempo hace en La Habana?" / "Will it rain tomorrow?" | `weather_now` / `weather_forecast` |
| "¿Qué hora es en Tokio?" / "¿Cuántas horas de diferencia hay con Madrid?" | `current_time` / `time_difference` |
| "¿Qué noticias hay?" / "Noticias de Hugging Face" | `news_headlines` / `news_search` |
| "¿Quién fue José Martí?" / "Tell me about Alan Turing" | `wiki_lookup` |

El resultado va a Vertex-Core como contexto `[TOOL ...]` y él lo cuenta con su voz. Si ninguna regla encaja, contesta solo, como siempre. El cliente recibe el evento `tool_call` con la herramienta, los argumentos y la latencia.

```powershell
# Probar el router sin servidor ni LM Studio (desde E:\vertex-voice-mcp)
.venv\Scripts\python.exe test\test_router.py          # qué herramienta elige
.venv\Scripts\python.exe test\test_router.py --run    # y qué datos le llegan al modelo
```

## Versión web: Hugging Face Space (`hf_space/`)
La misma idea en un Space Docker sobre CPU gratis, con Gradio: Whisper tiny + Vertex-Core (GGUF en llama-server) + Edge TTS por frases, y **cámara opcional** con SmolVLM2-500M como ojos. Todo en unos 4 GB de RAM.
En vivo: https://huggingface.co/spaces/Denisijcu/vertex-core-voice. Detalles y latencias en `hf_space/README.md`.

Para subir cambios, desde `E:\vertex-voice-mcp\hf_space`:
```powershell
..\.venv\Scripts\python.exe -c "from huggingface_hub import upload_folder; upload_folder(repo_id='Denisijcu/vertex-core-voice', repo_type='space', folder_path='.')"
```

## Protocolo WebSocket `/ws/voice`
Auth: `?token=...` o header `Authorization: Bearer ...`.
Cliente → servidor: frames binarios PCM 16 kHz, 16-bit, mono (≤ 8192 bytes).
Servidor → cliente: frames binarios PCM (voz del agente) y eventos JSON:
`asr_transcript`, `tool_call`, `agent_text_chunk`, `metrics`, `turn_complete`, `turn_discarded`, `barge_in_interrupted`, `error`.


# Vertex Voice Agent MCP (`vertex-voice-mcp`)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Real-time, low-latency, full-duplex voice agent gateway built for local AI model orchestration. Developed under the architectural standards of **Vertex Coders LLC** (Miami, FL).

## 🚀 Key Features

- **Full-Duplex WebSockets:** Async bidirectional audio streaming with FastAPI.
- **Voice Activity Detection (VAD):** Custom energy-based VAD with hysteresis and debounce filtering to eliminate environmental noise and fan hum.
- **Barge-In Support:** Instant sub-15ms task cancellation allowing users to interrupt the agent mid-sentence.
- **Local LLM & ASR Integration:** Powered by `faster-whisper` (int8 on CPU) and local OpenAI-compatible endpoints (LM Studio / `llama.cpp` / `Vertex-Core`) optimized for a 6GB VRAM budget (NVIDIA GTX 1660 Ti).
- **In-Memory Neural TTS:** Zero-disk overhead synthesis using `edge-tts` decoded instantly via `miniaudio` into raw PCM 16kHz streams.

---

## 🛠️ Installation & Quickstart

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/](https://github.com/)<your-username>/vertex-voice-mcp.git
   cd vertex-voice-mcp
