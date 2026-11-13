"""MS-03 real end-to-end: the Operator's real evidence reaches the Supervisor's screens; live WS revocation.

Isolated stack only (docker-compose.test.yml / .env.test). Real processes: AuthService :15001, portal :15000
(SESSION_SERVICE_URL, EVIDENCE_SERVICE_URL, MONGO_URL of the test stack), SessionService :15004,
EvidenceService :15005 (Garage of the test stack, bucket pef-evidence-test), VisionWorker in a container
(pef-ms-test/vision, current code read-only) with the provisional best.pt registered in the test database.

Flow: the Supervisor schedules a vision session through SessionService → the Operator starts it, uploads the
tray video, registers initial and final counts (the final count must equal the reference, MS-03.1) and validates →
the portal's outbox writes the DS06 checkpoints to MongoDB and the JPEGs to EvidenceService → the Supervisor opens
Session Details and Review Detail in Chromium: the images load through the authorized portal route, YOLO boxes,
model and counts match the Operator's events. Negative cases with the real services. Live WS: reconnection and
revocation (sign-out) while the connection is open. Only test data; the previously active model is restored.

    python scripts/e2e_ms03_evidence.py
"""
import json
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests
from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_test_env import load_test_env  # noqa: E402
import run_admin_browser_env as envctl  # noqa: E402
from e2e_ms02_model_pin import (BEST_SHA, CONTAINER, IMAGE, NETWORK, VIDEO, FRAME, docker, live_ws,  # noqa: E402
                                poll_state, wait)

AUTH, WEB, SESSION, EVIDENCE, VISION = ('http://127.0.0.1:15001', 'http://127.0.0.1:15000', 'http://127.0.0.1:15004',
                                        'http://127.0.0.1:15005', 'http://127.0.0.1:15002')
OPERATOR, SUPERVISOR, ADMIN = (('operator@instrumed.com', 'DemopwdOP78!'), ('supervisor@instrumed.com', 'DemopwdSPD78!'),
                               ('admin@instrumed.com', 'DemopwdADM78!'))
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + str(detail)[:300] if detail and not ok else ''}", flush=True)
    return ok


def info(name, detail):
    print(f'INFO  {name}: {detail}', flush=True)


def login(email, password):
    session = requests.Session()
    response = session.post(AUTH + '/login', json={'email': email, 'password': password}, timeout=10)
    response.raise_for_status()
    session.cookies.set('access_token', response.cookies['access_token'], domain='127.0.0.1')
    return session, response.cookies['access_token'], response.cookies.get('refresh_token')


def compose(*args):
    return subprocess.run(['docker', 'compose', '-p', 'pef_admin_test', '-f', 'docker-compose.test.yml',
                           '--env-file', '.env.test', *args], cwd=ROOT, capture_output=True, text=True, timeout=300)


