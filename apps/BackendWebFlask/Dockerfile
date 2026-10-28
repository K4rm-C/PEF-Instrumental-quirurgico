FROM python:3.12-slim

# Portal RF (Operator, Supervisor, Administration). Configuration comes ONLY from the environment
# (docker-compose.yml): no credentials in the image.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WEB_PORT=5000 \
    PEF_OUTBOX_DIR=/data/outbox

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt "gunicorn>=23.0,<24"

# Templates, static files, compiled translations (translations/*/LC_MESSAGES/*.mo) and the code.
COPY . .
# Non-root user; /data/outbox is the durable MongoDB/Evidence checkpoint outbox (a named volume).
RUN useradd --system --create-home pef \
    && mkdir -p /data/outbox \
    && chown -R pef /app /data
USER pef

EXPOSE 5000

# /health/ready: SELECT 1 on PostgreSQL only (no secrets, no other service).
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health/ready' % os.environ.get('WEB_PORT','5000'), timeout=4)"

# ONE process on purpose: the capture flow keeps the frozen YOLO snapshot of each session in process
# memory and the checkpoint outbox runs one thread; requests are served by threads (gthread).
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${WEB_PORT} --workers 1 --threads 8 --timeout 300 --graceful-timeout 30 --access-logfile - --access-logformat '%(h)s \"%(m)s %(U)s\" %(s)s %(L)ss' app:app"]
