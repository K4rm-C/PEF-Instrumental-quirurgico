"""MS-02 real end-to-end: model pinned per session + VisionWorker inferring with exactly those weights.

Isolated stack only (docker-compose.test.yml / .env.test). Runs:
* AuthService (:15001) and the portal (:15000) as local processes, Administration storage on the TEST Garage;
* the VisionWorker in a container from pef-ms-test/vision (current code mounted read-only), attached to the
  test network so it reaches Garage as garage:3900 (like production). It has NO bundled best.pt: the weights must
  come from Administration's storage through the portal's presigned link, verified by SHA-256;
* real inference with the provisional best.pt already registered in the test database (RF-AD-08 browser run).

Flow: Admin activates the real model → two vision sessions are scheduled (SQL test data, demo institution) →
the Operator starts A through the portal (begin) → pin + audit → the worker downloads/verifies/loads the
weights → video upload, initial count, full run, final count through the portal → live WebSocket in a real
Chromium page (ticket → bind → frames → detections) → reconnection → Admin activates ANOTHER model mid-session
→ A keeps its weights → session B (started afterwards) pins the other model, whose weights do not exist → clear
error, no substitution. Writes only test data (labelled MS02-E2E) and restores the previously active model.

    python scripts/e2e_ms02_model_pin.py
"""
import base64
import json
import os
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
import run_admin_browser_env as envctl  # noqa: E402

AUTH, WEB, VISION = 'http://127.0.0.1:15001', 'http://127.0.0.1:15000', 'http://127.0.0.1:15002'
sys.path.insert(0, str(Path(__file__).resolve().parent))
import portal_csrf  # noqa: E402  (MS-04: CSRF token for portal POSTs)
portal_csrf.enable(WEB)
CONTAINER, IMAGE, NETWORK = 'pef-ms02-e2e-vision', 'pef-ms-test/vision:latest', 'pef_admin_test_default'
# MS-03.1: Motion-JPEG tray video (scripts/make_ms031_count_videos.py): first and last frames are identical, so
# the final count equals the initial count with the real model (the old tray_full.mp4 drifted 4 -> 5 between
# its first and last frame, which the final count rule now rejects).
VIDEO, FRAME = ROOT / '.e2e' / 'videos' / 'ms031_full.avi', ROOT / '.e2e' / 'videos' / 'tray.jpg'
BEST_SHA = '6215cb746e20eb9e10c812fe03679b59263bf268b1fb0d419724611db2c785d9'
OPERATOR, ADMIN = ('operator@instrumed.com', 'DemopwdOP78!'), ('admin@instrumed.com', 'DemopwdADM78!')
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + str(detail)[:300] if detail and not ok else ''}", flush=True)
    return ok


def info(name, detail):
    print(f'INFO  {name}: {detail}', flush=True)


def docker(*args, check_rc=True):
    out = subprocess.run(['docker', *args], capture_output=True, text=True, env={**os.environ, 'MSYS_NO_PATHCONV': '1'})
    if check_rc and out.returncode != 0:
        raise RuntimeError(out.stderr[-500:])
    return out


def wait(url, label, seconds=120):
    for _ in range(seconds * 2):
        try:
            if requests.get(url, timeout=2).status_code < 500:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    sys.exit(f'{label} did not answer at {url}')


def login(email, password):
    session = requests.Session()
    response = session.post(AUTH + '/login', json={'email': email, 'password': password}, timeout=10)
    response.raise_for_status()
    session.cookies.set('access_token', response.cookies['access_token'], domain='127.0.0.1')
    return session, response.cookies['access_token']


