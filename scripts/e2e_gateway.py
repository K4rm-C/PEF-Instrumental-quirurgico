"""MS-04 real end-to-end: API Gateway (nginx), authorization through it, contracts, TLS and the portal behind it.

Isolated stack only (docker-compose.test.yml / .env.test). Real processes on the host: AuthService :15001
(AUTH_TRUSTED_PROXIES=1), portal :15000, CatalogService :15003, SessionService :15004, EvidenceService :15005,
AuditService :15006, NotificationsService :15007, MonitoringService :15008; VisionWorker in a container; the
gateway is the nginx image with the repository configuration (infra/gateway), once in development mode (HTTP,
127.0.0.1:18080) and once in production mode (TLS, 127.0.0.1:18443 + redirect on 18081) with a throw-away
self-signed certificate generated for this run in a temporary directory (never in the repository).

    PYTHONIOENCODING=utf-8 python scripts/e2e_gateway.py
"""
import base64
import json
import os
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import requests
from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_test_env import load_test_env  # noqa: E402
import run_admin_browser_env as envctl  # noqa: E402  (enables the portal CSRF helper for :15000)
import validate_contracts as contracts  # noqa: E402
from e2e_ms02_model_pin import CONTAINER as VISION_CONTAINER, IMAGE as VISION_IMAGE, NETWORK, docker, wait  # noqa: E402

GW_IMAGE = 'nginx:1.27.4-alpine'
HOST_IPV4 = None   # IPv4 of the Docker host as seen from the test network (resolved at start)
GW_HTTP, GW_TLS = 'pef-ms04-gateway-http', 'pef-ms04-gateway-tls'
GW = 'http://127.0.0.1:18080'
GW_TLS_URL, GW_TLS_REDIRECT = 'https://127.0.0.1:18443', 'http://127.0.0.1:18081'
API_HOST, PORTAL_HOST = 'api.pef.test', 'portal.pef.test'
PORTAL_ORIGIN = f'https://{PORTAL_HOST}:18443'
PORTS = {'web': 15000, 'auth': 15001, 'vision': 15002, 'catalog': 15003, 'session': 15004, 'evidence': 15005,
         'audit': 15006, 'notifications': 15007, 'monitoring': 15008, 'integration': 15009}
DIRECT = {name: f'http://127.0.0.1:{port}' for name, port in PORTS.items()}
OPERATOR, SUPERVISOR, ADMIN = (('operator@instrumed.com', 'DemopwdOP78!'), ('supervisor@instrumed.com', 'DemopwdSPD78!'),
                               ('admin@instrumed.com', 'DemopwdADM78!'))
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + str(detail)[:400] if detail and not ok else ''}", flush=True)
    return ok


def info(name, detail):
    print(f'INFO  {name}: {detail}', flush=True)


def gateway_env(*, tls):
    # IPv4-only alias of the host (--add-host ...:host-gateway): host.docker.internal may also resolve to an
    # unreachable IPv6 address on Docker Desktop, which nginx would round-robin into 502s.
    host = HOST_IPV4
    values = {
        'PEF_GW_AUTH_UPSTREAM': f'{host}:{PORTS["auth"]}', 'PEF_GW_CATALOG_UPSTREAM': f'{host}:{PORTS["catalog"]}',
        'PEF_GW_SESSION_UPSTREAM': f'{host}:{PORTS["session"]}', 'PEF_GW_EVIDENCE_UPSTREAM': f'{host}:{PORTS["evidence"]}',
        'PEF_GW_NOTIFICATIONS_UPSTREAM': f'{host}:{PORTS["notifications"]}',
        'PEF_GW_MONITORING_UPSTREAM': f'{host}:{PORTS["monitoring"]}',
        'PEF_GW_INTEGRATION_UPSTREAM': f'{host}:{PORTS["integration"]}',   # MS-07
        'PEF_GW_VISION_UPSTREAM': f'{VISION_CONTAINER}:5002', 'PEF_GW_PORTAL_UPSTREAM': f'{host}:{PORTS["web"]}',
        'PEF_GW_RATE_API': '20r/s', 'PEF_GW_BURST_API': '40', 'PEF_GW_RATE_AUTH': '10r/m', 'PEF_GW_BURST_AUTH': '20',
        'PEF_GW_MAX_CONN_PER_IP': '50', 'PEF_GW_MAX_BODY': '1m', 'PEF_GW_EVIDENCE_MAX_BODY': '10m',
        'PEF_GW_UPSTREAM_TIMEOUT': '30s', 'PEF_GW_WS_TIMEOUT': '300s', 'PEF_GW_PORTAL_MAX_BODY': '600m',
        'PEF_GW_PORTAL_TIMEOUT': '300s', 'PEF_GW_RATE_PORTAL': '50r/s', 'PEF_GW_BURST_PORTAL': '200', 'PEF_GW_API_HOST': API_HOST, 'PEF_GW_PORTAL_HOST': PORTAL_HOST,
        'PEF_GW_RESOLVER_DIRECTIVE': '', 'PEF_GW_UPSTREAM_RESOLVE': '',   # MS-11: static upstreams (host IP) here
    }
    args = []
    for key, value in values.items():
        args += ['-e', f'{key}={value}']
    infra = ROOT / 'infra' / 'gateway'
    args += ['-v', f'{infra / "templates" / "00-pef-http.conf.template"}:/etc/nginx/templates/00-pef-http.conf.template:ro',
             '-v', f'{infra / "templates" / "pef"}:/etc/nginx/templates/pef:ro',
             '-v', f'{infra / "servers" / ("tls" if tls else "http")}.conf.template:/etc/nginx/templates/default.conf.template:ro']
    return args


