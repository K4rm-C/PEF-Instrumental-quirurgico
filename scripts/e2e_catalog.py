"""CatalogService integration end to end (live services; see GUIA_CATALOG.md).

Needs: BackendWebFlask (:5000) started with CATALOG_SERVICE_URL, BackendAuthService (:5001), the compose
services postgres, redis and catalog. Step 10 stops and starts the compose `catalog` service.

usage: python scripts/e2e_catalog.py
env:   DATABASE_URL, CATALOG_SERVICE_URL (as seen from this machine), COMPOSE_ENV_FILE (.env),
       COMPOSE_PROJECT (optional)
"""
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg
import requests
from lxml import etree

WEB, AUTH = os.environ.get('PEF_WEB_URL', 'http://127.0.0.1:5000'), os.environ.get('PEF_AUTH_URL', 'http://127.0.0.1:5001')
CATALOG = os.environ['CATALOG_SERVICE_URL'].rstrip('/') + '/api/v1/catalog'
DSN = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')
XSD = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parents[1] / 'apps/CatalogService/schemas/catalog.xsd')))
COMPOSE = ['docker', 'compose', '--env-file', os.environ.get('COMPOSE_ENV_FILE', '.env')] + (
    ['-p', os.environ['COMPOSE_PROJECT']] if os.environ.get('COMPOSE_PROJECT') else [])
TAG = uuid.uuid4().hex[:6].upper()
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail != '' else ''), flush=True)


def q(sql, *args):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, args).fetchall()


def login(email, password):
    session = requests.Session()
    response = session.post(f'{AUTH}/login', json={'email': email, 'password': password}, timeout=10)
    return session, response.cookies.get('access_token')


def bearer(token, **extra):
    return {'Authorization': f'Bearer {token}', **extra}


admin, admin_token = login('admin@instrumed.com', 'DemopwdADM78!')
supervisor, supervisor_token = login('supervisor@instrumed.com', 'DemopwdSPD78!')
operator, operator_token = login('operator@instrumed.com', 'DemopwdOP78!')
category = q("SELECT id, code FROM cat_instrument_category ORDER BY code LIMIT 1")[0]
cycles = [r[0] for r in q('SELECT code FROM cat_instrument_cycle_status ORDER BY code')]

print('== 1-2. Family from the RF Admin screen')
code = f'FAM_{TAG}'
r = admin.post(f'{WEB}/admin/instrument-families/new', allow_redirects=False, data={
    'code': code, 'name': f'Familia {TAG}', 'category': str(category[0]), 'function': 'Pinzar', 'status': 'active'})
row = q('SELECT id, name, function_text, active FROM instrument_family WHERE code=%s', code)
check('alta de familia por la pantalla RF (302)', r.status_code == 302, r.status_code)
check('registro en PostgreSQL', row and row[0][1:] == (f'Familia {TAG}', 'Pinzar', True), row)
family_id = str(row[0][0])
audit = q("SELECT action, host(ip) FROM access_audit WHERE resource_id=%s", family_id)
check('auditoría CREATE_INSTRUMENT_FAMILY escrita por CatalogService', audit and audit[0][0] == 'CREATE_INSTRUMENT_FAMILY', audit)
r = admin.post(f'{WEB}/admin/instrument-families/new', allow_redirects=False, data={
    'code': code, 'name': 'dup', 'category': str(category[0]), 'status': 'active'})
check('familia duplicada: 409 con el mensaje de Admin', r.status_code == 409 and 'That family code already exists' in r.text)

print('== 3-4. Instrument linked to the family, then modified')
icode = f'INS-{TAG}'
r = supervisor.post(f'{WEB}/admin/instruments/new', allow_redirects=False, data={
    'internal_code': icode, 'instrument_family': family_id, 'cycle_status': 'available', 'active_status': 'active'})
row = q('SELECT i.id, i.family_id FROM instrument i WHERE internal_code=%s', icode)
check('alta de instrumento (Supervisor) relacionado con la familia', r.status_code == 302 and str(row[0][1]) == family_id, r.status_code)
instrument_id = str(row[0][0])
other = next(c for c in cycles if c != 'available')
r = admin.post(f'{WEB}/admin/instruments/{instrument_id}/edit', allow_redirects=False, data={
    'instrument_family': family_id, 'cycle_status': other, 'active_status': 'active', 'change_reason': ''})
check('cambio de ciclo sin motivo: 400 con el mensaje de Admin', r.status_code == 400 and 'A change reason is required' in r.text)
r = admin.post(f'{WEB}/admin/instruments/{instrument_id}/edit', allow_redirects=False, data={
    'instrument_family': family_id, 'cycle_status': other, 'active_status': 'active', 'change_reason': 'Mantenimiento'})
events = q('SELECT notes FROM instrument_cycle_event WHERE instrument_id=%s ORDER BY occurred_at', instrument_id)
check('cambio con motivo: evento de ciclo', r.status_code == 302 and [e[0] for e in events][-1].endswith('reason: Mantenimiento'), events)

