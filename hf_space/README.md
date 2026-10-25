---
title: Vertex-Core Voice
emoji: 🎙️
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
license: apache-2.0
short_description: Agente de voz Vertex-Core que habla y mira, en CPU
models:
- Denisijcu/vertex-core-gguf
- ggml-org/SmolVLM2-500M-Video-Instruct-GGUF
- Systran/faster-whisper-tiny
---

# 🎙️👁️ Vertex-Core Voice

Agente de voz de **Vertex Coders LLC**. Le hablas o le escribes en español o inglés, te contesta con voz y, si quieres, **mira por tu webcam**.
Todo corre en el **CPU Basic gratis** de Hugging Face (2 vCPU, sin GPU), con unos **4 GB de RAM**. GPU-poor and proud 🤗.

## Cómo funciona

```
🎤 micrófono / ⌨️ texto ──► Oído ──► Cerebro ──► Voz ──► 🔊
                                       ▲
📷 webcam (opcional) ──► Ojos ─────────┘
```

| Pieza | Modelo | Cómo corre |
|---|---|---|
| **Oído** | faster-whisper `tiny` (multilingüe) | int8 en CPU, ~75 MB. Solo elige entre español e inglés. |
| **Cerebro** | [Denisijcu/vertex-core-gguf](https://huggingface.co/Denisijcu/vertex-core-gguf), Qwen3-4B-Instruct-2507 afinado con QLoRA | GGUF Q4_K_M (2.5 GB) en `llama-server` de llama.cpp, puerto 8080 |
| **Ojos** | [SmolVLM2-500M](https://huggingface.co/ggml-org/SmolVLM2-500M-Video-Instruct-GGUF) | Q8 + mmproj (~550 MB) en un segundo `llama-server`, puerto 8081 |
| **Voz** | Edge Neural TTS (Alonso para español, Christopher para inglés) | Frase por frase: empieza a hablar antes de terminar de pensar |

- La respuesta del modelo se corta por cláusulas (punto, signo de interrogación o coma en frases largas), y cada cláusula se convierte en voz en cuanto está lista.
- **La cámara es opcional.** Viene cerrada y apagada, y el navegador no pide permiso hasta que la abres. Con "👁️ Mirar" activado, toma una foto por pregunta (a 384 px), SmolVLM2 la describe en una frase y Vertex-Core la recibe como lo que está viendo. Responde en primera persona y se acuerda de lo que vio durante la conversación.
- No se compila nada: la imagen Docker parte de la oficial `ghcr.io/ggml-org/llama.cpp:server`.
- Al arrancar, el Space detecta los CPUs reales del contenedor y calienta el modelo, así el system prompt queda ya en caché.

## Latencia medida (CPU Basic, 2 vCPU)

| Prueba | Resultado |
|---|---|
| Primera versión, solo texto | ~56 s hasta el primer audio |
| Con hilos reales + calentamiento + cámara | vista ~7 s · primer audio ~12 s |
| Sin cámara (estimado) | primer audio ~4-5 s |

## Variables de entorno (Settings del Space)

| Variable | Default | Para qué |
|---|---|---|
| `MAX_TOKENS` | `80` | Largo máximo de la respuesta. Menos = más rápido. |
| `VISION_ENABLED` | `1` | `0` quita los ojos y ahorra ~700 MB de RAM. |
| `WHISPER_SIZE` | `tiny` | `base` entiende mejor, pero es más lento. |
| `LLAMA_CTX` | `2048` | Contexto del modelo. |
| `LLAMA_THREADS` | auto | Hilos de llama.cpp (por defecto, los CPUs reales del contenedor). |
| `TTS_VOICE_ES` / `TTS_VOICE_EN` | Alonso / Christopher | Voces de Edge TTS. |
| `HF_TOKEN` (secret) | — | Opcional: descargas más rápidas de los modelos. |

## Límites

- En CPU, un 4B genera pocos tokens por segundo, así que las respuestas son cortas a propósito.
- SmolVLM2-500M ve lo general (una mano con un bolígrafo, un mando), pero no los detalles finos, y a veces inventa.
- Edge TTS es un servicio de Microsoft y necesita internet. No es 100 % local.
- Es un experimento, no un producto.

## Origen

Nace del gateway local `vertex-voice-mcp`: FastAPI + WebSockets con VAD, pre-roll, barge-in y Vertex-Core en LM Studio. Este Space es su versión web, ligera y con ojos.

**Licencias:** Qwen3-4B-Instruct-2507 y el adaptador de Vertex-Core, Apache 2.0. SmolVLM2, Apache 2.0.
