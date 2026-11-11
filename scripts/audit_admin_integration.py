"""Auditoría de integración Administración (monolito) <-> microservicios del Supervisor (ADMIN-01).

Arranca BackendAuthService (15001), BackendWebFlask (15000), CatalogService (15003) y SessionService
(15004) como procesos locales contra la BD/Redis del entorno aislado (docker-compose.test.yml), con
el mismo JWT_SECRET_KEY, y comprueba que lo que el Administrador publica en el portal es lo que ven
los servicios del Supervisor (misma PostgreSQL), sin tocar registros existentes:

- Familias creadas/desactivadas por Admin  -> CatalogService GET /families (token del Supervisor).
- Kits/piezas: Administración recibe 403 en el portal; CatalogService sigue sirviendo al Supervisor.
- Procedimiento creado por Admin y aviso de privacidad desactivado por Admin -> SessionService
  POST /schedule rechaza el aviso inactivo (422) DESPUÉS de aceptar el procedimiento: no se escribe nada.
- Fase desactivada por Admin -> si SessionService la acepta se informa como brecha (no escribe: el
  aviso inactivo sigue haciendo fallar la petición).

Datos creados (solo en la BD de prueba, marcados ZZ_AUD_<tag> / aud-<tag>): una familia, un
procedimiento, una fase y un aviso, todos inactivos o sin uso. Uso:

    python scripts/audit_admin_integration.py
"""
import io
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

import requests
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_test_env import load_test_env  # noqa: E402

AUTH, WEB = 'http://127.0.0.1:15001', 'http://127.0.0.1:15000'
sys.path.insert(0, str(Path(__file__).resolve().parent))
import portal_csrf  # noqa: E402  (MS-04: CSRF token for portal POSTs)
portal_csrf.enable(WEB)
CATALOG, SESSION = 'http://127.0.0.1:15003', 'http://127.0.0.1:15004'
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + str(detail) if detail and not ok else ''}", flush=True)


def note(name, detail):
    print(f'NOTE  {name}: {detail}', flush=True)


def wait(url, process, label):
    for _ in range(80):
        if process.poll() is not None:
            sys.exit(f'{label} terminó al arrancar ({process.returncode})')
        try:
            if requests.get(url, timeout=1).status_code < 500:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    sys.exit(f'{label} no respondió en {url}')


def login(email, password):
    response = requests.post(AUTH + '/login', json={'email': email, 'password': password}, timeout=10)
    response.raise_for_status()
    return response.cookies.get('access_token')


