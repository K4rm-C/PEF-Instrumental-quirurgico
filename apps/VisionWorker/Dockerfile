FROM python:3.12-slim

# VisionWorker (YOLO counting). CPU by default: PyTorch comes from the official CPU wheel index, so the
# image does not carry CUDA. The model weights are NOT in the image: docker-compose.yml mounts
# apps/VisionWorker/weights read-only at /models (VISION_WEIGHTS_PATH=/models/best.pt).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VISION_WORKER_PORT=5002 \
    VISION_WEIGHTS_PATH=/models/best.pt \
    VISION_UPLOAD_DIR=/data/uploads \
    TELEMETRY_SPOOL_DIR=/data/telemetry_spool \
    YOLO_CONFIG_DIR=/tmp/ultralytics \
    YOLO_OFFLINE=1

# OpenCV (pulled by ultralytics) needs these shared libraries even without a display.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.14.1" "torchvision==0.29.1" \
    && pip install -r requirements.txt "gunicorn>=23.0,<24"

COPY . .
RUN useradd --system --create-home vision \
    && mkdir -p /data/uploads /data/telemetry_spool /models /tmp/ultralytics \
    && chown -R vision /app /data /tmp/ultralytics
USER vision

EXPOSE 5002

# /health reports whether the weights file is present (the path only, never its content).
HEALTHCHECK --interval=20s --timeout=5s --start-period=60s --retries=5 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('VISION_WORKER_PORT','5002'), timeout=4)"

# ONE process on purpose: the bound sessions and their YOLO runs live in process memory; the video of a
# session is processed by a background thread while requests are served by threads (gthread).
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${VISION_WORKER_PORT} --workers 1 --threads 8 --timeout 600 --graceful-timeout 60 main:app"]
