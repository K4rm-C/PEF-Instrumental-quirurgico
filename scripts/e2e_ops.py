"""E2E de Supervisor y Operador RF contra Web(5000) + Auth(5001) + PostgreSQL reales."""
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg
import requests

WEB = os.environ.get('PEF_WEB_URL', 'http://127.0.0.1:5000')
AUTH = os.environ.get('PEF_AUTH_URL', 'http://127.0.0.1:5001')
DSN = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')
VISION_UP = os.environ.get('VISION_UP') == '1'
results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail != '' else ''))


def q(sql, *args):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, args).fetchall()


def login(email, password):
    s = requests.Session()
    r = s.post(f'{AUTH}/login', json={'email': email, 'password': password}, timeout=10)
    return s, r


def status(session_id):
    return q('SELECT s.code, w.capture_mode FROM work_session w JOIN cat_session_status s ON s.id=w.status_id '
             'WHERE w.id=%s', session_id)[0]


sup, r = login('supervisor@instrumed.com', 'DemopwdSPD78!')
check('login supervisor', r.status_code == 200, r.status_code)
op, r = login('operator@instrumed.com', 'DemopwdOP78!')
check('login operador', r.status_code == 200, r.status_code)
op2, r = login('operador2@instrumed.com', 'DemopwdOP278!')
check('login operador 2', r.status_code == 200, r.status_code)

seeded = [row[0] for row in q('SELECT id FROM work_session ORDER BY id')]
# Sessions assigned to operator@ (other operators' sessions are 404 for this operator by design).
seeded_op = [row[0] for row in q("SELECT w.id FROM work_session w JOIN \"user\" u ON u.id = w.user_id "
                                   "WHERE u.email = 'operator@instrumed.com' ORDER BY w.id")]

# --- Supervisor: pantallas RF ---------------------------------------------------------------
for path in ['/supervisor/dashboard', '/supervisor/sessions', '/supervisor/sessions/new',
             '/supervisor/sessions/history', '/supervisor/discrepancies', '/supervisor/reports',
             '/supervisor/indicators', '/supervisor/audit-log', '/supervisor/services-health',
             '/supervisor/profile'] + [f'/supervisor/sessions/{sid}' for sid in seeded]:
    resp = sup.get(WEB + path, allow_redirects=False)
    check(f'SUP GET {path}', resp.status_code == 200 and 'Traceback' not in resp.text, resp.status_code)

# --- Supervisor: programar sesiones (RF-SP-02) ----------------------------------------------
operator_id = q("SELECT id FROM \"user\" WHERE email='operator@instrumed.com'")[0][0]
kit_id, proc_id = q('SELECT pk.kit_id, pk.procedure_type_id FROM procedure_kit pk JOIN kit k ON k.id=pk.kit_id '
                    'WHERE k.active AND pk.active ORDER BY pk.is_default DESC LIMIT 1')[0]
room_id, station_id = q('SELECT r.id, cs.id FROM capture_station cs JOIN operating_room r ON r.id=cs.room_id '
                        'WHERE cs.active AND r.active LIMIT 1')[0]
lines = q('SELECT family_id, quantity FROM kit_item WHERE kit_id=%s', kit_id)
physician_id = q('SELECT id FROM physician ORDER BY id LIMIT 1')[0][0]
patient_id = q('SELECT id FROM patient ORDER BY id LIMIT 1')[0][0]
privacy_id = q('SELECT id FROM privacy_notice_version ORDER BY version DESC LIMIT 1')[0][0]
when = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')


def schedule(with_privacy):
    data = {'action': 'schedule', 'operator_user_id': str(operator_id), 'procedure_type_id': str(proc_id),
            'room_id': str(room_id), 'station_id': str(station_id), 'kit_id': str(kit_id), 'scheduled_at': when,
            'family_id[]': [str(f) for f, _ in lines], 'expected_quantity[]': [str(n) for _, n in lines],
            'physician_id[]': [str(physician_id)], 'surgical_role_code[]': ['surgeon'],
            'patient_id': str(patient_id)}
    if with_privacy:
        data.update({'confirm_privacy': '1', 'privacy_notice_version_id': str(privacy_id),
                     'privacy_confirm_checkbox': '1'})
    return sup.post(WEB + '/supervisor/sessions/new', data=data, allow_redirects=False)


