"""Isolated Compose stack for the Android integration test (ANDROID-02). Writes ONLY to <repo>/.e2e/android_it
(ignored by git).

Usage:  python -I setup_android_it_stack.py <backend repo> <directory containing best.pt>
Then (from the backend repo):
  docker compose -p pef_android_it --env-file .e2e/android_it/compose.env -f docker-compose.yml       -f .e2e/android_it/override.yml --profile gateway up -d --build --wait
Optional evidence-through-gateway prototype: add  -f .e2e/android_it/override-gateway-evidence.yml Same approach as scripts/compose_ms11.py, but with the plain-HTTP DEVELOPMENT gateway on
127.0.0.1 and every published port bound to loopback (Garage included)."""
import json, secrets, sys
from pathlib import Path

ROOT = Path(sys.argv[1])
WEIGHTS = Path(sys.argv[2])
WORK = ROOT / '.e2e' / 'android_it'
WORK.mkdir(parents=True, exist_ok=True)
PORTS = {'WEB_PORT': 27000, 'AUTH_PORT': 27001, 'VISION_PORT': 27002, 'CATALOG_PORT': 27003, 'SESSION_PORT': 27004,
         'EVIDENCE_PORT': 27005, 'AUDIT_PORT': 27006, 'NOTIFICATIONS_PORT': 27007, 'MONITORING_PORT': 27008,
         'INTEGRATION_PORT': 27009, 'POSTGRES_PORT': 27432, 'REDIS_PORT': 27379, 'MONGO_PORT': 27017,
         'S3_API_PORT': 27900, 'GARAGE_RPC_PORT': 27901, 'GARAGE_ADMIN_PORT': 27903, 'GATEWAY_HTTP_PORT': 27080}
env = WORK / 'compose.env'
if not env.exists():
    values = {
        'POSTGRES_DB': 'pef', 'POSTGRES_USER': 'pef', 'POSTGRES_PASSWORD': secrets.token_hex(16),
        'REDIS_PASSWORD': secrets.token_hex(16), 'MONGO_USER': 'pef', 'MONGO_PASSWORD': secrets.token_hex(16),
        'S3_ACCESS_KEY': 'GK' + secrets.token_hex(12), 'S3_SECRET_KEY': secrets.token_hex(32),
        'S3_BUCKET_EVIDENCE': 'pef-evidence', 'S3_REGION': 'garage',
        # Reached from Android through `adb reverse tcp:27900 tcp:27900` (loopback only, see README Android).
        'S3_PUBLIC_ENDPOINT': f'http://127.0.0.1:{PORTS["S3_API_PORT"]}',
        'JWT_SECRET_KEY': secrets.token_hex(32), 'SESSION_SECRET_KEY': secrets.token_hex(32),
        'VISION_WS_SECRET': secrets.token_hex(24), 'SYNC_CURSOR_SECRET': secrets.token_hex(32),
        'MONITORING_INTERVAL_SECONDS': '10', 'MONITORING_STATION_STALE_SECONDS': '90',
        # TEST ONLY: short access tokens so the Android refresh path runs during the manual walk-through.
        'SPD_ACCESS_TOKEN_MINUTES': '2', 'OP_ACCESS_TOKEN_MINUTES': '2', 'ACCESS_TOKEN_MINUTES': '2',
        'GATEWAY_RATE_AUTH': '60r/m', 'GATEWAY_BURST_AUTH': '30',
        **{k: str(v) for k, v in PORTS.items()},
    }
    env.write_text(''.join(f'{k}={v}\n' for k, v in values.items()), encoding='utf-8')
garage = WORK / 'garage.toml'
if not garage.exists():
    text = (ROOT / 'infra' / 'garage.toml.example').read_text(encoding='utf-8')
    for marker in ('REEMPLAZAR_CON_64_CARACTERES_HEXADECIMALES', 'REEMPLAZAR_TOKEN_ADMIN', 'REEMPLAZAR_TOKEN_METRICAS'):
        text = text.replace(marker, secrets.token_hex(32))
    garage.write_text(text, encoding='utf-8')
g = garage.as_posix(); w = WEIGHTS.as_posix(); P = PORTS
(WORK / 'override.yml').write_text(f"""services:
  garage:
    ports: !override
    - 127.0.0.1:{P['S3_API_PORT']}:3900
    - 127.0.0.1:{P['GARAGE_RPC_PORT']}:3901
    - 127.0.0.1:{P['GARAGE_ADMIN_PORT']}:3903
    volumes:
    - {g}:/etc/garage.toml:ro
  auth:
    ports: !override
    - 127.0.0.1:{P['AUTH_PORT']}:5001
  web:
    ports: !override
    - 127.0.0.1:{P['WEB_PORT']}:5000
  vision:
    volumes:
    - {w}:/models:ro
""", encoding='utf-8')
# Prototype copy of the gateway templates with the presigned-evidence route (docs/backend-proposal/*.patch).
gw = WORK / 'gateway'; (gw / 'pef').mkdir(parents=True, exist_ok=True)
tpl = ROOT / 'infra' / 'gateway' / 'templates'
http = (tpl / '00-pef-http.conf.template').read_text(encoding='utf-8')
http = http.replace('upstream pef_portal {', 'upstream pef_garage { zone pef_up_garage 64k; server ${PEF_GW_GARAGE_UPSTREAM}${PEF_GW_UPSTREAM_RESOLVE}; }' + chr(10) + 'upstream pef_portal {', 1)
(gw / '00-pef-http.conf.template').write_text(http, encoding='utf-8')
for f in (tpl / 'pef').glob('*.template'):
    (gw / 'pef' / f.name).write_text(f.read_text(encoding='utf-8'), encoding='utf-8')
route = Path(__file__).with_name('presigned-evidence-route.conf.txt').read_text(encoding='utf-8')
routes = (gw / 'pef' / 'routes.conf.template').read_text(encoding='utf-8')
marker = '# --- VisionWorker live preview'
(gw / 'pef' / 'routes.conf.template').write_text(routes.replace(marker, route + chr(10) + marker, 1), encoding='utf-8')
(WORK / 'override-gateway-evidence.yml').write_text(
    Path(__file__).with_name('override-gateway-evidence.example.yml').read_text(encoding='utf-8'), encoding='utf-8')
print(WORK)
