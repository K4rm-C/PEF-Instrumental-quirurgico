"""Smoke REAL de autenticación compartida + pantallas Admin contra el entorno aislado (ADMIN-01.0).

Arranca BackendAuthService (127.0.0.1:15001) y BackendWebFlask (127.0.0.1:15000) como procesos locales
apuntando a la BD/Redis de docker-compose.test.yml (.env.test). Comprueba, para los tres roles:
login en AuthService -> página del portal -> refresh transparente -> logout -> token revocado.
Para it_admin además recorre en modo LECTURA todas las pantallas /admin/*.

Escrituras: las del login/logout (tokens Redis, user.last_login_at, filas LOGIN/LOGOUT) y el ciclo de
identidad (un usuario smoke.<tag>@test.invalid por ejecución: alta, desactivación, reactivación, reset),
únicamente en la BD de prueba. Usa las cuentas demo de data/seeds/003_seed_demo.sql.
CATALOG/AUDIT/MONITORING/SESSION_SERVICE_URL se dejan vacíos: Admin usa sus módulos locales, salvo
usuarios/roles, que hoy siguen yendo a AuthService por HTTP (ver docs/ADMIN_MATRIZ_CUMPLIMIENTO.md).

    python scripts/smoke_admin_test_env.py
"""
import os
import re
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
ACCOUNTS = {
    'station_operator': ('operator@instrumed.com', 'DemopwdOP78!', '/operator/profile'),
    'spd_supervisor': ('supervisor@instrumed.com', 'DemopwdSPD78!', '/supervisor/profile'),
    'it_admin': ('admin@instrumed.com', 'DemopwdADM78!', '/admin/profile'),
}
ADMIN_PAGES = [
    '/admin/dashboard', '/admin/instrument-families', '/admin/instrument-families/new', '/admin/procedures',
    '/admin/procedures/new', '/admin/selection-catalogs/phases', '/admin/selection-catalogs/categories',
    '/admin/selection-catalogs/reasons', '/admin/privacy-notices', '/admin/privacy-notices/new', '/admin/users',
    '/admin/users/new', '/admin/roles', '/admin/vision-models', '/admin/vision-models/new', '/admin/audit-log',
    '/admin/audit-log?date_from=2026-01-01&date_to=2026-12-31', '/admin/configuration',
    '/admin/configuration/capture-stations/new', '/admin/system-status',
]
# RACI: pieces and kits belong to the SPD supervisor (Administration gets 403).
SPD_ONLY_PAGES = ['/admin/instruments', '/admin/kits']
FIXED_ROLES = ('station_operator', 'spd_supervisor', 'it_admin', 'or_charge')
results = []


def check(name, ok, detail=''):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + detail if detail and not ok else ''}", flush=True)


def wait_ready(url, process, label):
    for _ in range(60):
        if process.poll() is not None:
            sys.exit(f'{label} terminó al arrancar (código {process.returncode})')
        try:
            if requests.get(url, timeout=1).status_code < 500:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    sys.exit(f'{label} no respondió en {url}')


def portal_get(path, cookies):
    return requests.get(WEB + path, cookies=cookies, allow_redirects=False, timeout=15)


