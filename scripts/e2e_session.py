"""SessionService integration end to end (live services; see GUIA_SESSION.md).

Needs: BackendWebFlask (:5000) started with SESSION_SERVICE_URL (and CATALOG_SERVICE_URL), AuthService
(:5001), VisionWorker (:5002, weights/best.pt), the compose services postgres, redis and session.
Steps 15-16 stop and start the compose `session` service. Everything is real: RF screens over HTTP,
YOLO on the sample tray videos, SessionService /api/v1/sessions, PostgreSQL rows.

The tray videos are built from the UI reference photo: they demonstrate the flow, not the accuracy of
the model. The script creates its own sessions (A, B, C, D, E) on the demo institution.

usage: python scripts/e2e_session.py <videos_dir>
env:   DATABASE_URL, SESSION_SERVICE_URL (as seen from this machine), COMPOSE_ENV_FILE, COMPOSE_PROJECT
"""
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import requests
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
WEB, AUTH = os.environ.get('PEF_WEB_URL', 'http://127.0.0.1:5000'), os.environ.get('PEF_AUTH_URL', 'http://127.0.0.1:5001')
SESSION = os.environ['SESSION_SERVICE_URL'].rstrip('/')
API = f'{SESSION}/api/v1/sessions'
DSN = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')
VIDEOS = Path(sys.argv[1])
COMPOSE = ['docker', 'compose', '--env-file', os.environ.get('COMPOSE_ENV_FILE', '.env')] + (
    ['-p', os.environ['COMPOSE_PROJECT']] if os.environ.get('COMPOSE_PROJECT') else [])
XSD = etree.XMLSchema(etree.parse(str(ROOT / 'apps/SessionService/schemas/session-requests.xsd')))
INSTITUTION = '11111111-1111-1111-1111-111111111111'
KIT = '51515151-0000-4000-8000-000000000001'          # Video Demo Kit 1 (the tray of the sample videos)
ROOM, STATION = '12121212-0000-4000-8000-000000000001', '13131313-0000-4000-8000-000000000001'
PHYSICIAN, PATIENT = '32323232-0000-4000-8000-000000000001', '31313131-0000-4000-8000-000000000001'
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail != '' else ''), flush=True)


def q(sql, *args):
    with psycopg.connect(DSN, autocommit=True) as conn:
        cursor = conn.execute(sql, args)
        return cursor.fetchall() if cursor.description else []


def login(email, password):
    session = requests.Session()
    response = session.post(f'{AUTH}/login', json={'email': email, 'password': password}, timeout=10)
    assert response.status_code == 200, (email, response.status_code)
    return session


def bearer(session, **extra):
    return {'Authorization': f'Bearer {session.cookies.get("access_token")}', **extra}


def is_physical_shortage(discrepancy_id):
    """reason `shortage`: a confirmed missing instrument (only `lost` / `recovered`, never a note)."""
    return q("SELECT r.code FROM discrepancy d LEFT JOIN cat_discrepancy_reason r ON r.id = d.reason_id "
             "WHERE d.id = %s", str(discrepancy_id))[0][0] == 'shortage'


def has_physical_shortage(session_id):
    return bool(q("SELECT 1 FROM discrepancy d JOIN cat_discrepancy_reason r ON r.id = d.reason_id "
                  "WHERE d.session_id = %s AND r.code = 'shortage'", str(session_id)))


def outcome_for(discrepancy_id):
    return 'lost' if is_physical_shortage(discrepancy_id) else 'resolved'


def status(sid):
    return tuple(q('SELECT s.code, w.capture_mode FROM work_session w JOIN cat_session_status s ON s.id = w.status_id '
                   'WHERE w.id = %s', sid)[0])


def events(sid, code):
    return q('SELECT ce.expected_quantity, ce.detected_quantity, ce.payload, ce.user_id FROM count_event ce '
             'JOIN cat_event_type t ON t.id = ce.event_type_id WHERE ce.session_id = %s AND t.code = %s '
             'ORDER BY ce.occurred_at', sid, code)


def audit(sid, outcome='success'):
    return [row[0] for row in q('SELECT action FROM access_audit WHERE resource_id = %s AND outcome = %s '
                                'ORDER BY occurred_at', sid, outcome)]


def flashes(html):
    return html


