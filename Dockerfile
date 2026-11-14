# AI Studio for the NVIDIA DGX Spark (arm64, GB10 "Blackwell", sm_121).
#
# Build it on the Spark itself:   docker compose build
# (or: docker build -t ai-image-studio:local .). See README "Build and run on the DGX Spark".
#
# NGC_TAG picks NVIDIA's PyTorch release. 25.10 is the first one reported to support GB10;
# a newer YY.MM-py3 tag should work too and is worth trying if 25.10 gives trouble.
ARG NGC_TAG=25.10-py3

# ---- 1. The web page, built once and copied into the final image as static files -------------
FROM node:22-bookworm-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. The server, on NVIDIA's PyTorch ------------------------------------------------------
FROM nvcr.io/nvidia/pytorch:${NGC_TAG}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1

# diffusers comes from a pinned GitHub commit, which needs git (NGC images normally have it).
# hadolint ignore=DL3008
RUN command -v git >/dev/null \
    || (apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*)

WORKDIR /app

# Keep NVIDIA's builds. Pin the packages NVIDIA compiled for this GPU to the versions the image
# ships, so a dependency that wants to replace them fails the build instead of quietly swapping
# in a generic wheel. (NGC images may set PIP_CONSTRAINT too; pip applies both.)
COPY docker/ngc_pins.py /app/docker/
RUN python /app/docker/ngc_pins.py > /app/ngc-pins.txt
COPY backend/requirements-server.txt backend/requirements-container.txt /app/backend/
RUN cat /app/ngc-pins.txt \
    && pip install -c /app/ngc-pins.txt -r /app/backend/requirements-container.txt \
    && pip freeze > /app/pip-freeze.txt

# NVIDIA's image bundles torchao (0.14 in 25.10), a model-quantization library the studio doesn't
# use. diffusers imports torchao whenever it is installed and needs a newer one (FqnToConfig, added
# in torchao 0.15), so next to 0.14 the Qwen-Image pipeline can't even be imported. Remove it.
RUN pip uninstall -y torchao \
    && pip freeze > /app/pip-freeze.txt

# Vector (DESIGN.md section 31.6): the tracer (vtracer) and the geometry library (shapely). A step of their own, and a failure here is a
# note and not a failed build: Vector then says it is unavailable (capabilities.vector) and the rest of the studio is unaffected.
# numpy stays NVIDIA's (the pins above are constraints here too).
COPY backend/requirements-vector.txt /app/backend/
RUN (pip install -c /app/ngc-pins.txt -r /app/backend/requirements-vector.txt \
        || echo "NOTE: vtracer and shapely could not be installed; the Vector tab will say it is unavailable (DESIGN.md section 31.6)") \
    && pip freeze > /app/pip-freeze.txt

# The music worker's own copy of diffusers (DESIGN.md §26.4): the released 0.40.0 has the MiniMax-Music3 pipeline, the
# commit above has Qwen-Image-2.1, and neither has the other's. Installed without dependencies (the image has them) into
# a folder that only the music worker puts first on its Python path.
COPY backend/requirements-music.txt /app/backend/
RUN pip install --no-deps --target /opt/music-libs -r /app/backend/requirements-music.txt
ENV STUDIO_MUSIC_LIBS=/opt/music-libs

# Speech (DESIGN.md sections 38.3 and 38.18). EVERY piece of it is optional and in a step of its own: a failure prints a note, the engine is then shown as "not installed"
# with its reason on the Speech tab, and docker/check_image.py prints a line for each ("Speech: ..."). None of it can fail the build, and none of it replaces NVIDIA's builds
# (the pins protect them, and check_image.py fails the build if one was replaced).
#
# ffmpeg reads a voice recording that is not a plain WAV (MP3, M4A, OGG, FLAC, WebM), and makes the M4B audiobook file (DESIGN.md section 44.4), for which it needs an AAC
# encoder. The system package's own encoder ("aac") is built into ffmpeg and needs no extra library; docker/check_image.py prints whether this ffmpeg has it.
# hadolint ignore=DL3008
RUN (apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*) \
    || echo "NOTE: ffmpeg could not be installed; a voice recording that is not a plain WAV file will be refused, and so will the M4B audiobook file (DESIGN.md sections 38.4 and 44.4)"
COPY backend/requirements-speech-kokoro.txt backend/requirements-speech-piper.txt backend/requirements-speech-qwen3-extras.txt \
     backend/requirements-speech-qwen3.txt backend/requirements-speech-voxtral.txt /app/backend/