def schedule(conn, tag):
    """A scheduled vision session for the demo Operator (test data, demo institution)."""
    q = lambda sql, **p: conn.execute(text(sql), p)  # noqa: E731
    operator, inst = q("SELECT id, institution_id FROM \"user\" WHERE email = :e", e=OPERATOR[0]).one()
    station = q('SELECT s.id, s.room_id FROM capture_station s JOIN operating_room r ON r.id = s.room_id '
                'WHERE s.active AND r.active AND r.institution_id = :i ORDER BY s.name LIMIT 1', i=inst).one()
    kit = q("SELECT id FROM kit WHERE active AND institution_id = :i AND name = 'Video Demo Kit 1'", i=inst).scalar_one()
    notice = q('SELECT id FROM privacy_notice_version WHERE active AND effective_at <= now() ORDER BY effective_at LIMIT 1').scalar_one()
    procedure = q('SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1').scalar_one()
    operation, session_id = uuid.uuid4(), uuid.uuid4()
    q('INSERT INTO operation (id, scheduled_at, status_id, procedure_type_id, room_id, institution_id) VALUES '
      "(:o, now(), (SELECT id FROM cat_operation_status WHERE code = 'scheduled'), :p, :r, :i)",
      o=operation, p=procedure, r=station.room_id, i=inst)
    q('INSERT INTO work_session (id, status_id, user_id, operation_id, station_id, kit_id, current_phase_id) VALUES '
      "(:s, (SELECT id FROM cat_session_status WHERE code = 'scheduled'), :u, :o, :st, :k, "
      "(SELECT id FROM cat_operation_phase WHERE code = 'start'))", s=session_id, u=operator, o=operation, st=station.id, k=kit)
    q("INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source) "
      "SELECT :s, family_id, quantity, 'kit_snapshot' FROM kit_item WHERE kit_id = :k", s=session_id, k=kit)
    q('INSERT INTO session_processing_agreement (session_id, privacy_notice_version_id, purpose_quality_ops, agreed_by_user_id) '
      'VALUES (:s, :n, TRUE, (SELECT id FROM "user" WHERE email = \'supervisor@instrumed.com\'))', s=session_id, n=notice)
    info(f'session {tag}', session_id)
    return session_id


def poll_state(op, sid, until, seconds=300):
    state = {}
    for _ in range(seconds):
        response = op.get(f'{WEB}/operator/sessions/{sid}/capture/state', timeout=30)
        state = (response.json() or {}).get('session') or {}
        if until(state):
            return state
        time.sleep(1)
    return state


def live_ws(admin_ctx_page, sid, frames=2):
    """Real Chromium page on the portal origin: ticket → WS bind → frames → detections (as capture-live-ws.js)."""
    frame_b64 = base64.b64encode(FRAME.read_bytes()).decode()
    return admin_ctx_page.evaluate("""async ([sid, frameB64, frames]) => {
        const t = await (await fetch(`/operator/sessions/${sid}/capture/ws-ticket`)).json();
        if (!t.ok) return {error: 'ticket', detail: t};
        const ws = new WebSocket(t.ws_url);
        const msgs = [];
        const done = new Promise((resolve) => {
            let sent = 0;
            ws.onmessage = (ev) => {
                const m = JSON.parse(ev.data); msgs.push(m);
                if (m.type === 'worker.status' && m.state === 'ready' && sent < frames) {
                    sent += 1;
                    ws.send(JSON.stringify({type: 'frame.submit', frame_id: 'f' + sent, image_base64: frameB64}));
                }
                if (m.type === 'error' || msgs.filter(x => x.type === 'detections.result').length >= frames) resolve();
            };
            ws.onclose = () => resolve();
            setTimeout(resolve, 120000);
        });
        ws.onopen = () => ws.send(JSON.stringify({type: 'session.bind', session_id: sid, auth_token: t.ticket}));
        await done;
        ws.close();
        return {msgs: msgs.map(m => ({type: m.type, code: m.code, state: m.state, message: m.message,
                                       model_version: m.model_version, model_sha256: m.model_sha256,
                                       detections: (m.detections || []).length}))};
    }""", [str(sid), frame_b64, frames])


