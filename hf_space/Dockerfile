# Vertex-Core Voice — Hugging Face Space (Docker, CPU Basic gratis)
# Base: imagen oficial de llama.cpp con llama-server YA compilado.
# Así no se compila llama-cpp-python en el Space (eso es lo que suele dar timeout).
FROM ghcr.io/ggml-org/llama.cpp:server

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv python3-pip ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Los Spaces corren con uid 1000. Algunas imágenes Ubuntu ya traen ese uid ("ubuntu").
RUN id -u 1000 >/dev/null 2>&1 || useradd -m -u 1000 user \
 && mkdir -p /home/user/app /home/user/.cache \
 && chown -R 1000:1000 /home/user

RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    HOME=/home/user \
    HF_HOME=/home/user/.cache/huggingface \
    PYTHONUNBUFFERED=1 \
    LD_LIBRARY_PATH=/app \
    LLAMA_SERVER_BIN=/app/llama-server

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /home/user/app
COPY --chown=1000:1000 . .

USER 1000
EXPOSE 7860
# La imagen base arranca llama-server por defecto; lo lanza app.py.
ENTRYPOINT []
CMD ["python", "app.py"]
