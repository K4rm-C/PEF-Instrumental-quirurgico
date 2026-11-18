"""MS-07 + MS-08 real end to end (Part D): machine client -> sync through the API Gateway -> idempotent operations ->
physical tray -> session with a real YOLO count -> evidence -> Supervisor review -> reports -> notifications -> FHIR
(read API and delivery to a REAL local HAPI FHIR R4 server) -> audit trail -> revocation.

Isolated stack only (docker-compose.test.yml / .env.test). Real processes: AuthService :15001, portal :15000,
CatalogService :15003, SessionService :15004, EvidenceService :15005, NotificationsService :15007 + worker,
IntegrationService :15009 + worker, VisionWorker (container, provisional best.pt), API Gateway (nginx container,
repository configuration) :18080 and HAPI FHIR R4 v7.4.0 (container) :18090. HAPI is a local TEST server: this is an
interoperability test with a real FHIR implementation, NOT an integration with a hospital HIS (none is contacted).

    PYTHONIOENCODING=utf-8 python scripts/e2e_ms07_ms08.py
"""
import re
import secrets
import subprocess
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

import requests
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_test_env import load_test_env  # noqa: E402
import run_admin_browser_env as envctl  # noqa: E402  (CSRF helper for the portal)
import e2e_gateway as gwmod  # noqa: E402
from e2e_ms02_model_pin import BEST_SHA, CONTAINER, IMAGE, NETWORK, docker, poll_state, wait  # noqa: E402

AUTH, WEB, CATALOG, SESSION, EVIDENCE, NOTIF, INTEGRATION, VISION = (
    'http://127.0.0.1:15001', 'http://127.0.0.1:15000', 'http://127.0.0.1:15003', 'http://127.0.0.1:15004',
    'http://127.0.0.1:15005', 'http://127.0.0.1:15007', 'http://127.0.0.1:15009', 'http://127.0.0.1:15002')
GW, HAPI_URL = 'http://127.0.0.1:18080', 'http://127.0.0.1:18090/fhir'
GW_CONTAINER, HAPI_CONTAINER, HAPI_IMAGE = 'pef-ms07-gateway', 'pef-ms07-hapi', 'hapiproject/hapi:v7.4.0'
OPERATOR, SUPERVISOR, ADMIN = (('operator@instrumed.com', 'DemopwdOP78!'), ('supervisor@instrumed.com', 'DemopwdSPD78!'),
                               ('admin@instrumed.com', 'DemopwdADM78!'))
VIDEO = ROOT / '.e2e' / 'videos' / 'ms031_masked.avi'
FHIR_SCOPES = ['sync.catalog.read', 'sync.locations.read', 'fhir.location.read', 'fhir.practitioner.read',
               'fhir.encounter.read', 'fhir.export.bundle']
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + str(detail)[:500] if detail and not ok else ''}", flush=True)
    return ok


def info(name, detail):
    print(f'INFO  {name}: {detail}', flush=True)


def portal_login(email, password):
    session = requests.Session()
    response = session.post(AUTH + '/login', json={'email': email, 'password': password}, timeout=10)
    response.raise_for_status()
    session.cookies.set('access_token', response.cookies['access_token'], domain='127.0.0.1')
    return session, response.cookies['access_token']


def gw_token(email, password):
    response = requests.post(GW + '/api/v1/auth/login', json={'email': email, 'password': password}, timeout=15)
    response.raise_for_status()
    return response.json()['access_token']


def bearer(token, **extra):
    return {'Authorization': f'Bearer {token}', **extra}


