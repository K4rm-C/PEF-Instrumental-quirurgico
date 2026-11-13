"""MS-09 real end to end: count safety (escalation of a rejected final count, verified / refused contingency).

Isolated stack only. Real processes: AuthService :15001, portal :15000, SessionService :15004 (the Supervisor's
close goes through it), EvidenceService :15005, VisionWorker in a container with the provisional best.pt and the
MS-03.1 test videos (real inference).

A. Escalation: a real final capture that differs from the initial count is rejected; the Operator reports a possible
   physical difference; no final count, no human validation; discrepancy linked to the rejected capture; the
   Supervisor sees the escalation, cannot close without acknowledging it, and the close is never a safe close.
B. Contingency: with the worker answering, "worker_unavailable" is refused; with the VisionWorker container really
   stopped, the same claim is accepted as VERIFIED and the Supervisor closes it with the review confirmation only.

    PYTHONIOENCODING=utf-8 python scripts/e2e_ms09_count_safety.py
"""
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
import run_admin_browser_env as envctl  # noqa: E402
from e2e_ms02_model_pin import BEST_SHA, CONTAINER, IMAGE, NETWORK, docker, poll_state, wait  # noqa: E402
from e2e_restore_stock import restore_lost  # noqa: E402

AUTH, WEB, SESSION, EVIDENCE, VISION = ('http://127.0.0.1:15001', 'http://127.0.0.1:15000', 'http://127.0.0.1:15004',
                                        'http://127.0.0.1:15005', 'http://127.0.0.1:15002')
OPERATOR, SUPERVISOR, ADMIN = (('operator@instrumed.com', 'DemopwdOP78!'), ('supervisor@instrumed.com', 'DemopwdSPD78!'),
                               ('admin@instrumed.com', 'DemopwdADM78!'))
VIDEOS = ROOT / '.e2e' / 'videos'
ONE_REMOVED, MASKED = VIDEOS / 'ms031_one_removed.avi', VIDEOS / 'ms031_masked.avi'
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