def main():
    env = load_test_env()
    tag = secrets.token_hex(3).upper()
    jwt_secret = secrets.token_hex(32)
    base = {k: v for k, v in env.items()
            if k not in ('CATALOG_SERVICE_URL', 'AUDIT_SERVICE_URL', 'MONITORING_SERVICE_URL', 'SESSION_SERVICE_URL')}
    shared = {**base, 'JWT_SECRET_KEY': jwt_secret}
    log_dir = ROOT / '.e2e'
    log_dir.mkdir(exist_ok=True)
    specs = {
        'auth': (['main.py'], ROOT / 'apps' / 'BackendAuthService',
                 {**shared, 'HOST': '127.0.0.1', 'PORT': '15001', 'AUTH_COOKIE_SECURE': 'false'}, AUTH + '/health'),
        'web': (['-m', 'flask', '--app', 'app', 'run', '--host', '127.0.0.1', '--port', '15000'],
                ROOT / 'apps' / 'BackendWebFlask',
                {**base, 'AUTH_SERVICE_URL': AUTH, 'AUTH_BROWSER_URL': AUTH, 'SESSION_SECRET_KEY': secrets.token_hex(16),
                 'FRONTEND_DEMO_MODE': 'false', 'MONGO_URL': '', 'ADMIN_STORAGE_BACKEND': 'local',
                 'ADMIN_STORAGE_DIR': str(log_dir / 'admin_storage')}, WEB + '/health/live'),
        'catalog': (['main.py'], ROOT / 'apps' / 'CatalogService',
                    {**shared, 'CATALOG_HOST': '127.0.0.1', 'CATALOG_PORT': '15003'}, CATALOG + '/health/live'),
        'session': (['main.py'], ROOT / 'apps' / 'SessionService',
                    {**shared, 'SESSION_HOST': '127.0.0.1', 'SESSION_PORT': '15004'}, SESSION + '/health/live'),
    }
    processes, logs = {}, []
    try:
        for name, (args, cwd, process_env, _) in specs.items():
            log = open(log_dir / f'audit_{name}.log', 'w')
            logs.append(log)
            processes[name] = subprocess.Popen([sys.executable, *args], cwd=cwd, env=process_env,
                                               stdout=log, stderr=subprocess.STDOUT)
        for name, (_, _, _, health) in specs.items():
            wait(health, processes[name], name)
        engine = create_engine(env['DATABASE_URL'])
        q = lambda sql, **p: engine.connect().execute(text(sql), p)  # noqa: E731  (read-only lookups)

        admin = login('admin@instrumed.com', 'DemopwdADM78!')
        spd = login('supervisor@instrumed.com', 'DemopwdSPD78!')
        admin_cookies, spd_bearer = {'access_token': admin}, {'Authorization': f'Bearer {spd}'}

        # --- Families: Admin (portal) -> CatalogService ---------------------------------------------
        category = q('SELECT id FROM cat_instrument_category ORDER BY code LIMIT 1').scalar_one()
        code = f'ZZ_AUD_{tag}'
        created = requests.post(WEB + '/admin/instrument-families/new', cookies=admin_cookies, allow_redirects=False,
                                data={'code': code, 'name': f'Audit family {tag}', 'category': str(category),
                                      'status': 'active'}, timeout=10)
        check('Admin crea familia en el portal', created.status_code == 302, created.status_code)
        families = requests.get(CATALOG + '/api/v1/catalog/families', headers=spd_bearer, timeout=10).json()
        seen = next((f for f in families if f['code'] == code), None)
        check('CatalogService (token SPD) ve la familia creada por Admin, activa', seen and seen['active'], seen)
        family_id = q('SELECT id FROM instrument_family WHERE code = :c', c=code).scalar_one()
        requests.post(f'{WEB}/admin/instrument-families/{family_id}/edit', cookies=admin_cookies, timeout=10,
                      data={'name': f'Audit family {tag}', 'category': str(category), 'status': 'inactive'})
        families = requests.get(CATALOG + '/api/v1/catalog/families', headers=spd_bearer, timeout=10).json()
        seen = next((f for f in families if f['code'] == code), None)
        check('CatalogService refleja la desactivación hecha por Admin', seen and seen['active'] is False, seen)
        # MS-01 / P4: the family Admin just deactivated cannot be used in a NEW piece (nothing is created).
        cycle = q('SELECT code FROM cat_instrument_cycle_status ORDER BY code LIMIT 1').scalar_one()
        piece = requests.post(CATALOG + '/api/v1/catalog/instruments', headers=spd_bearer, timeout=10,
                              json={'internal_code': f'AUD-{tag}', 'family_id': str(family_id), 'cycle_status_code': cycle})
        check('CatalogService rechaza una pieza nueva con la familia desactivada por Admin (P4, 422 inactive_family)',
              piece.status_code == 422 and (piece.json().get('details') or {}).get('reason') == 'inactive_family',
              (piece.status_code, piece.text[:120]))

        # --- Kits / pieces: SPD only ---------------------------------------------------------------------
        portal_kits = requests.get(WEB + '/admin/kits', cookies=admin_cookies, allow_redirects=False, timeout=10)
        check('Portal: Administración no accede a kits (403)', portal_kits.status_code == 403, portal_kits.status_code)
        spd_kits = requests.get(CATALOG + '/api/v1/catalog/kits', headers=spd_bearer, timeout=10)
        check('CatalogService sigue sirviendo kits al Supervisor', spd_kits.ok, spd_kits.status_code)
        admin_bearer = {'Authorization': f'Bearer {admin}'}
        catalog_writers = requests.post(CATALOG + '/api/v1/catalog/kits', headers=admin_bearer, json={}, timeout=10)
        check('CatalogService: it_admin ya no escribe kits ni piezas (P3, 403)',
              catalog_writers.status_code == 403 and requests.post(
                  CATALOG + '/api/v1/catalog/instruments', headers=admin_bearer, json={}, timeout=10).status_code == 403,
              catalog_writers.status_code)

        # --- Procedure, phase and privacy notice: Admin -> SessionService ------------------------------------
        proc_code = f'zz_aud_{tag.lower()}'
        requests.post(WEB + '/admin/procedures/new', cookies=admin_cookies, timeout=10,
                      data={'code': proc_code, 'name': f'Audit procedure {tag}'})
        procedure_id = q('SELECT id FROM cat_procedure_type WHERE code = :c', c=proc_code).scalar_one()
        phase_code = f'zz_aud_{tag.lower()}'
        requests.post(WEB + '/admin/selection-catalogs/phases', cookies=admin_cookies, timeout=10,
                      data={'code': phase_code, 'name': f'Audit phase {tag}', 'status': 'inactive'})
        check('Admin crea una fase (inactiva)', q('SELECT active FROM cat_operation_phase WHERE code = :c',
                                                  c=phase_code).scalar_one() is False)
        published = requests.post(WEB + '/admin/privacy-notices/new', cookies=admin_cookies, allow_redirects=False,
                                  timeout=10, files={'document': ('aviso.pdf', io.BytesIO(b'%PDF-1.4 audit ' + tag.encode()))},
                                  data={'version': f'aud-{tag}', 'effective_at': '2026-11-03', 'status': 'inactive'})
        check('Admin publica un aviso (inactivo)', published.status_code == 302, published.status_code)
        notice_id = q('SELECT id FROM privacy_notice_version WHERE version = :v', v=f'aud-{tag}').scalar_one()
        operator = q("SELECT id FROM \"user\" WHERE email = 'operator@instrumed.com'").scalar_one()
        physician = q('SELECT id FROM physician LIMIT 1').scalar()
        station = q('SELECT s.id, s.room_id FROM capture_station s JOIN operating_room r ON r.id = s.room_id '
                    'JOIN "user" u ON u.institution_id = r.institution_id '
                    'WHERE s.active AND r.active AND u.id = :o ORDER BY s.name LIMIT 1', o=operator).one()
        kit = q('SELECT ki.kit_id, ki.family_id FROM kit_item ki JOIN instrument_family f ON f.id = ki.family_id '
                'WHERE f.active AND ki.kit_id = (SELECT k.id FROM kit k JOIN "user" u ON u.institution_id = k.institution_id '
                'WHERE k.active AND u.id = :o ORDER BY k.name LIMIT 1) LIMIT 1', o=operator).one()
        before = q('SELECT count(*) FROM work_session').scalar_one()

        def schedule(**overrides):
            body = {'operator_user_id': str(operator), 'procedure_type_id': str(procedure_id), 'room_id': str(station.room_id),
                    'station_id': str(station.id), 'kit_id': str(kit.kit_id), 'scheduled_at': '2026-12-01T10:00:00Z',
                    'phase_code': 'start', 'physicians': [{'physician_id': str(physician), 'surgical_role_code': 'surgeon'}],
                    'expected_lines': [{'family_id': str(kit.family_id), 'expected_quantity': 1}],
                    'privacy_notice_version_id': str(notice_id)}
            body.update(overrides)
            response = requests.post(SESSION + '/api/v1/sessions/schedule', headers=spd_bearer, json=body, timeout=10)
            return response.status_code, (response.json().get('details') or {}).get('reason')

        status, reason = schedule()
        check('SessionService acepta el procedimiento de Admin y rechaza su aviso INACTIVO (422)',
              (status, reason) == (422, 'invalid_privacy_notice'), (status, reason))
        status, reason = schedule(phase_code=phase_code)
        check('SessionService rechaza la fase desactivada por Admin (P5, 422 inactive_phase)',
              (status, reason) == (422, 'inactive_phase'), (status, reason))
        retired = q('SELECT s.id, s.room_id FROM capture_station s JOIN operating_room r ON r.id = s.room_id '
                    'JOIN "user" u ON u.institution_id = r.institution_id WHERE NOT r.active AND u.id = :o LIMIT 1',
                    o=operator).first()
        if retired is not None:
            status, reason = schedule(room_id=str(retired.room_id), station_id=str(retired.id))
            check('SessionService rechaza una sala desactivada por Admin (P5, 422 inactive_room)',
                  (status, reason) == (422, 'inactive_room'), (status, reason))
        else:
            note('Sin sala inactiva en la BD de prueba', 'la comprobación de sala inactiva no se ejecutó')
        check('Ninguna sesión fue escrita por la auditoría', q('SELECT count(*) FROM work_session').scalar_one() == before)
    finally:
        for process in processes.values():
            process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
        for log in logs:
            log.close()
    failed = results.count(False)
    print(f'\n{len(results) - failed} PASS, {failed} FAIL (logs en {log_dir}/audit_*.log)')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
