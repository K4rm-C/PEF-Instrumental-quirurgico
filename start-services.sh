#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [ ! -f "$ROOT/.env" ]; then
  echo "Falta .env en la raiz. Copia .env.example a .env y continua."
  exit 1
fi

cp "$ROOT/.env" "$ROOT/apps/BackendAuthService/.env"
cp "$ROOT/.env" "$ROOT/apps/BackendWebFlask/.env"
cp "$ROOT/.env" "$ROOT/apps/AdminService/.env"
cp "$ROOT/.env" "$ROOT/apps/SupervisorService/.env"

if ! docker info >/dev/null 2>&1; then
  echo "Docker no esta corriendo."
  exit 1
fi

docker compose up -d

echo "Contenedores arriba. Inicia Auth, AdminService, SupervisorService, Web y VisionWorker en cinco terminales:"
echo "  cd apps/BackendAuthService && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python main.py"
echo "  cd apps/AdminService && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python main.py"
echo "  cd apps/SupervisorService && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python main.py"
echo "  cd apps/BackendWebFlask && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python app.py"
echo "  cd apps/VisionWorker && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python main.py"
echo "Pesos: apps/VisionWorker/weights/best.pt (gitignored)"
echo "Web http://127.0.0.1:5000  Auth http://127.0.0.1:5001  Admin http://127.0.0.1:5003  Supervisor http://127.0.0.1:5004  Vision http://127.0.0.1:5002  Garage S3 http://127.0.0.1:3900"
