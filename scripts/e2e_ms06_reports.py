"""MS-05 + MS-06 integration, real end to end (Part C): station → Monitoring → session → reports → notifications →
e-mail → export.

Isolated stack only (docker-compose.test.yml / .env.test). Real processes: AuthService :15001, portal :15000,
SessionService :15004, NotificationsService API :15007 and its outbox worker (with the e-mail channel pointed at a
LOCAL SMTP test receiver started by this script: no provider, no credential, nothing leaves the machine),
MonitoringService API :15008 and its collector, VisionWorker in a container with the provisional best.pt.

Flow:
  1. Administration registers a camera on a station; the Supervisor schedules two sessions there.
  2. S1 (vision): the Operator chooses the camera, the capture page's heartbeat reaches MonitoringService (station
     online), a camera error opens a technical alert → station_alert notification (Supervisor + IT administrator,
     in-app and e-mail); recovery.
  3. S1: real video, real YOLO initial and final counts (accepted), human validation reporting the expected
     instrument as missing → discrepancies → the Supervisor records the shortage (lost) and closes acknowledging
     it → session_unresolved (Operator).
  4. S2: contingency quantity report with the expected quantities → awaiting SPD review → review_pending
     (Supervisor, in-app and e-mail) → safe close → session_closed (Operator).
  5. Reports for the station (SessionService API and the portal screen): the two sessions, outcomes, automatic
     counts and the station activity grew by exactly what happened; another institution sees nothing.
  6. The notifications are visible in the portal (Supervisor and Operator) with their labels.
  7. CSV and PDF export through the portal with the station filter; each download audited.

Counting rules, final capture and human validation are used, never changed.

    PYTHONIOENCODING=utf-8 python scripts/e2e_ms06_reports.py
"""
import csv
import io
import re
import secrets
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from datetime import date, timedelta
from email import message_from_bytes
from pathlib import Path

import requests
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_test_env import load_test_env  # noqa: E402
import run_admin_browser_env as envctl  # noqa: E402
from e2e_ms02_model_pin import BEST_SHA, CONTAINER, IMAGE, NETWORK, docker, poll_state, wait  # noqa: E402

AUTH, WEB, SESSION, NOTIFICATIONS, MONITORING, VISION = (
    'http://127.0.0.1:15001', 'http://127.0.0.1:15000', 'http://127.0.0.1:15004', 'http://127.0.0.1:15007',
    'http://127.0.0.1:15008', 'http://127.0.0.1:15002')
OPERATOR, SUPERVISOR, ADMIN = (('operator@instrumed.com', 'DemopwdOP78!'), ('supervisor@instrumed.com', 'DemopwdSPD78!'),
                               ('admin@instrumed.com', 'DemopwdADM78!'))
VIDEO = ROOT / '.e2e' / 'videos' / 'ms031_masked.avi'
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + str(detail)[:500] if detail and not ok else ''}", flush=True)
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


