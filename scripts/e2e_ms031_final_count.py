"""MS-03.1 real end-to-end: final count = reference, no confirmation bypass, recapture, WebSocket checks.

Isolated stack only (docker-compose.test.yml / .env.test), same processes as e2e_ms03_evidence.py: AuthService
:15001, portal :15000, SessionService :15004, EvidenceService :15005, VisionWorker in a container with the
provisional best.pt (real inference). Videos: scripts/make_ms031_count_videos.py (Motion-JPEG, identical frames
decode to identical images).

Scenario A (difference): the video ends with one of the detected instruments covered → the final count differs
from the reference → rejected with and without confirmation, no final count, no human validation, no quantity
report, the SessionService API refuses it too; re-running the same video is rejected again; a new recording of
the tray (recapture) with the reference total opens human validation.
Scenario B (same total): first and last frames identical → final = reference → human validation; the validated
total cannot differ from the physical total.
Supervisor: real evidence of both sessions; rejected attempts never appear as accepted final counts.
WebSocket: a valid connection renewing its ticket works for more than 120 s; after a revocation made directly
in AuthService (not through the portal) the ticket cannot be renewed and the worker closes the connection
within the ticket lifetime (120 s).

    PYTHONIOENCODING=utf-8 python scripts/e2e_ms031_final_count.py
"""
import base64
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
from e2e_ms02_model_pin import BEST_SHA, CONTAINER, FRAME, IMAGE, NETWORK, docker, poll_state, wait  # noqa: E402

AUTH, WEB, SESSION, EVIDENCE, VISION = ('http://127.0.0.1:15001', 'http://127.0.0.1:15000', 'http://127.0.0.1:15004',
                                        'http://127.0.0.1:15005', 'http://127.0.0.1:15002')
OPERATOR, SUPERVISOR, ADMIN = (('operator@instrumed.com', 'DemopwdOP78!'), ('supervisor@instrumed.com', 'DemopwdSPD78!'),
                               ('admin@instrumed.com', 'DemopwdADM78!'))
VIDEOS = ROOT / '.e2e' / 'videos'
MASKED, FULL, ONE_REMOVED = (VIDEOS / 'ms031_masked.avi', VIDEOS / 'ms031_full.avi',
                             VIDEOS / 'ms031_one_removed.avi')
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
    return session, response.cookies['access_token'], response.cookies.get('refresh_token')


# Browser harness of the live preview (same protocol as capture-live-ws.js: ticket → bind → frames → renew),
# started asynchronously so two connections can run at the same time. A camera is not needed: the frames are
# the test JPEG. renew=false behaves like a client that keeps the socket open without a new ticket.
WS_START = """async ([sid, frameB64, renew]) => {
    const st = window.__ws = {msgs: [], opened: Date.now(), closedAt: null, renewOk: 0, renewFail: 0,
                              results: [], lastResultAt: null};
    const t = await (await fetch(`/operator/sessions/${sid}/capture/ws-ticket`)).json();
    if (!t.ok) { st.error = 'ticket'; return st; }
    const ws = new WebSocket(t.ws_url);
    let n = 0;
    ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data); st.msgs.push({type: m.type, code: m.code, state: m.state, at: Date.now()});
        if (m.type === 'detections.result') { st.results.push(Date.now()); st.lastResultAt = Date.now(); }
    };
    ws.onclose = () => { st.closedAt = Date.now(); clearInterval(st.sender); clearInterval(st.renewer); };
    ws.onopen = () => ws.send(JSON.stringify({type: 'session.bind', session_id: sid, auth_token: t.ticket}));
    st.sender = setInterval(() => { if (ws.readyState === 1) ws.send(JSON.stringify(
        {type: 'frame.submit', frame_id: 'f' + (++n), image_base64: frameB64})); }, 5000);
    if (renew) st.renewer = setInterval(async () => {
        try {
            const r = await fetch(`/operator/sessions/${sid}/capture/ws-ticket`, {redirect: 'manual'});
            const j = r.ok ? await r.json() : null;
            if (j && j.ok) { st.renewOk++; ws.send(JSON.stringify({type: 'session.renew', session_id: sid,
                                                                     auth_token: j.ticket})); }
            else st.renewFail++;
        } catch (e) { st.renewFail++; }
    }, Math.max(10, Number(t.renew_after_s) || 40) * 1000);
    window.__wsSocket = ws;
    return {started: true, ttl: t.expires_in || t.ttl_s || null, renew_after_s: t.renew_after_s};
}"""
WS_STATE = "() => { const s = window.__ws || {}; return {opened: s.opened, closedAt: s.closedAt, renewOk: s.renewOk, " \
           "renewFail: s.renewFail, results: (s.results || []).length, lastResultAt: s.lastResultAt, " \
           "errors: (s.msgs || []).filter(m => m.type === 'error').map(m => m.code)}; }"