def identity_lifecycle(admin_access):
    """D-1 end to end: user created in the portal signs in at AuthService; deactivation and password
    reset in the portal revoke the user's Redis sessions through AuthService."""
    tag = secrets.token_hex(4)
    email, password, new_password = f'smoke.{tag}@test.invalid', f'Smoke-{tag}-Pass1', f'Reset-{tag}-Pass2'
    cookies = {'access_token': admin_access}
    created = requests.post(WEB + '/admin/users/new', cookies=cookies, allow_redirects=False, timeout=10, data={
        'name': f'Smoke {tag}', 'email': email, 'roles[]': ['station_operator'], 'status': 'active',
        'password': password, 'confirm_password': password})
    check('[identity] portal creates a user (local, same identity tables)', created.status_code == 302,
          f'HTTP {created.status_code}')
    login = requests.post(AUTH + '/login', json={'email': email, 'password': password}, timeout=10)
    check('[identity] the new user signs in at AuthService (shared password hash)', login.ok, f'HTTP {login.status_code}')
    if not login.ok:
        return
    token = login.cookies.get('access_token')
    listing = requests.get(WEB + '/admin/users?status=all&search=' + tag, cookies=cookies, timeout=10).text
    match = re.search(r'/admin/users/([0-9a-f-]{36})/edit', listing)
    user_id = match.group(1) if match else ''
    off = requests.post(f'{WEB}/admin/users/{user_id}/deactivate', cookies=cookies, allow_redirects=False, timeout=10)
    verify = requests.get(AUTH + '/verify', headers={'Authorization': f'Bearer {token}'}, timeout=5)
    check('[identity] deactivation in the portal revokes the session (AuthService + Redis)',
          off.status_code == 302 and verify.status_code == 401, f'deactivate {off.status_code}, verify {verify.status_code}')
    requests.post(f'{WEB}/admin/users/{user_id}/activate', cookies=cookies, allow_redirects=False, timeout=10)
    login = requests.post(AUTH + '/login', json={'email': email, 'password': password}, timeout=10)
    token = login.cookies.get('access_token') if login.ok else None
    reset = requests.post(f'{WEB}/admin/users/{user_id}/password', cookies=cookies, allow_redirects=False, timeout=10,
                          data={'new_password': new_password, 'confirm_new_password': new_password})
    verify = requests.get(AUTH + '/verify', headers={'Authorization': f'Bearer {token}'}, timeout=5)
    old = requests.post(AUTH + '/login', json={'email': email, 'password': password}, timeout=10)
    new = requests.post(AUTH + '/login', json={'email': email, 'password': new_password}, timeout=10)
    check('[identity] password reset revokes the session; only the new password works',
          reset.status_code == 302 and verify.status_code == 401 and old.status_code == 401 and new.ok,
          f'reset {reset.status_code}, verify {verify.status_code}, old {old.status_code}, new {new.status_code}')


