"""MS-10: Operator and Supervisor flows in a REAL browser (Chromium / Playwright) against real services.

Isolated test stack only. Real processes: AuthService, portal, CatalogService, SessionService, EvidenceService,
NotificationsService + worker, MonitoringService + collector, VisionWorker (container, provisional best.pt, real YOLO
inference on the MS-03.1 test videos). Screenshots (JPEG) + results.md are written to docs/evidencias/MS-10/ (not
committed, like ADMIN-09). No password, token or secret is written.

How the browser is driven: sign-in form, links, buttons, file inputs and the real forms of each screen (CSRF field,
server validation, redirects). Multi-step wizards whose steps are only presentation (scheduling, SPD close) are filled
field by field and submitted with form.requestSubmit() from the page: this is noted in the result of those checks.

Operator: login → assigned sessions → begin → choose camera → upload video → initial count (YOLO) → final count
rejected by a real physical difference → recapture → final accepted → validation (classification only) → review →
history; plus the escalation of a rejected final count and a technical contingency, neither producing a final count.
Supervisor: dashboard, scheduling, kits/instruments catalogs, trays, pending sessions, real evidence, discrepancy
resolution and close, notifications, reports/indicators, CSV/PDF downloads, services and stations health, audit log.

    PYTHONIOENCODING=utf-8 python scripts/browser_validate_flows.py
"""
import json
import re
import secrets
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import requests
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_test_env import load_test_env  # noqa: E402
import run_admin_browser_env as envctl  # noqa: E402
from e2e_ms02_model_pin import BEST_SHA, CONTAINER, IMAGE, NETWORK, docker, wait  # noqa: E402
from e2e_restore_stock import restore_lost  # noqa: E402

AUTH, WEB, CATALOG, SESSION, EVIDENCE, NOTIF, MONITORING, VISION = (
    'http://127.0.0.1:15001', 'http://127.0.0.1:15000', 'http://127.0.0.1:15003', 'http://127.0.0.1:15004',
    'http://127.0.0.1:15005', 'http://127.0.0.1:15007', 'http://127.0.0.1:15008', 'http://127.0.0.1:15002')
OPERATOR, SUPERVISOR, ADMIN = (('operator@instrumed.com', 'DemopwdOP78!'), ('supervisor@instrumed.com', 'DemopwdSPD78!'),
                               ('admin@instrumed.com', 'DemopwdADM78!'))
VIDEOS = ROOT / '.e2e' / 'videos'
ONE_REMOVED, MASKED = VIDEOS / 'ms031_one_removed.avi', VIDEOS / 'ms031_masked.avi'
EVIDENCE_DIR = ROOT / 'docs' / 'evidencias' / 'MS-10'
results = []
counter = {'n': 0}


def check(area, name, ok, detail='', note=''):
    results.append({'area': area, 'check': name, 'ok': bool(ok), 'note': note, 'detail': '' if ok else str(detail)[:300]})
    print(f"{'PASS' if ok else 'FAIL'}  [{area}] {name}{'  -- ' + str(detail)[:300] if detail and not ok else ''}", flush=True)
    return ok


def shot(page, name):
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    counter['n'] += 1
    file = f'{counter["n"]:03d}_{re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")}.jpg'
    page.screenshot(path=str(EVIDENCE_DIR / file), full_page=True, type='jpeg', quality=55)
    return file


def sign_in(page, email, password):
    page.goto(WEB + '/sign-in')
    page.fill('#institutional-email', email)
    page.fill('#password', password)
    page.click('#sign-in-form button[type=submit]')
    page.wait_for_url(lambda url: '/sign-in' not in url, timeout=20000)


def confirm_dialog(page, timeout=5000):
    """Accept the portal confirmation dialog (app-confirm.js) if the action opened one."""
    try:
        page.wait_for_selector('button[data-app-confirm-ok]:visible', timeout=timeout)
        page.click('button[data-app-confirm-ok]:visible')
    except Exception:
        pass
    page.wait_for_load_state('networkidle')


