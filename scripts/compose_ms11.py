"""MS-11: the WHOLE PEF in Docker Compose, as an ISOLATED project (`pef_ms11`) for integral E2E tests.

It never touches another Compose project, never deletes a volume and never writes the repository's .env or
infra/garage.toml. Everything it generates lives in .e2e/ms11_compose/ (ignored by git): an env file with random
secrets, a garage.toml, a self-signed TLS certificate for the test host names and a test override.

Layout = production layout of MS-04: only the API Gateway is published (TLS 26443 + redirect 26081; hosts
portal.pef.test and api.pef.test), AuthService and the portal publish nothing; the internal service ports are published
on 127.0.0.1 (26xxx) for diagnostics; PostgreSQL on 127.0.0.1:26432 for the E2E checks. TEST-ONLY additions (override):
the provisional best.pt mounted read-only into VisionWorker, a local HAPI FHIR R4 test server (`hapi`) and the
integration settings that allow deliveries to it over plain HTTP inside the Compose network.

    python scripts/compose_ms11.py up [--weights <dir with best.pt>]     # build + start + wait healthy
    python scripts/compose_ms11.py ps | stop | start | restart <service> | down     (down keeps every volume)
"""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.e2e' / 'ms11_compose'
PROJECT = 'pef_ms11'
API_HOST, PORTAL_HOST = 'api.pef.test', 'portal.pef.test'
HTTPS, HTTP_REDIRECT = 26443, 26081
DEFAULT_WEIGHTS = Path(os.environ.get('PEF_BEST_PT_DIR', 'C:/Users/pedro/OneDrive/Documentos/Desarrollo/'
                                                         'PEF_Integrado_Admin_Monolito/apps/VisionWorker/weights'))
PORTS = {'WEB_PORT': 26000, 'AUTH_PORT': 26001, 'VISION_PORT': 26002, 'CATALOG_PORT': 26003, 'SESSION_PORT': 26004,
         'EVIDENCE_PORT': 26005, 'AUDIT_PORT': 26006, 'NOTIFICATIONS_PORT': 26007, 'MONITORING_PORT': 26008,
         'INTEGRATION_PORT': 26009, 'POSTGRES_PORT': 26432, 'REDIS_PORT': 26379, 'MONGO_PORT': 26017,
         'S3_API_PORT': 26900, 'GARAGE_RPC_PORT': 26901, 'GARAGE_ADMIN_PORT': 26903, 'GATEWAY_HTTP_PORT': 26080}


def files() -> list[str]:
    return ['-p', PROJECT, '--env-file', str(WORK / 'compose.env'), '-f', str(ROOT / 'docker-compose.yml'),
            '-f', str(ROOT / 'docker-compose.gateway-tls.yml'), '-f', str(WORK / 'override.yml'), '--profile', 'gateway']


def compose(*args, check=True, capture=False):
    cmd = ['docker', 'compose', *files(), *args]
    return subprocess.run(cmd, cwd=ROOT, check=check, text=True, capture_output=capture)