# Kokoro, Piper and what Qwen3-TTS imports besides torch and transformers do not conflict with anything in the image, so they are installed with it, under its pins.
RUN (pip install -c /app/ngc-pins.txt -r /app/backend/requirements-speech-kokoro.txt \
        || echo "NOTE: Kokoro could not be installed; its engine will say so (DESIGN.md section 38.1)") \
    && pip freeze > /app/pip-freeze.txt
RUN (pip install -c /app/ngc-pins.txt -r /app/backend/requirements-speech-piper.txt \
        || echo "NOTE: Piper could not be installed; its engine will say so (DESIGN.md section 38.1)") \
    && pip freeze > /app/pip-freeze.txt
RUN (pip install -c /app/ngc-pins.txt -r /app/backend/requirements-speech-qwen3-extras.txt \
        || echo "NOTE: librosa, soundfile, einops, sox and onnxruntime could not be installed; Qwen3-TTS will say so (DESIGN.md section 38.1)") \
    && pip freeze > /app/pip-freeze.txt
# Qwen3-TTS's own transformers 4.57.3, accelerate, huggingface-hub and tokenizers (it pins them exactly; the image has others), without dependencies, in a folder that only its
# worker puts first on its Python path: the way the music model has its own diffusers above.
RUN pip install --no-deps --target /opt/speech-libs/qwen3-tts -r /app/backend/requirements-speech-qwen3.txt \
    || echo "NOTE: Qwen3-TTS's libraries could not be installed; its engine will say so (DESIGN.md section 38.1)"
# Voxtral runs as a vLLM-Omni server of its own, in a virtual environment of its own (vLLM brings its own PyTorch). Off unless asked for:
#   docker compose build --build-arg SPEECH_VOXTRAL=1
ARG SPEECH_VOXTRAL=0
RUN if [ "$SPEECH_VOXTRAL" = "1" ]; then \
        (python -m venv /opt/speech-libs/voxtral && /opt/speech-libs/voxtral/bin/pip install -r /app/backend/requirements-speech-voxtral.txt) \
        || echo "NOTE: Voxtral's server could not be installed; its engine will say so (DESIGN.md section 38.1)"; \
    else echo "NOTE: Voxtral is not built into this image (add --build-arg SPEECH_VOXTRAL=1 to build it); its engine will say so"; fi
ENV STUDIO_SPEECH_LIBS=/opt/speech-libs

COPY backend/studio /app/backend/studio
COPY docker/ /app/docker/
COPY scripts/minimax_music.py /app/scripts/minimax_music.py
COPY --from=ui /ui/dist /app/static

# qwen-tts imports torchaudio at the top of a module that only its old 25 Hz tokenizer uses, and NVIDIA's image has no torchaudio for the Spark, so without this the whole library
# fails to import ("No module named 'torchaudio'", seen on a Spark). docker/patch_qwen_tts.py makes that one import optional in our own copy, which only the Qwen3-TTS worker uses.
# It is after the COPY of docker/, and before the check below so that the check sees the result. If it cannot be done it is a note, and the engine says why it cannot run.
RUN python /app/docker/patch_qwen_tts.py /opt/speech-libs/qwen3-tts \
    || echo "NOTE: Qwen3-TTS's library could not be patched for the missing torchaudio; its engine will say why it cannot run (DESIGN.md section 38.18, item 72)"

# Fail the build now, not at the first Generate, if the stack doesn't fit together.
RUN python /app/docker/check_image.py

# Mount points (compose mounts host folders here). World-writable so that the container still
# works if it is started without the mounts, as any user; the data is then lost with it.
RUN mkdir -p /data /models && chmod 1777 /data /models

ENV PYTHONPATH=/app/backend \
    STUDIO_DATA_DIR=/data \
    STUDIO_STATIC_DIR=/app/static \
    HF_HUB_DISABLE_TELEMETRY=1 \
    STUDIO_HOST=0.0.0.0 \
    STUDIO_PORT=8080 \
    HF_HOME=/models \
    HOME=/tmp \
    USER=studio

# Not root. compose.yaml runs it as your own UID:GID instead, so ./data stays yours on the host.
USER 1000:1000
WORKDIR /app/backend
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "/app/docker/healthcheck.py"]

# NVIDIA's entrypoint (kept from the base image) prints the CUDA banner, then runs this.
CMD ["python", "-m", "studio"]