resp = schedule(False)
manual_sid = resp.headers.get('Location', '').rstrip('/').split('/')[-1]
ok = resp.status_code == 302 and status(manual_sid)[0] == 'scheduled'
check('programar sesión sin privacidad', ok, resp.status_code)
check('inventario esperado congelado al programar',
      q('SELECT count(*) FROM expected_inventory WHERE session_id=%s', manual_sid)[0][0] == len(lines))
resp = schedule(True)
vision_sid = resp.headers.get('Location', '').rstrip('/').split('/')[-1]
check('programar sesión con aviso de privacidad (Via A)', resp.status_code == 302 and
      q('SELECT count(*) FROM session_processing_agreement WHERE session_id=%s', vision_sid)[0][0] == 1,
      resp.status_code)
bad = sup.post(WEB + '/supervisor/sessions/new', data={'action': 'schedule', 'scheduled_at': 'no-date'},
               allow_redirects=False)
check('programar con datos inválidos -> 400', bad.status_code == 400, bad.status_code)
resp = sup.post(WEB + '/supervisor/sessions/stock-recheck',
                json={'lines': [{'family_id': str(lines[0][0]), 'expected_quantity': 1}]})
check('semáforo de stock (JSON)', resp.status_code == 200 and 'lines' in resp.json(), resp.status_code)

# --- Operador: pantallas y aislamiento ------------------------------------------------------
for path in ['/operator/sessions', '/operator/sessions/history', f'/operator/sessions/{manual_sid}',
             '/operator/profile'] + [f'/operator/sessions/{sid}' for sid in seeded_op]:
    resp = op.get(WEB + path, allow_redirects=False)
    check(f'OP GET {path}', resp.status_code == 200 and 'Traceback' not in resp.text, resp.status_code)
resp = op2.get(WEB + f'/operator/sessions/{manual_sid}', allow_redirects=False)
check('operador 2 no ve sesión asignada a otro (404)', resp.status_code == 404, resp.status_code)
resp = op.get(WEB + '/supervisor/dashboard', allow_redirects=False)
check('operador -> /supervisor/* = 403', resp.status_code == 403, resp.status_code)

# --- Operador: flujo manual RF-OP-03 / 04M / 06M ------------------------------------------
resp = op.post(WEB + f'/operator/sessions/{manual_sid}/begin', data={}, allow_redirects=False)
check('iniciar sin confirmar instrumental -> 400', resp.status_code == 400, resp.status_code)
resp = op.post(WEB + f'/operator/sessions/{manual_sid}/begin', data={'instruments_ready': '1'}, allow_redirects=False)
check('iniciar sesión manual -> /manual', resp.status_code == 302 and resp.headers['Location'].endswith('/manual')
      and status(manual_sid) == ('in_progress', 'manual_no_privacy'), status(manual_sid))
check('OP GET /manual', op.get(WEB + f'/operator/sessions/{manual_sid}/manual').status_code == 200)
expected = q('SELECT family_id, expected_quantity FROM expected_inventory WHERE session_id=%s', manual_sid)
form = {f'reported_{f}': str(n) for f, n in expected}
first = str(expected[0][0])
form_bad = dict(form, **{f'reported_{first}': str(expected[0][1] - 1)})
resp = op.post(WEB + f'/operator/sessions/{manual_sid}/manual-report', data=form_bad, allow_redirects=False)
check('reporte con faltante sin motivo -> 400', resp.status_code == 400, resp.status_code)
form_short = dict(form_bad, **{f'reason_{first}': 'shortage', f'notes_{first}': 'Falta una pieza'})
resp = op.post(WEB + f'/operator/sessions/{manual_sid}/manual-report', data=form_short, allow_redirects=False)
check('reporte con faltante justificado -> correction_required + discrepancia',
      resp.status_code == 302 and status(manual_sid)[0] == 'correction_required' and
      q('SELECT count(*) FROM discrepancy WHERE session_id=%s AND NOT resolved', manual_sid)[0][0] == 1,
      status(manual_sid))
check('count_event manual_count + human_correction registrados',
      q("SELECT count(*) FROM count_event ce JOIN cat_event_type t ON t.id=ce.event_type_id "
        "WHERE ce.session_id=%s AND t.code='manual_count'", manual_sid)[0][0] == len(expected) and
      q('SELECT count(*) FROM human_correction hc JOIN count_event ce ON ce.id=hc.count_event_id '
        'WHERE ce.session_id=%s', manual_sid)[0][0] == 1)

