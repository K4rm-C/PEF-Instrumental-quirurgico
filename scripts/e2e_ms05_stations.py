"""MS-05 real end-to-end: cameras, station heartbeat, server-side camera evidence and the Supervisor's view.

Isolated stack only (docker-compose.test.yml / .env.test). Real processes: AuthService :15001, portal :15000
(MONITORING_SERVICE_URL), SessionService :15004, MonitoringService API :15008 and its collector (5 s cycle,
station offline after 8 s), VisionWorker in a container with the provisional best.pt published by Administration.

Flow: Administration registers a camera on a station (portal form) → the Supervisor schedules a vision session
there → the Operator starts it, chooses the camera (audited) and the capture page's heartbeat reaches
MonitoringService through the portal → the station is online but the camera only "unverified" → real camera
frames reach VisionWorker over the live WebSocket (Chromium) → the collector reads them and the camera becomes
"ok" (verified by the server) → camera error and recovery → the station stops reporting (offline +
heartbeat_lost alert) and recovers → unauthorized and foreign heartbeats → disabled station → MonitoringService
restarted: state kept → the Supervisor sees all of it on Services health → the initial count keeps the station
and camera as evidence (counting unchanged).

    PYTHONIOENCODING=utf-8 python scripts/e2e_ms05_stations.py
"""
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_test_env import load_test_env  # noqa: E402
import run_admin_browser_env as envctl  # noqa: E402  (portal CSRF helper for :15000)
from e2e_ms02_model_pin import BEST_SHA, CONTAINER, IMAGE, NETWORK, docker, live_ws, poll_state, wait  # noqa: E402

AUTH, WEB, SESSION, MONITORING, VISION = ('http://127.0.0.1:15001', 'http://127.0.0.1:15000', 'http://127.0.0.1:15004',
                                          'http://127.0.0.1:15008', 'http://127.0.0.1:15002')
OPERATOR, SUPERVISOR, ADMIN = (('operator@instrumed.com', 'DemopwdOP78!'), ('supervisor@instrumed.com', 'DemopwdSPD78!'),
                               ('admin@instrumed.com', 'DemopwdADM78!'))
VIDEO = ROOT / '.e2e' / 'videos' / 'ms031_masked.avi'
STALE_SECONDS = 8
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + str(detail)[:400] if detail and not ok else ''}", flush=True)
    return ok


def info(name, detail):
    print(f'INFO  {name}: {detail}', flush=True)


def login(email, password):
    session = requests.Session()
    response = session.post(AUTH + '/login', json={'email': email, 'password': password}, timeout=10)
    response.raise_for_status()
    session.cookies.set('access_token', response.cookies['access_token'], domain='127.0.0.1')
    return session, response.cookies['access_token']


