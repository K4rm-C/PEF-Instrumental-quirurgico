"""AuditService end to end (live services; see GUIA_AUDIT.md).

Needs: BackendWebFlask (:5000) with AUDIT_SERVICE_URL (and CATALOG_SERVICE_URL, SESSION_SERVICE_URL),
AuthService (:5001), VisionWorker (:5002, weights/best.pt), compose postgres, redis, catalog, session,
audit. Steps 15-16 stop and start the compose `audit` service. Every event checked here is produced by
the real flows (sign-in, RF screens, YOLO counting) and read back through AuditService.

usage: python scripts/e2e_audit.py <videos_dir>
env:   DATABASE_URL, AUDIT_SERVICE_URL (as seen from this machine), COMPOSE_ENV_FILE, COMPOSE_PROJECT
"""
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
import requests
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
WEB, AUTH = os.environ.get('PEF_WEB_URL', 'http://127.0.0.1:5000'), os.environ.get('PEF_AUTH_URL', 'http://127.0.0.1:5001')
AUDIT = os.environ['AUDIT_SERVICE_URL'].rstrip('/')
API = f'{AUDIT}/api/v1/audit'
DSN = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')
VIDEOS = Path(sys.argv[1])
COMPOSE = ['docker', 'compose', '--env-file', os.environ.get('COMPOSE_ENV_FILE', '.env')] + (
    ['-p', os.environ['COMPOSE_PROJECT']] if os.environ.get('COMPOSE_PROJECT') else [])
XSD = etree.XMLSchema(etree.parse(str(ROOT / 'apps/AuditService/schemas/audit.xsd')))
INSTITUTION = '11111111-1111-1111-1111-111111111111'
KIT = '51515151-0000-4000-8000-000000000001'
ROOM, STATION = '12121212-0000-4000-8000-000000000001', '13131313-0000-4000-8000-000000000001'
PHYSICIAN, PATIENT = '32323232-0000-4000-8000-000000000001', '31313131-0000-4000-8000-000000000001'
TAG = uuid.uuid4().hex[:6].upper()
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail != '' else ''), flush=True)


def q(sql, *args):
    with psycopg.connect(DSN, autocommit=True) as conn:
        cursor = conn.execute(sql, args)
        return cursor.fetchall() if cursor.description else []


def login(email, password, correlation=None):
    session = requests.Session()
    headers = {'X-Correlation-ID': correlation} if correlation else {}
    response = session.post(f'{AUTH}/login', json={'email': email, 'password': password}, headers=headers, timeout=10)
    assert response.status_code == 200, (email, response.status_code)
    return session


def bearer(session, **extra):
    return {'Authorization': f'Bearer {session.cookies.get("access_token")}', **extra}


def events(session, **params):
    return requests.get(f'{API}/events', params=params, headers=bearer(session), timeout=10)


def status(sid):
    return q('SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = %s', sid)[0][0]


def capture(session, sid, video):
    url = f'{WEB}/operator/sessions/{sid}/capture'
    session.get(url)
    with open(VIDEOS / video, 'rb') as fh:
        session.post(f'{url}/upload', files={'video': (video, fh, 'video/mp4')})
    for _ in range(300):
        if session.get(f'{url}/state').json()['session']['status'] != 'processing':
            break
        time.sleep(1)
    return session.post(f'{url}/finish', allow_redirects=False).headers.get('Location', '')