# --- Supervisor: revisión de discrepancia y cierre ------------------------------------------
check('SUP GET review', sup.get(WEB + f'/supervisor/discrepancies/{manual_sid}/review').status_code == 200)
disc_id = q('SELECT id FROM discrepancy WHERE session_id=%s', manual_sid)[0][0]
resp = sup.post(WEB + f'/supervisor/discrepancies/{manual_sid}/review', data={
    'discrepancy_id': str(disc_id), 'notes': 'Pieza localizada en charola', 'resolution_outcome': 'recovered'},
    allow_redirects=False)
check('resolver discrepancia (recuperada)', resp.status_code == 302 and
      q('SELECT resolved FROM discrepancy WHERE id=%s', disc_id)[0][0] is True, resp.status_code)
st_after = status(manual_sid)[0]
resp = sup.post(WEB + f'/supervisor/sessions/{manual_sid}/close', data={'material_recovered': '1'},
                allow_redirects=False)
check('faltante físico: el cierre seguro se rechaza sin reconocer el faltante no conciliado',
      resp.status_code == 409 and status(manual_sid)[0] == st_after, resp.status_code)
resp = sup.post(WEB + f'/supervisor/sessions/{manual_sid}/close', allow_redirects=False,
                data={'material_recovered': '1', 'unreconciled_shortage_acknowledged': '1'})
close_payload = q("SELECT ce.payload FROM count_event ce JOIN cat_event_type t ON t.id=ce.event_type_id "
                  "WHERE ce.session_id=%s AND t.code='session_close'", manual_sid)[0][0]
check('cierre SPD de la sesión como faltante no conciliado (safe_close_confirmed=false)',
      resp.status_code == 302 and status(manual_sid)[0] == 'closed' and close_payload['safe_close_confirmed'] is False,
      (st_after, status(manual_sid)[0]))
aw = q("SELECT w.id FROM work_session w JOIN cat_session_status s ON s.id=w.status_id "
       "WHERE s.code='awaiting_spd_review' LIMIT 1")
if aw:
    shortage = q("SELECT count(*) FROM discrepancy d JOIN cat_discrepancy_reason r ON r.id=d.reason_id "
                 "WHERE d.session_id=%s AND r.code='shortage'", aw[0][0])[0][0]
    data = {'material_recovered': '1', **({'unreconciled_shortage_acknowledged': '1'} if shortage else {})}
    resp = sup.post(WEB + f'/supervisor/sessions/{aw[0][0]}/close', data=data, allow_redirects=False)
    check('cierre SPD de sesión sembrada awaiting_spd_review', status(aw[0][0])[0] == 'closed', resp.status_code)

# --- Operador: flujo visión (sin cámara: video subido) --------------------------------------
resp = op.post(WEB + f'/operator/sessions/{vision_sid}/begin', data={'instruments_ready': '1'}, allow_redirects=False)
check('iniciar sesión con privacidad -> /capture (modo vision)', resp.status_code == 302 and
      resp.headers['Location'].endswith('/capture') and status(vision_sid) == ('in_progress', 'vision'),
      status(vision_sid))
page = op.get(WEB + f'/operator/sessions/{vision_sid}/capture')
check('OP GET /capture (pantalla de subida de video)', page.status_code == 200 and 'video' in page.text.lower(),
      page.status_code)
state = op.get(WEB + f'/operator/sessions/{vision_sid}/capture/state')
if not VISION_UP:
    check('sin VisionWorker: /capture/state responde 503 JSON controlado',
          state.status_code == 503 and state.json().get('ok') is False, state.status_code)
    up = op.post(WEB + f'/operator/sessions/{vision_sid}/capture/upload',
                 files={'video': ('prueba.mp4', b'\x00' * 64, 'video/mp4')})
    check('sin VisionWorker: subida responde 503 JSON controlado', up.status_code == 503, up.status_code)
resp = op.get(WEB + f'/operator/sessions/{vision_sid}/detection-summary', allow_redirects=False)
check('sin snapshot: detection-summary redirige a capture', resp.status_code == 302, resp.status_code)
check('sesión vision sigue en curso (sin pérdida de estado)', status(vision_sid) == ('in_progress', 'vision'))

print(f'VISION_SID={vision_sid}')
failed = [r for r in results if not r[1]]
print(f'\nRESUMEN SUPERVISOR/OPERADOR: {len(results) - len(failed)}/{len(results)} PASS')
sys.exit(1 if failed else 0)