spd = login('supervisor@instrumed.com', 'DemopwdSPD78!')
op = login('operator@instrumed.com', 'DemopwdOP78!')
op2 = login('operador2@instrumed.com', 'DemopwdOP278!')
admin = login('admin@instrumed.com', 'DemopwdADM78!')
operator_id = str(q("SELECT id FROM \"user\" WHERE email = 'operator@instrumed.com'")[0][0])
operator2_id = str(q("SELECT id FROM \"user\" WHERE email = 'operador2@instrumed.com'")[0][0])
supervisor_id = str(q("SELECT id FROM \"user\" WHERE email = 'supervisor@instrumed.com'")[0][0])
notice = str(q('SELECT id FROM privacy_notice_version WHERE active ORDER BY effective_at DESC LIMIT 1')[0][0])
procedure = str(q("SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1")[0][0])
kit_lines = [(str(f), int(n)) for f, n in q('SELECT family_id, quantity FROM kit_item WHERE kit_id = %s ORDER BY family_id', KIT)]


def stock(family_id):
    return q("SELECT count(*) FROM instrument i JOIN cat_instrument_cycle_status s ON s.id = i.cycle_status_id "
             "WHERE i.family_id = %s AND i.institution_id = %s AND i.active AND s.code = 'available'",
             family_id, INSTITUTION)[0][0]


