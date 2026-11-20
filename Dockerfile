# TruthLens + Sync ID: one public web service (TruthLens on $PORT) with Sync ID on 127.0.0.1:8001 inside the container.
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg/ffprobe: video and audio decoding. glib: OpenCV runtime. util-linux: setpriv (drop root).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 util-linux curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .
RUN useradd --system --uid 10001 --home-dir /home/app --create-home app \
 && chmod +x deploy/start.sh \
 && python -m compileall -q truthlens syncid

# All runtime state lives here. On the Render free plan it is ephemeral (no disk).
ENV DATA_DIR=/tmp/truthlens-data \
    PORT=10000
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1
CMD ["deploy/start.sh"]