def main():
    env = load_test_env()
    engine = create_engine(env['DATABASE_URL'], future=True)
    for path in (MASKED, FULL, ONE_REMOVED, FRAME):
        if not path.exists():
            sys.exit(f'missing {path}: build it with scripts/make_ms031_count_videos.py (see GUIA_PRUEBAS.md)')
    jwt_secret, ws_secret = secrets.token_hex(32), secrets.token_hex(24)

    def q(sql, **params):
        with engine.connect() as c:
            return c.execute(text(sql), params).all()

    def q1(sql, **params):
        rows = q(sql, **params)
        return rows[0][0] if rows else None

    best = q('SELECT id, version_tag FROM yolo_model WHERE checksum = :c', c=BEST_SHA)
    original_active = q1('SELECT id FROM yolo_model WHERE active')
    if not best:
        sys.exit('The provisional best.pt is not registered in the test database (browser_validate_admin.py).')
    best = best[0]

    docker('rm', '-f', CONTAINER, check_rc=False)
    docker('run', '-d', '--name', CONTAINER, '--network', NETWORK, '-p', '127.0.0.1:15002:5002',
           '-v', f"{ROOT / 'apps' / 'VisionWorker'}:/app:ro", '-e', f'VISION_WS_SECRET={ws_secret}',
           '-e', f'VISION_WS_ORIGINS={WEB}', '-e', 'VISION_MODEL_FETCH_ALLOWED_HOSTS=garage:3900',
           '-e', 'VISION_MODEL_CACHE_DIR=/tmp/model_cache', '-e', 'VISION_WEIGHTS_PATH=/models/not-mounted.pt',
           '-e', 'VISION_UPLOAD_DIR=/tmp/uploads', '-e', 'TELEMETRY_SPOOL_DIR=/tmp/spool', IMAGE)
    auth_env, web_env = envctl.build_env('s3', '')
    auth_env['JWT_SECRET_KEY'] = jwt_secret
    s3 = env['PEF_TEST_S3_ENDPOINT']
    outbox = ROOT / '.e2e' / f'ms031_outbox_{secrets.token_hex(3)}'
    web_env.update({'VISION_WORKER_URL': VISION, 'VISION_WS_SECRET': ws_secret,
                    'VISION_WORKER_WS_URL': 'ws://127.0.0.1:15002/ws', 'S3_INTERNAL_ENDPOINT': 'http://garage:3900',
                    'ADMIN_S3_BUCKET': 'pef-admin-browser', 'SESSION_SERVICE_URL': SESSION,
                    'EVIDENCE_SERVICE_URL': EVIDENCE, 'MONGO_URL': env['PEF_TEST_MONGO_URL'],
                    'MONGO_DB': 'pef_ms031_e2e', 'PEF_OUTBOX_DIR': str(outbox)})
    service_env = {**env, 'JWT_SECRET_KEY': jwt_secret}
    session_env = {**service_env, 'SESSION_HOST': '127.0.0.1', 'SESSION_PORT': '15004'}
    evidence_env = {**service_env, 'EVIDENCE_HOST': '127.0.0.1', 'EVIDENCE_PORT': '15005', 'S3_INTERNAL_ENDPOINT': s3,
                    'S3_PUBLIC_ENDPOINT': s3, 'S3_REGION': 'garage', 'S3_ACCESS_KEY_ID': env['PEF_TEST_S3_ACCESS_KEY'],
                    'S3_SECRET_ACCESS_KEY': env['PEF_TEST_S3_SECRET_KEY'], 'S3_BUCKET_EVIDENCE': 'pef-evidence-test',
                    'EVIDENCE_SIGNED_URL_TTL_SECONDS': '120'}
    logs = ROOT / '.e2e'
    procs = {
        'auth': subprocess.Popen([sys.executable, 'main.py'], cwd=ROOT / 'apps' / 'BackendAuthService', env=auth_env,
                                 stdout=open(logs / 'ms031_auth.log', 'w'), stderr=subprocess.STDOUT),
        'session': subprocess.Popen([sys.executable, 'main.py'], cwd=ROOT / 'apps' / 'SessionService', env=session_env,
                                    stdout=open(logs / 'ms031_session.log', 'w'), stderr=subprocess.STDOUT),
        'evidence': subprocess.Popen([sys.executable, 'main.py'], cwd=ROOT / 'apps' / 'EvidenceService',
                                     env=evidence_env, stdout=open(logs / 'ms031_evidence.log', 'w'),
                                     stderr=subprocess.STDOUT),
        'web': subprocess.Popen([sys.executable, '-m', 'flask', '--app', 'app', 'run', '--host', '127.0.0.1',
                                 '--port', '15000'], cwd=ROOT / 'apps' / 'BackendWebFlask', env=web_env,
                                stdout=open(logs / 'ms031_web.log', 'w'), stderr=subprocess.STDOUT),
    }
    try:
        for url, label in ((AUTH + '/health', 'AuthService'), (SESSION + '/health/live', 'SessionService'),
                           (EVIDENCE + '/health/live', 'EvidenceService'), (WEB + '/health/live', 'portal'),
                           (VISION + '/health', 'VisionWorker')):
            wait(url, label, 240)
        admin, _, _ = login(*ADMIN)
        admin.post(f'{WEB}/admin/vision-models/{best.id}/activate', allow_redirects=False, timeout=30)
        spd, spd_token, _ = login(*SUPERVISOR)
        op, op_token, _ = login(*OPERATOR)

        operator_id, inst = q('SELECT id, institution_id FROM "user" WHERE email = :e', e=OPERATOR[0])[0]
        station = q('SELECT s.id, s.room_id FROM capture_station s JOIN operating_room r ON r.id = s.room_id '
                    'WHERE s.active AND r.active AND r.institution_id = :i ORDER BY s.name LIMIT 1', i=inst)[0]
        kit = q1("SELECT id FROM kit WHERE active AND institution_id = :i AND name = 'Video Demo Kit 1'", i=inst)
        family = q1('SELECT ki.family_id FROM kit_item ki WHERE ki.kit_id = :k ORDER BY ki.family_id LIMIT 1', k=kit)
        physician = q1('SELECT id FROM physician WHERE institution_id = :i AND active LIMIT 1', i=inst)
        notice = q1('SELECT id FROM privacy_notice_version WHERE active AND effective_at <= now() '
                    'ORDER BY effective_at LIMIT 1')
        procedure = q1('SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1')

        def new_session(label):
            body = {'operator_user_id': str(operator_id), 'procedure_type_id': str(procedure),
                    'room_id': str(station.room_id), 'station_id': str(station.id), 'kit_id': str(kit),
                    'scheduled_at': '2026-11-04T19:00:00Z', 'phase_code': 'start',
                    'physicians': [{'physician_id': str(physician)}], 'privacy_notice_version_id': str(notice),
                    'expected_lines': [{'family_id': str(family), 'expected_quantity': 1}]}
            response = requests.post(SESSION + '/api/v1/sessions/schedule', json=body, timeout=30,
                                     headers={'Authorization': f'Bearer {spd_token}'})
            sid = response.json()['id']
            op.post(f'{WEB}/operator/sessions/{sid}/begin', data={'instruments_ready': '1'}, allow_redirects=False,
                    timeout=300)
            op.get(f'{WEB}/operator/sessions/{sid}/capture', timeout=120)
            info(f'session {label}', sid)
            return sid

        def upload(sid, video):
            capture = f'{WEB}/operator/sessions/{sid}/capture'
            with open(video, 'rb') as fh:
                response = op.post(f'{capture}/upload', files={'video': (video.name, fh, 'video/x-msvideo')},
                                   timeout=300)
            state = poll_state(op, sid, lambda s: s.get('status') != 'loading'
                               and (s.get('initial_frame') or {}).get('ok') is not None)
            return response, state

        def play(sid):
            response = op.post(f'{WEB}/operator/sessions/{sid}/capture/play', timeout=60)
            state = poll_state(op, sid, lambda s: s.get('status') in ('ready', 'error')
                               and (s.get('final_frame') or {}).get('ok') is not None, seconds=600)
            return response, state

        def final(sid, **form):
            return op.post(f'{WEB}/operator/sessions/{sid}/capture/final', data=form, timeout=60)

        def events(sid, code):
            return q("SELECT e.detected_quantity, e.expected_quantity, e.payload FROM count_event e JOIN cat_event_type t "
                     "ON t.id = e.event_type_id WHERE e.session_id = :s AND t.code = :c ORDER BY e.occurred_at",
                     s=sid, c=code)

        def finals(sid):
            return [row for row in events(sid, 'auto_count') if (row.payload or {}).get('trigger') == 'final']

        def status(sid):
            return q1('SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id '
                      'WHERE w.id = :s', s=sid)

        def validation_form(sid):
            html = op.get(f'{WEB}/operator/sessions/{sid}/vision-validation', timeout=30).text
            values = {k: int(v) for k, v in re.findall(r'name="(validated_[0-9a-f-]{36})"[^>]*value="(\d+)"', html)}
            return values

        def validate(sid, values, extra, note='MS-03.1 E2E validation'):
            form = {k: str(v) for k, v in values.items()}
            for key, value in values.items():
                fid = key.split('_', 1)[1]
                form[f'reason_{fid}'] = 'surplus' if value > 1 else ('shortage' if value < 1 else '')
                form[f'notes_{fid}'] = note
            form['extra_instruments'] = str(extra)
            form['extra_notes'] = note
            return op.post(f'{WEB}/operator/sessions/{sid}/vision-validation', data=form, allow_redirects=False,
                           timeout=60)

        # ============ Scenario A: initial ≠ final (one more instrument uncovered at the end) ==================
        sid_a = new_session('A (one instrument removed at the end)')
        _, state = upload(sid_a, ONE_REMOVED)
        initial_a = len((state.get('initial_frame') or {}).get('boxes') or [])
        registered = op.post(f'{WEB}/operator/sessions/{sid_a}/capture/initial', data={'acknowledge_mismatch': '1'},
                             timeout=60)   # kit rule (first frame vs kit), not the final count rule
        _, state = play(sid_a)
        final_a = len((state.get('final_frame') or {}).get('boxes') or [])
        info('A: real inference', f'initial (first frame) {initial_a}, last frame {final_a}, model '
                                  f'{state.get("model_sha256", "")[:12]}')
        real_difference = final_a != initial_a
        check('A: the real model sees a different total in the last frame (the scenario exists)',
              registered.status_code == 200 and real_difference and state.get('model_sha256') == BEST_SHA,
              (registered.text[:150], initial_a, final_a))
        if not real_difference:
            raise SystemExit('Scenario A needs a different total in the last frame; the provisional model did not '
                             'produce one (unstable detections): see the deterministic tests.')
        rejected = final(sid_a)
        check(f'A: final count {final_a} vs reference {initial_a} is rejected (409, capture again)',
              rejected.status_code == 409 and 'must match the reference exactly' in rejected.json().get('error', ''),
              rejected.text[:200])
        bypass = final(sid_a, acknowledge_mismatch='1')
        check('A: the explicit confirmation cannot skip the rule (409 again)', bypass.status_code == 409, bypass.text[:200])
        blocked = events(sid_a, 'close_blocked')
        check('A: the rejected attempt is kept (close_blocked final_count_mismatch), no final count registered',
              len(blocked) == 1 and blocked[0].payload.get('reason') == 'final_count_mismatch'
              and (blocked[0].detected_quantity, blocked[0].expected_quantity) == (final_a, initial_a)
              and finals(sid_a) == [] and status(sid_a) == 'in_progress', (blocked, finals(sid_a), status(sid_a)))
        page = op.get(f'{WEB}/operator/sessions/{sid_a}/vision-validation', allow_redirects=False, timeout=30)
        posted = validate(sid_a, {f'validated_{family}': 1}, max(0, final_a - 1))
        check('A: human validation is closed (page redirects, a direct POST registers nothing)',
              page.status_code == 302 and not events(sid_a, 'manual_close') and status(sid_a) == 'in_progress',
              (page.status_code, posted.status_code))
        report = op.post(f'{WEB}/operator/sessions/{sid_a}/manual-report', data={
            f'qty_{family}': '1', f'reported_{family}': '1', 'contingency_reason': 'worker_unavailable',
            'contingency_notes': 'Trying to skip the rejected count'}, allow_redirects=False, timeout=30)
        check('A: the quantity report cannot replace the rejected final count',
              not events(sid_a, 'manual_close') and status(sid_a) == 'in_progress', report.status_code)
        direct = requests.post(f'{SESSION}/api/v1/sessions/{sid_a}/vision-counts', timeout=30,
                               headers={'Authorization': f'Bearer {op_token}'},
                               json={'client_event_id': str(__import__('uuid').uuid4()),
                                     'occurred_at': '2026-11-04T19:30:00Z', 'model_version': best.version_tag,
                                     'trigger': 'final', 'counts': [{'family_id': str(family),
                                                                     'detected_quantity': final_a}]})
        # SessionService keeps the Operator writes disabled by default (410, the counting flow runs in the portal);
        # when SESSION_OPERATOR_WRITES_ENABLED is on, the same rule answers 409 final_count_mismatch (unit tests).
        direct_reason = (direct.json().get('details') or {}).get('reason') if direct.status_code == 409 else None
        check('A: a direct SessionService API call cannot register the different final count '
              f'({direct.status_code} {direct.json().get("error")})',
              (direct.status_code == 410 or direct_reason == 'final_count_mismatch') and finals(sid_a) == [],
              direct.text[:200])
        state_view = op.get(f'{WEB}/operator/sessions/{sid_a}/capture/state', timeout=30).json()
        check('A: the capture page is told to capture again (recapture step)',
              (state_view.get('pef') or {}).get('recapture', {}).get('reference') == initial_a, state_view.get('pef'))
        _, state = play(sid_a)   # repeat the capture with the same video: same result, rejected again
        again = final(sid_a)
        check('A: repeating the capture with the same video is rejected again (no final count)',
              again.status_code == 409 and finals(sid_a) == [], again.text[:200])
        uploaded, state = upload(sid_a, MASKED)   # a new recording of the tray as it was at the start
        _, state = play(sid_a)
        recaptured = len((state.get('final_frame') or {}).get('boxes') or [])
        accepted = final(sid_a)
        info('A: recapture', f'new recording accepted ({uploaded.status_code}), last frame {recaptured}')
        check('A: after the recapture with the reference total the final count is accepted',
              uploaded.status_code == 202 and recaptured == initial_a and accepted.status_code == 200
              and [row.detected_quantity for row in finals(sid_a)] == [initial_a]
              and finals(sid_a)[0].payload.get('recapture') is True, (uploaded.status_code, recaptured, accepted.text[:200]))
        values = validation_form(sid_a)
        changed = validate(sid_a, values, max(0, initial_a - sum(values.values())) + 1)
        check('A: validation cannot add an instrument to the physical total (400)',
              changed.status_code == 400 and 'only corrects the classification' in changed.text
              and not events(sid_a, 'manual_close'), changed.status_code)
        done = validate(sid_a, values, initial_a - sum(values.values()))
        human = sum(row.detected_quantity for row in events(sid_a, 'manual_count'))
        manual_close = events(sid_a, 'manual_close')
        extra = (manual_close[-1].payload or {}).get('extra_instruments', 0) if manual_close else None
        check('A: human validation with the same physical total goes through',
              done.status_code == 302 and manual_close and human + int(extra or 0) == initial_a,
              (done.status_code, human, extra))

        # ============ Scenario B: initial = final (identical first and last frames) ==========================
        sid_b = new_session('B (masked from start to end)')
        _, state = upload(sid_b, MASKED)
        initial_b = len((state.get('initial_frame') or {}).get('boxes') or [])
        op.post(f'{WEB}/operator/sessions/{sid_b}/capture/initial', data={'acknowledge_mismatch': '1'}, timeout=60)
        _, state = play(sid_b)
        final_b = len((state.get('final_frame') or {}).get('boxes') or [])
        info('B: real inference', f'initial {initial_b}, last frame {final_b}')
        confirmed = final(sid_b)
        check(f'B: final count {final_b} = reference {initial_b} is accepted without any confirmation',
              final_b == initial_b and confirmed.status_code == 200 and not events(sid_b, 'close_blocked'),
              confirmed.text[:200])
        page = op.get(f'{WEB}/operator/sessions/{sid_b}/vision-validation', allow_redirects=False, timeout=30)
        check('B: human validation is available', page.status_code == 200, page.status_code)

        # ============ WebSocket: > 120 s with renewal; direct AuthService revocation ===========================
        from playwright.sync_api import sync_playwright
        frame_b64 = base64.b64encode(FRAME.read_bytes()).decode()
        op2, op2_token, op2_refresh = login(*OPERATOR)   # a second, independent sign-in of the Operator
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx_valid, ctx_revoked = browser.new_context(), browser.new_context()
            ctx_valid.add_cookies([{'name': 'access_token', 'value': op_token, 'domain': '127.0.0.1', 'path': '/'}])
            ctx_revoked.add_cookies([{'name': 'access_token', 'value': op2_token, 'domain': '127.0.0.1', 'path': '/'},
                                     {'name': 'refresh_token', 'value': op2_refresh, 'domain': '127.0.0.1',
                                      'path': '/'}])
            page_valid, page_revoked = ctx_valid.new_page(), ctx_revoked.new_page()
            for page in (page_valid, page_revoked):
                page.goto(f'{WEB}/operator/sessions/{sid_b}/capture/state')
            started = page_valid.evaluate(WS_START, [sid_b, frame_b64, True])
            page_revoked.evaluate(WS_START, [sid_b, frame_b64, True])
            time.sleep(8)
            # Revocation made directly in AuthService (the portal is not involved, so it cannot notify the worker).
            revoked_at = time.time()
            logout = requests.post(AUTH + '/logout', timeout=10, headers={'Authorization': f'Bearer {op2_token}'},
                                   cookies={'refresh_token': op2_refresh})
            info('direct AuthService logout', logout.status_code)
            closed = None
            for _ in range(160):          # ~160 s
                time.sleep(1)
                revoked_state = page_revoked.evaluate(WS_STATE)
                if revoked_state['closedAt'] and closed is None:
                    closed = time.time() - revoked_at
                valid_state = page_valid.evaluate(WS_STATE)
                if closed is not None and (time.time() - valid_state['opened'] / 1000) > 135:
                    break
            valid_state = page_valid.evaluate(WS_STATE)
            lifetime = (time.time() * 1000 - valid_state['opened']) / 1000
            fresh_result = valid_state['lastResultAt'] and (time.time() * 1000 - valid_state['lastResultAt']) < 15000
            check('WS: a valid connection renews its ticket and keeps working for more than 120 s',
                  started.get('started') and valid_state['closedAt'] is None and lifetime > 120
                  and valid_state['renewOk'] >= 2 and fresh_result, (started, valid_state, round(lifetime)))
            check('WS: after a direct AuthService revocation the ticket cannot be renewed and the worker closes '
                  'the connection within the ticket lifetime (≤ 120 s)',
                  logout.ok and closed is not None and closed <= 125 and revoked_state['renewFail'] >= 1
                  and 'session_expired' in revoked_state['errors'], (round(closed or -1), revoked_state))
            info('WS', f'valid connection {round(lifetime)} s, renewals {valid_state["renewOk"]}; revoked connection '
                       f'closed {round(closed or -1)} s after the AuthService logout ({revoked_state["errors"]})')
            browser.close()

        done_b = validate(sid_b, validation_form(sid_b), 0)
        values = validation_form(sid_b) if done_b.status_code != 302 else {}
        if done_b.status_code == 400:   # assigned + unassigned: account for every detection of the last frame
            done_b = validate(sid_b, values, initial_b - sum(values.values()))
        human_b = sum(row.detected_quantity for row in events(sid_b, 'manual_count'))
        close_b = events(sid_b, 'manual_close')
        extra_b = int((close_b[-1].payload or {}).get('extra_instruments') or 0) if close_b else 0
        check('B: validation keeps the physical total (validated + outside the kit = final count)',
              done_b.status_code == 302 and human_b + extra_b == final_b, (done_b.status_code, human_b, extra_b))

        # ============ Supervisor: real evidence; rejected attempts are not accepted final counts ==============
        time.sleep(5)
        detail = spd.get(f'{WEB}/supervisor/sessions/{sid_a}', timeout=60).text
        captures = re.findall(r'class="v3-evidence__title">([^<·]+)', detail)
        ai_rows = re.findall(r'<td>[^<]*<code>(auto_count|close_blocked)</code></td>\s*<td[^>]*>([^<]*)</td>\s*'
                             r'<td[^>]*>([^<]*)</td>', detail)
        info('Supervisor A: captures', [c.strip() for c in captures])
        info('Supervisor A: AI counts (code, reference, detected)', ai_rows)
        accepted_final = [row for row in finals(sid_a)]
        check('Supervisor: Session Details shows the real evidence of the session (images, model)',
              'data-evidence-traceability' in detail and len(captures) >= 2 and BEST_SHA[:12] in detail,
              (len(captures), BEST_SHA[:12] in detail))
        check('Supervisor: rejected attempts are listed as close_blocked, never as an accepted final count',
              len(accepted_final) == 1 and accepted_final[0].detected_quantity == initial_a
              and sum(1 for code, _, det in ai_rows if code == 'close_blocked' and det.strip() == str(final_a)) >= 1
              and sum(1 for c in captures if c.strip() == 'Final count') == 1, (ai_rows, captures))
    finally:
        for proc in procs.values():
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
