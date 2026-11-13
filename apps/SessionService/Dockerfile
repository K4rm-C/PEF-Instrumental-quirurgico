FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SESSION_PORT=5004

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --system --no-create-home sessionsvc && chown -R sessionsvc /app
USER sessionsvc

EXPOSE 5004

# Healthcheck uses the Python stdlib only (the slim image has no curl).
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health/ready' % os.environ.get('SESSION_PORT','5004'), timeout=4)"

CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${SESSION_PORT} --workers 2 'main:create_app()'"]