class _SMTPHandler(socketserver.StreamRequestHandler):
    """Minimal RFC 5321 receiver: keeps the messages in memory (a test double of the mail server)."""

    def handle(self):
        self.wfile.write(b'220 e2e-smtp ready\r\n')
        data_mode, lines = False, []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if data_mode:
                if line in (b'.\r\n', b'.\n'):
                    self.server.messages.append(message_from_bytes(b''.join(lines)))
                    data_mode, lines = False, []
                    self.wfile.write(b'250 queued\r\n')
                else:
                    lines.append(line[1:] if line.startswith(b'..') else line)
                continue
            command = line.strip().upper()
            if command.startswith((b'EHLO', b'HELO')):
                self.wfile.write(b'250 e2e-smtp\r\n')
            elif command.startswith((b'MAIL', b'RCPT', b'RSET', b'NOOP')):
                self.wfile.write(b'250 ok\r\n')
            elif command == b'DATA':
                data_mode = True
                self.wfile.write(b'354 end with .\r\n')
            elif command == b'QUIT':
                self.wfile.write(b'221 bye\r\n')
                return
            else:
                self.wfile.write(b'502 not implemented\r\n')


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

    smtp = socketserver.ThreadingTCPServer(('127.0.0.1', 0), _SMTPHandler)
    smtp.daemon_threads, smtp.messages = True, []
    threading.Thread(target=smtp.serve_forever, daemon=True).start()

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
                    'MONITORING_SERVICE_URL': MONITORING, 'NOTIFICATIONS_SERVICE_URL': NOTIFICATIONS})
    service_env = {**env, 'JWT_SECRET_KEY': jwt_secret}
    monitoring_env = {**service_env, 'MONITORING_HOST': '127.0.0.1', 'MONITORING_PORT': '15008',
                      'MONITORING_STATION_STALE_SECONDS': '600', 'MONITORING_INTERVAL_SECONDS': '5',
                      'MONITORING_TARGET_VISION_URL': VISION, 'MONITORING_MONGO_URL': ''}
    notifications_env = {**service_env, 'NOTIFICATIONS_HOST': '127.0.0.1', 'NOTIFICATIONS_PORT': '15007',
                         'NOTIFICATIONS_WORKER_POLL_SECONDS': '1',
                         'NOTIFICATIONS_SMTP_HOST': '127.0.0.1', 'NOTIFICATIONS_SMTP_PORT': str(smtp.server_address[1]),
                         'NOTIFICATIONS_SMTP_SECURITY': 'none', 'NOTIFICATIONS_SMTP_FROM': 'pef-e2e@hospital.test',
                         'NOTIFICATIONS_PORTAL_URL': WEB}
    logs = ROOT / '.e2e'

    def start(name, args, cwd, environment):
        return subprocess.Popen(args, cwd=cwd, env=environment, stdout=open(logs / f'ms06_{name}.log', 'a'),
                                stderr=subprocess.STDOUT)

    procs = {
        'auth': start('auth', [sys.executable, 'main.py'], ROOT / 'apps' / 'BackendAuthService', auth_env),
        'session': start('session', [sys.executable, 'main.py'], ROOT / 'apps' / 'SessionService',
                         {**service_env, 'SESSION_HOST': '127.0.0.1', 'SESSION_PORT': '15004'}),
        'notifications': start('notifications', [sys.executable, 'main.py'], ROOT / 'apps' / 'NotificationsService',
                               notifications_env),
        'worker': start('worker', [sys.executable, 'worker.py'], ROOT / 'apps' / 'NotificationsService', notifications_env),
        'monitoring': start('monitoring', [sys.executable, 'main.py'], ROOT / 'apps' / 'MonitoringService', monitoring_env),
        'collector': start('collector', [sys.executable, 'collector.py'], ROOT / 'apps' / 'MonitoringService', monitoring_env),
        'web': start('web', [sys.executable, '-m', 'flask', '--app', 'app', 'run', '--host', '127.0.0.1', '--port', '15000'],
                     ROOT / 'apps' / 'BackendWebFlask', web_env),
    }
    try:
        for url, label in ((AUTH + '/health', 'AuthService'), (SESSION + '/health/live', 'SessionService'),
                           (NOTIFICATIONS + '/health/live', 'NotificationsService'),
                           (MONITORING + '/health/live', 'MonitoringService'), (WEB + '/health/live', 'portal'),
                           (VISION + '/health', 'VisionWorker')):
            wait(url, label, 240)
        admin, admin_token = login(*ADMIN)
        admin.post(f'{WEB}/admin/vision-models/{best.id}/activate', allow_redirects=False, timeout=30)
        spd, spd_token = login(*SUPERVISOR)
        op, op_token = login(*OPERATOR)
        operator_id, inst = q('SELECT id, institution_id FROM "user" WHERE email = :e', e=OPERATOR[0])[0]
        spd_id = q1('SELECT id FROM "user" WHERE email = :e', e=SUPERVISOR[0])
        station = q('SELECT s.id, s.name, s.room_id FROM capture_station s JOIN operating_room r ON r.id = s.room_id '
                    'WHERE s.active AND r.active AND r.institution_id = :i ORDER BY s.name LIMIT 1', i=inst)[0]
        today = date.today()
        period = {'from': str(today - timedelta(days=1)), 'to': str(today + timedelta(days=1)), 'tz': 'UTC',
                  'station_id': str(station.id)}

        def report(path, token=spd_token, **extra):
            return requests.get(f'{SESSION}/api/v1/sessions/reports/{path}', params={**period, **extra}, timeout=30,
                                headers={'Authorization': f'Bearer {token}'})

        def station_row():
            rows = report('stations').json()['stations']
            return next((r for r in rows if r['station_id'] == str(station.id)), {'sessions': 0, 'closed': 0,
                                                                                  'with_final_count': 0})
        before_summary, before_station = report('summary').json(), station_row()
        exports_before = q1("SELECT count(*) FROM access_audit WHERE action LIKE 'report.export.%' AND actor_user_id = :u",
                            u=spd_id)

        # --- 1. Camera and sessions at the station -----------------------------------------------------------------
        camera_name = f'E2E MS06 camera {secrets.token_hex(3)}'
        admin.post(f'{WEB}/admin/configuration/cameras/new', allow_redirects=False, timeout=30, data={
            'name': camera_name, 'station_id': str(station.id), 'device_type': 'usb', 'status': 'active'})
        camera_id = q1('SELECT id FROM capture_camera WHERE name = :n', n=camera_name)
        kit = q1("SELECT id FROM kit WHERE active AND institution_id = :i AND name = 'Video Demo Kit 1'", i=inst)
        family = q1('SELECT ki.family_id FROM kit_item ki WHERE ki.kit_id = :k ORDER BY ki.family_id LIMIT 1', k=kit)

        def schedule(label):
            body = {'operator_user_id': str(operator_id),
                    'procedure_type_id': str(q1('SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1')),
                    'room_id': str(station.room_id), 'station_id': str(station.id), 'kit_id': str(kit),
                    'scheduled_at': f'{today}T09:00:00Z', 'phase_code': 'start',
                    'physicians': [{'physician_id': str(q1('SELECT id FROM physician WHERE institution_id = :i AND active '
                                                           'LIMIT 1', i=inst))}],
                    'privacy_notice_version_id': str(q1('SELECT id FROM privacy_notice_version WHERE active AND '
                                                        'effective_at <= now() ORDER BY effective_at LIMIT 1')),
                    'expected_lines': [{'family_id': str(family), 'expected_quantity': 1}]}
            sid = requests.post(SESSION + '/api/v1/sessions/schedule', json=body, timeout=30,
                                headers={'Authorization': f'Bearer {spd_token}'}).json()['id']
            op.post(f'{WEB}/operator/sessions/{sid}/begin', data={'instruments_ready': '1'}, allow_redirects=False,
                    timeout=300)
            info(label, sid)
            return sid

        def status(sid):
            return q1('SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = :s',
                      s=sid)

        def notified(sid=None, kind=None):
            sql = 'SELECT n.type, u.email FROM notification n JOIN "user" u ON u.id = n.recipient_user_id WHERE 1=1'
            if sid:
                sql += ' AND n.session_id = :s'
            if kind:
                sql += ' AND n.type = :k'
            return sorted(q(sql + ' AND n.created_at > now() - interval \'1 hour\'', s=sid, k=kind))

        s1, s2 = schedule('S1 vision session'), schedule('S2 contingency session')
        check('camera registered and both sessions in progress at the station',
              camera_id and status(s1) == status(s2) == 'in_progress', (camera_id, status(s1), status(s2)))

        # --- 2. Station → Monitoring; camera error → technical alert → notification ----------------------------------
        capture = f'{WEB}/operator/sessions/{s1}/capture'
        op.get(capture, timeout=120)
        op.post(f'{capture}/camera', json={'camera_id': str(camera_id)}, timeout=30)
        beat = op.post(f'{capture}/heartbeat', json={'camera_status': 'ok'}, timeout=30).json()
        view = requests.get(f'{MONITORING}/api/v1/monitoring/stations/{station.id}', timeout=10,
                            headers={'Authorization': f'Bearer {spd_token}'}).json()
        check('the station reports through the capture page and MonitoringService registers it (online)',
              beat.get('recorded') and view.get('status') == 'online', (beat, view.get('status')))

        def station_alerts(email):
            return q("SELECT n.id, n.status, n.data->>'kind' FROM notification n JOIN \"user\" u ON u.id = n.recipient_user_id "
                     "WHERE n.type = 'station_alert' AND u.email = :e AND n.data->>'station_id' = :s ORDER BY n.created_at",
                     e=email, s=str(station.id))

        def camera_error(reason):
            op.post(f'{capture}/heartbeat', json={'camera_status': 'error', 'camera_error': reason}, timeout=30)
            event_id = q1("SELECT id FROM station_event WHERE station_id = :s AND kind = 'camera_error' AND resolved_at IS NULL",
                          s=station.id)
            until(lambda: q1("SELECT processed_at IS NOT NULL FROM notification_outbox WHERE source_table = 'station_event' "
                             'AND source_id = :e', e=event_id), 30)
            op.post(f'{capture}/heartbeat', json={'camera_status': 'ok'}, timeout=30)
            return event_id

        # (a) With an unread camera alert of this station already in the inbox, a new error does not pile up.
        camera_error('NotReadableError')
        unread_before = [r for r in station_alerts(SUPERVISOR[0]) if r.status == 'unread' and r[2] == 'camera_error']
        camera_error('NotReadableError')
        unread_after = [r for r in station_alerts(SUPERVISOR[0]) if r.status == 'unread' and r[2] == 'camera_error']
        check('a repeated camera error while the previous alert is unread is not duplicated (one unread per station/kind)',
              len(unread_before) == len(unread_after) == 1, (unread_before, unread_after))
        # (b) Once the Supervisor and the IT administrator read their alerts, the next error is a new notification.
        for token in (spd_token, admin_token):
            requests.patch(f'{NOTIFICATIONS}/api/v1/notifications/read-all', json={'type': 'station_alert'}, timeout=10,
                           headers={'Authorization': f'Bearer {token}'})
        event_id = camera_error('NotReadableError')
        fresh = {email: [r for r in station_alerts(email) if r.status == 'unread' and r[2] == 'camera_error']
                 for email in (SUPERVISOR[0], ADMIN[0], OPERATOR[0])}
        check('camera error → station_event → outbox → a new station_alert for the Supervisor and the IT administrator '
              '(not the Operator)', event_id and len(fresh[SUPERVISOR[0]]) == 1 and len(fresh[ADMIN[0]]) == 1
              and not fresh[OPERATOR[0]], fresh)
        op.post(f'{capture}/heartbeat', json={'camera_status': 'ok'}, timeout=30)

        # --- 3. S1: real counting, validation with a missing instrument, unresolved close ---------------------------
        with open(VIDEO, 'rb') as fh:
            op.post(f'{capture}/upload', files={'video': (VIDEO.name, fh, 'video/x-msvideo')}, timeout=300)
        state = poll_state(op, s1, lambda s: s.get('status') != 'loading' and (s.get('initial_frame') or {}).get('ok') is not None)
        op.post(f'{capture}/initial', data={'acknowledge_mismatch': '1'}, timeout=60)
        op.post(f'{capture}/play', timeout=60)
        state = poll_state(op, s1, lambda s: s.get('status') in ('ready', 'error')
                           and (s.get('final_frame') or {}).get('ok') is not None, seconds=600)
        final_total = len((state.get('final_frame') or {}).get('boxes') or [])
        accepted = op.post(f'{capture}/final', data={}, timeout=60)
        check(f'S1: real YOLO initial and final counts (final {final_total}) accepted by the unchanged rule',
              accepted.status_code == 200 and state.get('model_sha256') == BEST_SHA, accepted.text[:200])
        html = op.get(f'{WEB}/operator/sessions/{s1}/vision-validation', timeout=30).text
        fields = re.findall(r'name="validated_([0-9a-f-]{36})"', html)
        form = {f'validated_{fid}': '0' for fid in fields}
        form.update({f'reason_{family}': 'shortage', f'notes_{family}': 'MS-06 E2E: not on the tray',
                     'extra_instruments': str(final_total), 'extra_notes': 'MS-06 E2E: outside the kit'})
        validated = op.post(f'{WEB}/operator/sessions/{s1}/vision-validation', data=form, allow_redirects=False, timeout=60)
        check('S1: human validation (same physical total) reports the expected instrument as missing → correction',
              validated.status_code == 302 and status(s1) == 'correction_required', (validated.status_code, status(s1)))
        until(lambda: ('discrepancy_pending', SUPERVISOR[0]) in notified(s1), 30)
        for disc, reason in q('SELECT d.id, r.code FROM discrepancy d LEFT JOIN cat_discrepancy_reason r ON r.id = d.reason_id '
                              'WHERE d.session_id = :s AND NOT d.resolved', s=s1):
            spd.post(f'{WEB}/supervisor/discrepancies/{s1}/review', allow_redirects=False, data={
                'discrepancy_id': str(disc), 'notes': 'MS-06 E2E review',
                'resolution_outcome': 'lost' if reason == 'shortage' else 'resolved'})
        closed = spd.post(f'{WEB}/supervisor/sessions/{s1}/close', allow_redirects=False, data={
            'material_recovered': '1', 'contingency_reviewed': '1', 'unreconciled_shortage_acknowledged': '1'})
        unresolved = until(lambda: ('session_unresolved', OPERATOR[0]) in notified(s1), 30)
        check('S1: closed with the shortage acknowledged → session_unresolved for the Operator (no "closed" notice)',
              closed.status_code == 302 and status(s1) == 'closed' and unresolved
              and not [t for t, _ in notified(s1) if t == 'session_closed'], (closed.status_code, status(s1), notified(s1)))

        # --- 4. S2: contingency report → review pending → close ------------------------------------------------------
        # MS-09: the vision worker is up here, so "worker_unavailable" would be refused (contradicted claim). The
        # Operator declares another reason with a description: recorded as DECLARED (not verified by the system).
        reported = op.post(f'{WEB}/operator/sessions/{s2}/manual-report', allow_redirects=False, timeout=60, data={
            f'qty_{family}': '1', f'reported_{family}': '1', 'contingency_reason': 'other',
            'contingency_notes': 'MS-06 E2E: camera image unusable (lighting)', 'extra_instruments': '0'})
        pending = until(lambda: ('review_pending', SUPERVISOR[0]) in notified(s2), 30)
        check('S2: quantity report matching the kit → awaiting SPD review → review_pending for the Supervisor',
              reported.status_code == 302 and status(s2) == 'awaiting_spd_review' and pending,
              (reported.status_code, status(s2), notified(s2)))
        not_acknowledged = spd.post(f'{WEB}/supervisor/sessions/{s2}/close', allow_redirects=False,
                                    data={'material_recovered': '1', 'contingency_reviewed': '1'})
        check('S2 (MS-09): a declared contingency cannot be closed without acknowledging it was not verified',
              not_acknowledged.status_code == 409 and status(s2) == 'awaiting_spd_review', not_acknowledged.status_code)
        closed2 = spd.post(f'{WEB}/supervisor/sessions/{s2}/close', allow_redirects=False,
                           data={'material_recovered': '1', 'contingency_reviewed': '1',
                                 'unverified_contingency_acknowledged': '1'})
        safe = until(lambda: ('session_closed', OPERATOR[0]) in notified(s2), 30)
        check('S2: safe close → session_closed for the Operator', closed2.status_code == 302 and status(s2) == 'closed'
              and safe, (closed2.status_code, status(s2)))

        # --- 5. Reports: exactly what happened at the station --------------------------------------------------------
        after_summary, after_station = report('summary').json(), station_row()
        grow = {key: after_summary['outcomes'][key] - before_summary['outcomes'][key]
                for key in ('approved', 'approved_contingency', 'unresolved')}
        # MS-09: S2 was closed after a contingency count: reported apart, never as a normal approval.
        check('reports (station filter): +2 sessions, +1 approved after a contingency (not a normal approval), '
              '+1 unresolved, +1 accepted final count',
              after_summary['sessions']['total'] - before_summary['sessions']['total'] == 2
              and grow == {'approved': 0, 'approved_contingency': 1, 'unresolved': 1}
              and after_summary['automatic_counts']['with_final_count'] - before_summary['automatic_counts']['with_final_count'] == 1
              and after_station['sessions'] - before_station['sessions'] == 2
              and after_station['closed'] - before_station['closed'] == 2
              and after_station['with_final_count'] - before_station['with_final_count'] == 1
              and after_station['cameras_used'] >= 1, (grow, before_station, after_station))
        info('report (station)', {k: after_summary['outcomes'][k] for k in ('approved', 'unresolved', 'pending_review')}
             | {'automatic': {k: after_summary['automatic_counts'][k] for k in ('with_final_count', 'final_first_attempt_pct',
                                                                               'avg_initial_to_final_minutes')}})
        foreign_station = q1('SELECT s.id FROM capture_station s JOIN operating_room r ON r.id = s.room_id '
                             'WHERE r.institution_id <> :i LIMIT 1', i=inst)
        refused = report('summary', station_id=str(foreign_station)) if foreign_station else None
        check("another institution's station cannot be used as a report filter (400, nothing disclosed)",
              refused is None or (refused.status_code == 400 and refused.json()['details']['field'] == 'station_id'),
              refused.text[:200] if refused is not None else None)
        page = spd.get(f'{WEB}/supervisor/reports', params={'date_range': '7d', 'station': str(station.id)}, timeout=60).text
        check('the Supervisor sees outcomes, automatic count and the station row on the Reports screen',
              'data-report-outcomes' in page and f'data-station="{station.id}"' in page
              and 'This is not the accuracy of the model' in page, page[:200])

        # --- 6. Notifications in the portal; e-mail through the local SMTP receiver ----------------------------------
        spd_page = spd.get(f'{WEB}/notifications', timeout=30).text
        op_page = op.get(f'{WEB}/notifications', timeout=30).text
        check('portal: the Supervisor sees the station alert and the review request; the Operator the closes',
              f'Capture station {station.name} reported a camera error.' in spd_page and 'Session pending review' in spd_page
              and 'Session closed as unresolved' in op_page, (len(spd_page), len(op_page)))
        mails = until(lambda: [m for m in smtp.messages if m['To'] == SUPERVISOR[0]]
                      if {'[PEF] Capture station alert', '[PEF] Session pending review'}
                      <= {m['Subject'] for m in smtp.messages if m['To'] == SUPERVISOR[0]} else None, 40)
        delivered = q("SELECT d.status, count(*) FROM notification_delivery d JOIN notification n ON n.id = d.notification_id "
                      "WHERE n.created_at > now() - interval '1 hour' AND (n.session_id IN (:a, :b) OR n.type = 'station_alert') "
                      'GROUP BY d.status', a=s1, b=s2)
        check('e-mail channel: the Supervisor received the alert and the review request (local SMTP test receiver)',
              mails and all('/notifications' in m.get_payload() for m in mails)
              and dict(delivered).get('sent', 0) >= 2 and not dict(delivered).get('failed'), (len(smtp.messages), delivered))

        # --- 7. Export with the screen filters; audited --------------------------------------------------------------
        params = {'date_range': '7d', 'station': str(station.id)}
        exported = spd.get(f'{WEB}/supervisor/reports/export.csv', params=params, timeout=60)
        rows = list(csv.reader(io.StringIO(exported.content.decode('utf-8-sig')))) if exported.ok else []
        pdf = spd.get(f'{WEB}/supervisor/reports/export.pdf', params=params, timeout=60)
        denied = op.get(f'{WEB}/supervisor/reports/export.csv', params=params, timeout=30, allow_redirects=False)
        exports = q1("SELECT count(*) FROM access_audit WHERE action LIKE 'report.export.%' AND actor_user_id = :u", u=spd_id)
        check('CSV and PDF export of the filtered report through the portal; the Operator cannot; both audited',
              exported.status_code == 200 and exported.headers.get('Content-Disposition', '').startswith('attachment')
              and any(str(station.name) in ' '.join(r) for r in rows) and pdf.status_code == 200
              and pdf.content.startswith(b'%PDF') and denied.status_code in (302, 403) and exports - exports_before == 2,
              (exported.status_code, len(rows), pdf.status_code, denied.status_code, exports - exports_before))
    finally:
        from e2e_restore_stock import restore_lost
        info('test stock restored', restore_lost(engine, [s1] if 's1' in locals() else []))
        for proc in procs.values():
            if proc.poll() is None:
                proc.terminate()
        smtp.shutdown()
        docker('rm', '-f', CONTAINER, check_rc=False)
        if original_active:
            with engine.begin() as c:
                c.execute(text('UPDATE yolo_model SET active = FALSE WHERE active'))
                c.execute(text('UPDATE yolo_model SET active = TRUE WHERE id = :i'), {'i': original_active})
    print(f'\n{sum(results)}/{len(results)} checks passed')
    sys.exit(0 if results and all(results) else 1)


if __name__ == '__main__':
    main()