def schedule_rf(operator_user_id, *, privacy=True, minutes=60):
    when = (datetime.now() + timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M')
    data = {'operator_user_id': operator_user_id, 'procedure_type_id': procedure, 'room_id': ROOM, 'station_id': STATION,
            'kit_id': KIT, 'patient_id': PATIENT, 'scheduled_at': when,
            'family_id[]': [f for f, _ in kit_lines], 'expected_quantity[]': [str(n) for _, n in kit_lines],
            'physician_id[]': [PHYSICIAN], 'surgical_role_code[]': ['surgeon']}
    if privacy:
        data.update({'confirm_privacy': '1', 'privacy_notice_version_id': notice, 'privacy_confirm_checkbox': '1'})
    response = spd.post(f'{WEB}/supervisor/sessions/new', data=data, allow_redirects=False)
    location = response.headers.get('Location', '')
    return response, location.rstrip('/').split('/')[-1] if '/supervisor/sessions/' in location else None


def capture(session, sid, video):
    url = f'{WEB}/operator/sessions/{sid}/capture'
    session.get(url)
    with open(VIDEOS / video, 'rb') as fh:
        code = session.post(f'{url}/upload', files={'video': (video, fh, 'video/mp4')}).status_code
    state = {}
    for _ in range(300):
        state = session.get(f'{url}/state').json()['session']
        if state['status'] != 'processing':
            break
        time.sleep(1)
    finish = session.post(f'{url}/finish', allow_redirects=False)
    return code, state, finish.headers.get('Location', '')


def expected_now(sid):
    return {str(f): int(n) for f, n in q('SELECT family_id, expected_quantity FROM expected_inventory WHERE session_id = %s', sid)}


# ====================================================================================================
print('== 1-2. Supervisor schedules from RF (through SessionService); PostgreSQL holds it')
r, sid_a = schedule_rf(operator_id)
check('programación RF (302 al detalle)', r.status_code == 302 and sid_a, r.status_code)
row = q('SELECT w.user_id, o.institution_id, s.code, (SELECT count(*) FROM expected_inventory e WHERE e.session_id = w.id), '
        '(SELECT count(*) FROM operation_physician p WHERE p.operation_id = o.id), '
        '(SELECT count(*) FROM session_processing_agreement a WHERE a.session_id = w.id) '
        'FROM work_session w JOIN operation o ON o.id = w.operation_id JOIN cat_session_status s ON s.id = w.status_id '
        'WHERE w.id = %s', sid_a)[0]
check('work_session, operation, inventario, equipo y aviso de privacidad en PostgreSQL',
      str(row[0]) == operator_id and str(row[1]) == INSTITUTION and row[2] == 'scheduled' and row[3] == len(kit_lines)
      and row[4] == 1 and row[5] == 1, row)
check('auditoría escrita por SessionService (session.scheduled + privacy.session_agreement)',
      audit(sid_a) == ['session.scheduled', 'privacy.session_agreement'], audit(sid_a))
_, sid_b = schedule_rf(operator_id, minutes=70)
_, sid_c = schedule_rf(operator_id, minutes=80)
_, sid_d = schedule_rf(operator2_id, privacy=False, minutes=90)
check('sesiones B, C (visión) y D (manual) programadas', all((sid_b, sid_c, sid_d)))
detail_page = spd.get(f'{WEB}/supervisor/sessions/{sid_a}').text
check('detalle RF del Supervisor desde el snapshot de SessionService', f'WS-{sid_a[:8].upper()}' in detail_page)

print('== 3-4. Operator lists and begins it in the monolith; initial YOLO count')
listing = op.get(f'{WEB}/operator/sessions').text
check('Operador ve la sesión programada', f'WS-{sid_a[:8].upper()}' in listing)
r = op.post(f'{WEB}/operator/sessions/{sid_a}/begin', data={'instruments_ready': '1'}, allow_redirects=False)
check('inicio por el Operador (monolito) -> captura en modo visión', r.status_code == 302 and r.headers['Location'].endswith('/capture')
      and status(sid_a) == ('in_progress', 'vision'), status(sid_a))
code, state, location = capture(op, sid_a, 'tray_full.mp4')
initial_total = len(state.get('boxes') or [])
check('conteo inicial YOLO registrado', code == 202 and state['status'] == 'ready' and location.endswith('/capture')
      and initial_total > 0, (state['status'], initial_total, location))

print('== 5-6. Supervisor adds instruments (confirmed) -> instruments_added; the reference grows')
before = expected_now(sid_a)
target = next(f for f, n in before.items() if stock(f) > n)
lines = {**before, target: before[target] + 1}
r = spd.post(f'{WEB}/supervisor/sessions/{sid_a}/inventory', allow_redirects=False, data={
    'family_id[]': list(lines), 'expected_quantity[]': [str(n) for n in lines.values()], 'physically_added': '1'})
added = events(sid_a, 'instruments_added')
check('incorporación confirmada desde RF (302)', r.status_code == 302, r.status_code)
check('evento instruments_added escrito por SessionService', len(added) == 1 and added[0][1] == 1
      and str(added[0][3]) == supervisor_id and added[0][2]['confirmed_by_role'] == 'spd_supervisor', added)
check('reference_before = conteo inicial', added and added[0][2]['reference_before'] == initial_total, added and added[0][2])
check('línea live_add en el inventario', q('SELECT source FROM expected_inventory WHERE session_id = %s AND family_id = %s',
                                          sid_a, target)[0][0] == 'live_add')
r = spd.post(f'{WEB}/supervisor/sessions/{sid_a}/inventory', allow_redirects=False, data={
    'family_id[]': list(lines), 'expected_quantity[]': [str(n) for n in lines.values()], 'physically_added': '1'})
check('reenvío idéntico (doble envío) no suma otra incorporación', len(events(sid_a, 'instruments_added')) == 1)

print('== 7. Final count against the new reference')
code, state, location = capture(op, sid_a, 'tray_full.mp4')
blocked = events(sid_a, 'close_blocked')
check('conteo final con el total anterior rechazado contra la referencia nueva (initial + 1)',
      location.endswith('/capture') and blocked and blocked[-1][0] == initial_total + 1 and blocked[-1][1] == initial_total,
      (location, blocked and blocked[-1][:2]))

print('== 8-9. Contingency report (the Operator finishes) -> discrepancy')
form = {f'reported_{f}': str(n) for f, n in expected_now(sid_a).items()}
form.update({'contingency_reason': 'unreliable_detection',
             'contingency_notes': 'The final capture cannot see the added instrument'})
r = op.post(f'{WEB}/operator/sessions/{sid_a}/manual-report', data=form, allow_redirects=False)
check('reporte de contingencia del Operador (monolito)', r.status_code == 302 and status(sid_a)[0] == 'correction_required',
      (r.status_code, status(sid_a)))
disc_a = q('SELECT id, description FROM discrepancy WHERE session_id = %s AND NOT resolved', sid_a)
check('discrepancia generada (faltante contra la referencia)', len(disc_a) >= 1, disc_a)
queue_page = spd.get(f'{WEB}/supervisor/discrepancies').text
check('cola de discrepancias del Supervisor (SessionService)', f'WS-{sid_a[:8].upper()}' in queue_page)
queue_api = requests.get(f'{API}/discrepancies?status=open', headers=bearer(spd), timeout=10).json()
check('API: cola de discrepancias abiertas incluye A', {str(d[0]) for d in disc_a} <= {d['id'] for d in queue_api})
r = spd.post(f'{WEB}/supervisor/sessions/{sid_a}/inventory', allow_redirects=False, data={
    'family_id[]': list(lines), 'expected_quantity[]': [str(n + 1) for n in lines.values()]})
check('RF: el formulario de inventario ya no está disponible tras el reporte (vuelve al detalle, sin cambios)',
      r.status_code == 302 and r.headers['Location'].endswith(f'/supervisor/sessions/{sid_a}')
      and expected_now(sid_a) == lines, r.status_code)
r = requests.put(f'{API}/{sid_a}/expected-inventory', headers=bearer(spd), timeout=10, json={
    'lines': [{'family_id': f, 'expected_quantity': n + 1} for f, n in lines.items()], 'physically_added': True})
check('API: inventario congelado tras el reporte -> 409 inventory_locked', r.status_code == 409 and
      r.json()['details']['reason'] == 'inventory_locked' and expected_now(sid_a) == lines)

print('== 10-11. Supervisor resolves; a physical shortage is never a safe close')
first = disc_a[0][0]
r = spd.post(f'{WEB}/supervisor/discrepancies/{sid_a}/review', allow_redirects=False,
             data={'discrepancy_id': str(first), 'notes': 'Counted on the tray', 'resolution_outcome': 'notes_only'})
check('faltante físico: una nota no lo resuelve (mensaje RF, sin cambios)', is_physical_shortage(first) and
      r.status_code == 400 and 'cannot be resolved with notes only' in r.text and status(sid_a)[0] == 'correction_required',
      r.status_code)
for disc_id, _ in disc_a:
    r = spd.post(f'{WEB}/supervisor/discrepancies/{sid_a}/review', allow_redirects=False,
                 data={'discrepancy_id': str(disc_id), 'notes': 'Piece not found after search',
                       'resolution_outcome': outcome_for(disc_id)})
check('resolución RF (SessionService) -> awaiting_spd_review', status(sid_a)[0] == 'awaiting_spd_review', status(sid_a))
r = spd.post(f'{WEB}/supervisor/sessions/{sid_a}/close', data={'material_recovered': '1'}, allow_redirects=False)
check('cierre sin revisar la contingencia: 400 con el mensaje RF', r.status_code == 400 and
      'Confirm that you reviewed the contingency count before closing.' in r.text, r.status_code)
r = spd.post(f'{WEB}/supervisor/sessions/{sid_a}/close', data={'material_recovered': '1', 'contingency_reviewed': '1'},
             allow_redirects=False)
check('cierre con faltante sin reconocerlo: 409 con el mensaje RF, sigue abierta', r.status_code == 409 and
      'Acknowledge that it is closed as an unreconciled shortage.' in r.text and status(sid_a)[0] == 'awaiting_spd_review',
      r.status_code)
r = spd.post(f'{WEB}/supervisor/sessions/{sid_a}/close', allow_redirects=False,
             data={'material_recovered': '1', 'contingency_reviewed': '1', 'unreconciled_shortage_acknowledged': '1'})
close_events = events(sid_a, 'session_close')
check('cierre como faltante no conciliado -> closed, safe_close_confirmed=false y los ids del faltante',
      r.status_code == 302 and status(sid_a)[0] == 'closed' and len(close_events) == 1
      and close_events[0][2]['contingency_reviewed'] is True and close_events[0][2]['safe_close_confirmed'] is False
      and close_events[0][2]['unreconciled_physical_shortages'], (r.status_code, status(sid_a)))
r = requests.post(f'{API}/{sid_a}/close', json={'material_recovered': True, 'contingency_reviewed': True},
                  headers=bearer(spd), timeout=10)
check('segundo cierre: 409 not_awaiting_spd_review (sin duplicar)', r.status_code == 409 and
      r.json()['details']['reason'] == 'not_awaiting_spd_review' and len(events(sid_a, 'session_close')) == 1)

print('== B. Initial = final, human validation with a shortage; concurrent resolution; close')
op.post(f'{WEB}/operator/sessions/{sid_b}/begin', data={'instruments_ready': '1'})
_, _, loc1 = capture(op, sid_b, 'tray_full.mp4')
_, _, loc2 = capture(op, sid_b, 'tray_full.mp4')
check('B: conteo inicial y final coincidentes', loc1.endswith('/capture') and loc2.endswith('/detection-summary'), (loc1, loc2))
exp_b = expected_now(sid_b)
short = next(iter(exp_b))
form = {f'validated_{f}': str(n - 1 if f == short else n) for f, n in exp_b.items()}
form.update({f'notes_{f}': 'Verified on the physical tray' for f in exp_b})
form[f'reason_{short}'] = 'shortage'
r = op.post(f'{WEB}/operator/sessions/{sid_b}/vision-validation', data=form, allow_redirects=False)
check('B: validación humana del Operador (monolito) -> correction_required', r.status_code == 302 and
      status(sid_b)[0] == 'correction_required', (r.status_code, status(sid_b)))
disc_b = str(q('SELECT id FROM discrepancy WHERE session_id = %s AND NOT resolved', sid_b)[0][0])
responses = []
barrier = threading.Barrier(2)


def resolve_b():
    barrier.wait()
    responses.append(requests.post(f'{API}/{sid_b}/discrepancies/{disc_b}/resolve', headers=bearer(spd), timeout=20,
                                   json={'notes': 'Piece not found after search', 'outcome': 'lost'}))


threads = [threading.Thread(target=resolve_b) for _ in range(2)]
[t.start() for t in threads]
[t.join() for t in threads]
codes = sorted(r.status_code for r in responses)
check('B: resolución concurrente de la misma discrepancia -> 200 + 409', codes == [200, 409], codes)
lost = q("SELECT count(*) FROM instrument_cycle_event e JOIN cat_instrument_cycle_status s ON s.id = e.cycle_status_id "
         "WHERE e.session_id = %s AND s.code = 'lost'", sid_b)[0][0]
check('B: regla por familia — una pieza marcada perdida una sola vez', lost == 1, lost)
for (other,) in q('SELECT id FROM discrepancy WHERE session_id = %s AND NOT resolved', sid_b):
    spd.post(f'{WEB}/supervisor/discrepancies/{sid_b}/review', allow_redirects=False,
             data={'discrepancy_id': str(other), 'notes': 'Reviewed on the tray', 'resolution_outcome': outcome_for(other)})
r = spd.post(f'{WEB}/supervisor/sessions/{sid_b}/close', allow_redirects=False,
             data={'material_recovered': '1', 'unreconciled_shortage_acknowledged': '1'})
check('B: cerrada como faltante no conciliado (pieza perdida, no es cierre seguro)', r.status_code == 302 and
      status(sid_b)[0] == 'closed' and events(sid_b, 'session_close')[0][2]['safe_close_confirmed'] is False)

print('== 12. Permissions, unknown sessions, invalid transitions')
check('Operador en pantalla del Supervisor: 403', op.get(f'{WEB}/supervisor/sessions', allow_redirects=False).status_code == 403)
r = requests.post(f'{API}/schedule', json={}, headers=bearer(op), timeout=10)
check('Operador directo a SessionService (programar): 403', r.status_code == 403 and r.json()['error'] == 'forbidden')
r = requests.get(f'{API}?expand=detail', headers=bearer(admin), timeout=10)
check('Administrador: 403 en SessionService', r.status_code == 403)
denied = q("SELECT action FROM access_audit WHERE outcome = 'denied' AND actor_user_id = %s AND action = 'DENIED_POST_WORK_SESSION' "
           "AND occurred_at > now() - interval '5 minutes'", operator_id)
check('acceso denegado auditado (DENIED_POST_WORK_SESSION)', len(denied) >= 1, denied)
r = requests.get(f'{API}?expand=detail', headers={'X-User-Id': supervisor_id, 'X-User-Role': 'spd_supervisor'}, timeout=10)
check('cabeceras de identidad sin token: 401', r.status_code == 401)
ghost = str(uuid.uuid4())
check('sesión inexistente: 404 (RF y API)', spd.get(f'{WEB}/supervisor/sessions/{ghost}').status_code == 404
      and requests.get(f'{API}/{ghost}?expand=detail', headers=bearer(spd), timeout=10).status_code == 404)
r = requests.post(f'{API}/{sid_c}/close', json={'material_recovered': True}, headers=bearer(spd), timeout=10)
check('cierre de una sesión programada: 409', r.status_code == 409 and r.json()['details']['reason'] == 'not_awaiting_spd_review')
r = requests.post(f'{API}/{sid_a}/discrepancies/{disc_a[0][0]}/resolve', json={'notes': 'x', 'outcome': 'resolved'},
                  headers=bearer(spd), timeout=10)
check('resolver en sesión cerrada: 409 session_not_reviewable', r.status_code == 409 and
      r.json()['details']['reason'] == 'session_not_reviewable')
r = requests.post(f'{API}/{sid_c}/start', headers=bearer(op), timeout=10)
check('endpoint antiguo de conteo del Operador deshabilitado: 410', r.status_code == 410 and
      r.json()['details']['reason'] == 'operator_flow_in_portal' and status(sid_c)[0] == 'scheduled')

print('== 13. JSON and XML')
js = requests.get(f'{API}/{sid_a}?expand=detail', headers=bearer(spd), timeout=10).json()
check('detalle JSON con eventos, discrepancias y contingencia', js['status']['code'] == 'closed' and js['contingency']
      and any(e['event_code'] == 'instruments_added' for e in js['events']))
xml = requests.get(f'{API}/{sid_a}?expand=detail', headers=bearer(spd, Accept='application/xml'), timeout=10)
doc = etree.fromstring(xml.content)
check('detalle XML (misma información)', doc.tag == 'session' and doc.findtext('status/code') == 'closed'
      and len(doc.findall('events/event')) == len(js['events']))
when = (datetime.now(timezone.utc) + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M:%SZ')
body = (f'<schedule_request><operator_user_id>{operator2_id}</operator_user_id><procedure_type_id>{procedure}</procedure_type_id>'
        f'<room_id>{ROOM}</room_id><station_id>{STATION}</station_id><kit_id>{KIT}</kit_id><scheduled_at>{when}</scheduled_at>'
        f'<phase_code>start</phase_code><physicians><physician><physician_id>{PHYSICIAN}</physician_id></physician></physicians>'
        '<expected_lines>' + ''.join(f'<line><family_id>{f}</family_id><expected_quantity>{n}</expected_quantity></line>'
                                     for f, n in kit_lines) + '</expected_lines></schedule_request>').encode()
check('la petición XML cumple session-requests.xsd', XSD.validate(etree.fromstring(body)))
r = requests.post(f'{API}/schedule', data=body, headers=bearer(spd, **{'Content-Type': 'application/xml', 'Accept': 'application/xml'}),
                  timeout=10)
sid_e = etree.fromstring(r.content).findtext('id') if r.status_code == 201 else None
check('programación por API en XML: 201', r.status_code == 201 and sid_e, r.status_code)
bad = requests.post(f'{API}/schedule', data=b'<schedule_request><kit_id lang="x">1</kit_id></schedule_request>',
                    headers=bearer(spd, **{'Content-Type': 'application/xml'}), timeout=10)
check('XML que no cumple el XSD: 400 xsd_validation_failed', bad.status_code == 400 and
      bad.json()['details']['reason'] == 'xsd_validation_failed')
paged = requests.get(f'{API}?expand=detail&status=closed&page=1&per_page=2', headers=bearer(spd), timeout=10)
check('paginación y filtro por estado', paged.status_code == 200 and len(paged.json()) <= 2
      and int(paged.headers['X-Total-Count']) >= 2 and all(s['status']['code'] == 'closed' for s in paged.json()))

print('== 14. Audit in PostgreSQL')
actions = audit(sid_a)
for action in ('session.scheduled', 'privacy.session_agreement', 'session.start', 'session.instruments_added',
               'session.contingency_close', 'discrepancy.resolve', 'session.spd_close'):
    check(f'access_audit {action}', action in actions, actions)
rows = q("SELECT correlation_id, actor_user_id FROM access_audit WHERE resource_id = %s AND action = 'session.spd_close'", sid_a)
check('auditoría del cierre: actor = Supervisor y correlation id del portal', rows and len(rows[0][0] or '') == 32
      and str(rows[0][1]) == supervisor_id, rows)

print('== Concurrency: Operator report (portal) vs Supervisor inventory (SessionService) on session D')
op2.post(f'{WEB}/operator/sessions/{sid_d}/begin', data={'instruments_ready': '1'})
exp_d = expected_now(sid_d)
target_d = next(f for f, n in exp_d.items() if stock(f) > n)
out = {}
barrier = threading.Barrier(2)


def operator_report():
    form = {f'reported_{f}': str(n) for f, n in exp_d.items()}
    barrier.wait()
    out['report'] = op2.post(f'{WEB}/operator/sessions/{sid_d}/manual-report', data=form, allow_redirects=False)


def supervisor_edit():
    lines_d = {**exp_d, target_d: exp_d[target_d] + 1}
    barrier.wait()
    out['edit'] = spd.post(f'{WEB}/supervisor/sessions/{sid_d}/inventory', allow_redirects=False, data={
        'family_id[]': list(lines_d), 'expected_quantity[]': [str(n) for n in lines_d.values()], 'physically_added': '1'})


threads = [threading.Thread(target=operator_report), threading.Thread(target=supervisor_edit)]
[t.start() for t in threads]
[t.join() for t in threads]
final_qty = expected_now(sid_d)[target_d]


def counted_d():
    return q("SELECT ce.expected_quantity FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id "
             "WHERE ce.session_id = %s AND t.code = 'manual_count' AND ce.family_id = %s", sid_d, target_d)


edit_won = out['edit'].status_code == 302
# Both orders are valid serializations; neither may leave a mixed state:
# - report first: the session is reported against the original inventory and the edit is refused;
# - edit first: the report (built on the old quantities) is refused WITHOUT writing anything (still
#   in_progress, no manual_count); the Operator then reports against the new inventory.
rejected_cleanly = (edit_won and out['report'].status_code == 400 and status(sid_d)[0] == 'in_progress'
                    and not counted_d())
if rejected_cleanly:
    op2.post(f'{WEB}/operator/sessions/{sid_d}/manual-report', allow_redirects=False,
             data={f'reported_{f}': str(n) for f, n in expected_now(sid_d).items()})
counted = counted_d()
check('D: las dos escrituras se serializan sobre la misma fila (sin estado mixto)',
      (rejected_cleanly or out['report'].status_code == 302)
      and status(sid_d)[0] in ('awaiting_spd_review', 'correction_required') and counted and counted[0][0] == final_qty,
      {'order': 'edit, then report' if edit_won else 'report, then edit', 'edit': out['edit'].status_code,
       'report': out['report'].status_code, 'expected_now': final_qty, 'manual_count_expected': counted and counted[0][0]})
check('D: si el reporte ganó, la edición del Supervisor responde el mensaje RF y no cambia nada',
      edit_won or ('Inventory can only be modified while scheduled or in progress.' in out['edit'].text
                   and final_qty == exp_d[target_d]))

print('== 15. SessionService stopped: the Operator keeps working on session C')
subprocess.run(COMPOSE + ['stop', 'session'], cwd=ROOT, check=True, capture_output=True)
try:
    requests.get(f'{SESSION}/health', timeout=2)
    session_down = False
except requests.RequestException:
    session_down = True
check('contenedor session detenido', session_down)
check('Operador lista sus sesiones', f'WS-{sid_c[:8].upper()}' in op.get(f'{WEB}/operator/sessions').text)
r = op.post(f'{WEB}/operator/sessions/{sid_c}/begin', data={'instruments_ready': '1'}, allow_redirects=False)
check('Operador inicia la sesión programada', r.status_code == 302 and status(sid_c) == ('in_progress', 'vision'), status(sid_c))
_, st1, loc1 = capture(op, sid_c, 'tray_full.mp4')
_, st2, loc2 = capture(op, sid_c, 'tray_full.mp4')
check('VisionWorker: conteo inicial y final', loc1.endswith('/capture') and loc2.endswith('/detection-summary'), (loc1, loc2))
exp_c = expected_now(sid_c)
form = {f'validated_{f}': str(n) for f, n in exp_c.items()}
form.update({f'notes_{f}': 'Verified on the physical tray' for f in exp_c})
r = op.post(f'{WEB}/operator/sessions/{sid_c}/vision-validation', data=form, allow_redirects=False)
finished = status(sid_c)[0]
check('validación humana: el Operador termina su participación', r.status_code == 302 and
      finished in ('awaiting_spd_review', 'correction_required'), (r.status_code, status(sid_c)))
page = spd.get(f'{WEB}/supervisor/sessions')
check('Supervisor: mensaje controlado 503 (sin datos locales)', page.status_code == 503 and
      'The session service is temporarily unavailable' in page.text, page.status_code)
r = spd.post(f'{WEB}/supervisor/sessions/{sid_c}/close', data={'material_recovered': '1'}, allow_redirects=False)
check('Supervisor: el cierre no se ejecuta localmente (503, estado sin cambios)', r.status_code == 503 and
      status(sid_c)[0] == finished and not events(sid_c, 'session_close'), r.status_code)

print('== 16. SessionService restarted: the Supervisor operations recover')
subprocess.run(COMPOSE + ['start', 'session'], cwd=ROOT, check=True, capture_output=True)
for _ in range(60):
    try:
        if requests.get(f'{SESSION}/health', timeout=2).status_code == 200:
            break
    except requests.RequestException:
        pass
    time.sleep(1)
check('Supervisor vuelve a ver la sesión C', f'WS-{sid_c[:8].upper()}' in spd.get(f'{WEB}/supervisor/sessions').text)
for (other,) in q('SELECT id FROM discrepancy WHERE session_id = %s AND NOT resolved', sid_c):
    spd.post(f'{WEB}/supervisor/discrepancies/{sid_c}/review', allow_redirects=False,
             data={'discrepancy_id': str(other), 'notes': 'Extra piece belongs to another tray',
                   'resolution_outcome': outcome_for(other)})
check('C: discrepancias resueltas tras la recuperación -> awaiting_spd_review', status(sid_c)[0] == 'awaiting_spd_review')
responses = []
barrier = threading.Barrier(2)


def close_c():
    barrier.wait()
    responses.append(requests.post(f'{API}/{sid_c}/close', headers=bearer(spd), timeout=20, json={
        'material_recovered': True, 'unreconciled_shortage_acknowledged': has_physical_shortage(sid_c)}))


threads = [threading.Thread(target=close_c) for _ in range(2)]
[t.start() for t in threads]
[t.join() for t in threads]
check('cierre concurrente de C: 200 + 409, un solo session_close', sorted(r.status_code for r in responses) == [200, 409]
      and len(events(sid_c, 'session_close')) == 1 and status(sid_c)[0] == 'closed',
      sorted(r.status_code for r in responses))

print('== Snapshot parity: SessionService expand=detail == portal local snapshot')
sys.path.insert(0, str(ROOT / 'apps' / 'BackendWebFlask'))
os.environ['MONGO_URL'] = ''
from app import app  # noqa: E402
from services import session_snapshot  # noqa: E402
with app.app_context():
    ids = [sid_a, sid_b, sid_c, sid_d, sid_e] + [str(r[0]) for r in q(
        'SELECT w.id FROM work_session w JOIN operation o ON o.id = w.operation_id WHERE o.institution_id = %s '
        'ORDER BY w.updated_at DESC LIMIT 15', INSTITUTION)]
    same = 0
    for sid in dict.fromkeys(ids):
        local = json.loads(json.dumps(session_snapshot.session_detail(uuid.UUID(sid), uuid.UUID(INSTITUTION))))
        remote = requests.get(f'{API}/{sid}?expand=detail', headers=bearer(spd), timeout=10).json()
        if local == remote:
            same += 1
        else:
            diff = sorted(k for k in set(local) | set(remote) if local.get(k) != remote.get(k))
            print('   differs', sid, diff)
    check(f'instantáneas idénticas en {same} sesiones reales', same == len(dict.fromkeys(ids)), same)
    remote_list = requests.get(f'{API}?expand=detail', headers=bearer(spd), timeout=10).json()
    local_list = json.loads(json.dumps(session_snapshot.list_sessions(uuid.UUID(INSTITUTION))))
    check('lista: misma instantánea en ambas fuentes', sorted(remote_list, key=lambda s: s['id']) ==
          sorted(local_list, key=lambda s: s['id']), (len(remote_list), len(local_list)))

print(f'\n{sum(results)}/{len(results)} checks passed')
sys.exit(0 if all(results) else 1)