def submit_form(page, selector, fields, checks=()):
    """Fill fields of a real form (also inside hidden wizard steps) and submit it with requestSubmit()."""
    page.evaluate("""([selector, fields, checks]) => {
        const form = document.querySelector(selector);
        for (const [name, value] of Object.entries(fields)) {
            let input = form.querySelector(`[name="${name}"]`);
            if (!input) { input = document.createElement('input'); input.type = 'hidden'; input.name = name; form.appendChild(input); }
            input.value = value;
        }
        for (const name of checks) { const box = form.querySelector(`input[type=checkbox][name="${name}"]`); if (box) box.checked = true; }
        form.requestSubmit();
    }""", [selector, fields, list(checks)])
    page.wait_for_load_state('networkidle')


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
                    'NOTIFICATIONS_SERVICE_URL': NOTIF, 'MONITORING_SERVICE_URL': MONITORING, 'CATALOG_SERVICE_URL': CATALOG,
                    'MONGO_URL': env['PEF_TEST_MONGO_URL'], 'MONGO_DB': 'pef_ms10_browser',
                    'PEF_OUTBOX_DIR': str(ROOT / '.e2e' / f'ms10_outbox_{secrets.token_hex(3)}')})
    service_env = {**env, 'JWT_SECRET_KEY': jwt_secret}
    monitoring_env = {**service_env, 'MONITORING_HOST': '127.0.0.1', 'MONITORING_PORT': '15008', 'MONITORING_INTERVAL_SECONDS': '5',
                      'MONITORING_TARGET_VISION_URL': VISION, 'MONITORING_TARGET_SESSION_URL': SESSION,
                      'MONITORING_TARGET_AUTH_URL': AUTH, 'MONITORING_MONGO_URL': ''}
    logs = ROOT / '.e2e'

    def start(name, args, cwd, environment):
        return subprocess.Popen(args, cwd=cwd, env=environment, stdout=open(logs / f'ms10_{name}.log', 'a'),
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
        'monitoring': start('monitoring', [sys.executable, 'main.py'], ROOT / 'apps' / 'MonitoringService', monitoring_env),
        'collector': start('collector', [sys.executable, 'collector.py'], ROOT / 'apps' / 'MonitoringService', monitoring_env),
        'web': start('web', [sys.executable, '-m', 'flask', '--app', 'app', 'run', '--host', '127.0.0.1', '--port', '15000'],
                     ROOT / 'apps' / 'BackendWebFlask', web_env),
    }
    sessions = []
    try:
        for url, label in ((AUTH + '/health', 'AuthService'), (CATALOG + '/health/live', 'CatalogService'),
                           (SESSION + '/health/live', 'SessionService'), (EVIDENCE + '/health/live', 'EvidenceService'),
                           (NOTIF + '/health/live', 'NotificationsService'), (MONITORING + '/health/live', 'MonitoringService'),
                           (WEB + '/health/live', 'portal'), (VISION + '/health', 'VisionWorker')):
            wait(url, label, 240)
        admin_http = requests.Session()
        token = admin_http.post(AUTH + '/login', json={'email': ADMIN[0], 'password': ADMIN[1]}, timeout=10).cookies['access_token']
        admin_http.cookies.set('access_token', token, domain='127.0.0.1')
        admin_http.post(f'{WEB}/admin/vision-models/{best.id}/activate', allow_redirects=False, timeout=30)
        operator_id, inst = q('SELECT id, institution_id FROM "user" WHERE email = :e', e=OPERATOR[0])[0]
        station = q('SELECT s.id, s.name, s.room_id FROM capture_station s JOIN operating_room r ON r.id = s.room_id '
                    'WHERE s.active AND r.active AND r.institution_id = :i ORDER BY s.name LIMIT 1', i=inst)[0]
        camera = q1('SELECT id FROM capture_camera WHERE station_id = :s AND active ORDER BY name LIMIT 1', s=station.id)
        if camera is None:
            admin_http.post(f'{WEB}/admin/configuration/cameras/new', allow_redirects=False, timeout=30, data={
                'name': 'MS-10 browser camera', 'station_id': str(station.id), 'device_type': 'usb', 'status': 'active'})
            camera = q1('SELECT id FROM capture_camera WHERE station_id = :s AND active ORDER BY name LIMIT 1', s=station.id)
        kit = q1("SELECT id FROM kit WHERE active AND institution_id = :i AND name = 'Video Demo Kit 1'", i=inst)
        family = q1('SELECT family_id FROM kit_item WHERE kit_id = :k ORDER BY family_id LIMIT 1', k=kit)
        physician, physician_name = q('SELECT id, name FROM physician WHERE institution_id = :i AND active ORDER BY name LIMIT 1', i=inst)[0]
        notice = q1('SELECT id FROM privacy_notice_version WHERE active AND effective_at <= now() ORDER BY effective_at LIMIT 1')
        procedure = q1('SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1')

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            spd_ctx, op_ctx = browser.new_context(accept_downloads=True), browser.new_context()
            spd, op = spd_ctx.new_page(), op_ctx.new_page()
            console_errors = []
            for page in (spd, op):
                page.on('console', lambda m: console_errors.append(m.text) if m.type == 'error' else None)
            sign_in(spd, *SUPERVISOR)
            sign_in(op, *OPERATOR)

            # ===== Supervisor: dashboard and scheduling ============================================================
            spd.goto(WEB + '/supervisor/dashboard')
            check('Supervisor', 'dashboard renders with real data (no demo fixture)', spd.locator('main').count() == 1
                  and 'data-v3-demo' not in spd.content(), note=shot(spd, 'supervisor dashboard'))

            def schedule(label):
                spd.goto(f'{WEB}/supervisor/sessions/new?kit_id={kit}')
                when = f'{date.today()}T{10 + len(sessions):02d}:00'
                before = spd.url
                # reference of one piece (as in the MS-09 E2E): keep only the first kit line
                spd.evaluate("""() => { const f = document.querySelector('#schedule-session-form');
                    for (const n of ['family_id[]', 'expected_quantity[]']) {
                        [...f.querySelectorAll(`[name="${n}"]`)].slice(1).forEach(i => i.remove()); } }""")
                # physician typeahead: the visible name and its hidden id, as choosing it from the menu does
                spd.evaluate("""([name]) => { const i = document.querySelector('[data-physician-typeahead]');
                    if (i) i.value = name; }""", [physician_name])
                submit_form(spd, '#schedule-session-form', {
                    'operator_user_id': str(operator_id), 'procedure_type_id': str(procedure), 'room_id': str(station.room_id),
                    'station_id': str(station.id), 'scheduled_at': when, 'kit_id': str(kit), 'family_id[]': str(family),
                    'expected_quantity[]': '1', 'physician_id[]': str(physician), 'surgical_role_code[]': 'surgeon',
                    'action': 'schedule'})
                invalid = spd.evaluate("""() => [...document.querySelectorAll('#schedule-session-form :invalid')]
                    .map(e => e.outerHTML.slice(0, 300))""")
                print('invalid fields after filling:', invalid, flush=True)
                # the page opens its privacy modal on submit: choose the notice, confirm, save (as a user does)
                spd.wait_for_selector('#privacy-continue-modal.is-open', timeout=10000)
                spd.select_option('#privacy-notice-version', str(notice))
                spd.check('#privacy-confirm-checkbox')
                spd.click('[data-privacy-modal-with-notice]')
                try:
                    spd.wait_for_url(re.compile(r'.*/supervisor/sessions/[0-9a-f-]{36}$'), timeout=8000)
                except Exception:
                    if spd.locator('button[data-app-confirm-ok]:visible').count():
                        spd.click('button[data-app-confirm-ok]:visible')
                    spd.wait_for_url(re.compile(r'.*/supervisor/sessions/[0-9a-f-]{36}$'), timeout=30000)
                match = re.search(r'/supervisor/sessions/([0-9a-f-]{36})', spd.url)
                sid = match.group(1) if match else None
                if sid:
                    sessions.append(sid)
                return sid, before
            sid, _ = schedule('main')
            check('Supervisor', 'schedules a session with the real form (kit, station, operator, physician, privacy)',
                  sid and q1('SELECT kit_id FROM work_session WHERE id = :s', s=sid) == kit,
                  (spd.locator('.alert').all_inner_texts() or [spd.url]), note='wizard fields filled and submitted, privacy modal driven by clicks · '
                  + shot(spd, 'supervisor session scheduled'))
            if not sid:
                raise SystemExit('scheduling failed; the remaining flow needs a session')
            for url, label in (('/admin/kits', 'kits'), ('/admin/instruments', 'instruments'), ('/supervisor/trays', 'trays')):
                response = spd.goto(WEB + url)
                check('Supervisor', f'catalog screen: {label} (real data)', response.status < 400 and 'data-v3-demo' not in spd.content(),
                      response.status, note=shot(spd, f'supervisor {label}'))
            spd.goto(WEB + '/supervisor/trays/new')
            tray_code = f'TR-MS10-{secrets.token_hex(2)}'
            spd.fill('#tray-code', tray_code)
            spd.select_option('#tray-kit', str(kit))
            spd.click('[data-tray-form] button[type=submit]')
            spd.wait_for_load_state('networkidle')
            check('Supervisor', 'creates a tray from the trays screen (real form)', tray_code in spd.content()
                  and q1('SELECT count(*) FROM surgical_tray WHERE code = :c', c=tray_code) == 1, note=shot(spd, 'supervisor tray created'))

            # ===== Operator: full counting flow =========================================================================
            op.goto(WEB + '/operator/sessions')
            check('Operator', 'sees the assigned session', sid in op.content(), note=shot(op, 'operator sessions'))
            op.goto(f'{WEB}/operator/sessions/{sid}/begin')
            for _ in range(5):
                if op.locator('#instruments-ready').is_visible():
                    break
                nxt = op.locator('[data-confirm-next]:visible')
                if nxt.count():
                    nxt.first.click()
            op.check('#instruments-ready')
            op.click('[data-v3-readiness-submit]')
            confirm_dialog(op)
            status = q1('SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = :s', s=sid)
            check('Operator', 'starts the session (readiness confirmation)', status == 'in_progress', status, note=shot(op, 'operator started'))
            op.goto(f'{WEB}/operator/sessions/{sid}/capture')
            op.wait_for_selector('#capture-station-camera')
            op.select_option('#capture-station-camera', str(camera))
            time.sleep(1.5)
            check('Operator', 'chooses the station camera (stored and audited)',
                  q1('SELECT camera_id FROM work_session WHERE id = :s', s=sid) == camera, note=shot(op, 'operator camera'))

            def upload_and_wait(video, selector_ready):
                op.set_input_files('#capture-video', str(video))
                op.click('#capture-upload-btn')
                op.wait_for_function(selector_ready, timeout=600000)

            def register_initial():
                ack = op.locator('#capture-initial-ack-input').is_visible()
                if ack:
                    op.check('#capture-initial-ack-input')
                op.wait_for_function("() => !document.getElementById('capture-initial-btn').disabled", timeout=30000)
                op.click('#capture-initial-btn')
                return ack

            upload_and_wait(ONE_REMOVED, "() => !document.getElementById('capture-initial-panel').hidden")
            ack_shown = register_initial()
            op.wait_for_selector('#capture-registered-panel:not([hidden])', timeout=60000)
            initial = q("SELECT detected_quantity FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
                        "WHERE e.session_id = :s AND t.code = 'auto_count' AND e.payload->>'source' = 'first_frame'", s=sid)
            check('Operator', 'initial count from the first frame (real YOLO)', len(initial) == 1, initial,
                  note=('difference with the expected inventory acknowledged in the page · ' if ack_shown else '')
                  + shot(op, 'operator initial count'))
            op.wait_for_function("() => !document.getElementById('capture-play-btn').disabled", timeout=60000)
            op.click('#capture-play-btn')
            op.wait_for_function("() => !document.getElementById('capture-final-btn').disabled", timeout=900000)
            op.click('#capture-final-btn')
            time.sleep(3)
            blocked = q('SELECT detected_quantity, expected_quantity FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id '
                        "WHERE e.session_id = :s AND t.code = 'close_blocked'", s=sid)
            check('Operator', 'final count with a real physical difference is rejected (capture again)',
                  len(blocked) == 1 and blocked[0][0] != blocked[0][1], blocked, note=shot(op, 'operator final rejected'))
            check('Operator', 'the escalation option is offered in the recapture step',
                  op.locator('#capture-escalation').is_visible())
            # recapture with the tray as it was at the start
            op.wait_for_function("() => !document.getElementById('capture-upload-btn').disabled", timeout=60000)
            op.set_input_files('#capture-video', str(MASKED))
            op.click('#capture-upload-btn')
            op.wait_for_function("() => !document.getElementById('capture-play-btn').disabled", timeout=600000)
            op.click('#capture-play-btn')
            op.wait_for_function("() => !document.getElementById('capture-final-btn').disabled", timeout=900000)
            op.click('#capture-final-btn')
            op.wait_for_url(re.compile(r'.*/vision-validation.*'), timeout=60000)
            finals = q("SELECT detected_quantity FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
                       "WHERE e.session_id = :s AND t.code = 'auto_count' AND e.payload->>'trigger' = 'final'", s=sid)
            check('Operator', 'recapture accepted: final count = reference', len(finals) == 1, finals, note=shot(op, 'operator validation'))
            # validation: classification only, same total
            values = op.evaluate("() => [...document.querySelectorAll('input[name^=validated_]')].map(i => [i.name, Number(i.value)])")
            final_total = finals[0][0]
            extra = final_total - sum(v for _, v in values)
            fields = {'extra_instruments': str(extra), 'extra_notes': 'MS-10 browser: outside the kit'}
            for name, value in values:
                fid = name.split('_', 1)[1]
                expected = 1 if fid == str(family) else 0
                fields[f'reason_{fid}'] = 'surplus' if value > expected else ('shortage' if value < expected else '')
                fields[f'notes_{fid}'] = 'MS-10 browser validation'
            changed = dict(fields, extra_instruments=str(extra + 1))
            submit_form(op, '#vision-validation-form', changed)
            confirm_dialog(op)   # the page asks 'Submit validation?'
            check('Operator', 'validation cannot change the physical total (refused)',
                  'only corrects the classification' in op.content(), note=shot(op, 'operator validation refused'))
            op.goto(f'{WEB}/operator/sessions/{sid}/vision-validation')
            submit_form(op, '#vision-validation-form', fields)
            confirm_dialog(op)   # the page asks 'Submit validation?'
            status = q1('SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = :s', s=sid)
            check('Operator', 'validation with the same total sends the session to review', status in ('awaiting_spd_review', 'correction_required'),
                  status, note=shot(op, 'operator sent to review'))
            response = op.goto(WEB + '/operator/sessions/history')
            check('Operator', 'history screen', response.status < 400, response.status, note=shot(op, 'operator history'))

            # ===== Operator: escalation and contingency ============================================================
            sid2, _ = schedule('escalation')
            sid3, _ = schedule('contingency')
            for s in (sid2, sid3):
                op.goto(f'{WEB}/operator/sessions/{s}/begin')
                for _ in range(5):
                    if op.locator('#instruments-ready').is_visible():
                        break
                    nxt = op.locator('[data-confirm-next]:visible')
                    if nxt.count():
                        nxt.first.click()
                op.check('#instruments-ready')
                op.click('[data-v3-readiness-submit]')
                confirm_dialog(op)
            op.goto(f'{WEB}/operator/sessions/{sid2}/capture')
            upload_and_wait(ONE_REMOVED, "() => !document.getElementById('capture-initial-panel').hidden")
            register_initial()
            op.wait_for_function("() => !document.getElementById('capture-play-btn').disabled", timeout=60000)
            op.click('#capture-play-btn')
            op.wait_for_function("() => !document.getElementById('capture-final-btn').disabled", timeout=900000)
            op.click('#capture-final-btn')
            op.wait_for_selector('#capture-escalation:not([hidden])', timeout=60000)
            op.fill('#capture-escalation-notes', 'MS-10 browser: tray checked twice, one piece missing')
            op.click('#capture-escalation-btn')
            op.wait_for_url(re.compile(rf'.*/operator/sessions/{sid2}$'), timeout=60000)
            esc_status = q1('SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = :s', s=sid2)
            esc_finals = q1("SELECT count(*) FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id WHERE e.session_id = :s "
                            "AND t.code = 'auto_count' AND e.payload->>'trigger' = 'final'", s=sid2)
            check('Operator', 'escalation of a possible physical shortage: to the Supervisor, no final count',
                  esc_status == 'correction_required' and esc_finals == 0, (esc_status, esc_finals), note=shot(op, 'operator escalation'))
            op.goto(f'{WEB}/operator/sessions/{sid3}/manual-report')
            refused = op.evaluate("""async ([url, family]) => {
                const form = document.querySelector('#quantity-report-form'); const data = new FormData(form);
                data.set('reported_' + family, '1'); data.set('contingency_reason', 'worker_unavailable');
                const r = await fetch(url, {method: 'POST', body: data, redirect: 'manual'}); return r.status; }""",
                [f'{WEB}/operator/sessions/{sid3}/manual-report', str(family)])
            check('Operator', 'contingency "worker unavailable" while the worker answers is refused (409)', refused == 409, refused)
            op.goto(f'{WEB}/operator/sessions/{sid3}/manual-report')
            submit_form(op, '#quantity-report-form', {f'reported_{family}': '1', 'contingency_reason': 'other',
                                                      'contingency_notes': 'MS-10 browser: camera image unusable'})
            confirm_dialog(op)   # the page asks 'Close counting session?'
            payload = q1("SELECT e.payload FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id WHERE e.session_id = :s "
                         "AND t.code = 'manual_close'", s=sid3) or {}
            c_finals = q1("SELECT count(*) FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id WHERE e.session_id = :s "
                          "AND t.code = 'auto_count' AND e.payload->>'trigger' = 'final'", s=sid3)
            check('Operator', 'declared contingency recorded (not verified), no final YOLO count',
                  (payload.get('contingency') or {}).get('verification', {}).get('status') == 'declared' and c_finals == 0,
                  payload.get('contingency'), note=shot(op, 'operator contingency'))

            # ===== Supervisor: review, evidence, resolution, close ====================================================
            spd.goto(WEB + '/supervisor/sessions')
            check('Supervisor', 'pending sessions list shows the sessions', all(s in spd.content() for s in (sid, sid2, sid3)),
                  note=shot(spd, 'supervisor sessions'))
            time.sleep(5)
            spd.goto(f'{WEB}/supervisor/sessions/{sid}#timeline')     # wizard step 3 (Timeline) holds the evidence
            loaded = 0
            if spd.locator('[data-evidence-captures]').count():
                spd.wait_for_selector('[data-evidence-captures]', state='visible', timeout=15000)
                spd.locator('[data-evidence-captures]').scroll_into_view_if_needed()
                spd.wait_for_timeout(2000)
                loaded = spd.evaluate("() => [...document.querySelectorAll('img[data-evidence-image]')].filter(i => i.complete && i.naturalWidth > 0).length")
            check('Supervisor', 'session details show real evidence images (EvidenceService)', loaded >= 2, loaded,
                  note=shot(spd, 'supervisor evidence'))
            for s in (sid, sid2):
                for disc, reason in q('SELECT d.id, r.code FROM discrepancy d LEFT JOIN cat_discrepancy_reason r ON r.id = d.reason_id '
                                      'WHERE d.session_id = :s AND NOT d.resolved', s=s):
                    # as a Supervisor: choose the outcome radio, write notes, click Resolve, confirm the dialog
                    spd.goto(f'{WEB}/supervisor/discrepancies/{s}/review')
                    form = spd.locator(f'form:has(input[name="discrepancy_id"][value="{disc}"])')
                    form.locator(f'input[name="resolution_outcome"][value="{"recovered" if reason == "shortage" else "notes_only"}"]').check()
                    form.locator('[data-resolution-notes]').fill('MS-10 browser review: tray checked by the Supervisor')
                    form.locator('button[type=submit]').click()
                    confirm_dialog(spd)
            open_left = q1('SELECT count(*) FROM discrepancy WHERE session_id = ANY(:s) AND NOT resolved', s=[sid, sid2])
            check('Supervisor', 'resolves the discrepancies from the review screen', open_left == 0, open_left,
                  note=shot(spd, 'supervisor review'))
            spd.goto(f'{WEB}/supervisor/sessions/{sid2}#close')
            check('Supervisor', 'sees the escalation warning in the close step', spd.locator('[data-final-count-escalation]').count() == 1,
                  note=shot(spd, 'supervisor escalation warning'))
            shown = {}
            for s in (sid, sid2, sid3):
                # tick exactly the acknowledgments the page shows for this session (nothing injected), then submit
                spd.goto(f'{WEB}/supervisor/sessions/{s}#close')
                shown[s] = spd.evaluate("""() => { const f = document.querySelector('[data-session-close-form]'); if (!f) return null;
                    const boxes = [...f.querySelectorAll('input[type=checkbox][required]')]; boxes.forEach(b => b.checked = true);
                    return boxes.map(b => b.name); }""")
                if shown[s] is not None:
                    spd.locator('[data-session-close-form] button[type=submit]').click()
                    confirm_dialog(spd)   # the page asks 'Close Counting Session?'
            closes = {s: q1("SELECT e.payload FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
                            "WHERE e.session_id = :s AND t.code = 'session_close'", s=s) or {} for s in (sid, sid2, sid3)}
            check('Supervisor', 'escalated session asks for escalation_acknowledged; contingency asks for the unverified acknowledgment',
                  'escalation_acknowledged' in (shown[sid2] or []) and 'unverified_contingency_acknowledged' in (shown[sid3] or [])
                  and 'escalation_acknowledged' not in (shown[sid] or []), {k[:8]: v for k, v in shown.items()})
            check('Supervisor', 'closes: normal session, escalated (never safe), contingency (basis recorded)',
                  closes[sid].get('close_basis') == 'final_count'
                  and closes[sid2].get('close_basis') == 'final_count_escalation' and closes[sid2].get('safe_close_confirmed') is False
                  and closes[sid3].get('close_basis') == 'contingency_count', {k[:8]: v.get('close_basis') for k, v in closes.items()},
                  note='required boxes shown by the page ticked, Confirm close + dialog clicked · ' + shot(spd, 'supervisor closed'))

            # ===== Supervisor: notifications, reports, exports, monitoring, audit ======================================
            time.sleep(3)
            spd.goto(WEB + '/notifications')
            check('Supervisor', 'notifications of these sessions', any(f'/sessions/{s}' in spd.content() for s in (sid, sid2, sid3)),
                  note=shot(spd, 'supervisor notifications'))
            spd.goto(WEB + '/supervisor/reports?date_range=7d')
            check('Supervisor', 'reports with outcomes, automatic count and stations (real data)',
                  'data-report-outcomes' in spd.content() and 'data-report-unavailable' not in spd.content(), note=shot(spd, 'supervisor reports'))
            downloads = {}
            for fmt in ('csv', 'pdf'):
                with spd.expect_download() as info:
                    spd.click(f'[data-export="{fmt}"]')
                path = info.value.path()
                downloads[fmt] = Path(path).read_bytes()[:4] if path else b''
            check('Supervisor', 'CSV and PDF downloads from the reports screen',
                  downloads['csv'].startswith(b'\xef\xbb\xbf') and downloads['pdf'].startswith(b'%PDF'), downloads)
            spd.goto(WEB + '/supervisor/indicators')
            check('Supervisor', 'indicators screen', 'data-report-unavailable' not in spd.content(), note=shot(spd, 'supervisor indicators'))
            spd.goto(WEB + '/supervisor/services-health')
            check('Supervisor', 'services and stations health', 'data-stations-table' in spd.content() or 'data-stations-health' in spd.content(),
                  note=shot(spd, 'supervisor services health'))
            spd.goto(WEB + '/supervisor/audit-log')
            check('Supervisor', 'audit log screen with real entries', spd.locator('table').count() >= 1, note=shot(spd, 'supervisor audit'))
            spd.goto(f'{WEB}/supervisor/trays')
            check('Supervisor', 'trays list shows the new tray', tray_code in spd.content())
            refused = [e for e in console_errors if e.startswith('Failed to load resource: the server responded with a status of 4')]
            js_errors = [e for e in console_errors if e not in refused and 'favicon' not in e]
            check('Browser', 'no JavaScript errors in the console', not js_errors, js_errors[:5],
                  note=f'{len(refused)} HTTP 4xx console lines = the refusals this script provokes on purpose '
                       '(rejected final 409, refused validation 400, contradicted contingency 409)')
            browser.close()
    finally:
        restore_lost(engine, sessions)
        for proc in procs.values():
            if proc.poll() is None:
                proc.terminate()
        docker('rm', '-f', CONTAINER, check_rc=False)
        if original_active:
            with engine.begin() as c:
                c.execute(text('UPDATE yolo_model SET active = FALSE WHERE active'))
                c.execute(text('UPDATE yolo_model SET active = TRUE WHERE id = :i'), {'i': original_active})
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        (EVIDENCE_DIR / 'results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
        lines = ['# MS-10 — Operator and Supervisor in a real browser', '', '| Area | Check | Result | Evidence / note |',
                 '| :--- | :--- | :--- | :--- |']
        lines += [f"| {r['area']} | {r['check']} | {'PASS' if r['ok'] else 'FAIL'} | {r['note'] or r['detail']} |" for r in results]
        (EVIDENCE_DIR / 'results.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f"\n{sum(r['ok'] for r in results)}/{len(results)} checks passed")
    sys.exit(0 if results and all(r['ok'] for r in results) else 1)


if __name__ == '__main__':
    main()