print('== 5-6. Kit and versioned edit')
fam2 = q('SELECT id FROM instrument_family WHERE active AND id<>%s ORDER BY code LIMIT 1', family_id)[0][0]
kname = f'Kit {TAG}'
r = admin.post(f'{WEB}/admin/kits/new', allow_redirects=False, data={
    'name': kname, 'status': 'active', 'instrument_family[]': [family_id, str(fam2)], 'expected_quantity[]': ['2', '3']})
v1 = q('SELECT id, version FROM kit WHERE name=%s', kname)
check('alta de kit v1', r.status_code == 302 and len(v1) == 1 and v1[0][1] == 1, r.status_code)
procedure = q('SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1')[0][0]
with psycopg.connect(DSN) as conn:
    conn.execute('INSERT INTO procedure_kit (procedure_type_id, kit_id, is_default, active) VALUES (%s, %s, FALSE, TRUE)',
                 (procedure, v1[0][0]))
r = supervisor.post(f'{WEB}/admin/kits/{v1[0][0]}/edit', allow_redirects=False, data={
    'name': kname, 'status': 'active', 'instrument_family[]': [family_id, str(fam2)], 'expected_quantity[]': ['2', '4']})
rows = q('SELECT id, version, active FROM kit WHERE name=%s ORDER BY version', kname)
check('edición crea v2 y desactiva v1', r.status_code == 302 and [(v, a) for _, v, a in rows] == [(1, False), (2, True)], rows)
link = q('SELECT kit_id FROM procedure_kit WHERE procedure_type_id=%s AND kit_id IN (%s, %s)', procedure, rows[0][0], rows[1][0])
check('vínculo de procedimiento movido a v2', [str(k[0]) for k in link] == [str(rows[1][0])], link)
check('auditoría UPDATE_KIT + SUPERSEDE_KIT',
      q("SELECT action FROM access_audit WHERE resource_id=%s", rows[1][0])[0][0] == 'UPDATE_KIT'
      and q("SELECT action FROM access_audit WHERE resource_id=%s AND action='SUPERSEDE_KIT'", rows[0][0]))
kit_v2 = str(rows[1][0])

print('== 7. Records through CatalogService')
family = requests.get(f'{CATALOG}/families/{family_id}', headers=bearer(admin_token), timeout=5).json()
instrument = requests.get(f'{CATALOG}/instruments/{instrument_id}', headers=bearer(admin_token), timeout=5).json()
kit = requests.get(f'{CATALOG}/kits/{kit_v2}?expand=detail', headers=bearer(admin_token), timeout=5).json()
check('GET /families/{id}', family['code'] == code and family['function_text'] == 'Pinzar')
check('GET /instruments/{id} con historial de ciclo', instrument['cycle_status_code'] == other and len(instrument['cycle_events']) == 2)
check('GET /kits/{id}?expand=detail', kit['version'] == 2 and kit['total_expected'] == 6 and kit['procedures'])
contract = requests.get(f'{CATALOG}/kits/{kit_v2}', headers=bearer(admin_token), timeout=5).json()
check('GET /kits/{id} conserva el contrato DS09', set(contract) == {'id', 'name', 'version', 'active', 'institution_id', 'items'})

print('== 8. JSON and XML')
xml_body = (f'<instrument><internal_code>XML-{TAG}</internal_code><family_id>{family_id}</family_id>'
            '<cycle_status_code>available</cycle_status_code><active>true</active></instrument>').encode()
r = requests.post(f'{CATALOG}/instruments', data=xml_body, timeout=5, headers=bearer(
    admin_token, **{'Content-Type': 'application/xml', 'Accept': 'application/xml'}))
document = etree.fromstring(r.content)
check('POST /instruments en XML (201) válido contra XSD', r.status_code == 201 and XSD.validate(document), r.status_code)
r = requests.get(f'{CATALOG}/kits?expand=detail', headers=bearer(admin_token, Accept='application/xml'), timeout=5)
check('GET /kits en XML válido contra XSD', r.status_code == 200 and XSD.validate(etree.fromstring(r.content)))
r = requests.post(f'{CATALOG}/kits', json={'name': f'K {TAG}', 'items': []}, headers=bearer(admin_token), timeout=5)
check('error JSON controlado (422 empty_composition)', r.status_code == 422 and r.json()['details']['reason'] == 'empty_composition')
r = requests.post(f'{CATALOG}/kits', data=b'<kit><name>x', headers=bearer(
    admin_token, **{'Content-Type': 'application/xml', 'Accept': 'application/xml'}), timeout=5)
check('error XML controlado (400 error_response)', r.status_code == 400 and etree.fromstring(r.content).tag == 'error_response')
check('Swagger UI y OpenAPI servidos', requests.get(f'{CATALOG}/docs', timeout=5).status_code == 200
      and b'/api/v1/catalog/instruments/{id}' in requests.get(f'{CATALOG}/openapi.yaml', timeout=5).content)

print('== 9. Authorized and unauthorized users')
body = {'internal_code': f'OP-{TAG}', 'family_id': family_id, 'cycle_status_code': 'available'}
r = requests.post(f'{CATALOG}/instruments', json=body, headers=bearer(operator_token), timeout=5)
check('Operador: 403 en POST /instruments', r.status_code == 403)
check('intento denegado auditado (DENIED_POST_INSTRUMENT)',
      q("SELECT count(*) FROM access_audit WHERE action='DENIED_POST_INSTRUMENT' AND occurred_at > now() - interval '1 minute'")[0][0] >= 1)
