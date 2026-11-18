FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MONITORING_PORT=5008

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --system --no-create-home monitoring && chown -R monitoring /app
USER monitoring

EXPOSE 5008

# Healthcheck uses the Python stdlib only (the slim image has no curl). The collector service of the
# same image overrides it (it has no HTTP port).
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health/ready' % os.environ.get('MONITORING_PORT','5008'), timeout=4)"

# API by default; the compose service monitoring-collector runs `python collector.py`.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${MONITORING_PORT} --workers 2 'main:create_app()'"]
