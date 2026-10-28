"""Real HTTP flow on an isolated seeded deployment; never run on production.

No mocked inference, no physical-integrity attestation. The human-validation
submission is explicitly an automated test using the actual model suggestions.
Run inside pef_video_live-web-1 with the real video path as argument.
"""
import json
import os
import sys
import time
import re
from datetime import datetime, timedelta

import psycopg
import requests

if os.getenv('PEF_ISOLATED_VIDEO_TEST') != '1':
    raise SystemExit('Requires explicit isolated-test environment')
WEB, AUTH = 'http://web:5000', 'http://auth:5001'
dsn = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')

def q(sql, *params):
    with psycopg.connect(dsn) as conn:
        return conn.execute(sql, params).fetchall()

def login(email, password):
    s = requests.Session()
    r = s.post(AUTH + '/login', json={'email': email, 'password': password}, timeout=20)
    assert r.status_code == 200, r.status_code
    # Container service names differ; share the authenticated cookie explicitly
    # within this isolated test client, as localhost ports do in a browser.
    cookies = {cookie.name: cookie.value for cookie in s.cookies}
    s.cookies.clear()
    s.cookies.update(cookies)
    return s

spd = login('supervisor@instrumed.com', 'DemopwdSPD78!')
op = login('operator@instrumed.com', 'DemopwdOP78!')
if os.getenv('PEF_VERIFY_SESSION'):
    import hashlib
    sid = os.environ['PEF_VERIFY_SESSION']
    api = 'http://session:5004/api/v1/sessions/' + sid
    r = requests.post(api + '/operator-close', headers={'Authorization': 'Bearer ' + op.cookies.get('access_token')},
                      json={'material_recovered': True, 'physical_integrity_confirmed': True}, timeout=30)
    assert r.status_code == 409, (r.status_code, r.text[:300])
    rows = q("SELECT payload FROM count_event e JOIN cat_event_type t ON t.id=e.event_type_id "
             "WHERE e.session_id=%s AND t.code='auto_count' AND e.payload->>'trigger' IN ('initial','final')", sid)
    assert len(rows) == 2
    for (p,) in rows:
        assert hashlib.sha256(p['frame_jpeg_b64'].encode()).hexdigest() == p['frame_sha256']
    read = requests.get(api, headers={'Authorization': 'Bearer ' + spd.cookies.get('access_token')}, timeout=30)
    assert read.status_code == 200 and read.json()['status_code'] == 'correction_required'
    media = q('SELECT count(*) FROM media_asset WHERE session_id=%s', sid)[0][0]
    assert media >= 2, media
    print(json.dumps({'session': sid, 'operator_close_with_discrepancies': r.status_code,
                      'blockers': r.json().get('details'), 'endpoint_events': len(rows),
                      'image_hashes_valid': True, 'media_assets': media,
                      'endpoint_totals': {p['trigger']: p.get('capture_total') for (p,) in rows},
                      'supervisor_read': read.status_code}), flush=True)
    raise SystemExit(0)
kit = '51515151-0000-4000-8000-000000000001'
lines = [(str(f), int(n)) for f, n in q('SELECT family_id, quantity FROM kit_item WHERE kit_id=%s', kit)]
operator = str(q('SELECT id FROM "user" WHERE email=%s', 'operator@instrumed.com')[0][0])
notice = str(q('SELECT id FROM privacy_notice_version WHERE active ORDER BY effective_at DESC LIMIT 1')[0][0])
form = {'operator_user_id': operator, 'kit_id': kit,
        'procedure_type_id': str(q('SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1')[0][0]),
        'room_id': '12121212-0000-4000-8000-000000000001',
        'station_id': '13131313-0000-4000-8000-000000000001',
        'patient_id': '31313131-0000-4000-8000-000000000001',
        'scheduled_at': (datetime.now() + timedelta(minutes=60)).strftime('%Y-%m-%dT%H:%M'),
        'family_id[]': [f for f, _ in lines], 'expected_quantity[]': [str(n) for _, n in lines],
        'physician_id[]': ['32323232-0000-4000-8000-000000000001'], 'surgical_role_code[]': ['surgeon'],
        'confirm_privacy': '1', 'privacy_notice_version_id': notice, 'privacy_confirm_checkbox': '1'}
resume = os.getenv('PEF_RESUME_SESSION')
if resume:
    sid = resume