def until(predicate, seconds=40, step=1.0):
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

    best = q('SELECT id, version_tag FROM yolo_model WHERE checksum = :c', c=BEST_SHA)[0]
    original_active = q1('SELECT id FROM yolo_model WHERE active')
    if not VIDEO.exists():
        sys.exit(f'missing {VIDEO}: scripts/make_ms031_count_videos.py (GUIA_PRUEBAS.md §2.4)')

    docker('rm', '-f', CONTAINER, check_rc=False)
    docker('run', '-d', '--name', CONTAINER, '--network', NETWORK, '-p', '127.0.0.1:15002:5002',
           '-v', f"{ROOT / 'apps' / 'VisionWorker'}:/app:ro", '-e', f'VISION_WS_SECRET={ws_secret}',
           '-e', f'VISION_WS_ORIGINS={WEB}', '-e', 'VISION_MODEL_FETCH_ALLOWED_HOSTS=garage:3900',
           '-e', 'VISION_MODEL_CACHE_DIR=/tmp/model_cache', '-e', 'VISION_WEIGHTS_PATH=/models/not-mounted.pt',
           '-e', 'VISION_UPLOAD_DIR=/tmp/uploads', '-e', 'TELEMETRY_SPOOL_DIR=/tmp/spool', IMAGE)
    auth_env, web_env = envctl.build_env('s3', '')
    auth_env['JWT_SECRET_KEY'] = jwt_secret
    web_env.update({'JWT_SECRET_KEY': jwt_secret, 'VISION_WORKER_URL': VISION, 'VISION_WS_SECRET': ws_secret,
                    'VISION_WORKER_WS_URL': 'ws://127.0.0.1:15002/ws', 'S3_INTERNAL_ENDPOINT': 'http://garage:3900',
                    'ADMIN_S3_BUCKET': 'pef-admin-browser', 'SESSION_SERVICE_URL': SESSION,
                    'MONITORING_SERVICE_URL': MONITORING})
    service_env = {**env, 'JWT_SECRET_KEY': jwt_secret}
    monitoring_env = {**service_env, 'MONITORING_HOST': '127.0.0.1', 'MONITORING_PORT': '15008',
                      'MONITORING_STATION_STALE_SECONDS': str(STALE_SECONDS), 'MONITORING_INTERVAL_SECONDS': '5',
                      'MONITORING_TARGET_VISION_URL': VISION, 'MONITORING_MONGO_URL': ''}
    logs = ROOT / '.e2e'

    def start(name, args, cwd, environment):
        return subprocess.Popen(args, cwd=cwd, env=environment, stdout=open(logs / f'ms05_{name}.log', 'a'),
                                stderr=subprocess.STDOUT)

    procs = {
        'auth': start('auth', [sys.executable, 'main.py'], ROOT / 'apps' / 'BackendAuthService', auth_env),
        'session': start('session', [sys.executable, 'main.py'], ROOT / 'apps' / 'SessionService',
                         {**service_env, 'SESSION_HOST': '127.0.0.1', 'SESSION_PORT': '15004'}),
        'monitoring': start('monitoring', [sys.executable, 'main.py'], ROOT / 'apps' / 'MonitoringService', monitoring_env),
        'collector': start('collector', [sys.executable, 'collector.py'], ROOT / 'apps' / 'MonitoringService', monitoring_env),
        'web': start('web', [sys.executable, '-m', 'flask', '--app', 'app', 'run', '--host', '127.0.0.1', '--port', '15000'],
                     ROOT / 'apps' / 'BackendWebFlask', web_env),
    }
    restore_station = None
    try:
        for url, label in ((AUTH + '/health', 'AuthService'), (SESSION + '/health/live', 'SessionService'),
                           (MONITORING + '/health/live', 'MonitoringService'), (WEB + '/health/live', 'portal'),
                           (VISION + '/health', 'VisionWorker')):
            wait(url, label, 240)
        admin, _ = login(*ADMIN)
        admin.post(f'{WEB}/admin/vision-models/{best.id}/activate', allow_redirects=False, timeout=30)
        spd, spd_token = login(*SUPERVISOR)
        op, op_token = login(*OPERATOR)
        operator_id, inst = q('SELECT id, institution_id FROM "user" WHERE email = :e', e=OPERATOR[0])[0]
        station = q('SELECT s.id, s.name, s.room_id FROM capture_station s JOIN operating_room r ON r.id = s.room_id '
                    'WHERE s.active AND r.active AND r.institution_id = :i ORDER BY s.name LIMIT 1', i=inst)[0]

        # --- 1. Administration registers a camera (portal form) ---------------------------------------------
        camera_name = f'E2E camera {secrets.token_hex(3)}'
        created = admin.post(f'{WEB}/admin/configuration/cameras/new', allow_redirects=False, timeout=30, data={
            'name': camera_name, 'station_id': str(station.id), 'device_type': 'usb', 'resolution_width': '1280',
            'resolution_height': '720', 'fps': '30', 'status': 'active'})
        camera = q('SELECT id, institution_id, station_id FROM capture_camera WHERE name = :n', n=camera_name)
        check('Administration registers a camera on a station of its institution (portal form, audited)',
              created.status_code == 302 and camera and camera[0].institution_id == inst
              and q1("SELECT count(*) FROM access_audit WHERE action = 'CREATE_CAPTURE_CAMERA' AND resource_id = :c",
                     c=camera[0].id) == 1, (created.status_code, camera))
        camera_id = camera[0].id

        # --- 2. Session at that station; the Operator chooses the camera ---------------------------------------
        kit = q1("SELECT id FROM kit WHERE active AND institution_id = :i AND name = 'Video Demo Kit 1'", i=inst)
        family = q1('SELECT ki.family_id FROM kit_item ki WHERE ki.kit_id = :k ORDER BY ki.family_id LIMIT 1', k=kit)
        body = {'operator_user_id': str(operator_id), 'procedure_type_id': str(q1('SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1')),
                'room_id': str(station.room_id), 'station_id': str(station.id), 'kit_id': str(kit),
                'scheduled_at': '2026-11-09T09:00:00Z', 'phase_code': 'start',
                'physicians': [{'physician_id': str(q1('SELECT id FROM physician WHERE institution_id = :i AND active LIMIT 1', i=inst))}],
                'privacy_notice_version_id': str(q1('SELECT id FROM privacy_notice_version WHERE active AND effective_at <= now() '
                                                    'ORDER BY effective_at LIMIT 1')),
                'expected_lines': [{'family_id': str(family), 'expected_quantity': 1}]}
        sid = requests.post(SESSION + '/api/v1/sessions/schedule', json=body, timeout=30,
                            headers={'Authorization': f'Bearer {spd_token}'}).json()['id']
        op.post(f'{WEB}/operator/sessions/{sid}/begin', data={'instruments_ready': '1'}, allow_redirects=False, timeout=300)
        capture = f'{WEB}/operator/sessions/{sid}/capture'
        page = op.get(capture, timeout=120).text
        chosen = op.post(f'{capture}/camera', json={'camera_id': str(camera_id)}, timeout=30)
        check('the capture page offers the station cameras; the Operator chooses one (stored and audited)',
              'id="capture-station"' in page and camera_name in page and chosen.status_code == 200
              and q1('SELECT camera_id FROM work_session WHERE id = :s', s=sid) == camera_id
              and q1("SELECT count(*) FROM access_audit WHERE action = 'session.camera_selected' AND resource_id = :s",
                     s=sid) == 1, chosen.text[:200])
        info('session', sid)

        def station_view():
            response = requests.get(f'{MONITORING}/api/v1/monitoring/stations/{station.id}', timeout=10,
                                    headers={'Authorization': f'Bearer {spd_token}'})
            return response.json() if response.ok else {}

        def camera_view(view):
            return next((c for c in view.get('cameras', []) if c['id'] == str(camera_id)), {})

        # --- 3. Heartbeat through the portal: station online, camera only "unverified" -------------------------
        beat = op.post(f'{capture}/heartbeat', json={'camera_status': 'ok'}, timeout=30).json()
        view = station_view()
        check('heartbeat of the capture page (relayed by the portal) → station online; the camera is not "ok" yet',
              beat.get('recorded') and view.get('status') == 'online' and camera_view(view).get('status') == 'unverified',
              (beat, view.get('status'), camera_view(view)))

        # --- 4. Real camera frames reach VisionWorker → the collector verifies the camera ------------------------
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            context.add_cookies([{'name': 'access_token', 'value': op_token, 'domain': '127.0.0.1', 'path': '/'}])
            browser_page = context.new_page()
            browser_page.goto(capture)
            sent = live_ws(browser_page, sid, frames=2)
            browser.close()
        activity = requests.get(VISION + '/activity', timeout=10).json()
        verified = until(lambda: camera_view(station_view()).get('status') == 'ok' and camera_view(station_view()), 30)
        check('live frames received by VisionWorker (/activity) → the collector marks the camera working (server-verified)',
              any(s['session_id'] == sid for s in activity['sessions']) and verified
              and verified.get('verified_by_server') is True,
              (sent.get('msgs', [])[-2:] if isinstance(sent, dict) else sent, activity, verified))

        # --- 5. Camera error and recovery --------------------------------------------------------------------------
        op.post(f'{capture}/heartbeat', json={'camera_status': 'error', 'camera_error': 'NotReadableError'}, timeout=30)
        view = station_view()
        alerts = requests.get(f'{MONITORING}/api/v1/monitoring/alerts', timeout=10,
                              headers={'Authorization': f'Bearer {spd_token}'}).json()['items']
        check('camera error reported by a connected station → camera "error" and one open alert (station still online)',
              view.get('status') == 'online' and camera_view(view).get('status') == 'error'
              and [a['kind'] for a in alerts if a['station_id'] == str(station.id)] == ['camera_error'],
              (view.get('status'), camera_view(view), alerts))
        health_page = spd.get(f'{WEB}/supervisor/services-health', timeout=30).text
        check('the Supervisor sees the station, the camera error and the pending alert on Services health',
              'data-stations-table' in health_page and 'data-camera-status="error"' in health_page
              and 'data-alert-kind="camera_error"' in health_page and 'NotReadableError' in health_page)
        op.post(f'{capture}/heartbeat', json={'camera_status': 'ok'}, timeout=30)
        check('camera recovery closes the alert', station_view().get('open_alerts') == 0, station_view())

        # --- 6. The station stops reporting → offline + heartbeat_lost; then recovers ---------------------------
        lost = until(lambda: (lambda v: v if v.get('status') == 'offline' and v.get('open_alerts') == 1 else None)(
            station_view()), STALE_SECONDS + 25)
        kinds = [e['kind'] for e in (lost or {}).get('events', [])]
        check(f'no heartbeat for more than {STALE_SECONDS} s → offline and a heartbeat_lost alert (collector)',
              lost and 'heartbeat_lost' in kinds, lost)
        offline_page = spd.get(f'{WEB}/supervisor/services-health', timeout=30).text
        check('the Supervisor sees the station without communication and the alert',
              f'data-station="{station.name}" data-status="offline"' in offline_page
              and 'data-alert-kind="heartbeat_lost"' in offline_page)
        op.post(f'{capture}/heartbeat', json={'camera_status': 'ok'}, timeout=30)
        view = station_view()
        check('communication recovered → online, alert closed, recovery recorded',
              view.get('status') == 'online' and view.get('open_alerts') == 0
              and 'heartbeat_recovered' in [e['kind'] for e in view.get('events', [])], view)

        # --- 7. Unauthorized and foreign heartbeats ----------------------------------------------------------------
        url = f'{MONITORING}/api/v1/monitoring/stations/{station.id}/heartbeat'
        as_spd = requests.post(url, json={}, headers={'Authorization': f'Bearer {spd_token}'}, timeout=10)
        anonymous = requests.post(url, json={}, timeout=10)
        foreign_station = q1('SELECT s.id FROM capture_station s JOIN operating_room r ON r.id = s.room_id '
                             'WHERE r.institution_id <> :i LIMIT 1', i=inst)
        foreign = requests.post(f'{MONITORING}/api/v1/monitoring/stations/{foreign_station}/heartbeat', json={},
                                headers={'Authorization': f'Bearer {op_token}'}, timeout=10) if foreign_station else None
        check('heartbeats by another role, without token or for another institution\'s station are refused',
              as_spd.status_code == 403 and anonymous.status_code == 401 and (foreign is None or foreign.status_code == 404),
              (as_spd.status_code, anonymous.status_code, foreign.status_code if foreign is not None else None))

        # --- 8. Disabled station (Administration) ------------------------------------------------------------------
        restore_station = (station.id, station.name, station.room_id)
        edit = f'{WEB}/admin/configuration/capture-stations/{station.id}/edit'
        admin.post(edit, data={'name': station.name, 'operating_room': str(station.room_id), 'status': 'inactive'},
                   allow_redirects=False, timeout=30)
        refused = op.post(f'{capture}/heartbeat', json={'camera_status': 'ok'}, timeout=30).json()
        check('a disabled station cannot report: heartbeat refused, station and camera "disabled"',
              refused.get('recorded') is False and refused.get('reason') == 'station_disabled'
              and station_view().get('status') == 'disabled' and camera_view(station_view()).get('status') == 'disabled',
              (refused, station_view().get('status')))
        admin.post(edit, data={'name': station.name, 'operating_room': str(station.room_id), 'status': 'active'},
                   allow_redirects=False, timeout=30)
        restore_station = None

        # --- 9. MonitoringService restart: state kept (PostgreSQL) -------------------------------------------------
        op.post(f'{capture}/heartbeat', json={'camera_status': 'error', 'camera_error': 'Disconnected'}, timeout=30)
        before = station_view()
        procs['monitoring'].terminate()
        procs['monitoring'].wait(timeout=30)
        procs['monitoring'] = start('monitoring', [sys.executable, 'main.py'], ROOT / 'apps' / 'MonitoringService',
                                    monitoring_env)
        wait(MONITORING + '/health/live', 'MonitoringService (restart)', 120)
        after = station_view()
        check('after a MonitoringService restart the station, camera error and alert are still there',
              after.get('last_seen_at') == before.get('last_seen_at') and camera_view(after).get('status') == 'error'
              and after.get('open_alerts') == 1, (before, after))
        op.post(f'{capture}/heartbeat', json={'camera_status': 'ok'}, timeout=30)

        # --- 10. The initial count keeps the station and camera; counting unchanged ---------------------------------
        with open(VIDEO, 'rb') as fh:
            op.post(f'{capture}/upload', files={'video': (VIDEO.name, fh, 'video/x-msvideo')}, timeout=300)
        poll_state(op, sid, lambda s: s.get('status') != 'loading' and (s.get('initial_frame') or {}).get('ok') is not None)
        initial = op.post(f'{capture}/initial', data={'acknowledge_mismatch': '1'}, timeout=60)
        payload = q1("SELECT ce.payload FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id "
                     "WHERE ce.session_id = :s AND t.code = 'auto_count' AND ce.payload->>'trigger' = 'initial'", s=sid)
        check('VisionWorker counting works as before; the initial count keeps the station and camera used',
              initial.status_code == 200 and payload and payload.get('capture_device') == {
                  'station_id': str(station.id), 'camera_id': str(camera_id)}, (initial.text[:200], (payload or {}).get('capture_device')))
    finally:
        if restore_station:
            sid_, name_, room_ = restore_station
            with engine.begin() as c:
                c.execute(text('UPDATE capture_station SET active = TRUE WHERE id = :s'), {'s': sid_})
        for proc in procs.values():
            if proc.poll() is None:
                proc.terminate()
        docker('rm', '-f', CONTAINER, check_rc=False)
        if original_active:
            with engine.begin() as c:
                c.execute(text('UPDATE yolo_model SET active = FALSE WHERE active'))
                c.execute(text('UPDATE yolo_model SET active = TRUE WHERE id = :i'), {'i': original_active})
    print(f'\n{sum(results)}/{len(results)} checks passed')
    sys.exit(0 if results and all(results) else 1)


if __name__ == '__main__':
    main()