def main():
    env = load_test_env()
    common = {k: v for k, v in env.items()
              if k not in ('CATALOG_SERVICE_URL', 'AUDIT_SERVICE_URL', 'MONITORING_SERVICE_URL', 'SESSION_SERVICE_URL')}
    log_dir = Path(os.environ.get('SMOKE_LOG_DIR') or (ROOT / '.e2e'))
    auth_env = {**common, 'HOST': '127.0.0.1', 'PORT': '15001', 'JWT_SECRET_KEY': secrets.token_hex(32),
                'AUTH_COOKIE_SECURE': 'false', 'AUTH_ALLOWED_ORIGINS': WEB}
    web_env = {**common, 'AUTH_SERVICE_URL': AUTH, 'AUTH_BROWSER_URL': AUTH, 'SESSION_SECRET_KEY': secrets.token_hex(32),
               'FRONTEND_DEMO_MODE': 'false', 'MONGO_URL': '', 'ADMIN_STORAGE_BACKEND': 'local',
               'ADMIN_STORAGE_DIR': str(log_dir / 'admin_storage')}
    log_dir.mkdir(exist_ok=True)
    auth_log, web_log = open(log_dir / 'smoke_auth.log', 'w'), open(log_dir / 'smoke_web.log', 'w')
    auth = subprocess.Popen([sys.executable, 'main.py'], cwd=ROOT / 'apps' / 'BackendAuthService', env=auth_env,
                            stdout=auth_log, stderr=subprocess.STDOUT)
    web = subprocess.Popen([sys.executable, '-m', 'flask', '--app', 'app', 'run', '--host', '127.0.0.1', '--port', '15000'],
                           cwd=ROOT / 'apps' / 'BackendWebFlask', env=web_env, stdout=web_log, stderr=subprocess.STDOUT)
    try:
        wait_ready(AUTH + '/health', auth, 'AuthService')
        wait_ready(WEB + '/health/live', web, 'BackendWebFlask')
        check('AuthService /health', requests.get(AUTH + '/health', timeout=5).ok)
        check('BackendWebFlask /health/ready (PostgreSQL de prueba)', requests.get(WEB + '/health/ready', timeout=5).ok)
        for role, (email, password, home) in ACCOUNTS.items():
            login = requests.post(AUTH + '/login', json={'email': email, 'password': password}, timeout=10)
            codes = {r['code'] for r in (login.json().get('user', {}).get('roles') or [])} if login.ok else set()
            check(f'[{role}] login AuthService', login.ok and role in codes, f'HTTP {login.status_code}')
            if not login.ok:
                continue
            access, refresh = login.cookies.get('access_token'), login.cookies.get('refresh_token')
            page = portal_get(home, {'access_token': access, 'refresh_token': refresh})
            check(f'[{role}] {home} con sesión', page.status_code == 200, f'HTTP {page.status_code}')
            renewed = portal_get(home, {'refresh_token': refresh})
            check(f'[{role}] refresh transparente (solo refresh_token)',
                  renewed.status_code == 200 and 'access_token=' in renewed.headers.get('Set-Cookie', ''),
                  f'HTTP {renewed.status_code}')
            new_access = renewed.cookies.get('access_token') or access
            if role == 'it_admin':
                for path in ADMIN_PAGES:
                    response = portal_get(path, {'access_token': new_access})
                    check(f'[it_admin] GET {path}', response.status_code == 200, f'HTTP {response.status_code}')
                for path in SPD_ONLY_PAGES:
                    response = portal_get(path, {'access_token': new_access})
                    check(f'[it_admin] GET {path} -> 403 (SPD only)', response.status_code == 403, f'HTTP {response.status_code}')
                identity_lifecycle(new_access)
                roles_page = portal_get('/admin/roles', {'access_token': new_access}).text
                check('[it_admin] /admin/roles: 4 códigos fijos, sin crear/borrar (RF-AD-06)',
                      all(f'>{code}</th>' in roles_page for code in FIXED_ROLES)
                      and 'New Role' not in roles_page and '/delete' not in roles_page)
                created = requests.post(WEB + '/admin/roles/new', cookies={'access_token': new_access},
                                        data={'code': 'nurse_reviewer', 'description': 'x'}, allow_redirects=False, timeout=10)
                check('[it_admin] POST /admin/roles/new no existe', created.status_code == 404, f'HTTP {created.status_code}')
            else:
                forbidden = portal_get('/admin/users', {'access_token': new_access})
                check(f'[{role}] /admin/users denegado', forbidden.status_code == 403, f'HTTP {forbidden.status_code}')
            out = requests.get(WEB + '/sign-out', cookies={'access_token': new_access}, allow_redirects=False, timeout=10)
            check(f'[{role}] logout', out.status_code == 302, f'HTTP {out.status_code}')
            verify = requests.get(AUTH + '/verify', headers={'Authorization': f'Bearer {new_access}'}, timeout=5)
            check(f'[{role}] token revocado tras logout (Redis)', verify.status_code == 401, f'HTTP {verify.status_code}')
            after = portal_get(home, {'access_token': new_access})
            check(f'[{role}] portal redirige a sign-in tras logout', after.status_code == 302 and '/sign-in' in after.headers.get('Location', ''),
                  f'HTTP {after.status_code}')
        anonymous = portal_get('/admin/dashboard', {})
        check('[anónimo] /admin/dashboard redirige a sign-in', anonymous.status_code == 302, f'HTTP {anonymous.status_code}')
    finally:
        for process in (web, auth):
            process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
        auth_log.close()
        web_log.close()
    failed = results.count(False)
    print(f'\n{len(results) - failed} PASS, {failed} FAIL (logs en {log_dir})')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