def gw_login(email, password, **headers):
    response = requests.post(GW + '/api/v1/auth/login', json={'email': email, 'password': password}, timeout=15,
                             headers=headers)
    return response


def bearer(token):
    return {'Authorization': f'Bearer {token}'}


def main():
    env = load_test_env()
    engine = create_engine(env['DATABASE_URL'], future=True)
    jwt_secret, ws_secret = secrets.token_hex(32), secrets.token_hex(24)

    def q(sql, **params):
        with engine.connect() as c:
            return c.execute(text(sql), params).all()

    def q1(sql, **params):
        rows = q(sql, **params)
        return rows[0][0] if rows else None

    global HOST_IPV4
    import redis
    test_redis = redis.Redis.from_url(env['REDIS_URL'])

    def clear_login_lockouts():
        # Isolated test Redis only: the rate-limit check of this script leaves the gateway's client address
        # locked out for AUTH_LOGIN_FAIL_TTL_SECONDS (expected AuthService behaviour); runs must be repeatable.
        for key in test_redis.scan_iter('auth:login_fail:*'):
            test_redis.delete(key)
    clear_login_lockouts()
    resolved = subprocess.run(['docker', 'run', '--rm', '--network', NETWORK, GW_IMAGE, 'sh', '-c',
                               'getent ahostsv4 host.docker.internal | head -n 1'], capture_output=True, text=True)
    HOST_IPV4 = resolved.stdout.split()[0]
    info('Docker host (IPv4) for the upstreams of this test', HOST_IPV4)
    certs = Path(tempfile.mkdtemp(prefix='pef-ms04-certs-'))
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=pef-ms04-test',
                    '-addext', f'subjectAltName=DNS:{API_HOST},DNS:{PORTAL_HOST},IP:127.0.0.1',
                    '-keyout', str(certs / 'privkey.pem'), '-out', str(certs / 'fullchain.pem')],
                   check=True, capture_output=True)

    # --- processes ------------------------------------------------------------------------------------------
    for name in (GW_HTTP, GW_TLS, VISION_CONTAINER):
        docker('rm', '-f', name, check_rc=False)
    docker('run', '-d', '--name', VISION_CONTAINER, '--network', NETWORK, '-p', '127.0.0.1:15002:5002',
           '-v', f"{ROOT / 'apps' / 'VisionWorker'}:/app:ro", '-e', f'VISION_WS_SECRET={ws_secret}',
           '-e', f'VISION_WS_ORIGINS={DIRECT["web"]},{PORTAL_ORIGIN}', '-e', 'VISION_WEIGHTS_PATH=/models/not-mounted.pt',
           '-e', 'VISION_UPLOAD_DIR=/tmp/uploads', '-e', 'TELEMETRY_SPOOL_DIR=/tmp/spool', VISION_IMAGE)
    auth_env, web_env = envctl.build_env('local', '')
    auth_env.update({'JWT_SECRET_KEY': jwt_secret, 'AUTH_TRUSTED_PROXIES': '1', 'AUTH_COOKIE_SECURE': 'true',
                     'AUTH_ALLOWED_ORIGINS': f'{DIRECT["web"]},{PORTAL_ORIGIN}'})
    service_env = {**env, 'JWT_SECRET_KEY': jwt_secret}
    s3 = env['PEF_TEST_S3_ENDPOINT']
    envs = {
        'catalog': {**service_env, 'CATALOG_HOST': '127.0.0.1', 'CATALOG_PORT': str(PORTS['catalog'])},
        'session': {**service_env, 'SESSION_HOST': '127.0.0.1', 'SESSION_PORT': str(PORTS['session'])},
        'evidence': {**service_env, 'EVIDENCE_HOST': '127.0.0.1', 'EVIDENCE_PORT': str(PORTS['evidence']),
                     'S3_INTERNAL_ENDPOINT': s3, 'S3_PUBLIC_ENDPOINT': s3, 'S3_REGION': 'garage',
                     'S3_ACCESS_KEY_ID': env['PEF_TEST_S3_ACCESS_KEY'], 'S3_SECRET_ACCESS_KEY': env['PEF_TEST_S3_SECRET_KEY'],
                     'S3_BUCKET_EVIDENCE': 'pef-evidence-test'},
        'audit': {**service_env, 'AUDIT_HOST': '127.0.0.1', 'AUDIT_PORT': str(PORTS['audit'])},
        'notifications': {**service_env, 'NOTIFICATIONS_HOST': '127.0.0.1', 'NOTIFICATIONS_PORT': str(PORTS['notifications'])},
        'monitoring': {**service_env, 'MONITORING_HOST': '127.0.0.1', 'MONITORING_PORT': str(PORTS['monitoring']),
                       'MONITORING_MONGO_URL': env['PEF_TEST_MONGO_URL']},
    }
    folders = {'catalog': 'CatalogService', 'session': 'SessionService', 'evidence': 'EvidenceService',
               'audit': 'AuditService', 'notifications': 'NotificationsService', 'monitoring': 'MonitoringService'}
    web_env.update({'JWT_SECRET_KEY': jwt_secret, 'VISION_WORKER_URL': DIRECT['vision'], 'VISION_WS_SECRET': ws_secret,
                    'SESSION_SERVICE_URL': DIRECT['session'], 'CATALOG_SERVICE_URL': DIRECT['catalog'],
                    'EVIDENCE_SERVICE_URL': DIRECT['evidence'], 'NOTIFICATIONS_SERVICE_URL': DIRECT['notifications'],
                    'AUDIT_SERVICE_URL': DIRECT['audit'], 'MONITORING_SERVICE_URL': DIRECT['monitoring'],
                    # The portal is reached through the TLS gateway in the production-mode part of this test.
                    'AUTH_BROWSER_URL': f'{PORTAL_ORIGIN}/auth', 'PORTAL_COOKIE_SECURE': 'true',
                    'VISION_WORKER_WS_URL': f'wss://{PORTAL_HOST}:18443/ws'})
    logs = ROOT / '.e2e'
    procs = {'auth': subprocess.Popen([sys.executable, 'main.py'], cwd=ROOT / 'apps' / 'BackendAuthService', env=auth_env,
                                      stdout=open(logs / 'ms04_auth.log', 'w'), stderr=subprocess.STDOUT)}
    for name, folder in folders.items():
        procs[name] = subprocess.Popen([sys.executable, 'main.py'], cwd=ROOT / 'apps' / folder, env=envs[name],
                                       stdout=open(logs / f'ms04_{name}.log', 'w'), stderr=subprocess.STDOUT)
    procs['web'] = subprocess.Popen([sys.executable, '-m', 'flask', '--app', 'app', 'run', '--host', '127.0.0.1',
                                     '--port', str(PORTS['web'])], cwd=ROOT / 'apps' / 'BackendWebFlask', env=web_env,
                                    stdout=open(logs / 'ms04_web.log', 'w'), stderr=subprocess.STDOUT)
    created = []
    try:
        wait(DIRECT['auth'] + '/health', 'AuthService', 240)
        for name in folders:
            wait(DIRECT[name] + '/health/live', name, 240)
        wait(DIRECT['web'] + '/health/live', 'portal', 240)
        wait(DIRECT['vision'] + '/health', 'VisionWorker', 240)
        alias = []
        docker('run', '-d', '--name', GW_HTTP, '--network', NETWORK, *alias, '-p', '127.0.0.1:18080:8080', *gateway_env(tls=False),
               GW_IMAGE)
        docker('run', '-d', '--name', GW_TLS, '--network', NETWORK, *alias, '-p', '127.0.0.1:18443:8443', '-p', '127.0.0.1:18081:8080',
               '-v', f'{certs}:/etc/nginx/certs:ro', *gateway_env(tls=True), GW_IMAGE)
        wait(GW + '/healthz', 'gateway (HTTP)', 60)

        # ===================== 1. Gateway basics ===============================================================
        health = requests.get(GW + '/healthz', timeout=5)
        check('gateway health answers itself (no upstream detail); Server header without version',
              health.status_code == 200 and health.json() == {'status': 'ok', 'service': 'pef-gateway'}
              and health.headers.get('Server') == 'nginx', (health.status_code, health.headers.get('Server')))

        # ===================== 2. Authentication through the gateway =========================================
        spd_login = gw_login(*SUPERVISOR, **{'X-Forwarded-For': '6.6.6.6'})
        spd = spd_login.json()
        spd_token, spd_refresh = spd['access_token'], spd['refresh_token']
        check('login through the gateway: tokens in the body, no cookie is set',
              spd_login.status_code == 200 and spd_token and 'Set-Cookie' not in spd_login.headers, spd_login.text[:200])
        spd_id = q1('SELECT id FROM "user" WHERE email = :e', e=SUPERVISOR[0])
        audit_ip = q1("SELECT host(ip) FROM access_audit WHERE actor_user_id = :u AND action = 'LOGIN' "
                      'ORDER BY occurred_at DESC LIMIT 1', u=spd_id)
        check('a spoofed X-Forwarded-For never reaches AuthService (audit records the address the gateway saw)',
              audit_ip and audit_ip != '6.6.6.6', audit_ip)
        info('login audit address (gateway view of the client)', audit_ip)
        op_token = gw_login(*OPERATOR).json()['access_token']
        admin_token = gw_login(*ADMIN).json()['access_token']

        families = requests.get(GW + '/api/v1/catalog/families', headers=bearer(spd_token), timeout=15)
        check('valid token: GET /api/v1/catalog/families → 200 JSON', families.status_code == 200
              and families.headers['Content-Type'].startswith('application/json'), families.status_code)
        xml = requests.get(GW + '/api/v1/catalog/families', headers={**bearer(spd_token), 'Accept': 'application/xml'}, timeout=15)
        check('content negotiation passes through: Accept application/xml → XML', xml.status_code == 200
              and 'xml' in xml.headers['Content-Type'], xml.headers.get('Content-Type'))
        published = ['/api/v1/catalog/families', '/api/v1/sessions', '/api/v1/notifications', '/api/v1/auth/me']
        anonymous = {path: requests.get(GW + path, timeout=15).status_code for path in published}
        check('anonymous access → 401 on every published service', set(anonymous.values()) == {401}, anonymous)
        now = datetime.now(timezone.utc)
        expired = jwt.encode({'sub': str(spd_id), 'jti': str(uuid.uuid4()), 'type': 'access', 'iat': now - timedelta(hours=2),
                              'exp': now - timedelta(hours=1), 'role': 'spd_supervisor'}, jwt_secret, algorithm='HS256')
        forged = jwt.encode({'sub': str(spd_id), 'jti': str(uuid.uuid4()), 'type': 'access', 'iat': now,
                             'exp': now + timedelta(minutes=5), 'role': 'spd_supervisor'}, 'not-the-secret-' + 'x' * 32,
                            algorithm='HS256')
        unsigned = jwt.encode({'sub': str(spd_id), 'jti': str(uuid.uuid4()), 'type': 'access',
                               'exp': now + timedelta(minutes=5)}, None, algorithm='none')
        for label, token in (('expired token', expired), ('invalid signature', forged), ('alg=none token', unsigned)):
            codes = {path: requests.get(GW + path, headers=bearer(token), timeout=15).status_code for path in published}
            check(f'{label} → 401 on every published service', set(codes.values()) == {401}, codes)
        cookie_only = requests.get(GW + '/api/v1/auth/me', cookies={'access_token': spd_token}, timeout=15)
        check('cookies never authenticate through the gateway (Cookie header dropped) → 401',
              cookie_only.status_code == 401, cookie_only.status_code)

        # Revocation in Redis: logout through the gateway, then the same access token everywhere.
        victim = gw_login(*SUPERVISOR).json()
        before = requests.get(GW + '/api/v1/catalog/families', headers=bearer(victim['access_token']), timeout=15).status_code
        logout = requests.post(GW + '/api/v1/auth/logout', json={'refresh_token': victim['refresh_token']},
                               headers=bearer(victim['access_token']), timeout=15)
        revoked = {path: requests.get(GW + path, headers=bearer(victim['access_token']), timeout=15).status_code
                   for path in published}
        refresh = requests.post(GW + '/api/v1/auth/refresh', json={'refresh_token': victim['refresh_token']}, timeout=15)
        check('revoked token (Redis) → 401 on every service through the gateway; its refresh token too',
              before == 200 and logout.status_code == 204 and set(revoked.values()) == {401} and refresh.status_code == 401,
              (before, logout.status_code, revoked, refresh.status_code))

        # ===================== 3. Authorization (roles, institutions) ========================================
        inst = q1('SELECT institution_id FROM "user" WHERE email = :e', e=SUPERVISOR[0])
        session_id = q1('SELECT w.id FROM work_session w JOIN operation o ON o.id = w.operation_id '
                        'WHERE o.institution_id = :i ORDER BY w.started_at DESC NULLS LAST LIMIT 1', i=inst)
        family_body = {'code': f'GW{secrets.token_hex(3).upper()}', 'name': 'Gateway test family'}
        op_write = requests.post(GW + '/api/v1/catalog/families', json=family_body, headers=bearer(op_token), timeout=15)
        admin_evidence = requests.get(f'{GW}/api/v1/sessions/{session_id}/evidence', headers=bearer(admin_token), timeout=15)
        op_schedule = requests.post(GW + '/api/v1/sessions/schedule', json={}, headers=bearer(op_token), timeout=15)
        check('role without permission → 403 (operator writes the catalog, it_admin reads evidence, operator schedules)',
              (op_write.status_code, admin_evidence.status_code, op_schedule.status_code) == (403, 403, 403),
              (op_write.status_code, admin_evidence.status_code, op_schedule.status_code))
        inst_b = q1("SELECT id FROM institution WHERE name = 'ZZ Browser Institution B'")
        password_b = 'Ms04-' + secrets.token_urlsafe(10)
        with engine.begin() as c:
            role_b = c.execute(text("SELECT id FROM role WHERE institution_id = :i AND code = 'spd_supervisor'"), {'i': inst_b}).scalar_one()
            uid_b = c.execute(text('SELECT id FROM "user" WHERE email = :e'), {'e': 'spd.b@ms03-test.invalid'}).scalar()
            if uid_b is None:
                uid_b = c.execute(text('INSERT INTO "user" (name, email, password_hash, institution_id) VALUES '
                                       "('SPD B (test)', 'spd.b@ms03-test.invalid', :h, :i) RETURNING id"),
                                  {'h': generate_password_hash(password_b), 'i': inst_b}).scalar_one()
                c.execute(text('INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)'), {'u': uid_b, 'r': role_b})
            else:
                c.execute(text('UPDATE "user" SET password_hash = :h, active = TRUE WHERE id = :u'),
                          {'h': generate_password_hash(password_b), 'u': uid_b})
        token_b = gw_login('spd.b@ms03-test.invalid', password_b).json()['access_token']
        cross = {'session': requests.get(f'{GW}/api/v1/sessions/{session_id}', headers=bearer(token_b), timeout=15).status_code,
                 'evidence': requests.get(f'{GW}/api/v1/sessions/{session_id}/evidence', headers=bearer(token_b),
                                          timeout=15).status_code}
        own = requests.get(f'{GW}/api/v1/sessions/{session_id}', headers=bearer(spd_token), timeout=15).status_code
        check('another institution: session and evidence → 404 (the owner gets 200)',
              cross == {'session': 404, 'evidence': 404} and own == 200, (cross, own))

        # or_charge (floor supervisor): recognized by AuthService, no business permission granted by MS-04.
        role_oc = q1("SELECT id FROM role WHERE institution_id = :i AND code = 'or_charge'", i=inst)
        password_oc = 'Ms04-' + secrets.token_urlsafe(10)
        with engine.begin() as c:
            uid_oc = c.execute(text('SELECT id FROM "user" WHERE email = :e'), {'e': 'orcharge.a@ms04-test.invalid'}).scalar()
            if uid_oc is None:
                uid_oc = c.execute(text('INSERT INTO "user" (name, email, password_hash, institution_id) VALUES '
                                        "('Floor supervisor (MS-04 test)', 'orcharge.a@ms04-test.invalid', :h, :i) RETURNING id"),
                                   {'h': generate_password_hash(password_oc), 'i': inst}).scalar_one()
                c.execute(text('INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)'), {'u': uid_oc, 'r': role_oc})
            else:
                c.execute(text('UPDATE "user" SET password_hash = :h, active = TRUE WHERE id = :u'),
                          {'h': generate_password_hash(password_oc), 'u': uid_oc})
        oc_login = gw_login('orcharge.a@ms04-test.invalid', password_oc)
        oc_token = oc_login.json().get('access_token')
        oc_me = requests.get(GW + '/api/v1/auth/me', headers=bearer(oc_token), timeout=15)
        oc_codes = {path: requests.get(GW + path, headers=bearer(oc_token), timeout=15).status_code for path in
                    ('/api/v1/catalog/families', '/api/v1/sessions', f'/api/v1/sessions/{session_id}/evidence',
                     '/api/v1/notifications')}
        me_body = oc_me.json() if oc_me.ok else {}
        raw_roles = me_body.get('roles') or (me_body.get('user') or {}).get('roles') or []
        roles_oc = [r.get('code') if isinstance(r, dict) else r for r in raw_roles]
        info('or_charge through the gateway', f'login {oc_login.status_code}, /me roles {roles_oc}, business {oc_codes}')
        # Catalog reads are open to every authenticated role of the institution (existing CatalogService design,
        # @require_auth() without roles); notifications are the user's own. MS-04 grants nothing new.
        business = {k: v for k, v in oc_codes.items() if '/sessions' in k}
        check('or_charge is recognized by AuthService (login, /me) and gets no session or evidence access',
              oc_login.status_code == 200 and 'or_charge' in roles_oc and all(v in (403, 404) for v in business.values()),
              (oc_login.status_code, roles_oc, oc_codes))

        # ===================== 4. Routes that are not published ===============================================
        hidden = ['/api/v1/auth/users', f'/api/v1/auth/users/{spd_id}', '/api/v1/auth/roles', '/api/v1/auth/docs',
                  '/api/v1/audit/events', '/api/v1/monitoring/summary', '/health/ready', '/health/live',
                  '/api/v1/monitoring/stations', '/api/v1/monitoring/alerts', '/api/v1/sessions/reports/export',
                  '/api/v1/integration/endpoints', '/api/v1/integration/deliveries', '/fhir/r4/Observation',
                  '/api/v1/catalog/docs', '/api/v1/sessions/docs', '/api/v1/notifications/docs', '/api/v1/whoami',
                  '/__test__/reset', f'/sessions/{session_id}/bind', f'/sessions/{session_id}/state', '/auth/revoke',
                  '/login', '/verify', '/refresh', '/sign-in', '/admin/users', '/', '/api/v1/catalog/health']
        exposed = {}
        for path in hidden:
            response = requests.get(GW + path, headers=bearer(admin_token), timeout=15)
            if response.status_code != 404 or 'not published' not in response.text:
                exposed[path] = response.status_code
        posted = {path: requests.post(GW + path, json={}, headers=bearer(admin_token), timeout=15).status_code
                  for path in (f'/api/v1/auth/users/{spd_id}/sessions/revoke', '/auth/revoke', '/logout',
                               f'/sessions/{session_id}/bind')}
        check(f'{len(hidden) + len(posted)} internal / administrative routes are not published (404 from the gateway)',
              not exposed and set(posted.values()) == {404}, (exposed, posted))
        docs = {path: requests.get(GW + path, timeout=15).status_code for path in
                ('/api/v1/catalog/openapi.yaml', '/api/v1/sessions/schemas/session-reports.xsd',
                 '/api/v1/auth/schemas/identity.xsd', '/api/v1/notifications/openapi.yaml')}
        check('contract documents (OpenAPI, XSD) are published read-only', set(docs.values()) == {200}, docs)
        # MS-05: only the station heartbeat of MonitoringService is published (desktop station clients).
        station_id = q1('SELECT s.id FROM capture_station s JOIN operating_room r ON r.id = s.room_id '
                        'WHERE r.institution_id = :i AND s.active AND r.active ORDER BY s.name LIMIT 1', i=inst)
        beat = requests.post(f'{GW}/api/v1/monitoring/stations/{station_id}/heartbeat', json={'camera_status': 'unknown',
                             'client': 'gateway-e2e'}, headers=bearer(op_token), timeout=15)
        beat_spd = requests.post(f'{GW}/api/v1/monitoring/stations/{station_id}/heartbeat', json={},
                                 headers=bearer(spd_token), timeout=15)
        check('station heartbeat is published (operator 200, supervisor 403); station lists and alerts are not',
              beat.status_code == 200 and beat_spd.status_code == 403, (beat.status_code, beat_spd.status_code))

        # ===================== 5. Abuse controls and headers ==================================================
        big = requests.post(GW + '/api/v1/catalog/families', data=b'{"x":"' + b'a' * (2 * 1024 * 1024) + b'"}',
                            headers={**bearer(spd_token), 'Content-Type': 'application/json'}, timeout=30)
        check('body over the limit → 413 JSON from the gateway (never reaches the service)',
              big.status_code == 413 and big.json()['error'] == 'payload_too_large', big.status_code)
        methods = {m: requests.request(m, GW + '/api/v1/catalog/families', headers=bearer(spd_token), timeout=15).status_code
                   for m in ('DELETE', 'PATCH', 'TRACE')}
        check('methods outside each route are refused (DELETE/PATCH on the catalog, TRACE)',
              methods['DELETE'] in (403, 405) and methods['PATCH'] in (403, 405) and methods['TRACE'] == 405, methods)
        cid = 'ms04-' + uuid.uuid4().hex
        kept = requests.get(GW + '/api/v1/catalog/families', headers={**bearer(spd_token), 'X-Correlation-ID': cid}, timeout=15)
        replaced = requests.get(GW + '/api/v1/catalog/families', headers={**bearer(spd_token), 'X-Correlation-ID': 'bad id<>'},
                                timeout=15)
        check('X-Correlation-ID: a valid one is kept end to end, an invalid one is replaced',
              kept.headers.get('X-Correlation-ID') == cid and replaced.headers.get('X-Correlation-ID') not in (None, 'bad id<>'),
              (kept.headers.get('X-Correlation-ID'), replaced.headers.get('X-Correlation-ID')))
        check('security headers on API responses (nosniff; no upstream Server/X-Powered-By)',
              kept.headers.get('X-Content-Type-Options') == 'nosniff' and 'Werkzeug' not in kept.headers.get('Server', ''),
              dict(kept.headers))

        # ===================== 6. WebSocket through the gateway ==============================================
        import websocket
        sys.path.insert(0, str(ROOT / 'apps' / 'BackendWebFlask'))
        os.environ['VISION_WS_SECRET'] = ws_secret
        from services.vision_ws_ticket import issue_ws_ticket
        op_id = q1('SELECT id FROM "user" WHERE email = :e', e=OPERATOR[0])

        def ws_bind(ticket, origin):
            try:
                ws = websocket.create_connection('ws://127.0.0.1:18080/ws', origin=origin, timeout=10)
            except Exception as exc:  # noqa: BLE001
                return f'handshake: {type(exc).__name__}'
            try:
                ws.send(json.dumps({'type': 'session.bind', 'session_id': str(session_id), 'auth_token': ticket}))
                messages = []
                for _ in range(3):
                    try:
                        messages.append(json.loads(ws.recv()))
                    except Exception:  # noqa: BLE001
                        break
                return messages
            finally:
                ws.close()

        good = ws_bind(issue_ws_ticket(str(session_id), str(op_id))['ticket'], DIRECT['web'])
        bad_ticket = ws_bind('forged.ticket.0.0.0', DIRECT['web'])
        bad_origin = ws_bind(issue_ws_ticket(str(session_id), str(op_id))['ticket'], 'https://evil.example')
        codes = lambda ms: [m.get('code') for m in ms if isinstance(m, dict) and m.get('type') == 'error']  # noqa: E731
        # Refusal reasons of the worker: a ticket / origin problem, as opposed to "Session not bound" (the ticket
        # was accepted; the session has no portal bind in this test) — the protocol after the ticket is unchanged.
        reasons = lambda ms: ' '.join(str(m.get('message', '')) for m in ms if isinstance(m, dict)).lower()  # noqa: E731
        info('WS through the gateway', {'valid': [m.get('type') for m in good] if isinstance(good, list) else good,
                                        'forged': codes(bad_ticket) if isinstance(bad_ticket, list) else bad_ticket,
                                        'foreign origin': codes(bad_origin) if isinstance(bad_origin, list) else bad_origin})
        check('WebSocket upgrade through the gateway reaches VisionWorker with its own checks: valid ticket accepted, '
              'forged ticket and foreign Origin refused',
              isinstance(good, list) and 'ticket' not in reasons(good) and 'origin' not in reasons(good)
              and isinstance(bad_ticket, list) and 'ticket' in reasons(bad_ticket)
              and (not isinstance(bad_origin, list) or 'origin' in reasons(bad_origin)),
              (good, bad_ticket, bad_origin))

        # ===================== 7. Contracts (JSON vs OpenAPI, XML vs XSD) ======================================
        family_id = q1('SELECT id FROM instrument_family ORDER BY code LIMIT 1')
        kit_id = q1('SELECT id FROM kit WHERE institution_id = :i ORDER BY name LIMIT 1', i=inst)
        instrument_id = q1('SELECT id FROM instrument WHERE institution_id = :i LIMIT 1', i=inst)
        asset_id = q1('SELECT m.id FROM media_asset m JOIN work_session w ON w.id = m.session_id JOIN operation o '
                      'ON o.id = w.operation_id WHERE o.institution_id = :i AND m.purged_at IS NULL LIMIT 1', i=inst)
        evidence_session = q1('SELECT m.session_id FROM media_asset m WHERE m.id = :a', a=asset_id) if asset_id else session_id
        notification_id = q1('SELECT id FROM notification WHERE recipient_user_id = :u ORDER BY created_at DESC LIMIT 1', u=spd_id)
        audit_events = requests.get(DIRECT['audit'] + '/api/v1/audit/events', headers=bearer(spd_token), timeout=15).json()
        event_id = ((audit_events.get('items') or [{}])[0]).get('id') if isinstance(audit_events, dict) else None
        role_id = q1("SELECT id FROM role WHERE institution_id = :i AND code = 'spd_supervisor'", i=inst)
        services = [
            contracts.Service('AuthService', 'BackendAuthService', DIRECT['auth'], admin_token, 'identity.xsd',
                              {'/api/v1/auth/users/{id}': str(spd_id), '/api/v1/auth/roles/{id}': str(role_id)},
                              include_health=True),
            contracts.Service('CatalogService (gateway)', 'CatalogService', GW, spd_token, 'catalog.xsd',
                              {'/api/v1/catalog/families/{id}': str(family_id), '/api/v1/catalog/kits/{id}': str(kit_id),
                               '/api/v1/catalog/instruments/{id}': str(instrument_id),
                               '/api/v1/catalog/kits/{id}/equivalences': str(kit_id)},          # MS-12
                              include_health=True, health_base=DIRECT['catalog']),
            contracts.Service('SessionService (gateway)', 'SessionService', GW, spd_token, 'session-reports.xsd',
                              {f'/api/v1/sessions/{{id}}{suffix}': str(session_id)
                               for suffix in ('', '/expected-inventory', '/counts', '/discrepancies',
                                              '/tracking-events', '/validation-seal')},              # MS-12
                              include_health=True, health_base=DIRECT['session'],
                              unpublished={'/api/v1/sessions/reports/export': 'MS-06 browser download through the '
                                           'portal; SessionService tests + e2e_ms06_reports.py'}),
            contracts.Service('EvidenceService (gateway)', 'EvidenceService', GW, spd_token, 'evidence.xsd',
                              {'/api/v1/sessions/{id}/evidence': str(evidence_session),
                               **({'/api/v1/evidence/{id}': str(asset_id)} if asset_id else {})},
                              include_health=True, health_base=DIRECT['evidence']),
            contracts.Service('NotificationsService (gateway)', 'NotificationsService', GW, spd_token, 'notifications.xsd',
                              {'/api/v1/notifications/{id}': str(notification_id)} if notification_id else {},
                              include_health=True, health_base=DIRECT['notifications']),
            contracts.Service('AuditService (direct, not published)', 'AuditService', DIRECT['audit'], spd_token, 'audit.xsd',
                              {'/api/v1/audit/events/{id}': str(event_id)} if event_id else {}, include_health=True),
            contracts.Service('MonitoringService (direct, not published)', 'MonitoringService', DIRECT['monitoring'],
                              admin_token, 'monitoring.xsd', {'/api/v1/monitoring/services/{name}': 'auth', '/api/v1/monitoring/stations/{id}': str(station_id)},
                              include_health=True),
        ]
        contract_results = contracts.validate(services)
        report = contracts.summary(contract_results)
        (logs / 'ms04_contracts.txt').write_text(report + '\n', encoding='utf-8')
        print(report, flush=True)
        violations = [r for r in contract_results if r.kind == 'violation']
        gaps = [r for r in contract_results if r.kind == 'gap']
        exercised = [r for r in contract_results if 'not exercised' not in r.detail]
        info('contract checks', f'{len(exercised)} exercised: {sum(r.ok for r in exercised)} conform, '
                                f'{len(violations)} violation(s), {len(gaps)} documentation gap(s); '
                                f'{len(contract_results) - len(exercised)} not exercised (no sample id)')
        check('contracts: no response contradicts its OpenAPI / XSD (gaps listed in .e2e/ms04_contracts.txt)',
              not violations, [f'{r.service} {r.check}: {r.detail}' for r in violations][:5])

        # ===================== 8. Production mode: TLS + portal behind the gateway ===============================
        wait(GW_TLS_REDIRECT + '/healthz', 'gateway (TLS)', 60)
        cafile = str(certs / 'fullchain.pem')
        tls = requests.get(GW_TLS_URL + '/healthz', verify=cafile, headers={'Host': API_HOST}, timeout=10)
        api_tls = requests.get(GW_TLS_URL + '/api/v1/catalog/families', verify=cafile,
                               headers={'Host': API_HOST, **bearer(spd_token)}, timeout=15)
        check('TLS listener: API over HTTPS with HSTS', tls.status_code == 200 and api_tls.status_code == 200
              and api_tls.headers.get('Strict-Transport-Security', '').startswith('max-age='),
              (tls.status_code, api_tls.status_code, api_tls.headers.get('Strict-Transport-Security')))
        redirect = requests.get(GW_TLS_REDIRECT + '/api/v1/catalog/families', allow_redirects=False, timeout=10,
                                headers={'Host': API_HOST})
        check('plain HTTP on the production listener only redirects to HTTPS (308)',
              redirect.status_code == 308 and redirect.headers['Location'].startswith('https://'), redirect.status_code)
        context = ssl.create_default_context(cafile=cafile)
        import socket
        with socket.create_connection(('127.0.0.1', 18443), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname=API_HOST) as wrapped:
                negotiated = wrapped.version()
        old = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        old.check_hostname, old.verify_mode = False, ssl.CERT_NONE
        old.maximum_version = ssl.TLSVersion.TLSv1_1
        try:
            old.minimum_version = ssl.TLSVersion.TLSv1
            with socket.create_connection(('127.0.0.1', 18443), timeout=10) as raw:
                with old.wrap_socket(raw, server_hostname=API_HOST):
                    legacy = 'accepted'
        except (ssl.SSLError, OSError, ValueError) as exc:
            legacy = f'refused ({type(exc).__name__})'
        check('only TLS 1.2/1.3 are negotiated', negotiated in ('TLSv1.2', 'TLSv1.3') and legacy.startswith('refused'),
              (negotiated, legacy))

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(args=[f'--host-resolver-rules=MAP {PORTAL_HOST} 127.0.0.1, MAP {API_HOST} 127.0.0.1'])
            context_b = browser.new_context(ignore_https_errors=True)   # throw-away test certificate
            page = context_b.new_page()
            page.goto(f'{PORTAL_ORIGIN}/sign-in')
            page.fill('[name="institutional_email"]', ADMIN[0])
            page.fill('[name="password"]', ADMIN[1])
            page.click('button[type="submit"]')
            page.wait_for_url(lambda url: '/sign-in' not in url, timeout=30000)
            landed = page.url
            cookies = {c['name']: c for c in context_b.cookies()}
            access = cookies.get('access_token', {})
            csrf_cookie = cookies.get('__Host-pef_csrf', {})
            check('portal behind the TLS gateway: sign-in through /auth on the SAME origin; cookies Secure + HttpOnly',
                  landed.startswith(PORTAL_ORIGIN) and access.get('secure') and access.get('httpOnly')
                  and access.get('sameSite') == 'Lax', (landed, access))
            page.goto(f'{PORTAL_ORIGIN}/admin/procedures/new')
            ajax = page.evaluate("""async () => (await fetch('/locale', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify({locale: 'en'})})).status""")
            check('CSRF in the real browser: same-origin AJAX carries the token (200); __Host- cookie Secure + HttpOnly',
                  ajax == 200 and csrf_cookie.get('secure') and csrf_cookie.get('httpOnly'), (ajax, csrf_cookie))
            jar = {name: c['value'] for name, c in cookies.items()}
            forged_post = requests.post(f'{GW_TLS_URL}/locale', json={'locale': 'es-MX'}, cookies=jar, verify=cafile,
                                        headers={'Host': f'{PORTAL_HOST}:18443', 'Origin': 'https://evil.example'}, timeout=15)
            tokenless = requests.post(f'{GW_TLS_URL}/locale', json={'locale': 'es-MX'}, cookies=jar, verify=cafile,
                                      headers={'Host': f'{PORTAL_HOST}:18443'}, timeout=15)
            check('a request with the victim\'s cookies but without the token, or from a foreign origin, is refused (403)',
                  forged_post.status_code == 403 and tokenless.status_code == 403,
                  (forged_post.status_code, tokenless.status_code))
            other_auth = requests.get(f'{GW_TLS_URL}/auth/api/v1/auth/users', verify=cafile,
                                      headers={'Host': f'{PORTAL_HOST}:18443', **bearer(admin_token)}, timeout=15)
            check('on the portal host only the browser AuthService routes are reachable', other_auth.status_code == 404,
                  other_auth.status_code)
            browser.close()

        # ===================== 9. Rate limit (last: it consumes the login budget of this address) ============
        burst = [gw_login('nobody@ms04-test.invalid', 'wrong').status_code for _ in range(30)]
        check('login rate limit per address → 429 JSON from the gateway', 429 in burst, burst)

        # Upstream down → controlled 502 JSON.
        procs['notifications'].terminate()
        procs['notifications'].wait(timeout=30)
        down = requests.get(GW + '/api/v1/notifications', headers=bearer(spd_token), timeout=30)
        check('service down → 502 JSON from the gateway (no internal address in the body)',
              down.status_code == 502 and down.json()['error'] == 'bad_gateway' and 'host.docker' not in down.text,
              (down.status_code, down.text[:200]))
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.terminate()
        for name in (GW_HTTP, GW_TLS):
            logged = subprocess.run(['docker', 'logs', name], capture_output=True, text=True, encoding='utf-8',
                                    errors='replace')
            (logs / f'ms04_{name}.log').write_text(logged.stdout + logged.stderr, encoding='utf-8')
        for name in (GW_HTTP, GW_TLS, VISION_CONTAINER):
            docker('rm', '-f', name, check_rc=False)
        shutil.rmtree(certs, ignore_errors=True)
        clear_login_lockouts()
    print(f'\n{sum(results)}/{len(results)} checks passed')
    sys.exit(0 if results and all(results) else 1)


if __name__ == '__main__':
    main()