def main():
    env = load_test_env()
    engine = create_engine(env['DATABASE_URL'], future=True)
    for path in (ONE_REMOVED, MASKED):
        if not path.exists():
            sys.exit(f'missing {path}: scripts/make_ms031_count_videos.py (GUIA_PRUEBAS.md §2.4)')
    jwt_secret, ws_secret = secrets.token_hex(32), secrets.token_hex(24)

    def q(sql, **params):
        with engine.connect() as c:
            return c.execute(text(sql), params).all()

    def q1(sql, **params):
        rows = q(sql, **params)
        return rows[0][0] if rows else None

    best = q('SELECT id FROM yolo_model WHERE checksum = :c', c=BEST_SHA)[0]
    original_active = q1('SELECT id FROM yolo_model WHERE active')
    docker('rm', '-f', CONTAINER, check_rc=False)
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
                    'MONGO_URL': env['PEF_TEST_MONGO_URL'], 'MONGO_DB': 'pef_ms09_e2e',
                    'PEF_OUTBOX_DIR': str(ROOT / '.e2e' / f'ms09_outbox_{secrets.token_hex(3)}')})
    service_env = {**env, 'JWT_SECRET_KEY': jwt_secret}
    logs = ROOT / '.e2e'

    def start(name, args, cwd, environment):
        return subprocess.Popen(args, cwd=cwd, env=environment, stdout=open(logs / f'ms09_{name}.log', 'a'),
                                stderr=subprocess.STDOUT)
    procs = {
        'auth': start('auth', [sys.executable, 'main.py'], ROOT / 'apps' / 'BackendAuthService', auth_env),
        'session': start('session', [sys.executable, 'main.py'], ROOT / 'apps' / 'SessionService',
                         {**service_env, 'SESSION_HOST': '127.0.0.1', 'SESSION_PORT': '15004'}),
        'evidence': start('evidence', [sys.executable, 'main.py'], ROOT / 'apps' / 'EvidenceService',
                          {**service_env, 'EVIDENCE_HOST': '127.0.0.1', 'EVIDENCE_PORT': '15005', 'S3_INTERNAL_ENDPOINT': s3,
                           'S3_PUBLIC_ENDPOINT': s3, 'S3_REGION': 'garage', 'S3_ACCESS_KEY_ID': env['PEF_TEST_S3_ACCESS_KEY'],
                           'S3_SECRET_ACCESS_KEY': env['PEF_TEST_S3_SECRET_KEY'], 'S3_BUCKET_EVIDENCE': 'pef-evidence-test'}),
        'web': start('web', [sys.executable, '-m', 'flask', '--app', 'app', 'run', '--host', '127.0.0.1', '--port', '15000'],
                     ROOT / 'apps' / 'BackendWebFlask', web_env),
    }
    sessions = []
    try:
        for url, label in ((AUTH + '/health', 'AuthService'), (SESSION + '/health/live', 'SessionService'),
                           (EVIDENCE + '/health/live', 'EvidenceService'), (WEB + '/health/live', 'portal'),
                           (VISION + '/health', 'VisionWorker')):
            wait(url, label, 240)
        admin, _ = login(*ADMIN)
        admin.post(f'{WEB}/admin/vision-models/{best.id}/activate', allow_redirects=False, timeout=30)
        spd, spd_token = login(*SUPERVISOR)
        op, _ = login(*OPERATOR)
        operator_id, inst = q('SELECT id, institution_id FROM "user" WHERE email = :e', e=OPERATOR[0])[0]
        station = q('SELECT s.id, s.room_id FROM capture_station s JOIN operating_room r ON r.id = s.room_id '
                    'WHERE s.active AND r.active AND r.institution_id = :i ORDER BY s.name LIMIT 1', i=inst)[0]
        kit = q1("SELECT id FROM kit WHERE active AND institution_id = :i AND name = 'Video Demo Kit 1'", i=inst)
        family = q1('SELECT ki.family_id FROM kit_item ki WHERE ki.kit_id = :k ORDER BY ki.family_id LIMIT 1', k=kit)

        def new_session(label):
            body = {'operator_user_id': str(operator_id),
                    'procedure_type_id': str(q1('SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1')),
                    'room_id': str(station.room_id), 'station_id': str(station.id), 'kit_id': str(kit),
                    'scheduled_at': '2026-11-09T19:00:00Z', 'phase_code': 'start',
                    'physicians': [{'physician_id': str(q1('SELECT id FROM physician WHERE institution_id = :i AND active '
                                                           'LIMIT 1', i=inst))}],
                    'privacy_notice_version_id': str(q1('SELECT id FROM privacy_notice_version WHERE active AND '
                                                        'effective_at <= now() ORDER BY effective_at LIMIT 1')),
                    'expected_lines': [{'family_id': str(family), 'expected_quantity': 1}]}
            sid = requests.post(SESSION + '/api/v1/sessions/schedule', json=body, timeout=30,
                                headers={'Authorization': f'Bearer {spd_token}'}).json()['id']
            op.post(f'{WEB}/operator/sessions/{sid}/begin', data={'instruments_ready': '1'}, allow_redirects=False, timeout=300)
            op.get(f'{WEB}/operator/sessions/{sid}/capture', timeout=120)
            sessions.append(sid)
            info(label, sid)
            return sid

        def status(sid):
            return q1('SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = :s',
                      s=sid)

        def events(sid, code):
            return q("SELECT e.id, e.detected_quantity, e.expected_quantity, e.payload FROM count_event e "
                     "JOIN cat_event_type t ON t.id = e.event_type_id WHERE e.session_id = :s AND t.code = :c "
                     "ORDER BY e.occurred_at", s=sid, c=code)

        # ===== A. escalation of a rejected final count (real YOLO) ==============================================
        sid = new_session('A (one instrument removed at the end)')
        capture = f'{WEB}/operator/sessions/{sid}/capture'
        with open(ONE_REMOVED, 'rb') as fh:
            op.post(f'{capture}/upload', files={'video': (ONE_REMOVED.name, fh, 'video/x-msvideo')}, timeout=300)
        state = poll_state(op, sid, lambda s: s.get('status') != 'loading' and (s.get('initial_frame') or {}).get('ok') is not None)
        initial = len((state.get('initial_frame') or {}).get('boxes') or [])
        op.post(f'{capture}/initial', data={'acknowledge_mismatch': '1'}, timeout=60)
        op.post(f'{capture}/play', timeout=60)
        state = poll_state(op, sid, lambda s: s.get('status') in ('ready', 'error')
                           and (s.get('final_frame') or {}).get('ok') is not None, seconds=600)
        final = len((state.get('final_frame') or {}).get('boxes') or [])
        rejected = op.post(f'{capture}/final', timeout=60)
        check(f'A: real final capture {final} vs reference {initial} rejected (409, close_blocked kept)',
              final != initial and rejected.status_code == 409 and len(events(sid, 'close_blocked')) == 1,
              (initial, final, rejected.status_code))
        page = op.get(capture, timeout=60).text
        check('A: the capture page offers the escalation in the recapture step',
              'id="capture-escalation"' in page and 'id="capture-escalation" data-escalate-url' in page)
        escalated = op.post(f'{capture}/escalate', json={'notes': 'MS-09 E2E: tray checked twice, one piece missing'},
                            timeout=60)
        body = escalated.json()
        blocked = events(sid, 'close_blocked')[0]
        disc = q('SELECT r.code, d.expected_quantity, d.detected_quantity, d.origin_event_id FROM discrepancy d '
                 'JOIN cat_discrepancy_reason r ON r.id = d.reason_id WHERE d.session_id = :s', s=sid)
        finals = [e for e in events(sid, 'auto_count') if (e.payload or {}).get('trigger') == 'final']
        check('A: escalation registered: correction_required, discrepancy linked to the rejected capture, no final '
              'count, audit, no human validation',
              escalated.status_code == 200 and status(sid) == 'correction_required' and finals == []
              and len(disc) == 1 and disc[0].origin_event_id == blocked.id
              and (disc[0].expected_quantity, disc[0].detected_quantity) == (initial, final)
              and q1("SELECT count(*) FROM access_audit WHERE action = 'session.final_count_escalated' AND resource_id = :s",
                     s=sid) == 1
              and op.get(f'{WEB}/operator/sessions/{sid}/vision-validation', allow_redirects=False, timeout=30).status_code == 302,
              (escalated.text[:200], status(sid), disc))
        info('A: escalation', body)
        disc_id = q1('SELECT id FROM discrepancy WHERE session_id = :s', s=sid)
        spd.post(f'{WEB}/supervisor/discrepancies/{sid}/review', allow_redirects=False, data={
            'discrepancy_id': str(disc_id), 'notes': 'MS-09 E2E review',
            'resolution_outcome': 'recovered' if disc[0].code == 'shortage' else 'resolved'})
        detail = spd.get(f'{WEB}/supervisor/sessions/{sid}', timeout=60).text
        refused = spd.post(f'{WEB}/supervisor/sessions/{sid}/close', allow_redirects=False, timeout=60, data={
            'material_recovered': '1', 'unreconciled_shortage_acknowledged': '1'})
        closed = spd.post(f'{WEB}/supervisor/sessions/{sid}/close', allow_redirects=False, timeout=60, data={
            'material_recovered': '1', 'unreconciled_shortage_acknowledged': '1', 'escalation_acknowledged': '1'})
        close_payload = (events(sid, 'session_close') or [None])[-1]
        check('A: the Supervisor sees the escalation; without its acknowledgment 409; closed only as "without a valid '
              'final count" (never a safe close) — through SessionService',
              'data-final-count-escalation' in detail and refused.status_code == 409 and closed.status_code == 302
              and status(sid) == 'closed' and close_payload is not None
              and close_payload.payload['safe_close_confirmed'] is False
              and close_payload.payload['close_basis'] == 'final_count_escalation',
              (refused.status_code, closed.status_code, close_payload.payload if close_payload else None))

        # ===== B. contingency verified on the server ===========================================================
        sid = new_session('B (contingency)')
        report = {f'reported_{family}': '1', 'contingency_reason': 'worker_unavailable', 'contingency_notes': ''}
        while_up = op.post(f'{WEB}/operator/sessions/{sid}/manual-report', data=report, allow_redirects=False, timeout=60)
        check('B: "worker unavailable" while VisionWorker answers is refused (409); nothing recorded',
              while_up.status_code == 409 and 'vision worker is available' in while_up.text and not events(sid, 'manual_close'),
              while_up.status_code)
        docker('stop', CONTAINER)
        down = op.post(f'{WEB}/operator/sessions/{sid}/manual-report', data=report, allow_redirects=False, timeout=60)
        payload = (events(sid, 'manual_close') or [None])[-1]
        contingency = (payload.payload or {}).get('contingency') if payload else None
        check('B: with the container really stopped the same claim is VERIFIED (evidence, station, time kept); '
              'no final count',
              down.status_code == 302 and contingency and contingency['verification']['status'] == 'verified'
              and contingency['station_id'] == str(station.id) and status(sid) == 'awaiting_spd_review'
              and not [e for e in events(sid, 'auto_count') if (e.payload or {}).get('trigger') == 'final'],
              (down.status_code, contingency))
        detail = spd.get(f'{WEB}/supervisor/sessions/{sid}', timeout=60).text
        closed = spd.post(f'{WEB}/supervisor/sessions/{sid}/close', allow_redirects=False, timeout=60,
                          data={'material_recovered': '1', 'contingency_reviewed': '1'})
        close_payload = (events(sid, 'session_close') or [None])[-1]
        check('B: the Supervisor sees "verified by the system" and closes with the review only; close_basis '
              'contingency_count, verification verified',
              'data-contingency-verification="verified"' in detail and closed.status_code == 302 and close_payload
              and close_payload.payload['close_basis'] == 'contingency_count'
              and close_payload.payload['contingency_verification'] == 'verified',
              (closed.status_code, close_payload.payload if close_payload else None))
    finally:
        info('test stock restored', restore_lost(engine, sessions))
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