def main():
    env = load_test_env()
    engine = create_engine(env['DATABASE_URL'], future=True)
    secret = secrets.token_hex(24)
    for path in (VIDEO, FRAME):
        if not path.exists():
            sys.exit(f'missing {path}: python scripts/make_test_tray_videos.py .e2e/videos and '
                     f'scripts/make_ms031_count_videos.py .e2e/videos (see GUIA_PRUEBAS.md)')
    with engine.connect() as c:
        best = c.execute(text('SELECT id, version_tag FROM yolo_model WHERE checksum = :c'), {'c': BEST_SHA}).first()
        original_active = c.execute(text('SELECT id FROM yolo_model WHERE active')).scalar()
        other = c.execute(text("SELECT id, version_tag FROM yolo_model WHERE version_tag = 'yolo26l-demo'")).first()
    if best is None:
        sys.exit('The provisional best.pt is not registered in the test database (run browser_validate_admin.py first).')
    info('real model', f'{best.version_tag} sha256 {BEST_SHA[:12]}…; other model {other.version_tag if other else None}')

    docker('rm', '-f', CONTAINER, check_rc=False)
    docker('run', '-d', '--name', CONTAINER, '--network', NETWORK, '-p', '127.0.0.1:15002:5002',
           '-v', f"{ROOT / 'apps' / 'VisionWorker'}:/app:ro",
           '-e', f'VISION_WS_SECRET={secret}', '-e', f'VISION_WS_ORIGINS={WEB}',
           '-e', 'VISION_MODEL_FETCH_ALLOWED_HOSTS=garage:3900', '-e', 'VISION_MODEL_CACHE_DIR=/tmp/model_cache',
           '-e', 'VISION_WEIGHTS_PATH=/models/not-mounted.pt', '-e', 'VISION_UPLOAD_DIR=/tmp/uploads',
           '-e', 'TELEMETRY_SPOOL_DIR=/tmp/spool', '-e', 'PYTHONDONTWRITEBYTECODE=1', IMAGE)
    auth_env, web_env = envctl.build_env('s3', '')
    web_env.update({'VISION_WORKER_URL': VISION, 'VISION_WS_SECRET': secret, 'VISION_WORKER_WS_URL': 'ws://127.0.0.1:15002/ws',
                    'S3_INTERNAL_ENDPOINT': 'http://garage:3900', 'ADMIN_S3_BUCKET': 'pef-admin-browser'})
    log_dir = ROOT / '.e2e'
    auth = subprocess.Popen([sys.executable, 'main.py'], cwd=ROOT / 'apps' / 'BackendAuthService', env=auth_env,
                            stdout=open(log_dir / 'ms02_auth.log', 'w'), stderr=subprocess.STDOUT)
    web = subprocess.Popen([sys.executable, '-m', 'flask', '--app', 'app', 'run', '--host', '127.0.0.1', '--port', '15000'],
                           cwd=ROOT / 'apps' / 'BackendWebFlask', env=web_env,
                           stdout=open(log_dir / 'ms02_web.log', 'w'), stderr=subprocess.STDOUT)
    try:
        wait(AUTH + '/health', 'AuthService')
        wait(WEB + '/health/live', 'portal')
        wait(VISION + '/health', 'VisionWorker', 240)
        health = requests.get(VISION + '/health', timeout=5).json()
        check('worker starts without bundled weights (nothing loaded yet)',
              health['weights_present'] is False and health['loaded_models'] == [], health)

        admin, _ = login(*ADMIN)
        activated = admin.post(f'{WEB}/admin/vision-models/{best.id}/activate', allow_redirects=False, timeout=30)
        with engine.connect() as c:
            is_active = c.execute(text('SELECT active FROM yolo_model WHERE id = :i'), {'i': best.id}).scalar()
        check('Admin activates the real model (portal)', activated.status_code == 302 and is_active, activated.status_code)

        with engine.begin() as c:
            session_a, session_b = schedule(c, 'A'), schedule(c, 'B')
        op, op_token = login(*OPERATOR)

        # --- Operator start: pin + bind -----------------------------------------------------------------
        started = time.monotonic()
        begin = op.post(f'{WEB}/operator/sessions/{session_a}/begin', data={'instruments_ready': '1'},
                        allow_redirects=False, timeout=300)
        elapsed = time.monotonic() - started
        with engine.connect() as c:
            pin = c.execute(text('SELECT yolo_model_id FROM work_session WHERE id = :s'), {'s': session_a}).scalar()
            audits = c.execute(text("SELECT count(*) FROM access_audit WHERE action = 'session.model_pinned' "
                                    "AND resource_id = :s"), {'s': session_a}).scalar_one()
        check('Operator start (portal) pins the active real model and audits it once',
              begin.status_code in (302, 303) and pin == best.id and audits == 1, (begin.status_code, pin, audits))
        health = requests.get(VISION + '/health', timeout=5).json()
        loaded = {m['sha256']: m for m in health['loaded_models']}
        check('worker downloaded the weights from Garage (presigned link), verified SHA-256 and loaded them',
              BEST_SHA in loaded and loaded[BEST_SHA]['source'] == 's3', health['loaded_models'])
        info('start + first bind (download + load)', f'{elapsed:.1f} s')

        # --- Video flow through the portal: counts only from the pinned weights ----------------------------
        capture = f'{WEB}/operator/sessions/{session_a}/capture'
        page = op.get(capture, timeout=120)
        check('capture page binds without error (no "Vision worker" warning)',
              page.status_code == 200 and '<strong>Vision worker:</strong>' not in page.text, page.status_code)
        with open(VIDEO, 'rb') as fh:
            upload = op.post(f'{capture}/upload', files={'video': (VIDEO.name, fh, 'video/x-msvideo')}, timeout=300)
        state = poll_state(op, session_a, lambda s: (s.get('initial_frame') or {}).get('ok') is not None
                           and s.get('status') != 'loading')
        initial_boxes = len((state.get('initial_frame') or {}).get('boxes') or [])
        check('first frame analysed by the pinned weights (real detections)',
              upload.status_code == 202 and state.get('model_sha256') == BEST_SHA and initial_boxes > 0,
              (upload.status_code, state.get('model_sha256'), initial_boxes))
        # The synthetic tray shows fewer pieces than the kit: the Operator confirms that count explicitly (existing
        # RF rule, unchanged by MS-02).
        initial = op.post(f'{capture}/initial', data={'acknowledge_mismatch': '1'}, timeout=60)
        check('initial count registered (portal checked the weights the worker used)', initial.status_code == 200
              and initial.json().get('ok'), initial.text[:200])
        play = op.post(f'{capture}/play', timeout=60)
        state = {}
        if play.status_code == 202:
            state = poll_state(op, session_a, lambda s: s.get('status') in ('ready', 'error')
                               and (s.get('final_frame') or {}).get('ok') is not None, seconds=600)
        # MS-03.1: no confirmation can register a final count that differs from the reference.
        final = op.post(f'{capture}/final', timeout=60)
        final_json = final.json() if final.headers.get('content-type', '').startswith('application/json') else {}
        check('full run + final count registered with the same weights', play.status_code == 202
              and state.get('model_sha256') == BEST_SHA and final.status_code == 200 and final_json.get('ok'),
              (play.status_code, final.status_code, final_json))
        with engine.connect() as c:
            events = c.execute(text("SELECT t.code, e.payload->>'model_version' FROM count_event e JOIN cat_event_type t "
                                    "ON t.id = e.event_type_id WHERE e.session_id = :s AND t.code = 'auto_count' "
                                    "AND e.family_id IS NULL"), {'s': session_a}).all()
        check('auto_count events carry the pinned model version (initial + final)',
              len(events) >= 2 and all(v == best.version_tag for _, v in events), events)

        # --- Live WebSocket in a real browser, then reconnection ------------------------------------------
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            context.add_cookies([{'name': 'access_token', 'value': op_token, 'domain': '127.0.0.1', 'path': '/'}])
            page = context.new_page()
            page.goto(f'{WEB}/operator/sessions/{session_a}/capture')
            first = live_ws(page, session_a)
            results_ws = [m for m in first.get('msgs', []) if m['type'] == 'detections.result']
            check('WebSocket live frames are inferred with the pinned weights (real detections)',
                  len(results_ws) == 2 and all(m['model_sha256'] == BEST_SHA for m in results_ws)
                  and any(m['detections'] > 0 for m in results_ws), first)
            loads_before = requests.get(VISION + '/health', timeout=5).json()['loaded_models']
            second = live_ws(page, session_a)
            results_ws = [m for m in second.get('msgs', []) if m['type'] == 'detections.result']
            loads_after = requests.get(VISION + '/health', timeout=5).json()['loaded_models']
            check('reconnection: new ticket, same weights, model reused from memory (no reload)',
                  len(results_ws) == 2 and all(m['model_sha256'] == BEST_SHA for m in results_ws)
                  and len(loads_after) == len(loads_before) == 1, (second, loads_after))

            # --- Administration activates another model mid-session -----------------------------------------
            switched = admin.post(f'{WEB}/admin/vision-models/{other.id}/activate', allow_redirects=False, timeout=30)
            page.goto(f'{WEB}/operator/sessions/{session_a}/capture')      # re-bind on page load
            third = live_ws(page, session_a, frames=1)
            results_ws = [m for m in third.get('msgs', []) if m['type'] == 'detections.result']
            with engine.connect() as c:
                pin = c.execute(text('SELECT yolo_model_id FROM work_session WHERE id = :s'), {'s': session_a}).scalar()
            check('another model activated mid-session: session A keeps its pinned weights',
                  switched.status_code == 302 and pin == best.id and results_ws
                  and results_ws[0]['model_sha256'] == BEST_SHA, (switched.status_code, third))
            browser.close()

        # --- Session B starts with the other (seed) model: no weights → clear error, no substitution ---------
        op.post(f'{WEB}/operator/sessions/{session_b}/begin', data={'instruments_ready': '1'}, allow_redirects=False, timeout=60)
        with engine.connect() as c:
            pin_b = c.execute(text('SELECT yolo_model_id FROM work_session WHERE id = :s'), {'s': session_b}).scalar()
        with open(VIDEO, 'rb') as fh:
            refused = op.post(f'{WEB}/operator/sessions/{session_b}/capture/upload',
                              files={'video': (VIDEO.name, fh, 'video/x-msvideo')}, timeout=60)
        health = requests.get(VISION + '/health', timeout=5).json()
        check('session B pins the other model; its missing weights are refused, never replaced by best.pt',
              pin_b == other.id and refused.status_code == 404 and 'weights' in refused.text.lower()
              and [m['sha256'] for m in health['loaded_models']] == [BEST_SHA], (pin_b, refused.status_code, refused.text[:200]))
    finally:
        if original_active:
            with engine.begin() as c:
                c.execute(text('UPDATE yolo_model SET active = FALSE WHERE active'))
                c.execute(text('UPDATE yolo_model SET active = TRUE WHERE id = :i'), {'i': original_active})
        logs = docker('logs', '--tail', '40', CONTAINER, check_rc=False)
        (log_dir / 'ms02_vision.log').write_text(logs.stdout + logs.stderr, encoding='utf-8')
        docker('rm', '-f', CONTAINER, check_rc=False)
        envctl.stop(web, auth)
    print(f'\n{sum(results)} PASS, {len(results) - sum(results)} FAIL  (logs: .e2e/ms02_*.log)')
    sys.exit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