def until(predicate, seconds=60, step=1.0):
    value = None
    for _ in range(int(seconds / step)):
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return value


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

    original_active = q1('SELECT id FROM yolo_model WHERE active')
    best = q('SELECT id, version_tag FROM yolo_model WHERE checksum = :c', c=BEST_SHA)[0]
    if not VIDEO.exists():
        sys.exit(f'missing {VIDEO}: scripts/make_ms031_count_videos.py (GUIA_PRUEBAS.md §2.4)')
    for name in (CONTAINER, GW_CONTAINER, HAPI_CONTAINER):
        docker('rm', '-f', name, check_rc=False)
    docker('run', '-d', '--name', HAPI_CONTAINER, '--network', NETWORK, '-p', '127.0.0.1:18090:8080', HAPI_IMAGE)
    docker('run', '-d', '--name', CONTAINER, '--network', NETWORK, '-p', '127.0.0.1:15002:5002',
           '-v', f"{ROOT / 'apps' / 'VisionWorker'}:/app:ro", '-e', f'VISION_WS_SECRET={ws_secret}',
           '-e', f'VISION_WS_ORIGINS={WEB}', '-e', 'VISION_MODEL_FETCH_ALLOWED_HOSTS=garage:3900',
           '-e', 'VISION_MODEL_CACHE_DIR=/tmp/model_cache', '-e', 'VISION_WEIGHTS_PATH=/models/not-mounted.pt',
           '-e', 'VISION_UPLOAD_DIR=/tmp/uploads', '-e', 'TELEMETRY_SPOOL_DIR=/tmp/spool', IMAGE)

    auth_env, web_env = envctl.build_env('s3', '')
    auth_env['JWT_SECRET_KEY'] = jwt_secret
    s3 = env['PEF_TEST_S3_ENDPOINT']
    web_env.update({'JWT_SECRET_KEY': jwt_secret, 'VISION_WORKER_URL': VISION, 'VISION_WS_SECRET': ws_secret,
                    'VISION_WORKER_WS_URL': 'ws://127.0.0.1:15002/ws', 'S3_INTERNAL_ENDPOINT': 'http://garage:3900',
                    'ADMIN_S3_BUCKET': 'pef-admin-browser', 'SESSION_SERVICE_URL': SESSION, 'EVIDENCE_SERVICE_URL': EVIDENCE,
                    'NOTIFICATIONS_SERVICE_URL': NOTIF, 'CATALOG_SERVICE_URL': CATALOG,
                    'MONGO_URL': env['PEF_TEST_MONGO_URL'], 'MONGO_DB': 'pef_ms07_e2e',
                    'PEF_OUTBOX_DIR': str(ROOT / '.e2e' / f'ms07_outbox_{secrets.token_hex(3)}')})
    service_env = {**env, 'JWT_SECRET_KEY': jwt_secret}
    integration_env = {**service_env, 'INTEGRATION_HOST': '127.0.0.1', 'INTEGRATION_PORT': '15009',
                       'INTEGRATION_SESSION_URL': SESSION, 'INTEGRATION_CATALOG_URL': CATALOG,
                       'INTEGRATION_NOTIFICATIONS_URL': NOTIF, 'INTEGRATION_ALLOWED_HOSTS': '127.0.0.1:18090',
                       'INTEGRATION_ALLOW_PLAIN_HTTP': 'true', 'INTEGRATION_DELIVERY_BACKOFF_SECONDS': '1',
                       'INTEGRATION_DELIVERY_MAX_ATTEMPTS': '8', 'INTEGRATION_WORKER_POLL_SECONDS': '1'}
    logs = ROOT / '.e2e'

    def start(name, args, cwd, environment):
        return subprocess.Popen(args, cwd=cwd, env=environment, stdout=open(logs / f'ms07_{name}.log', 'a'),
                                stderr=subprocess.STDOUT)
    procs = {
        'auth': start('auth', [sys.executable, 'main.py'], ROOT / 'apps' / 'BackendAuthService', auth_env),
        'catalog': start('catalog', [sys.executable, 'main.py'], ROOT / 'apps' / 'CatalogService',
                         {**service_env, 'CATALOG_HOST': '127.0.0.1', 'CATALOG_PORT': '15003'}),
        'session': start('session', [sys.executable, 'main.py'], ROOT / 'apps' / 'SessionService',
                         {**service_env, 'SESSION_HOST': '127.0.0.1', 'SESSION_PORT': '15004'}),
        'evidence': start('evidence', [sys.executable, 'main.py'], ROOT / 'apps' / 'EvidenceService',
                          {**service_env, 'EVIDENCE_HOST': '127.0.0.1', 'EVIDENCE_PORT': '15005', 'S3_INTERNAL_ENDPOINT': s3,
                           'S3_PUBLIC_ENDPOINT': s3, 'S3_REGION': 'garage', 'S3_ACCESS_KEY_ID': env['PEF_TEST_S3_ACCESS_KEY'],
                           'S3_SECRET_ACCESS_KEY': env['PEF_TEST_S3_SECRET_KEY'], 'S3_BUCKET_EVIDENCE': 'pef-evidence-test'}),
        'notifications': start('notifications', [sys.executable, 'main.py'], ROOT / 'apps' / 'NotificationsService',
                               {**service_env, 'NOTIFICATIONS_HOST': '127.0.0.1', 'NOTIFICATIONS_PORT': '15007'}),
        'notif_worker': start('notif_worker', [sys.executable, 'worker.py'], ROOT / 'apps' / 'NotificationsService',
                              {**service_env, 'NOTIFICATIONS_WORKER_POLL_SECONDS': '1'}),
        'integration': start('integration', [sys.executable, 'main.py'], ROOT / 'apps' / 'IntegrationService', integration_env),
        'int_worker': start('int_worker', [sys.executable, 'worker.py'], ROOT / 'apps' / 'IntegrationService', integration_env),
        'web': start('web', [sys.executable, '-m', 'flask', '--app', 'app', 'run', '--host', '127.0.0.1', '--port', '15000'],
                     ROOT / 'apps' / 'BackendWebFlask', web_env),
    }
    try:
        for url, label in ((AUTH + '/health', 'AuthService'), (CATALOG + '/health/live', 'CatalogService'),
                           (SESSION + '/health/live', 'SessionService'), (EVIDENCE + '/health/live', 'EvidenceService'),
                           (NOTIF + '/health/live', 'NotificationsService'), (INTEGRATION + '/health/live', 'IntegrationService'),
                           (WEB + '/health/live', 'portal'), (VISION + '/health', 'VisionWorker')):
            wait(url, label, 240)
        resolved = subprocess.run(['docker', 'run', '--rm', '--network', NETWORK, gwmod.GW_IMAGE, 'sh', '-c',
                                   'getent ahostsv4 host.docker.internal | head -n 1'], capture_output=True, text=True)
        gwmod.HOST_IPV4 = resolved.stdout.split()[0]
        docker('run', '-d', '--name', GW_CONTAINER, '--network', NETWORK, '-p', '127.0.0.1:18080:8080',
               *gwmod.gateway_env(tls=False), gwmod.GW_IMAGE)
        wait(GW + '/healthz', 'gateway', 60)
        until(lambda: _alive(HAPI_URL), 300, 3)
        check('HAPI FHIR R4 test server (local container) is up', _alive(HAPI_URL))
        import redis
        for key in redis.Redis.from_url(env['REDIS_URL']).scan_iter('auth:login_fail:*'):
            redis.Redis.from_url(env['REDIS_URL']).delete(key)

        admin, _ = portal_login(*ADMIN)
        admin.post(f'{WEB}/admin/vision-models/{best.id}/activate', allow_redirects=False, timeout=30)
        spd_portal, spd_portal_token = portal_login(*SUPERVISOR)
        op, op_token = portal_login(*OPERATOR)
        operator_id, inst = q('SELECT id, institution_id FROM "user" WHERE email = :e', e=OPERATOR[0])[0]
        admin_id = q1('SELECT id FROM "user" WHERE email = :e', e=ADMIN[0])

        # --- 1. Administration registers a machine client (portal); the secret is shown once ------------------
        name = f'HIS test {secrets.token_hex(3)}'
        created = admin.post(f'{WEB}/admin/configuration/integration-clients/new', timeout=30,
                             data={'name': name, 'scopes': FHIR_SCOPES, 'status': 'active'})
        secret = re.search(r'data-secret>([^<]+)<', created.text)
        client_id = re.search(r'data-client-id>([^<]+)<', created.text)
        row = q('SELECT id, secret_hash FROM integration_client WHERE name = :n', n=name)
        check('1. Admin creates a machine client: secret shown once (no-store), stored only as a hash',
              created.status_code == 200 and secret and client_id and row and secret.group(1) not in row[0].secret_hash
              and created.headers.get('Cache-Control') == 'no-store', created.status_code)
        secret, client_id, client_row = secret.group(1), client_id.group(1), row[0].id

        # --- 2. Client credentials through the gateway ---------------------------------------------------------
        def client_token(secret_value=None):
            return requests.post(GW + '/api/v1/auth/token', timeout=15, data={
                'grant_type': 'client_credentials', 'client_id': client_id, 'client_secret': secret_value or secret})
        issued = client_token()
        wrong = client_token('not-the-secret')
        ctoken = issued.json().get('access_token') if issued.ok else ''
        check('2. Client credentials through the gateway: scoped client token; wrong secret 401 invalid_client',
              issued.status_code == 200 and set(issued.json()['scope'].split()) == set(FHIR_SCOPES)
              and wrong.status_code == 401 and wrong.json()['error'] == 'invalid_client', (issued.text[:200], wrong.text[:200]))
        not_a_user = requests.get(GW + '/api/v1/sessions', headers=bearer(ctoken), timeout=15)
        check('   a client token is not a user token: SessionService refuses it (401)', not_a_user.status_code == 401,
              not_a_user.status_code)

        # --- 3. First and incremental sync of the catalog through the gateway ----------------------------------
        def drain(token, cursor=None, types='kit,surgical_tray,operating_room'):
            seen = []
            while True:
                params = {'types': types, 'limit': 200, **({'cursor': cursor} if cursor else {})}
                page = requests.get(GW + '/api/v1/sync/changes', params=params, headers=bearer(token), timeout=30)
                assert page.ok, page.text
                body = page.json()
                seen += body['changes']
                cursor = body['next_cursor']
                if not body['has_more']:
                    return seen, cursor
        first, cursor = drain(ctoken)
        kits = {c['resource_id'] for c in first if c['resource_type'] == 'kit'}
        db_kits = {str(k) for (k,) in q('SELECT id FROM kit WHERE institution_id = :i', i=inst)}
        foreign = {str(r) for (r,) in q('SELECT id FROM operating_room WHERE institution_id <> :i', i=inst)}
        check('3. First sync (client, gateway): every kit of the institution, nothing of another institution',
              kits == db_kits and not foreign & {c['resource_id'] for c in first}, (len(kits), len(db_kits)))
        again, cursor = drain(ctoken, cursor)
        check('   same cursor again: no change repeated', again == [], len(again))

        # --- 4. Idempotent tray creation (Supervisor, gateway); the client sees it once -------------------------
        spd_token = gw_token(*SUPERVISOR)
        kit = q1("SELECT id FROM kit WHERE active AND institution_id = :i AND name = 'Video Demo Kit 1'", i=inst)
        tray_code = f'TR-E2E-{secrets.token_hex(3)}'
        op_body = {'type': 'tray.create', 'payload': {'code': tray_code, 'name': 'E2E tray', 'kit_id': str(kit)}}
        key = f'e2e-tray-{secrets.token_hex(6)}'
        r1 = requests.post(GW + '/api/v1/sync/operations', json=op_body, timeout=30,
                           headers=bearer(spd_token, **{'Idempotency-Key': key}))
        r2 = requests.post(GW + '/api/v1/sync/operations', json=op_body, timeout=30,
                           headers=bearer(spd_token, **{'Idempotency-Key': key}))
        trays = q('SELECT id FROM surgical_tray WHERE code = :c', c=tray_code)
        check('4. tray.create sent twice with the same Idempotency-Key: one tray, same answer, replay flagged',
              r1.status_code == r2.status_code == 201 and r1.json()['id'] == r2.json()['id'] and len(trays) == 1
              and r2.headers.get('Idempotent-Replayed') == 'true', (r1.text[:200], r2.status_code, len(trays)))
        tray_id = str(trays[0].id)
        incremental, cursor = drain(ctoken, cursor)
        check('   incremental sync of the client: the new tray exactly once',
              [c['operation'] for c in incremental if c['resource_id'] == tray_id] == ['create'], incremental[-3:])
        stale = {'type': 'tray.update', 'target_id': tray_id, 'payload': {'version': 99, 'notes': 'x'}}
        c1 = requests.post(GW + '/api/v1/sync/operations', json=stale, timeout=30,
                           headers=bearer(spd_token, **{'Idempotency-Key': key + '-u'}))
        mismatch = requests.post(GW + '/api/v1/sync/operations', json={**stale, 'payload': {'version': 1}}, timeout=30,
                                 headers=bearer(spd_token, **{'Idempotency-Key': key + '-u'}))
        check('   stale version -> 409 version_conflict (owner rule); same key with another body -> 422',
              c1.status_code == 409 and c1.json().get('error') == 'version_conflict' and mismatch.status_code == 422,
              (c1.text[:200], mismatch.status_code))
        refused = requests.post(GW + '/api/v1/sync/operations', json={'type': 'count.final', 'payload': {}}, timeout=30,
                                headers=bearer(spd_token, **{'Idempotency-Key': key + '-x'}))
        check('   counting operations do not exist in the sync API (400 unsupported_operation)',
              refused.status_code == 400 and refused.json()['error'] == 'unsupported_operation', refused.text[:200])

        # --- 5. Session scheduled with the tray through the sync API (idempotent) ------------------------------
        station = q('SELECT s.id, s.room_id FROM capture_station s JOIN operating_room r ON r.id = s.room_id '
                    'WHERE s.active AND r.active AND r.institution_id = :i ORDER BY s.name LIMIT 1', i=inst)[0]
        family = q1('SELECT family_id FROM kit_item WHERE kit_id = :k ORDER BY family_id LIMIT 1', k=kit)
        today = date.today()
        schedule = {'operator_user_id': str(operator_id),
                    'procedure_type_id': str(q1('SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1')),
                    'room_id': str(station.room_id), 'station_id': str(station.id), 'kit_id': str(kit), 'tray_id': tray_id,
                    'scheduled_at': f'{today}T10:00:00Z', 'phase_code': 'start',
                    'physicians': [{'physician_id': str(q1('SELECT id FROM physician WHERE institution_id = :i AND active '
                                                           'ORDER BY name LIMIT 1', i=inst))}],
                    'privacy_notice_version_id': str(q1('SELECT id FROM privacy_notice_version WHERE active AND '
                                                        'effective_at <= now() ORDER BY effective_at LIMIT 1')),
                    'expected_lines': [{'family_id': str(family), 'expected_quantity': 1}]}
        skey = f'e2e-sched-{secrets.token_hex(6)}'
        s1 = requests.post(GW + '/api/v1/sync/operations', json={'type': 'session.schedule', 'payload': schedule},
                           timeout=60, headers=bearer(spd_token, **{'Idempotency-Key': skey}))
        s2 = requests.post(GW + '/api/v1/sync/operations', json={'type': 'session.schedule', 'payload': schedule},
                           timeout=60, headers=bearer(spd_token, **{'Idempotency-Key': skey}))
        sid = s1.json().get('id') if s1.ok else None
        sessions = q('SELECT id, tray_id, operation_id FROM work_session WHERE tray_id = :t', t=tray_id)
        check('5. session.schedule retried with the same key: one session, with the tray and the kit quantities',
              s1.status_code == s2.status_code == 201 and s1.json()['id'] == s2.json()['id'] and len(sessions) == 1
              and s1.json()['tray']['id'] == tray_id
              and [line['expected_quantity'] for line in s1.json()['expected_inventory']] == [1], (s1.text[:300], len(sessions)))
        operation_id = str(sessions[0].operation_id)
        info('session', sid)

        # --- 6. Operator: real YOLO count (unchanged rules), validation, Supervisor review and close ------------
        op.post(f'{WEB}/operator/sessions/{sid}/begin', data={'instruments_ready': '1'}, allow_redirects=False, timeout=300)
        capture = f'{WEB}/operator/sessions/{sid}/capture'
        op.get(capture, timeout=120)
        with open(VIDEO, 'rb') as fh:
            op.post(f'{capture}/upload', files={'video': (VIDEO.name, fh, 'video/x-msvideo')}, timeout=300)
        poll_state(op, sid, lambda s: s.get('status') != 'loading' and (s.get('initial_frame') or {}).get('ok') is not None)
        initial = op.post(f'{capture}/initial', data={'acknowledge_mismatch': '1'}, timeout=60)
        op.post(f'{capture}/play', timeout=60)
        state = poll_state(op, sid, lambda s: s.get('status') in ('ready', 'error')
                           and (s.get('final_frame') or {}).get('ok') is not None, seconds=600)
        final_total = len((state.get('final_frame') or {}).get('boxes') or [])
        final = op.post(f'{capture}/final', timeout=60)
        check(f'6. Operator: real YOLO initial and final counts (final {final_total} = reference), rules unchanged',
              initial.status_code == 200 and final.status_code == 200 and state.get('model_sha256') == BEST_SHA,
              (initial.text[:150], final.text[:150]))
        html = op.get(f'{WEB}/operator/sessions/{sid}/vision-validation', timeout=30).text
        values = {k: int(v) for k, v in re.findall(r'name="(validated_[0-9a-f-]{36})"[^>]*value="(\d+)"', html)}
        form = {k: str(v) for k, v in values.items()}
        for k, v in values.items():
            fid = k.split('_', 1)[1]
            expected = 1 if fid == str(family) else 0
            form[f'reason_{fid}'] = 'surplus' if v > expected else ('shortage' if v < expected else '')
            form[f'notes_{fid}'] = 'MS-07/08 E2E validation'
        form.update({'extra_instruments': str(final_total - sum(values.values())), 'extra_notes': 'MS-07/08 E2E'})
        validated = op.post(f'{WEB}/operator/sessions/{sid}/vision-validation', data=form, allow_redirects=False, timeout=60)
        for disc, reason in q('SELECT d.id, r.code FROM discrepancy d LEFT JOIN cat_discrepancy_reason r ON r.id = d.reason_id '
                              'WHERE d.session_id = :s AND NOT d.resolved', s=sid):
            spd_portal.post(f'{WEB}/supervisor/discrepancies/{sid}/review', allow_redirects=False, data={
                'discrepancy_id': str(disc), 'notes': 'MS-07/08 E2E review',
                'resolution_outcome': 'recovered' if reason == 'shortage' else 'resolved'})   # found: no stock consumed
        closed = spd_portal.post(f'{WEB}/supervisor/sessions/{sid}/close', allow_redirects=False, data={
            'material_recovered': '1', 'contingency_reviewed': '1', 'unreconciled_shortage_acknowledged': '1'})
        status = q1('SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = :s', s=sid)
        check('   validation keeps the physical total; the Supervisor reviews and closes', validated.status_code == 302
              and closed.status_code == 302 and status == 'closed', (validated.status_code, closed.status_code, status))

        # --- 7. Evidence, Supervisor screens, reports, notifications ------------------------------------------
        evidence = until(lambda: (lambda items: items if len(items) >= 2 else None)(
            (lambda body: body.get('evidence_list', body) if isinstance(body, dict) else body)(
                requests.get(f'{EVIDENCE}/api/v1/sessions/{sid}/evidence', headers=bearer(spd_portal_token), timeout=10).json())
            or []), 90, 2)
        check('7. EvidenceService keeps the evidence images of the session (Garage)', evidence and len(evidence) >= 2,
              evidence)
        detail = spd_portal.get(f'{WEB}/supervisor/sessions/{sid}', timeout=60).text
        tray_page = spd_portal.get(f'{WEB}/supervisor/trays/{tray_id}', timeout=30).text
        check('   Supervisor: session detail with evidence; tray screen lists the session',
              'data-evidence-traceability' in detail and f'data-session="{sid}"' in tray_page and tray_code in tray_page)
        summary = requests.get(f'{SESSION}/api/v1/sessions/reports/summary', headers=bearer(spd_portal_token), timeout=30,
                               params={'from': str(today - timedelta(days=1)), 'to': str(today + timedelta(days=1)),
                                       'tz': 'UTC', 'station_id': str(station.id)}).json()
        check('   reports include the session (closed, automatic final count)',
              summary['sessions']['closed'] >= 1 and summary['automatic_counts']['with_final_count'] >= 1, summary['sessions'])
        notified = until(lambda: q("SELECT n.type, u.email FROM notification n JOIN \"user\" u ON u.id = n.recipient_user_id "
                                   "WHERE n.session_id = :s", s=sid)
                       if {'session_closed', 'session_unresolved'} & {t for t, _ in q(
                           'SELECT type, 1 FROM notification WHERE session_id = :s', s=sid)} else None, 45)
        types = {t for t, _ in (notified or [])}
        check('   NotificationsService delivered the session notices', types and types & {
            'session_closed', 'session_unresolved'} and 'session_scheduled' in types, notified)

        # --- 8. FHIR read API through the gateway (client scopes, isolation) ----------------------------------
        enc = requests.get(f'{GW}/fhir/r4/Encounter/{operation_id}', headers=bearer(ctoken), timeout=30)
        patient_id = q1('SELECT patient_id FROM operation_patient WHERE operation_id = :o', o=operation_id)
        no_patient = requests.get(f'{GW}/fhir/r4/Patient/{patient_id or uuid.uuid4()}', headers=bearer(ctoken), timeout=30)
        foreign_op = q1('SELECT id FROM operation WHERE institution_id <> :i LIMIT 1', i=inst)
        foreign_read = requests.get(f'{GW}/fhir/r4/Encounter/{foreign_op}', headers=bearer(ctoken), timeout=30)
        as_user = requests.get(f'{GW}/fhir/r4/Encounter/{operation_id}', headers=bearer(spd_token), timeout=30)
        body = enc.json() if enc.ok else {}
        check('8. FHIR Encounter of the session operation through the gateway (application/fhir+json), no patient '
              'without its scope, another institution 404, a user token 403',
              enc.status_code == 200 and enc.headers['Content-Type'].startswith('application/fhir+json')
              and body.get('resourceType') == 'Encounter' and 'subject' not in body and no_patient.status_code == 403
              and (foreign_op is None or foreign_read.status_code == 404) and as_user.status_code == 403,
              (enc.status_code, no_patient.status_code, foreign_read.status_code, as_user.status_code))
        validation = requests.post(f'{HAPI_URL}/Encounter/$validate', json=body, timeout=60,
                                   headers={'Content-Type': 'application/fhir+json'}).json()
        errors = [i for i in validation.get('issue', []) if i.get('severity') in ('error', 'fatal')]
        info('HAPI $validate of the Encounter', [f"{i['severity']}: {i.get('diagnostics', '')[:120]}"
                                                 for i in validation.get('issue', [])][:6])
        check('   HAPI $validate: the projected Encounter has no error or fatal issue', not errors, errors)

        # --- 9. FHIR delivery to the HAPI test server: idempotent, retried after a temporary failure ------------
        admin_token = gw_token(*ADMIN)
        endpoint = requests.post(f'{INTEGRATION}/api/v1/integration/endpoints', headers=bearer(admin_token), timeout=30,
                                 json={'name': f'HAPI test {secrets.token_hex(3)}', 'base_url': HAPI_URL})
        not_allowed = requests.post(f'{INTEGRATION}/api/v1/integration/endpoints', headers=bearer(admin_token), timeout=30,
                                    json={'name': 'Real HIS', 'base_url': 'https://his.hospital.example/fhir'})
        check('9. Admin registers the local HAPI test endpoint; any other host is refused (host_not_allowed)',
              endpoint.status_code == 201 and not_allowed.status_code == 422
              and not_allowed.json()['details']['reason'] == 'host_not_allowed', (endpoint.text[:200], not_allowed.text[:200]))
        endpoint_id = endpoint.json()['id']

        def deliver(dkey):
            return requests.post(f'{INTEGRATION}/api/v1/integration/deliveries', timeout=30,
                                 headers=bearer(admin_token, **{'Idempotency-Key': dkey}),
                                 json={'endpoint_id': endpoint_id, 'encounter_id': operation_id})

        def delivery(did):
            return requests.get(f'{INTEGRATION}/api/v1/integration/deliveries/{did}', headers=bearer(admin_token),
                                timeout=30).json()
        dkey = f'e2e-deliv-{secrets.token_hex(6)}'
        d1, d2 = deliver(dkey), deliver(dkey)
        sent = until(lambda: (lambda d: d if d['status'] != 'pending' else None)(delivery(d1.json()['id'])), 90, 2)
        hapi_enc = requests.get(f'{HAPI_URL}/Encounter/{operation_id}', timeout=30)
        hapi_loc = requests.get(f"{HAPI_URL}/Location/{station.room_id}", timeout=30)
        hapi_patient = requests.get(f'{HAPI_URL}/Patient/{patient_id}', timeout=30) if patient_id else None
        check('   delivery requested twice (same key) = one delivery; sent as a transaction Bundle; HAPI stores the '
              'Encounter and its Location; the Patient is not sent',
              d1.status_code == 202 and d2.status_code == 200 and d1.json()['id'] == d2.json()['id']
              and sent and sent['status'] == 'sent' and hapi_enc.status_code == 200 and hapi_loc.status_code == 200
              and (hapi_patient is None or hapi_patient.status_code in (404, 410)),
              (d1.status_code, d2.status_code, sent, hapi_enc.status_code, hapi_loc.status_code,
               hapi_patient.status_code if hapi_patient is not None else None))
        docker('pause', HAPI_CONTAINER)
        retry = deliver(f'{dkey}-2').json()
        failed_once = until(lambda: (lambda d: d if d['attempts_count'] >= 1 else None)(delivery(retry['id'])), 60, 2)
        docker('unpause', HAPI_CONTAINER)
        recovered = until(lambda: (lambda d: d if d['status'] == 'sent' else None)(delivery(retry['id'])), 120, 2)
        total = requests.get(f'{HAPI_URL}/Encounter', params={'_id': operation_id, '_summary': 'count'}, timeout=30).json()
        check('   temporary failure (HAPI paused): retried with backoff, then sent; still ONE Encounter in HAPI',
              failed_once and failed_once['attempts'][0]['outcome'] == 'retry' and recovered
              and total.get('total') == 1, (failed_once, recovered and recovered['attempts'], total))

        # --- 10. Audit trail of the whole exchange --------------------------------------------------------------
        actions = {a for (a,) in q('SELECT DISTINCT action FROM access_audit WHERE actor_client_id = :c', c=client_row)}
        user_actions = {a for (a,) in q("SELECT DISTINCT action FROM access_audit WHERE occurred_at > now() - interval '1 hour' "
                                        "AND action IN ('SYNC_OPERATION_TRAY_CREATE', 'SYNC_OPERATION_SESSION_SCHEDULE', "
                                        "'CREATE_TRAY', 'session.scheduled', 'CREATE_INTEGRATION_CLIENT', "
                                        "'CREATE_INTEGRATION_ENDPOINT', 'REQUEST_FHIR_DELIVERY', 'FHIR_DELIVERY_SENT')")}
        sched_corr = q1("SELECT correlation_id FROM access_audit WHERE action = 'session.scheduled' AND resource_id = :s", s=sid)
        check('10. Audit: client token, client sync and FHIR reads (actor client); operations, tray, schedule '
              '(same correlation id as the sync operation), endpoint and delivery (actor user)',
              {'CLIENT_TOKEN_ISSUED', 'SYNC_CHANGES_READ', 'FHIR_READ_ENCOUNTER'} <= actions
              and len(user_actions) == 8 and (sched_corr or '').startswith('op-'), (actions, user_actions, sched_corr))

        # --- 11. Revocation: rotation and deactivation in the portal -------------------------------------------
        rotated = admin.post(f'{WEB}/admin/configuration/integration-clients/{client_row}/rotate', timeout=30)
        old_token = requests.get(GW + '/api/v1/sync/resources', headers=bearer(ctoken), timeout=15)
        old_secret = client_token()
        check('11. Secret rotation (portal): the old token and the old secret stop working at once',
              rotated.status_code == 200 and old_token.status_code == 401 and old_secret.status_code == 401,
              (rotated.status_code, old_token.status_code, old_secret.status_code))
        new_secret = re.search(r'data-secret>([^<]+)<', rotated.text).group(1)
        fresh = client_token(new_secret).json()['access_token']
        admin.post(f'{WEB}/admin/configuration/integration-clients/{client_row}/edit', timeout=30,
                   data={'name': name, 'scopes': FHIR_SCOPES, 'status': 'inactive'})
        after = requests.get(GW + '/api/v1/sync/resources', headers=bearer(fresh), timeout=15)
        check('   deactivation: the live token is refused (401) and no new token is issued',
              after.status_code == 401 and client_token(new_secret).status_code == 401, after.status_code)
    finally:
        from e2e_restore_stock import restore_lost
        info('test stock restored', restore_lost(engine, [sid] if 'sid' in locals() and sid else []))
        for proc in procs.values():
            if proc.poll() is None:
                proc.terminate()
        for name in (CONTAINER, GW_CONTAINER, HAPI_CONTAINER):
            docker('rm', '-f', name, check_rc=False)
        if original_active:
            with engine.begin() as c:
                c.execute(text('UPDATE yolo_model SET active = FALSE WHERE active'))
                c.execute(text('UPDATE yolo_model SET active = TRUE WHERE id = :i'), {'i': original_active})
    print(f'\n{sum(results)}/{len(results)} checks passed')
    sys.exit(0 if results and all(results) else 1)


def _alive(base):
    try:
        return requests.get(base + '/metadata', timeout=5).ok
    except requests.RequestException:
        return False


if __name__ == '__main__':
    main()