def schedule(spd, operator_id, privacy=True, minutes=60):
    kit_lines = q('SELECT family_id, quantity FROM kit_item WHERE kit_id = %s ORDER BY family_id', KIT)
    data = {'operator_user_id': operator_id, 'procedure_type_id': str(q('SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1')[0][0]),
            'room_id': ROOM, 'station_id': STATION, 'kit_id': KIT, 'patient_id': PATIENT,
            'scheduled_at': (datetime.now() + timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M'),
            'family_id[]': [str(f) for f, _ in kit_lines], 'expected_quantity[]': [str(n) for _, n in kit_lines],
            'physician_id[]': [PHYSICIAN], 'surgical_role_code[]': ['surgeon']}
    if privacy:
        notice = str(q('SELECT id FROM privacy_notice_version WHERE active ORDER BY effective_at DESC LIMIT 1')[0][0])
        data.update({'confirm_privacy': '1', 'privacy_notice_version_id': notice, 'privacy_confirm_checkbox': '1'})
    response = spd.post(f'{WEB}/supervisor/sessions/new', data=data, allow_redirects=False)
    return response.headers.get('Location', '').rstrip('/').split('/')[-1]


operator_id = str(q("SELECT id FROM \"user\" WHERE email = 'operator@instrumed.com'")[0][0])
operator2_id = str(q("SELECT id FROM \"user\" WHERE email = 'operador2@instrumed.com'")[0][0])
admin_id = str(q("SELECT id FROM \"user\" WHERE email = 'admin@instrumed.com'")[0][0])

# ====================================================================================================
print('== 1. A sign-in generates an event')
corr = f'e2e-login-{TAG}'
op = login('operator@instrumed.com', 'DemopwdOP78!', correlation=corr)
row = q("SELECT id, actor_user_id, institution_id, outcome FROM access_audit WHERE action = 'LOGIN' AND correlation_id = %s", corr)
check('LOGIN escrito por AuthService en PostgreSQL', len(row) == 1 and str(row[0][1]) == operator_id and row[0][3] == 'success', row)
admin = login('admin@instrumed.com', 'DemopwdADM78!')
spd = login('supervisor@instrumed.com', 'DemopwdSPD78!')
op2 = login('operador2@instrumed.com', 'DemopwdOP278!')
r = events(admin, action='LOGIN', correlation_id=corr)
check('AuditService devuelve el LOGIN (categoría security, origen auth)', r.status_code == 200 and r.json()['total'] == 1
      and r.json()['items'][0]['category'] == 'security' and r.json()['items'][0]['source_service'] == 'auth'
      and r.json()['items'][0]['id'] == str(row[0][0]), r.status_code)
bad_login = requests.post(f'{AUTH}/login', json={'email': 'operator@instrumed.com', 'password': 'wrong-password'}, timeout=10)
check('intento fallido -> LOGIN_FAILED (denied)', bad_login.status_code == 401 and
      events(admin, action='LOGIN_FAILED', user_id=operator_id, outcome='denied').json()['total'] >= 1)

print('== 2. Family created through CatalogService (RF) and its audit')
category = str(q('SELECT id FROM cat_instrument_category ORDER BY code LIMIT 1')[0][0])
r = admin.post(f'{WEB}/admin/instrument-families/new', allow_redirects=False,
               data={'code': f'AUD_{TAG}', 'name': f'Familia auditoría {TAG}', 'category': category, 'status': 'active'})
family_id = str(q('SELECT id FROM instrument_family WHERE code = %s', f'AUD_{TAG}')[0][0])
r = events(admin, resource_type='instrument_family', action='CREATE_INSTRUMENT_FAMILY', q=family_id)
item = r.json()['items'][0] if r.json()['items'] else {}
check('evento CREATE_INSTRUMENT_FAMILY consultado (origen catalog, etiqueta = código)',
      item.get('resource', {}).get('id') == family_id and item.get('source_service') == 'catalog'
      and item.get('resource', {}).get('label') == f'AUD_{TAG}' and item.get('actor', {}).get('user_id') == admin_id, item)

print('== 3. Session scheduled through SessionService (RF)')
sid = schedule(spd, operator_id)
r = events(spd, session_id=sid, action='session.scheduled')
check('evento session.scheduled (origen session) visible para el Supervisor', r.status_code == 200 and r.json()['total'] == 1
      and r.json()['items'][0]['source_service'] == 'session', r.json().get('total'))

print('== 4. Operator counting (monolith) and its events')
op.post(f'{WEB}/operator/sessions/{sid}/begin', data={'instruments_ready': '1'})
loc1 = capture(op, sid, 'tray_full.mp4')
loc2 = capture(op, sid, 'tray_full.mp4')
expected = {str(f): int(n) for f, n in q('SELECT family_id, expected_quantity FROM expected_inventory WHERE session_id = %s', sid)}
form = {f'validated_{f}': str(n) for f, n in expected.items()}
form.update({f'notes_{f}': 'Verified on the physical tray' for f in expected})
form['extra_instruments'] = '0'
r = op.post(f'{WEB}/operator/sessions/{sid}/vision-validation', data=form, allow_redirects=False)
check('conteo inicial, final y validación humana del Operador', loc1.endswith('/capture') and loc2.endswith('/detection-summary')
      and r.status_code == 302 and status(sid) in ('awaiting_spd_review', 'correction_required'), (loc1, loc2, status(sid)))

print('== 5. Those events through AuditService')
r = events(spd, session_id=sid, per_page=100, sort='occurred_at')
actions = [i['action'] for i in r.json()['items']]
# session.scheduled and privacy.session_agreement share one transaction (same instant).
check('trazabilidad completa de la sesión en orden', set(actions[:2]) == {'session.scheduled', 'privacy.session_agreement'}
      and 'session.start' in actions
      and 'session_open' in actions and actions.count('auto_count') >= 2 and 'manual_count' in actions
      and 'manual_close' in actions and 'session.manual_close' in actions, actions)
# Besides the frozen initial / final counts the capture stores intermediate stable_window lots.
triggers = [i['details'].get('trigger') for i in r.json()['items'] if i['action'] == 'auto_count']
frozen = [trig for trig in triggers if trig in ('initial', 'final')]
check('conteos inicial y final con trigger y totales (sin cajas ni imágenes)',
      frozen == ['initial', 'final'] and 'boxes' not in r.text and 'frame_jpeg' not in r.text, triggers)

print('== 6. Filters by user and institution')
r = events(admin, user_id=operator_id, institution_id=INSTITUTION, per_page=100)
check('filtro por usuario (todo del Operador)', r.status_code == 200 and r.json()['total'] > 0
      and all(i['actor'] and i['actor']['user_id'] == operator_id for i in r.json()['items']))
other_institution = q("SELECT id FROM institution WHERE id <> %s LIMIT 1", INSTITUTION)
r = events(admin, institution_id=str(other_institution[0][0]) if other_institution else str(uuid.uuid4()))
check('otra institución: 403 out_of_scope', r.status_code == 403 and r.json()['details']['reason'] == 'out_of_scope')
rows = events(admin, per_page=100).json()['items']
check('el Administrador solo ve eventos de su institución', all(i['institution_id'] == INSTITUTION for i in rows))

print('== 7. Filter by surgical session')
r = events(admin, session_id=sid, per_page=100)
check('filtro por sesión', r.json()['total'] == len(actions) and all(i['session_id'] == sid for i in r.json()['items']))

print('== 8. Pagination')
p1 = events(admin, session_id=sid, per_page=3, page=1).json()
p2 = events(admin, session_id=sid, per_page=3, page=2).json()
check('páginas consistentes sin duplicados', p1['total'] == len(actions) and p1['total_pages'] == -(-len(actions) // 3)
      and not {i['id'] for i in p1['items']} & {i['id'] for i in p2['items']}, (p1['total'], p1['total_pages']))

print('== 9-10. Unknown record, bad parameters')
r = requests.get(f'{API}/events/{uuid.uuid4()}', headers=bearer(admin), timeout=10)
check('registro inexistente: 404 not_found', r.status_code == 404 and r.json()['error'] == 'not_found')
for params, field in (({'per_page': 500}, 'per_page'), ({'from': 'ayer'}, 'from'), ({'category': 'x'}, 'category'),
                      ({'user_id': 'abc'}, 'user_id')):
    r = events(admin, **params)
    check(f'parámetro inválido {field}: 400', r.status_code == 400 and r.json()['details']['field'] == field)
r = events(admin, limit=5)
check('parámetro desconocido: 400', r.status_code == 400 and r.json()['details']['unknown_parameters'] == ['limit'])

print('== 11. Users without permission')
r = events(op)
check('Operador: 403', r.status_code == 403)
denied = q("SELECT outcome FROM access_audit WHERE actor_user_id = %s AND action = 'DENIED_GET_AUDIT_EVENT' "
           "AND occurred_at > now() - interval '2 minutes'", operator_id)
check('intento denegado registrado (DENIED_GET_AUDIT_EVENT)', denied and denied[0][0] == 'denied', denied)
r = events(spd, resource_type='user')
check('Supervisor no ve eventos de administración de identidad', r.status_code == 200 and r.json()['total'] == 0)
check('Operador sin pantalla de auditoría', op.get(f'{WEB}/supervisor/audit-log', allow_redirects=False).status_code == 403
      and op.get(f'{WEB}/admin/audit-log', allow_redirects=False).status_code == 403)

print('== 12. Invalid or revoked token')
check('token inválido: 401', requests.get(f'{API}/events', headers={'Authorization': 'Bearer x.y.z'}, timeout=10).status_code == 401)
temp = login('supervisor@instrumed.com', 'DemopwdSPD78!')
token = temp.cookies.get('access_token')
temp.post(f'{AUTH}/logout', timeout=10)
r = requests.get(f'{API}/events', headers={'Authorization': f'Bearer {token}'}, timeout=10)
check('token revocado por logout: 401', r.status_code == 401, r.status_code)
check('LOGOUT auditado', q("SELECT count(*) FROM access_audit WHERE action = 'LOGOUT' AND occurred_at > now() - interval '2 minutes'")[0][0] >= 1)

print('== 13. JSON and XML')
xml = requests.get(f'{API}/events', params={'session_id': sid}, headers=bearer(spd, Accept='application/xml'), timeout=10)
doc = etree.fromstring(xml.content)
check('página XML válida contra audit.xsd', XSD.validate(doc) and doc.tag == 'event_page'
      and len(doc.findall('items/event')) == min(20, len(actions)), XSD.error_log)
one = events(spd, session_id=sid, action='session.scheduled').json()['items'][0]
xml_one = etree.fromstring(requests.get(f'{API}/events/{one["id"]}', headers=bearer(spd, Accept='application/xml'), timeout=10).content)
check('detalle XML = JSON', XSD.validate(xml_one) and xml_one.findtext('id') == one['id'] and xml_one.findtext('action') == one['action'])
summary_xml = etree.fromstring(requests.get(f'{API}/events/summary', params={'session_id': sid},
                                            headers=bearer(admin, Accept='application/xml'), timeout=10).content)
check('resumen XML válido', XSD.validate(summary_xml) and int(summary_xml.findtext('total')) == len(actions))

print('== 14. The data matches PostgreSQL')
pg_total = q("SELECT (SELECT count(*) FROM access_audit WHERE resource_type = 'work_session' AND resource_id = %s) + "
             "(SELECT count(*) FROM count_event WHERE session_id = %s)", sid, sid)[0][0]
check('total de AuditService = filas reales de access_audit + count_event', pg_total == len(actions), (pg_total, len(actions)))
pg_inst = q("SELECT count(*) FROM access_audit WHERE institution_id = %s", INSTITUTION)[0][0] + q(
    "SELECT count(*) FROM count_event e JOIN work_session w ON w.id = e.session_id JOIN operation o ON o.id = w.operation_id "
    "WHERE o.institution_id = %s", INSTITUTION)[0][0]
api_inst = events(admin).json()['total']
check('total de la institución = PostgreSQL (los eventos de esta consulta pueden sumarse)', api_inst in (pg_inst, pg_inst + 1),
      (api_inst, pg_inst))
admin_page = admin.get(f'{WEB}/admin/audit-log?search=AUD_')
check('bitácora RF del Administrador (AuditService) muestra la familia', admin_page.status_code == 200)
sup_page = spd.get(f'{WEB}/supervisor/audit-log?session={sid}')
check('bitácora RF del Supervisor (AuditService) muestra la sesión', sup_page.status_code == 200 and
      f'WS-{sid.replace("-", "")[:8].upper()}' in sup_page.text)

print('== 15. AuditService stopped: the Operator keeps working')
subprocess.run(COMPOSE + ['stop', 'audit'], cwd=ROOT, check=True, capture_output=True)
try:
    requests.get(f'{AUDIT}/health', timeout=2)
    down = False
except requests.RequestException:
    down = True
check('contenedor audit detenido', down)
op_b = login('operador2@instrumed.com', 'DemopwdOP278!', correlation=f'e2e-login-down-{TAG}')
check('login durante la caída: LOGIN escrito igual (lo escribe AuthService)',
      len(q("SELECT 1 FROM access_audit WHERE correlation_id = %s", f'e2e-login-down-{TAG}')) == 1)
sid2 = schedule(spd, operator2_id, privacy=False, minutes=90)
r = op_b.post(f'{WEB}/operator/sessions/{sid2}/begin', data={'instruments_ready': '1'}, allow_redirects=False)
exp2 = {str(f): int(n) for f, n in q('SELECT family_id, expected_quantity FROM expected_inventory WHERE session_id = %s', sid2)}
r2 = op_b.post(f'{WEB}/operator/sessions/{sid2}/manual-report', data={f'reported_{f}': str(n) for f, n in exp2.items()},
               allow_redirects=False)
check('Operador inicia y reporta su conteo sin AuditService', r.status_code == 302 and r2.status_code == 302
      and status(sid2) == 'awaiting_spd_review', status(sid2))
written = q("SELECT action FROM access_audit WHERE resource_id = %s ORDER BY occurred_at", sid2)
check('sus eventos se escriben en PostgreSQL de forma transaccional', [w[0] for w in written][-2:] ==
      ['session.start', 'session.manual_close'], written)
page = spd.get(f'{WEB}/supervisor/audit-log')
check('bitácora del Supervisor: mensaje controlado 503', page.status_code == 503 and
      'The audit service is temporarily unavailable' in page.text, page.status_code)
check('bitácora del Administrador: mensaje controlado', 'The audit service is temporarily unavailable'
      in admin.get(f'{WEB}/admin/audit-log').text)

print('== 16. AuditService restarted: history (including the outage) is available')
subprocess.run(COMPOSE + ['start', 'audit'], cwd=ROOT, check=True, capture_output=True)
for _ in range(60):
    try:
        if requests.get(f'{AUDIT}/health', timeout=2).status_code == 200:
            break
    except requests.RequestException:
        pass
    time.sleep(1)
r = events(spd, session_id=sid2, per_page=100, sort='occurred_at')
got = [i['action'] for i in r.json()['items']]
check('eventos producidos durante la caída disponibles (sin pérdidas ni duplicados)',
      got.count('session.start') == 1 and got.count('session.manual_close') == 1 and 'manual_close' in got, got)
check('el evento LOGIN de la caída también', events(admin, correlation_id=f'e2e-login-down-{TAG}').json()['total'] == 1)
check('los históricos anteriores siguen ahí', events(admin, action='LOGIN', correlation_id=corr).json()['total'] == 1)

print(f'\n{sum(results)}/{len(results)} checks passed')
sys.exit(0 if all(results) else 1)
