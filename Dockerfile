# AI Image Studio for the NVIDIA DGX Spark (arm64, GB10 "Blackwell", sm_121).
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

# The music worker's own copy of diffusers (DESIGN.md §26.4): the released 0.40.0 has the MiniMax-Music3 pipeline, the
# commit above has Qwen-Image-2.1, and neither has the other's. Installed without dependencies (the image has them) into
# a folder that only the music worker puts first on its Python path.
COPY backend/requirements-music.txt /app/backend/
RUN pip install --no-deps --target /opt/music-libs -r /app/backend/requirements-music.txt
ENV STUDIO_MUSIC_LIBS=/opt/music-libs

COPY backend/studio /app/backend/studio
COPY docker/ /app/docker/
COPY --from=ui /ui/dist /app/static

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