def main():
    env = load_test_env()
    engine = create_engine(env['DATABASE_URL'], future=True)
    jwt_secret, ws_secret = secrets.token_hex(32), secrets.token_hex(24)
    q = lambda sql, **p: engine.connect().execute(text(sql), p)  # noqa: E731
    best = q('SELECT id, version_tag FROM yolo_model WHERE checksum = :c', c=BEST_SHA).first()
    original_active = q('SELECT id FROM yolo_model WHERE active').scalar()
    if best is None:
        sys.exit('The provisional best.pt is not registered in the test database (browser_validate_admin.py).')

    # --- processes -------------------------------------------------------------------------------------------
    docker('rm', '-f', CONTAINER, check_rc=False)
    docker('run', '-d', '--name', CONTAINER, '--network', NETWORK, '-p', '127.0.0.1:15002:5002',
           '-v', f"{ROOT / 'apps' / 'VisionWorker'}:/app:ro", '-e', f'VISION_WS_SECRET={ws_secret}',
           '-e', f'VISION_WS_ORIGINS={WEB}', '-e', 'VISION_MODEL_FETCH_ALLOWED_HOSTS=garage:3900',
           '-e', 'VISION_MODEL_CACHE_DIR=/tmp/model_cache', '-e', 'VISION_WEIGHTS_PATH=/models/not-mounted.pt',
           '-e', 'VISION_UPLOAD_DIR=/tmp/uploads', '-e', 'TELEMETRY_SPOOL_DIR=/tmp/spool', IMAGE)
    auth_env, web_env = envctl.build_env('s3', '')
    auth_env['JWT_SECRET_KEY'] = jwt_secret
    s3 = env['PEF_TEST_S3_ENDPOINT']
    outbox = ROOT / '.e2e' / f'ms03_outbox_{secrets.token_hex(3)}'
    web_env.update({'VISION_WORKER_URL': VISION, 'VISION_WS_SECRET': ws_secret, 'VISION_WORKER_WS_URL': 'ws://127.0.0.1:15002/ws',
                    'S3_INTERNAL_ENDPOINT': 'http://garage:3900', 'ADMIN_S3_BUCKET': 'pef-admin-browser',
                    'SESSION_SERVICE_URL': SESSION, 'EVIDENCE_SERVICE_URL': EVIDENCE,
                    'MONGO_URL': env['PEF_TEST_MONGO_URL'], 'MONGO_DB': 'pef_ms03_e2e', 'PEF_OUTBOX_DIR': str(outbox)})
    service_env = {k: v for k, v in env.items()}
    service_env['JWT_SECRET_KEY'] = jwt_secret
    session_env = {**service_env, 'SESSION_HOST': '127.0.0.1', 'SESSION_PORT': '15004'}
    evidence_env = {**service_env, 'EVIDENCE_HOST': '127.0.0.1', 'EVIDENCE_PORT': '15005', 'S3_INTERNAL_ENDPOINT': s3,
                    'S3_PUBLIC_ENDPOINT': s3, 'S3_REGION': 'garage', 'S3_ACCESS_KEY_ID': env['PEF_TEST_S3_ACCESS_KEY'],
                    'S3_SECRET_ACCESS_KEY': env['PEF_TEST_S3_SECRET_KEY'], 'S3_BUCKET_EVIDENCE': 'pef-evidence-test',
                    'EVIDENCE_SIGNED_URL_TTL_SECONDS': '120'}
    logs = ROOT / '.e2e'
    procs = {
        'auth': subprocess.Popen([sys.executable, 'main.py'], cwd=ROOT / 'apps' / 'BackendAuthService', env=auth_env,
                                 stdout=open(logs / 'ms03_auth.log', 'w'), stderr=subprocess.STDOUT),
        'session': subprocess.Popen([sys.executable, 'main.py'], cwd=ROOT / 'apps' / 'SessionService', env=session_env,
                                    stdout=open(logs / 'ms03_session.log', 'w'), stderr=subprocess.STDOUT),
        'evidence': subprocess.Popen([sys.executable, 'main.py'], cwd=ROOT / 'apps' / 'EvidenceService', env=evidence_env,
                                     stdout=open(logs / 'ms03_evidence.log', 'w'), stderr=subprocess.STDOUT),
        'web': subprocess.Popen([sys.executable, '-m', 'flask', '--app', 'app', 'run', '--host', '127.0.0.1', '--port', '15000'],
                                cwd=ROOT / 'apps' / 'BackendWebFlask', env=web_env,
                                stdout=open(logs / 'ms03_web.log', 'w'), stderr=subprocess.STDOUT),
    }
    created = {}
    try:
        for url, label in ((AUTH + '/health', 'AuthService'), (SESSION + '/health/live', 'SessionService'),
                           (EVIDENCE + '/health/live', 'EvidenceService'), (WEB + '/health/live', 'portal'),
                           (VISION + '/health', 'VisionWorker')):
            wait(url, label, 240)
        admin, _, _ = login(*ADMIN)
        admin.post(f'{WEB}/admin/vision-models/{best.id}/activate', allow_redirects=False, timeout=30)
        spd, spd_token, spd_refresh = login(*SUPERVISOR)

        # --- 1. the Supervisor schedules through SessionService ------------------------------------------
        operator_id, inst = q('SELECT id, institution_id FROM "user" WHERE email = :e', e=OPERATOR[0]).one()
        station = q('SELECT s.id, s.room_id FROM capture_station s JOIN operating_room r ON r.id = s.room_id WHERE s.active '
                    'AND r.active AND r.institution_id = :i ORDER BY s.name LIMIT 1', i=inst).one()
        kit = q("SELECT id FROM kit WHERE active AND institution_id = :i AND name = 'Video Demo Kit 1'", i=inst).scalar_one()
        family = q('SELECT ki.family_id FROM kit_item ki WHERE ki.kit_id = :k ORDER BY ki.family_id LIMIT 1', k=kit).scalar_one()
        physician = q('SELECT id FROM physician WHERE institution_id = :i AND active LIMIT 1', i=inst).scalar_one()
        notice = q('SELECT id FROM privacy_notice_version WHERE active AND effective_at <= now() ORDER BY effective_at LIMIT 1').scalar_one()
        procedure = q('SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1').scalar_one()
        body = {'operator_user_id': str(operator_id), 'procedure_type_id': str(procedure), 'room_id': str(station.room_id),
                'station_id': str(station.id), 'kit_id': str(kit), 'scheduled_at': '2026-11-04T18:00:00Z', 'phase_code': 'start',
                'physicians': [{'physician_id': str(physician)}], 'privacy_notice_version_id': str(notice),
                'expected_lines': [{'family_id': str(family), 'expected_quantity': 1}]}
        scheduled = requests.post(SESSION + '/api/v1/sessions/schedule', json=body, timeout=30,
                                  headers={'Authorization': f'Bearer {spd_token}'})
        check('Supervisor schedules a vision session (SessionService)', scheduled.status_code == 201, scheduled.text[:200])
        sid = scheduled.json()['id']
        created['session'] = sid
        info('session', sid)

        # --- 2-4. Operator: start, capture with the real model, counts, validation -----------------------
        op, op_token, _ = login(*OPERATOR)
        op.post(f'{WEB}/operator/sessions/{sid}/begin', data={'instruments_ready': '1'}, allow_redirects=False, timeout=300)
        capture = f'{WEB}/operator/sessions/{sid}/capture'
        op.get(capture, timeout=120)
        with open(VIDEO, 'rb') as fh:
            op.post(f'{capture}/upload', files={'video': (VIDEO.name, fh, 'video/x-msvideo')}, timeout=300)
        poll_state(op, sid, lambda s: (s.get('initial_frame') or {}).get('ok') is not None and s.get('status') != 'loading')
        initial = op.post(f'{capture}/initial', data={'acknowledge_mismatch': '1'}, timeout=60)
        op.post(f'{capture}/play', timeout=60)
        poll_state(op, sid, lambda s: s.get('status') in ('ready', 'error') and (s.get('final_frame') or {}).get('ok') is not None,
                   seconds=600)
        final = op.post(f'{capture}/final', timeout=60)   # MS-03.1: no confirmation fallback
        check('Operator: real inference, initial and final counts registered', initial.status_code == 200
              and final.status_code == 200, (initial.text[:150], final.text[:150]))
        # The portal's outbox stores DS06 checkpoints (MongoDB) and their JPEGs (EvidenceService) asynchronously.
        from pymongo import MongoClient
        mongo = MongoClient(env['PEF_TEST_MONGO_URL'], serverSelectionTimeoutMS=3000)['pef_ms03_e2e']
        checkpoints = []
        for _ in range(90):
            checkpoints = list(mongo['checkpoint_frame'].find({'session_id': sid}).sort('session_checkpoint_seq', 1))
            if len(checkpoints) >= 2 and all(c.get('media_asset_id') for c in checkpoints):
                break
            time.sleep(1)
        media_ids = [c.get('media_asset_id') for c in checkpoints]
        listed = requests.get(f'{EVIDENCE}/api/v1/sessions/{sid}/evidence', timeout=10,
                              headers={'Authorization': f'Bearer {spd_token}'}).json()
        listed_items = listed.get('evidence_list', listed) if isinstance(listed, dict) else listed
        evidence_ids = {item['id'] for item in (listed_items or [])}
        check('evidence recorded: DS06 checkpoints (MongoDB) with JPEGs stored in EvidenceService/Garage',
              len(checkpoints) >= 2 and all(media_ids) and set(media_ids) <= evidence_ids,
              (len(checkpoints), media_ids, evidence_ids))
        check('checkpoints carry the pinned model version', all(c.get('model_version') == best.version_tag for c in checkpoints),
              [c.get('model_version') for c in checkpoints])

        # --- 5-9. Supervisor screens in a real browser -----------------------------------------------------
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            context.add_cookies([{'name': 'access_token', 'value': spd_token, 'domain': '127.0.0.1', 'path': '/'}])
            page = context.new_page()
            page.goto(f'{WEB}/supervisor/discrepancies/{sid}/review')
            page.wait_for_load_state('networkidle')
            page.locator('[data-evidence-captures]').scroll_into_view_if_needed()   # images are loading="lazy"
            page.wait_for_function("() => [...document.querySelectorAll('img[data-evidence-image]')].every(i => i.complete)",
                                   timeout=30000)
            figures = page.locator('[data-evidence-figure]')
            asset_ids = figures.evaluate_all('fs => fs.map(f => f.dataset.mediaAssetId)')
            loaded = page.evaluate("() => [...document.querySelectorAll('img[data-evidence-image]')]"
                                   ".map(i => i.complete && i.naturalWidth > 0)")
            boxes = page.locator('[data-evidence-overlay] .v3-evidence__box:not([hidden])').count()
            html = page.content()
            ai_rows = page.locator('[data-evidence-ai-counts] tbody tr').count()
            page.screenshot(path=str(logs / 'ms03_review_evidence.png'), full_page=True)
            check('Review Detail shows the session\'s real images (loaded through the authorized route)',
                  sorted(a for a in asset_ids if a) == sorted(media_ids) and loaded and all(loaded), (asset_ids, loaded))
            inside = page.evaluate('''() => [...document.querySelectorAll('img[data-evidence-image]')].every(img => {
                const ov = img.parentElement.querySelector('[data-evidence-overlay]'); if (!ov) return true;
                const r = img.getBoundingClientRect(), s = Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight);
                const pic = {l: r.left + (r.width - img.naturalWidth * s) / 2, t: r.top + (r.height - img.naturalHeight * s) / 2};
                pic.r = pic.l + img.naturalWidth * s; pic.b = pic.t + img.naturalHeight * s;
                return [...ov.querySelectorAll('.v3-evidence__box:not([hidden])')].every(b => { const x = b.getBoundingClientRect();
                    return x.left >= pic.l - 1 && x.top >= pic.t - 1 && x.right <= pic.r + 1 && x.bottom <= pic.b + 1; }); })''')
            check('YOLO boxes placed over the real images, inside the drawn picture', boxes > 0 and inside, (boxes, inside))
            check('model version and weights SHA-256 shown; AI counts listed; no demo data',
                  best.version_tag in html and BEST_SHA[:12] in html and ai_rows >= 2 and 'WS-026' not in html, ai_rows)
            db_counts = q("SELECT e.detected_quantity FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
                          "WHERE e.session_id = :s AND t.code = 'auto_count' AND e.family_id IS NULL "
                          "AND e.payload->>'reason' IN ('detection_initial', 'detection_close') ORDER BY e.occurred_at", s=sid).scalars().all()
            page_counts = page.locator('[data-evidence-ai-counts] tbody tr').evaluate_all(
                "rows => rows.filter(r => /detection_(initial|close)/.test(r.innerText)).map(r => Number(r.cells[3].innerText))")
            check('counts on screen are the Operator\'s registered counts', page_counts == db_counts, (page_counts, db_counts))
            audited = q("SELECT count(*) FROM access_audit WHERE action = 'evidence.download_url' AND actor_user_id = "
                        "(SELECT id FROM \"user\" WHERE email = :e) AND occurred_at > now() - interval '10 minutes'",
                        e=SUPERVISOR[0]).scalar_one()
            check('each image link was audited by EvidenceService (evidence.download_url)', audited >= len(media_ids), audited)
            page.goto(f'{WEB}/supervisor/sessions/{sid}')
            page.click('#tab-timeline')
            page.wait_for_timeout(1500)
            timeline_loaded = page.evaluate("() => [...document.querySelectorAll('#tab-panel-timeline img[data-evidence-image]')]"
                                            ".filter(i => i.naturalWidth > 0).length")
            check('Session Details → Timeline shows the same evidence', timeline_loaded == len(media_ids), timeline_loaded)

            # --- negative cases with the real services -------------------------------------------------------
            image = lambda s, a: f'{WEB}/supervisor/sessions/{s}/evidence/{a}/image'  # noqa: E731
            check('unknown session → 404', spd.get(f'{WEB}/supervisor/sessions/{uuid.uuid4()}', allow_redirects=False).status_code == 404
                  and spd.get(image(uuid.uuid4(), media_ids[0]), allow_redirects=False).status_code == 404)
            check('unknown evidence → 404', spd.get(image(sid, uuid.uuid4()), allow_redirects=False).status_code == 404)
            check('operator and administrator cannot use the Supervisor evidence route (403)',
                  op.get(image(sid, media_ids[0]), allow_redirects=False).status_code == 403
                  and admin.get(image(sid, media_ids[0]), allow_redirects=False).status_code == 403)
            # Supervisor of ANOTHER institution (test data, demo-free institution).
            inst_b = q("SELECT id FROM institution WHERE name = 'ZZ Browser Institution B'").scalar()
            if inst_b:
                password = 'Ms03-' + secrets.token_urlsafe(10)
                with engine.begin() as c:
                    role = c.execute(text("SELECT id FROM role WHERE institution_id = :i AND code = 'spd_supervisor'"), {'i': inst_b}).scalar_one()
                    uid = c.execute(text('SELECT id FROM "user" WHERE email = :e'), {'e': 'spd.b@ms03-test.invalid'}).scalar()
                    if uid is None:
                        uid = c.execute(text('INSERT INTO "user" (name, email, password_hash, institution_id) VALUES '
                                             "('SPD B (MS-03 test)', 'spd.b@ms03-test.invalid', :h, :i) RETURNING id"),
                                        {'h': generate_password_hash(password), 'i': inst_b}).scalar_one()
                        c.execute(text('INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)'), {'u': uid, 'r': role})
                    else:
                        c.execute(text('UPDATE "user" SET password_hash = :h WHERE id = :u'), {'h': generate_password_hash(password), 'u': uid})
                foreign, _, _ = login('spd.b@ms03-test.invalid', password)
                check('Supervisor of another institution: session 404, evidence 404 (EvidenceService scope)',
                      foreign.get(f'{WEB}/supervisor/sessions/{sid}', allow_redirects=False).status_code == 404
                      and foreign.get(image(sid, media_ids[0]), allow_redirects=False).status_code == 404)
            # Missing file: the object of the last checkpoint is deleted from the TEST Garage bucket.
            import boto3
            from botocore.config import Config
            s3c = boto3.client('s3', endpoint_url=s3, region_name='garage', aws_access_key_id=env['PEF_TEST_S3_ACCESS_KEY'],
                               aws_secret_access_key=env['PEF_TEST_S3_SECRET_KEY'], config=Config(s3={'addressing_style': 'path'}))
            key = q('SELECT object_key FROM media_asset WHERE id = :a', a=media_ids[-1]).scalar_one()
            s3c.delete_object(Bucket='pef-evidence-test', Key=key)
            page.goto(f'{WEB}/supervisor/discrepancies/{sid}/review')
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(800)
            missing = page.locator('[data-evidence-missing]').count()
            still = page.evaluate("() => [...document.querySelectorAll('img[data-evidence-image]')].filter(i => i.naturalWidth > 0).length")
            check('missing file: that image shows a placeholder, the others and the page still work',
                  missing >= 1 and still == len(media_ids) - 1, (missing, still))
            # Garage temporarily down.
            compose('stop', 'garage')
            try:
                down = spd.get(f'{WEB}/supervisor/discrepancies/{sid}/review', timeout=30)
                check('Garage down: the review page still renders with the evidence metadata',
                      down.status_code == 200 and down.text.count('data-evidence-figure') == len(media_ids), down.status_code)
            finally:
                compose('start', 'garage')
                compose('up', '-d', '--wait', 'garage')
            # Revoked token: the Supervisor signs out through the portal (access AND refresh revoked); the old
            # cookies are then refused and no evidence link is issued.
            old_cookies = {'access_token': spd_token, 'refresh_token': spd_refresh}
            requests.get(f'{WEB}/sign-out', cookies=old_cookies, allow_redirects=False, timeout=10)
            revoked = requests.get(image(sid, media_ids[0]), cookies=old_cookies, allow_redirects=False, timeout=10)
            check('revoked Supervisor tokens (portal sign-out) → sent to sign-in, no link issued',
                  revoked.status_code in (302, 401) and 'sign-in' in revoked.headers.get('Location', ''),
                  (revoked.status_code, revoked.headers.get('Location')))

            # --- live WS: reconnection and revocation while open --------------------------------------------
            op2, op2_token, _ = login(*OPERATOR)
            octx = browser.new_context()
            octx.add_cookies([{'name': 'access_token', 'value': op2_token, 'domain': '127.0.0.1', 'path': '/'}])
            opage = octx.new_page()
            opage.goto(capture)
            first = live_ws(opage, sid, frames=1)
            again = live_ws(opage, sid, frames=1)
            check('live WS: valid reconnection keeps working',
                  any(m['type'] == 'detections.result' for m in first.get('msgs', []))
                  and any(m['type'] == 'detections.result' for m in again.get('msgs', [])), (first, again))
            revoked_ws = opage.evaluate("""async (sid) => {
                const t = await (await fetch(`/operator/sessions/${sid}/capture/ws-ticket`)).json();
                const ws = new WebSocket(t.ws_url); const msgs = [];
                const ready = new Promise(r => { ws.onmessage = ev => { const m = JSON.parse(ev.data); msgs.push(m);
                    if (m.type === 'worker.status' && m.state === 'ready') r(); }; });
                ws.onopen = () => ws.send(JSON.stringify({type: 'session.bind', session_id: sid, auth_token: t.ticket}));
                await ready;
                window.__ms03ws = {ws, msgs, closed: false};
                ws.onclose = () => { window.__ms03ws.closed = true; };
                return true; }""", sid)
            requests.get(f'{WEB}/sign-out', cookies={'access_token': op2_token}, allow_redirects=False, timeout=10)
            opage.wait_for_timeout(3000)
            state = opage.evaluate("() => ({closed: window.__ms03ws.closed, codes: window.__ms03ws.msgs.map(m => m.code || m.type)})")
            ticket_after = opage.evaluate(f"async () => (await fetch('/operator/sessions/{sid}/capture/ws-ticket', {{redirect: 'manual'}})).status")
            check('revocation while the WS is open (sign-out): server closes it with "revoked"; no new ticket',
                  revoked_ws and state['closed'] and 'revoked' in state['codes'] and ticket_after != 200, (state, ticket_after))
            browser.close()
    finally:
        if original_active:
            with engine.begin() as c:
                c.execute(text('UPDATE yolo_model SET active = FALSE WHERE active'))
                c.execute(text('UPDATE yolo_model SET active = TRUE WHERE id = :i'), {'i': original_active})
        out = docker('logs', '--tail', '60', CONTAINER, check_rc=False)
        (logs / 'ms03_vision.log').write_text(out.stdout + out.stderr, encoding='utf-8')
        docker('rm', '-f', CONTAINER, check_rc=False)
        envctl.stop(*procs.values())
    print(f'\n{sum(results)} PASS, {len(results) - sum(results)} FAIL  (session {created.get("session")}; logs .e2e/ms03_*)')
    sys.exit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
