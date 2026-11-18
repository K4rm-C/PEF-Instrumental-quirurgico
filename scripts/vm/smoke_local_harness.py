"""MS-11: runs scripts/vm/smoke_post_deploy.py against the ISOLATED test stack behind a real nginx Gateway, so the
post-deployment smoke test is proven before it is ever used on the VM. Never touches the VM.

Real processes: AuthService, portal, Catalog, Session, Evidence, Audit, Notifications (+ worker), Monitoring
(+ collector), Integration, VisionWorker (container, provisional best.pt) and the Gateway (nginx container with the
templates of infra/gateway, HTTP on 127.0.0.1:18080). The fixture is read from the test database; a test machine
client is created through Administration. Test data is restored afterwards (e2e_restore_stock).

    PYTHONIOENCODING=utf-8 python scripts/vm/smoke_local_harness.py
"""
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

import requests
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_test_env import load_test_env  # noqa: E402
import run_admin_browser_env as envctl  # noqa: E402
import e2e_gateway as gw  # noqa: E402
from e2e_ms02_model_pin import BEST_SHA, CONTAINER, IMAGE, NETWORK, docker, wait  # noqa: E402
from e2e_restore_stock import restore_lost  # noqa: E402

GW_NAME = 'pef-ms11-gateway'
PORTS = gw.PORTS
DIRECT = gw.DIRECT
WEB = DIRECT['web']
SCRATCH = ROOT / '.e2e'
BEST_PT = Path(os.environ.get('PEF_BEST_PT', 'C:/Users/pedro/OneDrive/Documentos/Desarrollo/PEF_Integrado_Admin_Monolito/apps/VisionWorker/weights/best.pt'))


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

    import redis
    for key in redis.Redis.from_url(env['REDIS_URL']).scan_iter('auth:login_fail:*'):
        redis.Redis.from_url(env['REDIS_URL']).delete(key)
    resolved = subprocess.run(['docker', 'run', '--rm', '--network', NETWORK, gw.GW_IMAGE, 'sh', '-c',
                               'getent ahostsv4 host.docker.internal | head -n 1'], capture_output=True, text=True)
    gw.HOST_IPV4 = resolved.stdout.split()[0]
    gateway_origin = f'https://{gw.PORTAL_HOST}:18443'
    # production layout (servers/tls.conf.template, the one render_gateway_vm.py uses): API and portal are two hosts
    import tempfile
    certs = Path(tempfile.mkdtemp(prefix='pef-ms11-certs-'))
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=pef-ms11-test',
                    '-addext', f'subjectAltName=DNS:{gw.API_HOST},DNS:{gw.PORTAL_HOST}', '-keyout', str(certs / 'privkey.pem'),
                    '-out', str(certs / 'fullchain.pem')], check=True, capture_output=True)
    for name in (GW_NAME, CONTAINER):
        docker('rm', '-f', name, check_rc=False)
    docker('run', '-d', '--name', CONTAINER, '--network', NETWORK, '-p', '127.0.0.1:15002:5002',
           '-v', f"{ROOT / 'apps' / 'VisionWorker'}:/app:ro", '-e', f'VISION_WS_SECRET={ws_secret}',
           '-e', f'VISION_WS_ORIGINS={WEB},{gateway_origin}', '-e', 'VISION_MODEL_FETCH_ALLOWED_HOSTS=garage:3900',
           '-e', 'VISION_MODEL_CACHE_DIR=/tmp/model_cache',
           # as recommended for the VM (MS-11 R3): a copy of the active model at VISION_WEIGHTS_PATH, so MonitoringService
           # does not report VisionWorker 'degraded' (sessions still load the pinned weights, verified by SHA-256)
           '-v', f'{BEST_PT}:/models/active.pt:ro', '-e', 'VISION_WEIGHTS_PATH=/models/active.pt',
           '-e', 'VISION_UPLOAD_DIR=/tmp/uploads', '-e', 'TELEMETRY_SPOOL_DIR=/tmp/spool', IMAGE)
    auth_env, web_env = envctl.build_env('s3', '')
    auth_env.update({'JWT_SECRET_KEY': jwt_secret, 'AUTH_TRUSTED_PROXIES': '1', 'AUTH_COOKIE_SECURE': 'true',
                     'AUTH_ALLOWED_ORIGINS': f'{WEB},{gateway_origin}'})
    service_env = {**env, 'JWT_SECRET_KEY': jwt_secret}
    s3 = env['PEF_TEST_S3_ENDPOINT']
    mon = {**service_env, 'MONITORING_HOST': '127.0.0.1', 'MONITORING_PORT': str(PORTS['monitoring']),
           'MONITORING_INTERVAL_SECONDS': '5', 'MONITORING_MONGO_URL': env['PEF_TEST_MONGO_URL'],
           **{f'MONITORING_TARGET_{n.upper()}_URL': DIRECT[n] for n in
              ('web', 'auth', 'catalog', 'session', 'evidence', 'audit', 'notifications', 'vision')}}
    envs = {
        'catalog': ('CatalogService', 'main.py', {**service_env, 'CATALOG_HOST': '127.0.0.1', 'CATALOG_PORT': str(PORTS['catalog'])}),
        'session': ('SessionService', 'main.py', {**service_env, 'SESSION_HOST': '127.0.0.1', 'SESSION_PORT': str(PORTS['session'])}),
        'evidence': ('EvidenceService', 'main.py', {**service_env, 'EVIDENCE_HOST': '127.0.0.1', 'EVIDENCE_PORT': str(PORTS['evidence']),
                     'S3_INTERNAL_ENDPOINT': s3, 'S3_PUBLIC_ENDPOINT': s3, 'S3_REGION': 'garage',
                     'S3_ACCESS_KEY_ID': env['PEF_TEST_S3_ACCESS_KEY'], 'S3_SECRET_ACCESS_KEY': env['PEF_TEST_S3_SECRET_KEY'],
                     'S3_BUCKET_EVIDENCE': 'pef-evidence-test'}),
        'audit': ('AuditService', 'main.py', {**service_env, 'AUDIT_HOST': '127.0.0.1', 'AUDIT_PORT': str(PORTS['audit'])}),
        'notifications': ('NotificationsService', 'main.py', {**service_env, 'NOTIFICATIONS_HOST': '127.0.0.1',
                                                              'NOTIFICATIONS_PORT': str(PORTS['notifications'])}),
        'notif_worker': ('NotificationsService', 'worker.py', {**service_env, 'NOTIFICATIONS_WORKER_POLL_SECONDS': '1'}),
        'monitoring': ('MonitoringService', 'main.py', mon),
        'collector': ('MonitoringService', 'collector.py', mon),
        'integration': ('IntegrationService', 'main.py', {**service_env, 'INTEGRATION_HOST': '127.0.0.1',
                                                          'INTEGRATION_PORT': str(PORTS['integration'])}),
    }
    web_env.update({'JWT_SECRET_KEY': jwt_secret, 'VISION_WORKER_URL': DIRECT['vision'], 'VISION_WS_SECRET': ws_secret,
                    'VISION_WORKER_WS_URL': f'wss://{gw.PORTAL_HOST}:18443/ws', 'AUTH_BROWSER_URL': f'{gateway_origin}/auth',
                    'PORTAL_COOKIE_SECURE': 'true', 'S3_INTERNAL_ENDPOINT': 'http://garage:3900',
                    'ADMIN_S3_BUCKET': 'pef-admin-browser', 'SESSION_SERVICE_URL': DIRECT['session'],
                    'CATALOG_SERVICE_URL': DIRECT['catalog'], 'EVIDENCE_SERVICE_URL': DIRECT['evidence'],
                    'NOTIFICATIONS_SERVICE_URL': DIRECT['notifications'], 'AUDIT_SERVICE_URL': DIRECT['audit'],
                    'MONITORING_SERVICE_URL': DIRECT['monitoring'], 'MONGO_URL': env['PEF_TEST_MONGO_URL'],
                    'MONGO_DB': 'pef_ms11_smoke', 'PEF_OUTBOX_DIR': str(SCRATCH / f'ms11_outbox_{secrets.token_hex(3)}')})

    def start(name, args, cwd, environment):
        return subprocess.Popen(args, cwd=cwd, env=environment, stdout=open(SCRATCH / f'ms11_{name}.log', 'w'),
                                stderr=subprocess.STDOUT)
    procs = {'auth': start('auth', [sys.executable, 'main.py'], ROOT / 'apps' / 'BackendAuthService', auth_env)}
    for name, (folder, script, environment) in envs.items():
        procs[name] = start(name, [sys.executable, script], ROOT / 'apps' / folder, environment)
    procs['web'] = start('web', [sys.executable, '-m', 'flask', '--app', 'app', 'run', '--host', '127.0.0.1', '--port',
                                 str(PORTS['web'])], ROOT / 'apps' / 'BackendWebFlask', web_env)
    original_active = q1('SELECT id FROM yolo_model WHERE active')
    sessions_before = {r[0] for r in q('SELECT id FROM work_session')}
    rc = 1
    try:
        wait(DIRECT['auth'] + '/health', 'AuthService', 240)
        for name in ('catalog', 'session', 'evidence', 'audit', 'notifications', 'monitoring', 'integration'):
            wait(DIRECT[name] + '/health/live', name, 240)
        wait(WEB + '/health/live', 'portal', 240)
        wait(DIRECT['vision'] + '/health', 'VisionWorker', 240)
        docker('run', '-d', '--name', GW_NAME, '--network', NETWORK, '-p', '127.0.0.1:18443:8443', '-p', '127.0.0.1:18081:8080',
               '-v', f'{certs}:/etc/nginx/certs:ro', *gw.gateway_env(tls=True), gw.GW_IMAGE)
        for _ in range(60):
            try:
                if requests.get('https://127.0.0.1:18443/healthz', headers={'Host': gw.API_HOST}, verify=False, timeout=3).ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(1)

        # test-institution preparation, as the deployer would do it in Administration (provisional model, client)
        # Through the TLS Gateway, like a real administrator: with PORTAL_COOKIE_SECURE the CSRF cookie is Secure and is
        # never sent over plain http://127.0.0.1 (the portal then refuses the POST with 403, as it must).
        import socket
        import portal_csrf
        native = socket.getaddrinfo
        socket.getaddrinfo = lambda host, *a, **k: native('127.0.0.1' if host in (gw.PORTAL_HOST, gw.API_HOST) else host, *a, **k)
        portal = f'https://{gw.PORTAL_HOST}:18443'
        portal_csrf.enable(portal)
        import urllib3
        urllib3.disable_warnings()
        admin = requests.Session()
        admin.verify = False   # self-signed certificate of this throw-away gateway
        token = admin.post(f'https://{gw.API_HOST}:18443/api/v1/auth/login', json={'email': gw.ADMIN[0], 'password': gw.ADMIN[1]},
                           timeout=15).json()['access_token']
        admin.cookies.set('access_token', token, domain=gw.PORTAL_HOST, path='/')
        best = q1('SELECT id FROM yolo_model WHERE checksum = :c', c=BEST_SHA)
        activated = admin.post(f'{portal}/admin/vision-models/{best}/activate', allow_redirects=False, timeout=60)
        if q1('SELECT id FROM yolo_model WHERE active') != best:
            raise SystemExit(f'could not activate the provisional model through Administration ({activated.status_code})')
        created = admin.post(f'{portal}/admin/configuration/integration-clients/new', timeout=30, data={
            'name': f'MS-11 smoke {secrets.token_hex(2)}', 'status': 'active',
            'scopes': ['sync.catalog.read', 'sync.locations.read', 'fhir.location.read']})
        secret = re.search(r'data-secret>([^<]+)<', created.text)
        client_id = re.search(r'data-client-id>([^<]+)<', created.text)

        operator_id, inst = q('SELECT id, institution_id FROM "user" WHERE email = :e', e=gw.OPERATOR[0])[0]
        station = q('SELECT s.id, s.room_id FROM capture_station s JOIN operating_room r ON r.id = s.room_id '
                    'WHERE s.active AND r.active AND r.institution_id = :i ORDER BY s.name LIMIT 1', i=inst)[0]
        kit = q1("SELECT id FROM kit WHERE active AND institution_id = :i AND name = 'Video Demo Kit 1'", i=inst)
        fixture = {
            'institution_id': str(inst), 'operator_user_id': str(operator_id), 'room_id': str(station.room_id),
            'station_id': str(station.id), 'kit_id': str(kit),
            'camera_id': str(q1('SELECT id FROM capture_camera WHERE station_id = :s AND active ORDER BY name LIMIT 1', s=station.id) or '') or None,
            'family_id': str(q1('SELECT family_id FROM kit_item WHERE kit_id = :k ORDER BY family_id LIMIT 1', k=kit)),
            'physician_id': str(q1('SELECT id FROM physician WHERE institution_id = :i AND active ORDER BY name LIMIT 1', i=inst)),
            'privacy_notice_version_id': str(q1('SELECT id FROM privacy_notice_version WHERE active AND effective_at <= now() '
                                                'ORDER BY effective_at LIMIT 1')),
            'procedure_type_id': str(q1('SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1')),
            'videos': {'one_removed': str(ROOT / '.e2e' / 'videos' / 'ms031_one_removed.avi'),
                       'full': str(ROOT / '.e2e' / 'videos' / 'ms031_masked.avi')}}
        fixture_path = SCRATCH / 'ms11_smoke_fixture.json'
        fixture_path.write_text(json.dumps(fixture, indent=2), encoding='utf-8')
        smoke_env = {**os.environ, 'PYTHONIOENCODING': 'utf-8',
                     'PEF_SMOKE_ADMIN_EMAIL': gw.ADMIN[0], 'PEF_SMOKE_ADMIN_PASSWORD': gw.ADMIN[1],
                     'PEF_SMOKE_SUPERVISOR_EMAIL': gw.SUPERVISOR[0], 'PEF_SMOKE_SUPERVISOR_PASSWORD': gw.SUPERVISOR[1],
                     'PEF_SMOKE_OPERATOR_EMAIL': gw.OPERATOR[0], 'PEF_SMOKE_OPERATOR_PASSWORD': gw.OPERATOR[1]}
        if secret and client_id:
            smoke_env.update({'PEF_SMOKE_CLIENT_ID': client_id.group(1), 'PEF_SMOKE_CLIENT_SECRET': secret.group(1)})
        else:
            print('INFO  no test machine client could be created: sync/FHIR client checks will be SKIP', flush=True)
        # Garage's admin API (3903) is not published by the test stack: MonitoringService cannot check it here.
        rc = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'vm' / 'smoke_post_deploy.py'),
                             '--base', f'https://{gw.PORTAL_HOST}:18443', '--api', f'https://{gw.API_HOST}:18443', '--insecure',
                             '--http-url', f'http://{gw.PORTAL_HOST}:18081',
                             '--resolve', f'{gw.PORTAL_HOST}:127.0.0.1', '--resolve', f'{gw.API_HOST}:127.0.0.1', '--fixture', str(fixture_path), '--confirm-test-institution',
                             '--not-monitored', 'garage', '--out', str(SCRATCH / 'ms11_smoke_result.json')],
                            env=smoke_env).returncode
    finally:
        new_sessions = [str(r[0]) for r in q('SELECT id FROM work_session') if r[0] not in sessions_before]
        restore_lost(engine, new_sessions)
        for proc in procs.values():
            if proc.poll() is None:
                proc.terminate()
        for name in (GW_NAME, CONTAINER):
            docker('rm', '-f', name, check_rc=False)
        import shutil
        shutil.rmtree(certs, ignore_errors=True)
        if original_active:
            with engine.begin() as c:
                c.execute(text('UPDATE yolo_model SET active = FALSE WHERE active'))
                c.execute(text('UPDATE yolo_model SET active = TRUE WHERE id = :i'), {'i': original_active})
    sys.exit(rc)


if __name__ == '__main__':
    main()