else:
    r = spd.post(WEB + '/supervisor/sessions/new', data=form, allow_redirects=False, timeout=30)
    assert r.status_code == 302 and '/supervisor/sessions/' in r.headers.get('Location', ''), (r.status_code, r.text[:300])
    sid = r.headers['Location'].rstrip('/').split('/')[-1]
    if sid == 'new':
        html = spd.get(WEB + r.headers['Location'], timeout=30).text
        alerts = re.findall(r'<[^>]*class="[^"]*alert[^>]*>.*?</div>', html, re.S)
        raise AssertionError([re.sub('<[^>]+>', ' ', alert).strip() for alert in alerts])
base = WEB + '/operator/sessions/' + sid
print(json.dumps({'scheduled_session': sid, 'expected': dict(lines)}), flush=True)
if not resume:
    r = op.post(base + '/begin', data={'instruments_ready': '1'}, allow_redirects=False, timeout=30)
    assert r.status_code == 302, r.status_code
    page = op.get(base + '/capture', timeout=30)
    assert page.status_code == 200 and 'autoplay' not in page.text
    assert op.post(base + '/capture/play', timeout=30).status_code == 409
    with open(sys.argv[1], 'rb') as video:
        r = op.post(base + '/capture/upload', files={'video': ('real.mp4', video, 'video/mp4')}, timeout=120)
    assert r.status_code == 202, (r.status_code, r.text[:300])

def wait_for(target):
    deadline = time.monotonic() + 1200
    last = None
    while time.monotonic() < deadline:
        try:
            r = op.get(base + '/capture/state', timeout=30)
        except requests.RequestException as exc:
            print(json.dumps({'poll_retry': type(exc).__name__}), flush=True)
            time.sleep(2)
            continue
        assert r.status_code == 200, r.status_code
        state = r.json()['session']
        progress = (state['status'], round(state.get('progress', 0), 1))
        if progress != last:
            print(json.dumps({'worker': progress}), flush=True)
            last = progress
        if state['status'] == target:
            return state
        assert state['status'] != 'error', state.get('last_error')
        time.sleep(2)
    raise AssertionError('Worker timeout')

if not resume:
    state = wait_for('awaiting_initial')
    assert state.get('frames_processed') == 0 and state['initial_frame']['frame_index'] == 0
    assert op.post(base + '/capture/initial', data={'acknowledge_mismatch': '1'}, timeout=30).status_code == 200
    assert op.post(base + '/capture/initial', timeout=30).json()['already_registered']
    assert op.post(base + '/capture/final', timeout=30).status_code == 409
    assert op.post(base + '/capture/play', timeout=30).status_code == 202
state = wait_for('ready')
assert op.post(base + '/capture/final', data={'acknowledge_mismatch': '1'}, timeout=30).status_code == 200
assert op.post(base + '/capture/final', timeout=30).json()['already_confirmed']
page = op.get(base + '/vision-validation', timeout=30)
assert page.status_code == 200
payload = q("SELECT payload FROM count_event e JOIN cat_event_type t ON t.id=e.event_type_id "
            "WHERE e.session_id=%s AND t.code='auto_count' AND e.payload->>'trigger'='final'", sid)[0][0]
detected = {str(f['family_id']): int(f['detected_quantity']) for f in payload['board']['families']}
validation = {'extra_instruments': str(sum(x.get('count', 0) for x in payload.get('out_of_kit', [])))}
for fid, qty in lines:
    n = detected.get(fid, 0)
    validation['validated_' + fid] = str(n)
    if n != qty:
        validation['reason_' + fid] = 'shortage' if n < qty else 'surplus'
        validation['notes_' + fid] = 'Isolated automated flow test: model suggestion, no clinical attestation'
r = op.post(base + '/vision-validation', data=validation, allow_redirects=False, timeout=30)
assert r.status_code == 302, (r.status_code, r.text[:300])
token = op.cookies.get('access_token')
r = requests.post('http://session:5004/api/v1/sessions/' + sid + '/operator-close',
                  headers={'Authorization': 'Bearer ' + token}, json={'material_recovered': True}, timeout=30)
assert r.status_code == 400, r.text
status = q('SELECT c.code FROM work_session w JOIN cat_session_status c ON c.id=w.status_id WHERE w.id=%s', sid)[0][0]
assert status != 'closed'
print(json.dumps({'session': sid, 'status': status, 'initial_frame': state['initial_frame']['frame_index'],
                  'final_frame': state['final_frame']['frame_index'], 'final_families': detected,
                  'physical_confirmation_omitted_rejected': r.status_code,
                  'result': 'real HTTP capture and validation completed; no physical safe close attempted'}), flush=True)