def generate(weights_dir: Path) -> None:
    """Creates the test configuration once; re-running keeps it (same secrets, same data)."""
    WORK.mkdir(parents=True, exist_ok=True)
    env_file = WORK / 'compose.env'
    if not env_file.exists():
        origin = f'https://{PORTAL_HOST}:{HTTPS}'
        values = {
            'POSTGRES_DB': 'pef', 'POSTGRES_USER': 'pef', 'POSTGRES_PASSWORD': secrets.token_hex(16),
            'REDIS_PASSWORD': secrets.token_hex(16), 'MONGO_USER': 'pef', 'MONGO_PASSWORD': secrets.token_hex(16),
            'S3_ACCESS_KEY': 'GK' + secrets.token_hex(12), 'S3_SECRET_KEY': secrets.token_hex(32),
            'S3_BUCKET_EVIDENCE': 'pef-evidence', 'S3_REGION': 'garage', 'S3_PUBLIC_ENDPOINT': f'http://127.0.0.1:{PORTS["S3_API_PORT"]}',
            'JWT_SECRET_KEY': secrets.token_hex(32), 'SESSION_SECRET_KEY': secrets.token_hex(32),
            'VISION_WS_SECRET': secrets.token_hex(24), 'SYNC_CURSOR_SECRET': secrets.token_hex(32),
            'MONITORING_INTERVAL_SECONDS': '10', 'MONITORING_STATION_STALE_SECONDS': '90',
            'GATEWAY_API_HOST': API_HOST, 'GATEWAY_PORTAL_HOST': PORTAL_HOST, 'GATEWAY_PORTAL_ORIGIN': origin,
            'GATEWAY_PORTAL_WS_URL': f'wss://{PORTAL_HOST}:{HTTPS}/ws', 'GATEWAY_TLS_CERT_DIR': str(WORK / 'certs'),
            'GATEWAY_HTTPS_PORT': str(HTTPS), 'GATEWAY_HTTP_REDIRECT_PORT': str(HTTP_REDIRECT),
            'GATEWAY_RATE_AUTH': '120r/m', 'GATEWAY_BURST_AUTH': '60',
            **{key: str(value) for key, value in PORTS.items()},
        }
        env_file.write_text(''.join(f'{k}={v}\n' for k, v in values.items()), encoding='utf-8')
    garage = WORK / 'garage.toml'
    if not garage.exists():
        text = (ROOT / 'infra' / 'garage.toml.example').read_text(encoding='utf-8')
        for marker in ('REEMPLAZAR_CON_64_CARACTERES_HEXADECIMALES', 'REEMPLAZAR_TOKEN_ADMIN', 'REEMPLAZAR_TOKEN_METRICAS'):
            text = text.replace(marker, secrets.token_hex(32))
        garage.write_text(text, encoding='utf-8')
    certs = WORK / 'certs'
    if not (certs / 'fullchain.pem').exists():
        certs.mkdir(exist_ok=True)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '30', '-subj', '/CN=pef-ms11-test',
                        '-addext', f'subjectAltName=DNS:{API_HOST},DNS:{PORTAL_HOST}',
                        '-keyout', str(certs / 'privkey.pem'), '-out', str(certs / 'fullchain.pem')], check=True,
                       capture_output=True)
    override = {
        'services': {
            'garage': {'volumes': [f'{garage.as_posix()}:/etc/garage.toml:ro', 'garage_meta:/var/lib/garage/meta',
                                   'garage_data:/var/lib/garage/data']},
            # TEST ONLY: provisional weights, read-only; the copy at VISION_WEIGHTS_PATH is the verified shortcut (R3)
            'vision': {'volumes': [f'{weights_dir.as_posix()}:/models:ro', 'web_admin_storage:/admin_storage:ro',
                                   'vision_data:/data']},
            # TEST ONLY: local HAPI FHIR R4 server and plain HTTP to it inside the Compose network
            'hapi': {'image': 'hapiproject/hapi:v7.4.0', 'networks': ['pef-network'], 'ports': ['127.0.0.1:26090:8080']},
            'integration': {'environment': {'INTEGRATION_ALLOWED_HOSTS': 'hapi:8080', 'INTEGRATION_ALLOW_PLAIN_HTTP': 'true'}},
            'integration-worker': {'environment': {'INTEGRATION_ALLOWED_HOSTS': 'hapi:8080', 'INTEGRATION_ALLOW_PLAIN_HTTP': 'true',
                                                   'INTEGRATION_DELIVERY_BACKOFF_SECONDS': '1'}},
        }
    }
    # JSON is valid YAML; it keeps the override free of quoting surprises.
    (WORK / 'override.yml').write_text(json.dumps(override, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('action', choices=('up', 'ps', 'stop', 'start', 'restart', 'down', 'config'))
    parser.add_argument('service', nargs='*')
    parser.add_argument('--weights', type=Path, default=DEFAULT_WEIGHTS)
    args = parser.parse_args()
    if not (args.weights / 'best.pt').exists():
        sys.exit(f'best.pt not found in {args.weights} (provisional weights are never part of the repository)')
    generate(args.weights)
    if args.action == 'up':
        compose('up', '-d', '--build', '--wait', '--wait-timeout', '900', *args.service)
    elif args.action == 'down':
        compose('down', '--remove-orphans')          # NEVER -v: volumes (data) are kept
    elif args.action == 'config':
        compose('config', '--quiet')
    else:
        compose(args.action, *args.service)
    if args.action in ('up', 'ps', 'start', 'restart'):
        compose('ps', '--format', 'table {{.Service}}\t{{.Status}}')


if __name__ == '__main__':
    if shutil.which('docker') is None:
        sys.exit('docker is not available')
    main()
