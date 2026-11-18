"""ADMIN-09: start AuthService (127.0.0.1:15001) and BackendWebFlask (127.0.0.1:15000) against the ISOLATED
test stack (docker-compose.test.yml / .env.test) so Administration can be checked in a real browser, either
by hand (docs/ADMIN_GUIA_VALIDACION_MANUAL.md) or by scripts/browser_validate_admin.py.

    docker compose -f docker-compose.test.yml --env-file .env.test up -d --wait
    python scripts/run_admin_browser_env.py                    # local storage in .e2e/admin_storage
    python scripts/run_admin_browser_env.py --storage s3       # Garage of the test stack (bucket pef-admin-browser)
    python scripts/run_admin_browser_env.py --steward <institution-uuid>

Open http://127.0.0.1:15000/sign-in and use the demo accounts of data/seeds/003_seed_demo.sql.
Ctrl+C stops both processes. Never points at the main stack: every URL comes from .env.test.
Logs: .e2e/browser_auth.log and .e2e/browser_web.log (gitignored).
"""
import argparse
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_test_env import load_test_env  # noqa: E402

AUTH, WEB = 'http://127.0.0.1:15001', 'http://127.0.0.1:15000'
sys.path.insert(0, str(Path(__file__).resolve().parent))
import portal_csrf  # noqa: E402  (MS-04: CSRF token for portal POSTs)
portal_csrf.enable(WEB)
STATE_FILE = ROOT / '.e2e' / 'browser_env_secret'


def _wait(url, process, label):
    for _ in range(90):
        if process.poll() is not None:
            sys.exit(f'{label} stopped while starting (code {process.returncode}); see .e2e/browser_*.log')
        try:
            if requests.get(url, timeout=1).status_code < 500:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    sys.exit(f'{label} did not answer at {url}')


def build_env(storage, steward):
    env = load_test_env()
    common = {k: v for k, v in env.items()
              if k not in ('CATALOG_SERVICE_URL', 'AUDIT_SERVICE_URL', 'MONITORING_SERVICE_URL', 'SESSION_SERVICE_URL')}
    log_dir = ROOT / '.e2e'
    log_dir.mkdir(exist_ok=True)
    # The JWT / session secrets survive portal restarts (persistence checks) but never leave .e2e/.
    if not STATE_FILE.exists():
        STATE_FILE.write_text(secrets.token_hex(32) + '\n' + secrets.token_hex(32), encoding='utf-8')
    jwt_secret, session_secret = STATE_FILE.read_text(encoding='utf-8').split()
    auth_env = {**common, 'HOST': '127.0.0.1', 'PORT': '15001', 'JWT_SECRET_KEY': jwt_secret,
                # Same cookie policy as docker-compose.yml on plain HTTP (browsers reject SameSite=None without Secure).
                'AUTH_COOKIE_SECURE': 'false', 'AUTH_COOKIE_SAMESITE': 'Lax', 'AUTH_ALLOWED_ORIGINS': WEB}
    web_env = {**common, 'AUTH_SERVICE_URL': AUTH, 'AUTH_BROWSER_URL': AUTH, 'SESSION_SECRET_KEY': session_secret,
               'FRONTEND_DEMO_MODE': 'false', 'MONGO_URL': env['PEF_TEST_MONGO_URL'],
               'ADMIN_STORAGE_BACKEND': storage, 'ADMIN_STORAGE_DIR': str(log_dir / 'admin_storage')}
    if storage == 's3':
        web_env.update({'S3_ENDPOINT': env['PEF_TEST_S3_ENDPOINT'], 'S3_REGION': 'garage',
                        'S3_ACCESS_KEY': env['PEF_TEST_S3_ACCESS_KEY'], 'S3_SECRET_KEY': env['PEF_TEST_S3_SECRET_KEY'],
                        'ADMIN_S3_BUCKET': 'pef-admin-browser'})
    if steward:
        web_env['ADMIN_CATALOG_STEWARD_INSTITUTION_ID'] = steward
    else:
        web_env.pop('ADMIN_CATALOG_STEWARD_INSTITUTION_ID', None)
    return auth_env, web_env


def start(storage='local', steward='', start_auth=True):
    """Start the processes; returns (auth, web). auth is None when start_auth is False (portal-only restart)."""
    auth_env, web_env = build_env(storage, steward)
    log_dir = ROOT / '.e2e'
    auth = None
    if start_auth:
        auth = subprocess.Popen([sys.executable, 'main.py'], cwd=ROOT / 'apps' / 'BackendAuthService', env=auth_env,
                                stdout=open(log_dir / 'browser_auth.log', 'a'), stderr=subprocess.STDOUT)
        _wait(AUTH + '/health', auth, 'AuthService')
    web = subprocess.Popen([sys.executable, '-m', 'flask', '--app', 'app', 'run', '--host', '127.0.0.1', '--port', '15000'],
                           cwd=ROOT / 'apps' / 'BackendWebFlask', env=web_env,
                           stdout=open(log_dir / 'browser_web.log', 'a'), stderr=subprocess.STDOUT)
    _wait(WEB + '/health/live', web, 'BackendWebFlask')
    return auth, web


def stop(*processes):
    for process in processes:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--storage', choices=('local', 's3'), default='local')
    parser.add_argument('--steward', default='', help='institution UUID for ADMIN_CATALOG_STEWARD_INSTITUTION_ID')
    args = parser.parse_args()
    auth, web = start(args.storage, args.steward)
    print(f'AuthService {AUTH}  ·  portal {WEB}/sign-in  ·  storage={args.storage}'
          f"{'  ·  steward=' + args.steward if args.steward else ''}\nCtrl+C to stop.", flush=True)
    try:
        while auth.poll() is None and web.poll() is None:
            time.sleep(1)
        print('A process stopped; see .e2e/browser_*.log')
    except KeyboardInterrupt:
        pass
    finally:
        stop(web, auth)


if __name__ == '__main__':
    main()
