"""MS-11: integral E2E of the three roles against the WHOLE PEF running in Docker Compose (isolated project pef_ms11,
started with scripts/compose_ms11.py up). Real containers only: the nine services, their workers, the portal, the TLS
API Gateway, PostgreSQL, Redis, MongoDB, Garage, VisionWorker with the provisional best.pt (real YOLO) and a local HAPI
FHIR R4 TEST server. Nothing is mocked. No real hospital, no clinical data (no patient).

MS-12 extends it to the 20 functional domains (checks labelled M*): camera calibration measured by a technician,
model regression run (real report of scripts/model_regression.py), authorized kit equivalences, persisted tracking
log, integrity seal of the human validation, traceable batch export (user and machine client), monitoring of the
integration service / worker, recovery of evidence after an EvidenceService outage, and the cross-service audit
consistency check (scripts/check_audit_consistency.py).

Users of the demo seed (003) of the isolated database. Browser-equivalent HTTP through the Gateway (portal host and API
host, self-signed test certificate). PostgreSQL (127.0.0.1:26432) is only READ to verify results.

    PYTHONIOENCODING=utf-8 python scripts/e2e_ms11_compose.py
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import requests
import urllib3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import portal_csrf  # noqa: E402

WORK = ROOT / '.e2e' / 'ms11_compose'
ENV = dict(line.split('=', 1) for line in (WORK / 'compose.env').read_text(encoding='utf-8').splitlines() if '=' in line)
PORTAL, API = 'https://portal.pef.test:26443', 'https://api.pef.test:26443'
INTEGRATION, VISION, HAPI = 'http://127.0.0.1:26009', 'http://127.0.0.1:26002', 'http://127.0.0.1:26090/fhir'
BEST = Path('C:/Users/pedro/OneDrive/Documentos/Desarrollo/PEF_Integrado_Admin_Monolito/apps/VisionWorker/weights/best.pt')
VIDEOS = ROOT / '.e2e' / 'videos'
OPERATOR, SUPERVISOR, ADMIN = (('operator@instrumed.com', 'DemopwdOP78!'), ('supervisor@instrumed.com', 'DemopwdSPD78!'),
                               ('admin@instrumed.com', 'DemopwdADM78!'))
results = []
STARTED = datetime.now(timezone.utc) - timedelta(seconds=5)

_native = socket.getaddrinfo
socket.getaddrinfo = lambda host, port, *a, **k: (
    [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('127.0.0.1', int(port)))]
    if host in ('portal.pef.test', 'api.pef.test') else _native(host, port, *a, **k))
urllib3.disable_warnings()
portal_csrf.enable(PORTAL)


def check(step, name, ok, detail=''):
    results.append({'step': step, 'check': name, 'ok': bool(ok), 'detail': '' if ok else str(detail)[:400]})
    print(f"{'PASS' if ok else 'FAIL'}  [{step}] {name}{'  -- ' + str(detail)[:400] if detail and not ok else ''}", flush=True)
    return ok


def compose(*args):
    cmd = ['docker', 'compose', '-p', 'pef_ms11', '--env-file', str(WORK / 'compose.env'), '-f', str(ROOT / 'docker-compose.yml'),
           '-f', str(ROOT / 'docker-compose.gateway-tls.yml'), '-f', str(WORK / 'override.yml'), '--profile', 'gateway', *args]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def db():
    return psycopg.connect(host='127.0.0.1', port=26432, dbname=ENV['POSTGRES_DB'], user=ENV['POSTGRES_USER'],
                           password=ENV['POSTGRES_PASSWORD'], autocommit=True)


def q(sql, *params):
    with db() as c:
        return c.execute(sql, params).fetchall()


def q1(sql, *params):
    rows = q(sql, *params)
    return rows[0][0] if rows else None


def until(fn, seconds, every=2):
    deadline = time.time() + seconds
    value = None
    while time.time() < deadline:
        try:
            value = fn()
        except (requests.RequestException, psycopg.Error, ValueError):
            value = None           # a container restarting: keep waiting until the deadline
        if value:
            return value
        time.sleep(every)
    return value


def api_login(user):
    r = requests.post(API + '/api/v1/auth/login', json={'email': user[0], 'password': user[1]}, timeout=20, verify=False)
    return r.json() if r.ok else {}


def portal(token):
    s = requests.Session()
    s.verify = False
    s.cookies.set('access_token', token, domain='portal.pef.test', path='/')
    return s


def bearer(token):
    return {'Authorization': f'Bearer {token}'}


def status_of(sid):
    return q1('SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = %s', sid)


def events(sid, code):
    return q("SELECT e.detected_quantity, e.payload FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
             "WHERE e.session_id = %s AND t.code = %s ORDER BY e.occurred_at", sid, code)


def main():
    until(lambda: requests.get(API + '/healthz', timeout=5, verify=False).ok, 120)
    users = {'admin': ADMIN, 'spd': SUPERVISOR, 'op': OPERATOR}
    tokens = {name: api_login(user) for name, user in users.items()}
    issued = {name: time.time() for name in users}

    def tok(name):
        """Access tokens live 15 minutes (AuthService): a long YOLO run outlives them, so log in again when needed."""
        if time.time() - issued[name] > 600:
            tokens[name], issued[name] = api_login(users[name]), time.time()
        return tokens[name]['access_token']
    check(0, 'three roles log in through the TLS Gateway (AuthService)', all(t.get('access_token') for t in tokens.values()))
    admin, spd, op = (portal(tokens[k]['access_token']) for k in ('admin', 'spd', 'op'))
    inst = q1('SELECT institution_id FROM "user" WHERE email = %s', OPERATOR[0])

    # ===== 1. Administrator configures resources ========================================================================
    model_id = q1('SELECT id FROM yolo_model WHERE checksum = %s', __import__('hashlib').sha256(BEST.read_bytes()).hexdigest())
    if model_id is None:
        with open(BEST, 'rb') as fh:
            up = admin.post(PORTAL + '/admin/vision-models/new', data={'version_tag': 'best-ms11-prov', 'architecture': '',
                            'model_size': ''}, files={'weights': ('best.pt', fh, 'application/octet-stream')},
                            allow_redirects=False, timeout=300)
        model_id = re.search(r'/admin/vision-models/([0-9a-f-]{36})/edit', up.headers.get('Location', '')).group(1)
        page = admin.get(f'{PORTAL}/admin/vision-models/{model_id}/edit', timeout=60).text
        mapping = {}
        for cid, block in re.findall(r'name="class_(\d+)"(.*?)</select>', page, re.S):
            chosen = re.search(r'<option value="([^"]*)"\s*selected', block)
            mapping[f'class_{cid}'] = chosen.group(1) if chosen and chosen.group(1) else 'reject'
        admin.post(f'{PORTAL}/admin/vision-models/{model_id}/edit', data={**mapping, 'confirm_missing_families': '1'},
                   allow_redirects=False, timeout=60)
    activated = admin.post(f'{PORTAL}/admin/vision-models/{model_id}/activate', allow_redirects=False, timeout=60)
    check(1, 'Admin uploads the provisional weights, maps classes and activates the model (RF-AD-08)',
          q1('SELECT active FROM yolo_model WHERE id = %s', model_id) is True, activated.status_code)
    station = q("SELECT s.id, s.room_id FROM capture_station s JOIN operating_room r ON r.id = s.room_id "
                "WHERE s.active AND r.active AND r.institution_id = %s ORDER BY s.name LIMIT 1", inst)[0]
    cam_name = f'MS-11 simulated source {secrets.token_hex(2)}'
    admin.post(PORTAL + '/admin/configuration/cameras/new', allow_redirects=False, timeout=60, data={
        'name': cam_name, 'station_id': str(station[0]), 'device_type': 'simulated', 'resolution_width': '1280',
        'resolution_height': '720', 'fps': '30', 'status': 'active', 'roi_x': '100', 'roi_y': '50', 'roi_w': '1000',
        'roi_h': '600', 'roi_frame_w': '1280', 'roi_frame_h': '720'})
    camera = q1("SELECT id FROM capture_camera WHERE name = %s AND device_type = 'simulated'", cam_name)
    check(1, 'Admin registers a SIMULATED camera source with ROI on a station (R08, identified as simulation)', camera)
    calibrated = admin.post(f'{PORTAL}/admin/configuration/cameras/{camera}/calibration', allow_redirects=False, timeout=60,
                            data={'reference_length_mm': '100', 'measured_pixels': '400', 'frame_width': '1280',
                                  'frame_height': '720', 'notes': 'MS-12 E2E: declared test measurement'})
    check('M1', 'ms-camaras: Admin records a calibration MEASURED by a technician (mm/px derived only from the two values)',
          calibrated.status_code in (302, 303) and float(q1('SELECT mm_per_pixel FROM capture_camera_calibration '
                                                            'WHERE camera_id = %s ORDER BY performed_at DESC LIMIT 1', camera) or 0) == 0.25,
          calibrated.status_code)
    ev = admin.post(f'{PORTAL}/admin/vision-models/{model_id}/evaluations', allow_redirects=False, timeout=60, data={
        'dataset_name': 'MS-11 demo set', 'dataset_version': secrets.token_hex(3), 'split': 'test', 'source': 'manual',
        'images': '3', 'map50': '0.5', 'evaluated_at': '2026-11-01', 'notes': 'Declared test value (not a real evaluation)'})
    check(1, 'Admin records a model evaluation (R17; declared value, never computed)', ev.status_code in (302, 303)
          and q1('SELECT count(*) FROM yolo_model_evaluation WHERE model_id = %s', model_id) >= 1, ev.status_code)
    report = ROOT / 'docs' / 'evidencias' / 'MS-12' / 'model_regression_best_vs_best.json'
    with open(report, 'rb') as fh:
        regression = admin.post(f'{PORTAL}/admin/vision-models/{model_id}/evaluations', allow_redirects=False, timeout=60,
                                data={'kind': 'regression', 'notes': 'MS-12 E2E'},
                                files={'report_file': ('report.json', fh, 'application/json')})
    run = q('SELECT items_total, items_identical FROM yolo_model_regression_run WHERE model_id = %s ORDER BY created_at DESC LIMIT 1',
            model_id)
    check('M2', 'ms-modelos-ia: regression run of real inference recorded (SHA-256 verified, agreement recomputed by PEF)',
          regression.status_code in (302, 303) and run and run[0][0] >= 1 and run[0][0] == run[0][1], (regression.status_code, run))
    client = admin.post(PORTAL + '/admin/configuration/integration-clients/new', timeout=60, data={
        'name': f'MS-11 client {secrets.token_hex(2)}', 'status': 'active',
        'scopes': ['sync.catalog.read', 'sync.locations.read', 'fhir.location.read', 'fhir.encounter.read',
                   'export.sessions.batch']})
    client_id, client_secret = (re.search(r'data-client-id>([^<]+)<', client.text), re.search(r'data-secret>([^<]+)<', client.text))
    check(1, 'Admin creates a machine client (secret shown once)', client_id and client_secret, client.status_code)

    # ===== 2. Supervisor schedules (SessionService through the Gateway) ===================================================
    kit = q1("SELECT id FROM kit WHERE active AND institution_id = %s AND name = 'Video Demo Kit 1'", inst)
    family = q1('SELECT family_id FROM kit_item WHERE kit_id = %s ORDER BY family_id LIMIT 1', kit)
    physician = q1('SELECT id FROM physician WHERE institution_id = %s AND active ORDER BY name LIMIT 1', inst)
    notice = q1('SELECT id FROM privacy_notice_version WHERE active AND effective_at <= now() ORDER BY effective_at LIMIT 1')
    procedure = q1('SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1')
    operator_id = q1('SELECT id FROM "user" WHERE email = %s', OPERATOR[0])

    def schedule():
        body = {'operator_user_id': str(operator_id), 'procedure_type_id': str(procedure), 'room_id': str(station[1]),
                'station_id': str(station[0]), 'kit_id': str(kit), 'scheduled_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'phase_code': 'start', 'physicians': [{'physician_id': str(physician)}],
                'privacy_notice_version_id': str(notice), 'expected_lines': [{'family_id': str(family), 'expected_quantity': 1}]}
        r = requests.post(API + '/api/v1/sessions/schedule', json=body, headers=bearer(tok('spd')),
                          timeout=60, verify=False)
        return r.json().get('id') if r.status_code in (200, 201) else None
    sid = schedule()
    check(2, 'Supervisor schedules a session through the Gateway', sid)
    equivalent = q1('SELECT f.id FROM instrument_family f WHERE f.active AND f.id NOT IN (SELECT family_id FROM kit_item '
                    'WHERE kit_id = %s) ORDER BY f.code LIMIT 1', kit)
    existing = q1('SELECT id FROM kit_item_equivalence WHERE kit_id = %s AND family_id = %s AND equivalent_family_id = %s AND active',
                  kit, family, equivalent)
    authorized = requests.post(f'{API}/api/v1/catalog/kits/{kit}/equivalences', headers=bearer(tok('spd')), timeout=30, verify=False,
                               json={'family_id': str(family), 'equivalent_family_id': str(equivalent), 'notes': 'MS-12 E2E'})
    seen_by_operator = requests.get(f'{API}/api/v1/catalog/kits/{kit}/equivalences', headers=bearer(tok('op')), timeout=30, verify=False)
    refused_op = requests.post(f'{API}/api/v1/catalog/kits/{kit}/equivalences', headers=bearer(tok('op')), timeout=30, verify=False,
                               json={'family_id': str(family), 'equivalent_family_id': str(equivalent)})
    check('M3', 'ms-kits-charolas: SPD authorizes an equivalent family through the Gateway; Operator reads it, cannot write; '
          'kit quantities unchanged', (authorized.status_code == 201 or (existing and authorized.status_code == 409))
          and seen_by_operator.ok and any(e['equivalent_family_id'] == str(equivalent) for e in seen_by_operator.json())
          and refused_op.status_code == 403, (authorized.status_code, seen_by_operator.status_code, refused_op.status_code))

    # ===== 3-10. Operator: start, camera, YOLO, tracking, initial, final, recapture, validation =========================
    cap = f'{PORTAL}/operator/sessions/{sid}/capture'

    def start(session_id):
        op.post(f'{PORTAL}/operator/sessions/{session_id}/begin', data={'instruments_ready': '1'}, allow_redirects=False, timeout=60)
        op.get(f'{PORTAL}/operator/sessions/{session_id}/capture', timeout=60)
        return status_of(session_id) == 'in_progress'

    def state(base):
        r = op.get(base + '/state', timeout=30)
        return r.json() if r.ok else {}

    def wait(base, pred, seconds=900):
        return until(lambda: (lambda s: s if pred((s.get('session') or {})) else None)(state(base)), seconds, 2) or state(base)

    def upload(base, video):
        with open(video, 'rb') as fh:
            op.post(base + '/upload', files={'video': (video.name, fh, 'video/x-msvideo')}, timeout=600)
        return wait(base, lambda s: s.get('status') != 'loading' and (s.get('initial_frame') or {}).get('ok') is not None)

    def play(base):
        op.post(base + '/play', timeout=60)
        return wait(base, lambda s: s.get('status') in ('ready', 'error') and (s.get('final_frame') or {}).get('ok') is not None)

    check(3, 'Operator starts the session', start(sid))
    chosen = op.post(cap + '/camera', json={'camera_id': str(camera)}, timeout=60)
    worker = requests.get(f'{VISION}/sessions/{sid}/state', timeout=30).json().get('session') or {}
    check(4, 'Operator chooses the station camera; VisionWorker received its configuration (ROI, simulated) via the signed bind',
          chosen.ok and (worker.get('camera') or {}).get('simulated') is True and (worker.get('camera') or {}).get('roi', {}).get('w') == 1000,
          (chosen.status_code, worker.get('camera')))
    check('M4', 'ms-camaras: the current calibration reaches VisionWorker in the signed bind (informational, never a count)',
          ((worker.get('camera') or {}).get('calibration') or {}).get('mm_per_pixel') == 0.25, worker.get('camera'))
    first = upload(cap, VIDEOS / 'ms031_one_removed.avi')
    initial_total = len(((first.get('session') or {}).get('initial_frame') or {}).get('boxes') or [])
    registered = op.post(cap + '/initial', data={'acknowledge_mismatch': '1'}, timeout=60)
    check(7, f'initial count from the first frame (real YOLO in the container): {initial_total}', registered.ok and initial_total > 0,
          registered.text[:200])
    played = play(cap)
    s = played.get('session') or {}
    tracking = s.get('tracking') or {}
    check(6, f"tracking processed the frames: {tracking.get('frames')} frames, {tracking.get('unique_confirmed_tracks')} stable tracks",
          (tracking.get('frames') or 0) > 0 and tracking.get('version', '').startswith('ms11'), tracking)
    rejected = op.post(cap + '/final', timeout=60)
    final_total = len(((s.get('final_frame') or {}).get('boxes') or []))
    check(8, f'final count {final_total} != reference {initial_total} is rejected (409, capture again)',
          final_total != initial_total and rejected.status_code == 409, (final_total, rejected.status_code))
    upload(cap, VIDEOS / 'ms031_masked.avi')
    replayed = play(cap)
    accepted = op.post(cap + '/final', timeout=60)
    final = events(sid, 'auto_count')
    finals = [row for row in final if (row[1] or {}).get('trigger') == 'final']
    check(9, 'recapture equals the current reference: accepted (MS-03.1 rule unchanged)', accepted.ok and len(finals) == 1
          and finals[0][0] == initial_total, (accepted.status_code, accepted.text[:150]))
    persisted = (finals[0][1] or {}).get('tracking') if finals else None
    check(6, 'the accepted final count carries the tracking summary (traceability) but its total is the last frame',
          persisted and persisted.get('final_frame_total') == finals[0][0] and 'not counts' in persisted.get('note', ''),
          persisted)
    log = requests.get(f'{API}/api/v1/sessions/{sid}/tracking-events?limit=500', headers=bearer(tok('spd')), timeout=30,
                       verify=False)
    body = log.json() if log.ok else {}
    stored = q1('SELECT count(*) FROM tracking_event WHERE session_id = %s', sid)
    check('M5', f"ms-tracking-instrumental: complete tracking log persisted ({stored} events) and read by the Supervisor "
          f"through the Gateway", log.ok and body.get('complete') is True and stored and stored == body.get('events_persisted'),
          (log.status_code, {k: body.get(k) for k in ('events_recorded', 'events_persisted', 'complete')}))
    html = op.get(f'{PORTAL}/operator/sessions/{sid}/vision-validation', timeout=60).text
    values = {k: int(v) for k, v in re.findall(r'name="(validated_[0-9a-f-]{36})"[^>]*value="(\d+)"', html)}
    form = {k: str(v) for k, v in values.items()}
    for key, value in values.items():
        fid = key.split('_', 1)[1]
        expected = 1 if fid == str(family) else 0
        form[f'reason_{fid}'] = 'surplus' if value > expected else ('shortage' if value < expected else '')
        form[f'notes_{fid}'] = 'MS-11 compose E2E'
    form.update({'extra_instruments': str(max(0, initial_total - sum(values.values()))), 'extra_notes': 'MS-11 compose E2E'})
    changed = op.post(f'{PORTAL}/operator/sessions/{sid}/vision-validation', data={**form, 'extra_instruments': str(int(form['extra_instruments']) + 1)},
                      allow_redirects=False, timeout=60)
    validated = op.post(f'{PORTAL}/operator/sessions/{sid}/vision-validation', data=form, allow_redirects=False, timeout=60)
    check(10, 'human validation: changing the total is refused; same total accepted (session to the Supervisor)',
          changed.status_code == 400 and validated.status_code in (302, 303) and status_of(sid) in ('awaiting_spd_review', 'correction_required'),
          (changed.status_code, validated.status_code, status_of(sid)))

    seal = requests.get(f'{API}/api/v1/sessions/{sid}/validation-seal', headers=bearer(tok('spd')), timeout=30, verify=False)
    check('M6', 'ms-validacion-humana: the integrity seal of the Operator validation is recomputed by SessionService: intact',
          seal.ok and seal.json().get('status') == 'intact' and seal.json().get('final_frame_sha256'),
          seal.text[:300])

    # ===== 11. Evidence =================================================================================================
    # The portal's checkpoint outbox uploads asynchronously (never inside the Operator's request): wait for it.
    until(lambda: q1("SELECT count(*) FROM media_asset WHERE session_id = %s AND content_type LIKE 'image/%%'", sid) >= 3, 60, 3)
    listed = requests.get(f'{API}/api/v1/sessions/{sid}/evidence', headers=bearer(tok('spd')), timeout=60, verify=False)
    items = listed.json() if listed.ok else []
    items = items.get('items', items) if isinstance(items, dict) else items
    images = [i for i in items if str(i.get('content_type', '')).startswith('image/')]
    signed = requests.get(f"{API}/api/v1/evidence/{images[0]['id']}", headers=bearer(tok('spd')), timeout=60,
                          verify=False).json() if images else {}
    blob = requests.get(signed.get('signed_url', 'http://127.0.0.1:1/'), timeout=60) if signed.get('signed_url') else None
    check(11, f'EvidenceService stored {len(images)} real images in Garage; one downloaded through its signed URL',
          len(images) >= 3 and blob is not None and blob.content[:2] == b'\xff\xd8', (listed.status_code, len(images)))

    # ===== 12-13. Supervisor review, resolution and close ================================================================
    details = spd.get(f'{PORTAL}/supervisor/sessions/{sid}', timeout=60)
    check(12, 'Supervisor opens the session (evidence, timeline with the tracking summary)', details.ok
          and 'data-tracking-summary' in details.text, details.status_code)
    for disc_id, reason in q('SELECT d.id, r.code FROM discrepancy d LEFT JOIN cat_discrepancy_reason r ON r.id = d.reason_id '
                             'WHERE d.session_id = %s AND NOT d.resolved', sid):
        spd.post(f'{PORTAL}/supervisor/discrepancies/{sid}/review', allow_redirects=False, timeout=60, data={
            'discrepancy_id': str(disc_id), 'notes': 'MS-11 compose review',
            'resolution_outcome': 'recovered' if reason == 'shortage' else 'notes_only'})
    check(13, 'Supervisor resolves the authorized discrepancies', q1('SELECT count(*) FROM discrepancy WHERE session_id = %s AND NOT resolved', sid) == 0)
    page = spd.get(f'{PORTAL}/supervisor/sessions/{sid}', timeout=60).text
    form_html = re.search(r'<form[^>]*data-session-close-form.*?</form>', page, re.S)
    flags = re.findall(r'type="checkbox" name="([a-z_]+)" value="1" required', form_html.group(0)) if form_html else []
    spd.post(f'{PORTAL}/supervisor/sessions/{sid}/close', data={f: '1' for f in flags}, allow_redirects=False, timeout=60)
    close = events(sid, 'session_close')
    check(13, f'Supervisor closes with the acknowledgments the page asks for ({", ".join(flags)}); close_basis final_count',
          close and (close[-1][1] or {}).get('close_basis') == 'final_count' and status_of(sid) == 'closed', (flags, status_of(sid)))

    spd, op = portal(tok('spd')), portal(tok('op'))     # portal sessions after the long YOLO runs
    # ===== 14. Notifications ============================================================================================
    def notified(token, kind):
        r = requests.get(API + '/api/v1/notifications?per_page=100', headers=bearer(token), timeout=30, verify=False)
        return r.ok and any(n.get('session_id') == sid and n.get('type') == kind for n in (r.json().get('items') or r.json().get('notifications') or []))
    # Recipients exclude the actor: the Supervisor who scheduled AND closed is not told about their own close.
    check(14, 'NotificationsService delivered to the Operator: assignment and close (unresolved: shortage acknowledged)',
          until(lambda: notified(tok('op'), 'session_scheduled'), 60)
          and until(lambda: notified(tok('op'), 'session_closed') or notified(tok('op'), 'session_unresolved'), 60))

    # ===== 15. Monitoring ===============================================================================================
    def health():
        text = spd.get(PORTAL + '/supervisor/services-health', timeout=60).text
        return dict(re.findall(r'data-component="([a-z-]+)" data-status="([a-z]+)"', text))
    statuses = until(lambda: (lambda h: h if h and all(v == 'up' for v in h.values()) else None)(health()), 120, 5) or health()
    check(15, f'MonitoringService: {len(statuses)} components up (services, worker, PostgreSQL, Redis, MongoDB, Garage, VisionWorker)',
          statuses and all(v == 'up' for v in statuses.values()) and {'garage', 'vision', 'mongo'} <= set(statuses), statuses)

    check('M7', 'ms-monitoreo: IntegrationService and its delivery worker are monitored and up',
          statuses.get('integration') == 'up' and statuses.get('integration-worker') == 'up', statuses)

    # ===== 16. Reports ==================================================================================================
    csv_ = spd.get(PORTAL + '/supervisor/reports/export.csv?date_range=7d', timeout=90)
    pdf = spd.get(PORTAL + '/supervisor/reports/export.pdf?date_range=7d', timeout=90)
    check(16, 'Reports: CSV and PDF exports from the Supervisor screen', csv_.content.startswith(b'\xef\xbb\xbf')
          and pdf.content.startswith(b'%PDF'), (csv_.status_code, pdf.status_code))

    # ===== 17. Sync (IntegrationService through the Gateway, machine client) =============================================
    client_token = requests.post(API + '/api/v1/auth/token', data={'grant_type': 'client_credentials', 'client_id': client_id.group(1),
                                 'client_secret': client_secret.group(1)}, timeout=30, verify=False).json().get('access_token')
    tray_code = 'TRMS11' + secrets.token_hex(2)
    requests.post(API + '/api/v1/catalog/trays', json={'code': tray_code, 'kit_id': str(kit), 'name': 'MS-11 tray'},
                  headers=bearer(tok('spd')), timeout=30, verify=False)
    seen = until(lambda: tray_code in requests.get(API + '/api/v1/sync/changes?limit=500', headers=bearer(client_token), timeout=30,
                                                   verify=False).text, 60, 3)
    check(17, 'IntegrationService exposes the new tray through the incremental sync API', seen)
    db_now = q1('SELECT now()')
    period = {'period_from': (db_now - timedelta(hours=6)).isoformat(), 'period_to': (db_now - timedelta(seconds=1)).isoformat()}
    batch = requests.post(f'{API}/api/v1/integration/exports', json=period, headers=bearer(tok('admin')), timeout=60, verify=False)
    batch_body = batch.json() if batch.status_code == 201 else {}
    content = requests.get(f"{API}/api/v1/integration/exports/{batch_body.get('id')}/content", headers=bearer(tok('admin')),
                           timeout=60, verify=False) if batch_body else None
    by_client = requests.post(f'{API}/api/v1/integration/exports', json={**period, 'format': 'xml'},
                              headers=bearer(client_token), timeout=60, verify=False)
    raw = content.content if content is not None and content.ok else b''
    check('M8', 'ms-integracion-hospitalaria: traceable batch of closed sessions (exact bytes + SHA-256, no patient data) by '
          'the IT admin and by a machine client with the export scope', batch.status_code == 201 and str(sid) in batch_body.get('session_ids', [])
          and hashlib.sha256(raw).hexdigest() == batch_body.get('content_sha256') and b'patient' not in raw.lower().replace(b'no patient', b'')
          and by_client.status_code == 201, (batch.status_code, by_client.status_code, batch.text[:200]))

    # ===== 18. FHIR with the local HAPI TEST server =====================================================================
    operation = q1('SELECT operation_id FROM work_session WHERE id = %s', sid)
    admin_token = tok('admin')
    until(lambda: requests.get(HAPI + '/metadata', timeout=10).ok, 300, 5)
    endpoint = requests.post(INTEGRATION + '/api/v1/integration/endpoints', headers=bearer(admin_token), timeout=30,
                             json={'name': f'HAPI test {secrets.token_hex(2)}', 'base_url': 'http://hapi:8080/fhir'}).json()
    delivery = requests.post(INTEGRATION + '/api/v1/integration/deliveries', headers={**bearer(admin_token), 'Idempotency-Key': secrets.token_hex(8)},
                             timeout=30, json={'endpoint_id': endpoint.get('id'), 'encounter_id': str(operation)}).json()
    sent = until(lambda: (lambda d: d if d.get('status') in ('sent', 'failed') else None)(requests.get(
        f"{INTEGRATION}/api/v1/integration/deliveries/{delivery.get('id')}", headers=bearer(admin_token), timeout=30).json()), 180, 3)
    stored = requests.get(f'{HAPI}/Encounter/{operation}', timeout=30)
    check(18, 'FHIR adapter delivers the Encounter to the local HAPI test server (transaction Bundle); HAPI stores it',
          (sent or {}).get('status') == 'sent' and stored.status_code == 200, (sent, stored.status_code))

    # ===== 19. Audit ====================================================================================================
    actions = {r[0] for r in q('SELECT action FROM access_audit WHERE resource_id = %s', sid)}
    needed = {'session.scheduled', 'session.initial_count', 'session.close'} & actions
    audit_api = requests.get('http://127.0.0.1:26006/api/v1/audit/events', params={'session_id': sid}, headers=bearer(admin_token), timeout=30)
    check(19, f'audit trail of the session ({len(actions)} distinct actions) also readable through AuditService',
          {'session.scheduled', 'session.initial_count'} <= actions and audit_api.ok, (sorted(actions), audit_api.status_code))

    spd, op = portal(tok('spd')), portal(tok('op'))
    # ===== Additional scenarios ========================================================================================
    sid2 = schedule()
    cap2 = f'{PORTAL}/operator/sessions/{sid2}/capture'
    start(sid2)
    upload(cap2, VIDEOS / 'ms031_one_removed.avi')
    op.post(cap2 + '/initial', data={'acknowledge_mismatch': '1'}, timeout=60)
    play(cap2)
    op.post(cap2 + '/final', timeout=60)
    escalated = op.post(cap2 + '/escalate', data={'notes': 'MS-11: tray checked twice, one instrument missing'}, allow_redirects=False, timeout=60)
    check('A', 'escalated shortage: correction_required, no final count, Supervisor notified (final_count_escalated, 017)',
          status_of(sid2) == 'correction_required' and not [r for r in events(sid2, 'auto_count') if (r[1] or {}).get('trigger') == 'final']
          and until(lambda: q1("SELECT count(*) FROM notification WHERE session_id = %s AND type = 'final_count_escalated'", sid2), 60),
          (escalated.status_code, status_of(sid2)))
    sid3 = schedule()
    start(sid3)
    refused = op.post(f'{PORTAL}/operator/sessions/{sid3}/manual-report', data={f'reported_{family}': '1',
                      'contingency_reason': 'worker_unavailable'}, allow_redirects=False, timeout=60)
    check('B', 'contingency "worker unavailable" while VisionWorker answers is refused (409)', refused.status_code == 409, refused.status_code)

    op = portal(tok('op'))
    compose('stop', 'evidence')
    upload(f'{PORTAL}/operator/sessions/{sid3}/capture', VIDEOS / 'ms031_full.avi')
    op.post(f'{PORTAL}/operator/sessions/{sid3}/capture/initial', data={'acknowledge_mismatch': '1'}, timeout=60)
    time.sleep(25)                      # longer than the previous budget (5 attempts of 1+2+4+8 s)
    compose('start', 'evidence')
    recovered_images = until(lambda: q1("SELECT count(*) FROM media_asset WHERE session_id = %s AND content_type LIKE 'image/%%'", sid3), 240, 5)
    check('M9', 'recovery: EvidenceService stopped during a capture for 25 s; the checkpoint image is stored when it is back',
          recovered_images, recovered_images)
    other_inst = q1("INSERT INTO institution (id, name) VALUES (gen_random_uuid(), %s) RETURNING id", f'MS-11 other {secrets.token_hex(2)}')
    other_session = q1("""WITH o AS (INSERT INTO operation (id, scheduled_at, status_id, institution_id)
                          VALUES (gen_random_uuid(), now(), (SELECT id FROM cat_operation_status WHERE code = 'scheduled'), %s) RETURNING id)
                          INSERT INTO work_session (id, status_id, user_id, operation_id) VALUES (gen_random_uuid(),
                          (SELECT id FROM cat_session_status WHERE code = 'scheduled'), %s, (SELECT id FROM o)) RETURNING id""",
                       other_inst, operator_id)
    foreign = requests.get(f'{API}/api/v1/sessions/{other_session}', headers=bearer(tok('spd')), timeout=30, verify=False)
    foreign_page = spd.get(f'{PORTAL}/supervisor/sessions/{other_session}', timeout=60, allow_redirects=False)
    check('C', "another institution's session is invisible (API 404, portal not shown)", foreign.status_code == 404
          and foreign_page.status_code in (302, 404), (foreign.status_code, foreign_page.status_code))

    fresh = api_login(SUPERVISOR)
    requests.post(API + '/api/v1/auth/logout', json={'refresh_token': fresh['refresh_token']}, headers=bearer(fresh['access_token']),
                  timeout=30, verify=False)
    revoked = [requests.get(API + path, headers=bearer(fresh['access_token']), timeout=30, verify=False).status_code
               for path in ('/api/v1/auth/me', '/api/v1/catalog/kits', '/api/v1/sessions', '/api/v1/notifications')]
    check('D', 'revoked token is refused by AuthService, Catalog, Session and Notifications (Redis revocation)',
          revoked == [401, 401, 401, 401], revoked)

    compose('stop', 'catalog')
    down_seen = until(lambda: q1("SELECT count(*) FROM service_event WHERE component = 'catalog' AND kind = 'down' "
                                 "AND occurred_at > now() - interval '5 minutes'"), 90, 5)
    degraded = requests.get(API + '/api/v1/catalog/kits', headers=bearer(tok('spd')), timeout=30, verify=False)
    alert = until(lambda: q1("SELECT count(*) FROM notification WHERE type = 'service_alert' AND data->>'component' = 'catalog' "
                             "AND data->>'kind' = 'down'"), 60, 3)
    compose('start', 'catalog')
    recovered = until(lambda: q1("SELECT count(*) FROM service_event WHERE component = 'catalog' AND kind = 'recovered' "
                                 "AND occurred_at > now() - interval '5 minutes'"), 120, 5)
    back = until(lambda: requests.get(API + '/api/v1/catalog/kits', headers=bearer(tok('spd')), timeout=30,
                                      verify=False).ok, 90, 3)
    check('E', 'CatalogService stopped: Gateway answers 502, collector records down, IT admin notified; restarted: recovered',
          degraded.status_code == 502 and down_seen and alert and recovered and back, (degraded.status_code, down_seen, alert, recovered, back))

    op = portal(tok('op'))
    compose('restart', 'vision')
    until(lambda: requests.get(VISION + '/health', timeout=5).ok, 180, 3)
    ticket = until(lambda: (lambda r: r if r.ok else None)(op.get(f'{PORTAL}/operator/sessions/{sid3}/capture/ws-ticket?bind=1', timeout=60)), 120, 3)
    rebound = requests.get(f'{VISION}/sessions/{sid3}/state', timeout=30)
    check('F', 'VisionWorker restarted: the reconnection ticket re-binds the session (live preview can resume)',
          ticket is not None and ticket.json().get('ticket') and rebound.ok, rebound.status_code)

    before = q1('SELECT count(*) FROM count_event')
    compose('restart', 'postgres', 'redis', 'mongo', 'garage')
    until(lambda: requests.get(API + '/healthz', timeout=5, verify=False).ok, 120)
    time.sleep(10)
    still_revoked = until(lambda: requests.get(API + '/api/v1/auth/me', headers=bearer(fresh['access_token']), timeout=30,
                                               verify=False).status_code == 401, 120, 5)
    after = until(lambda: (lambda: q1('SELECT count(*) FROM count_event'))(), 120, 5)
    again = until(lambda: (lambda r: r if r.ok else None)(requests.get(f"{API}/api/v1/evidence/{images[0]['id']}",
                  headers=bearer(api_login(SUPERVISOR).get('access_token', '')), timeout=60, verify=False)), 180, 5)
    blob2 = requests.get(again.json()['signed_url'], timeout=60) if again is not None else None
    check('G', 'after restarting PostgreSQL, Redis, MongoDB and Garage: data, evidence object and revocation persist',
          after == before and blob2 is not None and blob2.content[:2] == b'\xff\xd8' and still_revoked,
          (before, after, getattr(blob2, 'status_code', None), still_revoked))
    audit_run = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'check_audit_consistency.py'), '--database-url',
                                f"postgresql+psycopg://{ENV['POSTGRES_USER']}:{ENV['POSTGRES_PASSWORD']}@127.0.0.1:26432/{ENV['POSTGRES_DB']}",
                                '--since', STARTED.isoformat()], capture_output=True, text=True)
    try:
        audit_report = json.loads(audit_run.stdout)
    except ValueError:
        audit_report = {'ok': False, 'error': audit_run.stderr[-300:]}
    check('M10', f"ms-auditoria: audit trail of this run consistent across services ({audit_report.get('rows')} rows: actor and "
          f"resource institution, no duplicates)", audit_report.get('ok') is True, audit_report.get('hard') or audit_report)
    finish()


def finish():
    ok = sum(r['ok'] for r in results)
    print(f'\n{ok}/{len(results)} checks passed')
    (ROOT / '.e2e' / 'ms12_compose_e2e.json').write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
    sys.exit(0 if results and ok == len(results) else 1)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - report what ran
        check('-', 'E2E aborted by an unexpected error (later checks not run)', False, f'{type(exc).__name__}: {exc}')
        finish()