r = requests.put(f'{CATALOG}/families/{family_id}', json={'name': 'x', 'category_code': category[1]},
                 headers=bearer(supervisor_token), timeout=5)
check('Supervisor: 403 en PUT /families (solo it_admin)', r.status_code == 403)
check('sin token: 401', requests.get(f'{CATALOG}/kits', timeout=5).status_code == 401)
check('identidad en headers propios no vale: 401', requests.post(f'{CATALOG}/instruments', json=body, timeout=5,
                                                                 headers={'X-User-Id': 'x', 'X-Role': 'it_admin'}).status_code == 401)
check('Operador en la pantalla RF de Admin: 403', operator.get(f'{WEB}/admin/instruments', allow_redirects=False).status_code == 403)
_, revoked = login('admin@instrumed.com', 'DemopwdADM78!')
requests.post(f'{AUTH}/logout', headers=bearer(revoked), timeout=5)
check('token revocado (logout) en Redis: 401', requests.get(f'{CATALOG}/families', headers=bearer(revoked), timeout=5).status_code == 401)

print('== 10. CatalogService stopped: the Operator keeps working')
subprocess.run(COMPOSE + ['stop', 'catalog'], check=True, capture_output=True)
try:
    r = admin.get(f'{WEB}/admin/instrument-families')
    check('Admin de catálogo muestra el aviso controlado', 'The catalog service is temporarily unavailable' in r.text, r.status_code)
    check('el resto de Administración sigue local (usuarios)', admin.get(f'{WEB}/admin/users').status_code == 200)
    check('Operador: lista de sesiones', operator.get(f'{WEB}/operator/sessions').status_code == 200)
    # A new manual session (no privacy notice), scheduled by the SPD Supervisor while Catalog is down:
    # scheduling reads kits and inventory from the portal's PostgreSQL, not from CatalogService.
    kit_id, proc_id = q('SELECT pk.kit_id, pk.procedure_type_id FROM procedure_kit pk JOIN kit k ON k.id=pk.kit_id '
                        'WHERE k.active AND pk.active ORDER BY pk.is_default DESC LIMIT 1')[0]
    room_id, station_id = q('SELECT r.id, cs.id FROM capture_station cs JOIN operating_room r ON r.id=cs.room_id '
                            'WHERE cs.active AND r.active LIMIT 1')[0]
    lines = q('SELECT family_id, quantity FROM kit_item WHERE kit_id=%s', kit_id)
    r = supervisor.post(f'{WEB}/supervisor/sessions/new', allow_redirects=False, data={
        'action': 'schedule', 'operator_user_id': str(q("SELECT id FROM \"user\" WHERE email='operator@instrumed.com'")[0][0]),
        'procedure_type_id': str(proc_id), 'room_id': str(room_id), 'station_id': str(station_id), 'kit_id': str(kit_id),
        'scheduled_at': time.strftime('%Y-%m-%dT%H:%M', time.gmtime(time.time() + 3600)),
        'family_id[]': [str(f) for f, _ in lines], 'expected_quantity[]': [str(n) for _, n in lines],
        'physician_id[]': [str(q('SELECT id FROM physician ORDER BY id LIMIT 1')[0][0])], 'surgical_role_code[]': ['surgeon']})
    check('Supervisor programa una sesión manual (lecturas locales)', r.status_code == 302, r.status_code)
    sid = r.headers['Location'].rstrip('/').split('/')[-1]
    r = operator.post(f'{WEB}/operator/sessions/{sid}/begin', data={'instruments_ready': '1'}, allow_redirects=False)
    check('Operador: inicia la sesión (kit e inventario locales)', r.status_code == 302, r.headers.get('Location'))
    form = {f'reported_{f}': str(n) for f, n in q('SELECT family_id, expected_quantity FROM expected_inventory '
                                                   'WHERE session_id=%s', sid)}
    r = operator.post(f'{WEB}/operator/sessions/{sid}/manual-report', data=form, allow_redirects=False)
    status = q('SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id=w.status_id WHERE w.id=%s', sid)[0][0]
    check('Operador: reporta y termina su conteo', r.status_code == 302 and status == 'awaiting_spd_review', status)
finally:
    subprocess.run(COMPOSE + ['start', 'catalog'], check=True, capture_output=True)
for _ in range(40):
    try:
        if requests.get(os.environ['CATALOG_SERVICE_URL'].rstrip('/') + '/health/ready', timeout=2).status_code == 200:
            break
    except requests.RequestException:
        pass
    time.sleep(1)
check('Catalog recuperado: la pantalla RF vuelve a funcionar', admin.get(f'{WEB}/admin/instrument-families').status_code == 200
      and 'temporarily unavailable' not in admin.get(f'{WEB}/admin/instrument-families').text)
print(f'\nRESUMEN CATALOG: {sum(results)}/{len(results)} PASS')
sys.exit(0 if all(results) else 1)
