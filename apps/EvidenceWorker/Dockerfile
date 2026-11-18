FROM python:3.12-slim

# EvidenceWorker: Redis ring → clip assembly → Garage/Mongo (async, not the HTTP EvidenceService).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --system --create-home evidence \
    && mkdir -p /data/clips \
    && chown -R evidence /app /data

# Entrypoint re-chowns the named volume then drops to `evidence` (Python avoids CRLF shebang issues on Windows builds).
USER root
ENTRYPOINT ["python", "/app/docker_entrypoint.py"]
CMD ["python", "main.py"]
