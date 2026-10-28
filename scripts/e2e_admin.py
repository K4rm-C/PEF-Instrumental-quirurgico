"""E2E de Administración RF contra Web(5000) + Auth(5001) + PostgreSQL reales (stack aislado)."""
import os
import re
import sys
import uuid

import psycopg
import requests

WEB = os.environ.get('PEF_WEB_URL', 'http://127.0.0.1:5000')
AUTH = os.environ.get('PEF_AUTH_URL', 'http://127.0.0.1:5001')
DSN = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')
TAG = uuid.uuid4().hex[:6].upper()
results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail else ''))


def q(sql, *args):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, args).fetchall()


def login(email, password):
    s = requests.Session()
    r = s.post(f'{AUTH}/login', json={'email': email, 'password': password}, timeout=10)
    return s, r


def flashes(html):
    return re.findall(r'class="[^"]*alert[^"]*"[^>]*>(.*?)</', html, re.S)


def audit_count(action, resource_id):
    return q('SELECT count(*) FROM access_audit WHERE action=%s AND resource_id=%s', action, resource_id)[0][0]


admin, r = login('admin@instrumed.com', 'DemopwdADM78!')
check('login admin (AuthService)', r.status_code == 200, r.status_code)

# 1. Todas las pantallas RF de Admin cargan con datos reales
pages = ['/admin/dashboard', '/admin/instrument-families', '/admin/instruments', '/admin/kits',
         '/admin/procedures', '/admin/users', '/admin/roles', '/admin/vision-models', '/admin/audit-log',
         '/admin/configuration', '/admin/instrument-families/new', '/admin/instruments/new', '/admin/kits/new',
         '/admin/procedures/new', '/admin/users/new', '/admin/roles/new', '/admin/vision-models/new',
         '/admin/configuration/institution/edit', '/admin/configuration/operating-rooms/new',
         '/admin/configuration/capture-stations/new', '/admin/profile',
         '/admin/instrument-families?status=all&search=pinza', '/admin/audit-log?date=2026-10-20',
         '/admin/users?status=all&role=it_admin']
for path in pages:
    resp = admin.get(WEB + path, allow_redirects=False)
    bad = 'database is not available' in resp.text or 'Traceback' in resp.text
    check(f'GET {path}', resp.status_code == 200 and not bad, resp.status_code)

cat_id = q("SELECT id FROM cat_instrument_category ORDER BY code LIMIT 1")[0][0]
inst_id = q('SELECT id FROM institution LIMIT 1')[0][0]

# 2. Familias
code = f'FAM_{TAG}'
resp = admin.post(WEB + '/admin/instrument-families/new', data={
    'code': code, 'name': f'Familia {TAG}', 'category': str(cat_id), 'how_to_identify': 'x',
    'classification_characteristics': 'y', 'function': 'z', 'status': 'active'}, allow_redirects=False)
row = q('SELECT id, name, active FROM instrument_family WHERE code=%s', code)
check('crear familia -> 302 + fila en instrument_family', resp.status_code == 302 and len(row) == 1, resp.status_code)
fam_id = row[0][0]
check('auditoría CREATE_INSTRUMENT_FAMILY', audit_count('CREATE_INSTRUMENT_FAMILY', fam_id) == 1)
resp = admin.post(WEB + '/admin/instrument-families/new', data={
    'code': code, 'name': 'dup', 'category': str(cat_id), 'status': 'active'}, allow_redirects=False)
check('familia duplicada -> 409', resp.status_code == 409, resp.status_code)
resp = admin.post(WEB + '/admin/instrument-families/new', data={'code': '', 'name': '', 'category': ''},
                  allow_redirects=False)
check('familia sin campos -> 400', resp.status_code == 400, resp.status_code)
resp = admin.post(WEB + f'/admin/instrument-families/{fam_id}/edit', data={
    'name': f'Familia {TAG} editada', 'category': str(cat_id), 'status': 'inactive'}, allow_redirects=False)
row = q('SELECT name, active FROM instrument_family WHERE id=%s', fam_id)[0]
check('editar familia (nombre + inactivar)', resp.status_code == 302 and row == (f'Familia {TAG} editada', False), row)
check('auditoría UPDATE + DEACTIVATE familia',
      audit_count('UPDATE_INSTRUMENT_FAMILY', fam_id) == 1 and audit_count('DEACTIVATE_INSTRUMENT_FAMILY', fam_id) == 1)
admin.post(WEB + f'/admin/instrument-families/{fam_id}/edit', data={
    'name': f'Familia {TAG} editada', 'category': str(cat_id), 'status': 'active'})

# 3. Instrumentos
icode = f'INS-{TAG}'
resp = admin.post(WEB + '/admin/instruments/new', data={
    'internal_code': icode, 'instrument_family': str(fam_id), 'cycle_status': 'available',
    'active_status': 'active'}, allow_redirects=False)
row = q('SELECT id FROM instrument WHERE internal_code=%s', icode)
check('crear instrumento', resp.status_code == 302 and len(row) == 1, resp.status_code)
ins_id = row[0][0]
check('evento de ciclo "registered"', q('SELECT count(*) FROM instrument_cycle_event WHERE instrument_id=%s', ins_id)[0][0] == 1)
resp = admin.post(WEB + f'/admin/instruments/{ins_id}/edit', data={
    'instrument_family': str(fam_id), 'cycle_status': 'in_sterilization', 'active_status': 'active',
    'change_reason': ''}, allow_redirects=False)
cycle_codes = [r[0] for r in q('SELECT code FROM cat_instrument_cycle_status')]
other_cycle = next(c for c in cycle_codes if c != 'available')
resp = admin.post(WEB + f'/admin/instruments/{ins_id}/edit', data={
    'instrument_family': str(fam_id), 'cycle_status': other_cycle, 'active_status': 'active',
    'change_reason': ''}, allow_redirects=False)
check('cambio de ciclo sin motivo -> 400', resp.status_code == 400, resp.status_code)
resp = admin.post(WEB + f'/admin/instruments/{ins_id}/edit', data={
    'instrument_family': str(fam_id), 'cycle_status': other_cycle, 'active_status': 'active',
    'change_reason': 'mantenimiento'}, allow_redirects=False)
n_events = q('SELECT count(*) FROM instrument_cycle_event WHERE instrument_id=%s', ins_id)[0][0]
check('cambio de ciclo con motivo -> evento de ciclo', resp.status_code == 302 and n_events == 2, n_events)
resp = admin.post(WEB + '/admin/instruments/new', data={
    'internal_code': icode, 'instrument_family': str(fam_id), 'cycle_status': 'available',
    'active_status': 'active'}, allow_redirects=False)
check('instrumento duplicado -> 409', resp.status_code == 409, resp.status_code)

# 4. Kits (versionado)
fam2 = q('SELECT id FROM instrument_family WHERE active AND id<>%s ORDER BY code LIMIT 1', fam_id)[0][0]
kname = f'Kit {TAG}'
resp = admin.post(WEB + '/admin/kits/new', data={
    'name': kname, 'status': 'active', 'instrument_family[]': [str(fam_id), str(fam2)],
    'expected_quantity[]': ['2', '3']}, allow_redirects=False)
row = q('SELECT id, version FROM kit WHERE name=%s', kname)
check('crear kit v1 + kit_item', resp.status_code == 302 and len(row) == 1 and
      q('SELECT count(*) FROM kit_item WHERE kit_id=%s', row[0][0])[0][0] == 2, resp.status_code)
kit_v1 = row[0][0]
resp = admin.post(WEB + '/admin/kits/new', data={
    'name': f'Kit dup {TAG}', 'status': 'active', 'instrument_family[]': [str(fam_id), str(fam_id)],
    'expected_quantity[]': ['1', '1']}, allow_redirects=False)
check('kit con familia repetida -> 400', resp.status_code == 400, resp.status_code)
resp = admin.post(WEB + '/admin/kits/new', data={
    'name': f'Kit q {TAG}', 'status': 'active', 'instrument_family[]': [str(fam_id)],
    'expected_quantity[]': ['0']}, allow_redirects=False)
check('kit con cantidad 0 -> 400', resp.status_code == 400, resp.status_code)
resp = admin.post(WEB + f'/admin/kits/{kit_v1}/edit', data={
    'name': kname, 'status': 'active', 'instrument_family[]': [str(fam_id), str(fam2)],
    'expected_quantity[]': ['2', '4']}, allow_redirects=False)
rows = q('SELECT version, active FROM kit WHERE name=%s ORDER BY version', kname)
check('editar kit crea v2 y desactiva v1', rows == [(1, False), (2, True)], rows)
kit_v2 = q('SELECT id FROM kit WHERE name=%s AND version=2', kname)[0][0]

# 5. Procedimientos
phase_ids = [r[0] for r in q('SELECT id FROM cat_operation_phase ORDER BY code LIMIT 2')]
pcode = f'proc_{TAG.lower()}'
resp = admin.post(WEB + '/admin/procedures/new', data={
    'code': pcode, 'name': f'Procedimiento {TAG}',
    'kit[]': [str(kit_v2)], 'kit_row_index[]': ['0'], 'technique_label[]': ['abierta'], 'is_default[]': ['0'],
    'kit_active[]': ['0'], 'phase[]': [str(p) for p in phase_ids], 'phase_row_index[]': ['0', '1'],
    'sort_order[]': ['1', '2'], 'count_required[]': ['0', '1'], 'phase_active[]': ['0', '1']},
    allow_redirects=False)
row = q('SELECT id FROM cat_procedure_type WHERE code=%s', pcode)
ok = resp.status_code == 302 and len(row) == 1
check('crear procedimiento + kit default + fases', ok and
      q('SELECT count(*) FROM procedure_kit WHERE procedure_type_id=%s AND is_default', row[0][0])[0][0] == 1 and
      q('SELECT count(*) FROM procedure_phase WHERE procedure_type_id=%s', row[0][0])[0][0] == 2, resp.status_code)
resp = admin.post(WEB + '/admin/procedures/new', data={
    'code': f'{pcode}b', 'name': 'x', 'phase[]': [str(phase_ids[0]), str(phase_ids[1])],
    'phase_row_index[]': ['0', '1'], 'sort_order[]': ['1', '1']}, allow_redirects=False)
check('procedimiento con orden de fase repetido -> 400', resp.status_code == 400, resp.status_code)

# 6. Roles
rcode = f'auditor_{TAG.lower()}'
resp = admin.post(WEB + '/admin/roles/new', data={'code': rcode, 'description': 'Auditor externo'},
                  allow_redirects=False)
role = q('SELECT id FROM role WHERE code=%s', rcode)
check('crear rol', resp.status_code == 302 and len(role) == 1, resp.status_code)
resp = admin.post(WEB + '/admin/roles/new', data={'code': 'Bad Code!', 'description': 'x'}, allow_redirects=False)
check('rol con código inválido -> 400', resp.status_code == 400, resp.status_code)
sys_role = q("SELECT id FROM role WHERE code='it_admin'")[0][0]
resp = admin.post(WEB + f'/admin/roles/{sys_role}/delete', allow_redirects=True)
check('no se puede borrar rol de sistema', q('SELECT count(*) FROM role WHERE id=%s', sys_role)[0][0] == 1)

# 7. Usuarios (hash compatible con AuthService)
email = f'op_{TAG.lower()}@instrumed.com'
pwd = f'Pwd{TAG}seguro!'
resp = admin.post(WEB + '/admin/users/new', data={
    'name': f'Operador {TAG}', 'email': email, 'institution': str(inst_id),
    'roles[]': ['station_operator'], 'status': 'active', 'password': pwd, 'confirm_password': pwd},
    allow_redirects=False)
user = q('SELECT id FROM "user" WHERE email=%s', email)
check('crear usuario operador', resp.status_code == 302 and len(user) == 1, resp.status_code)
user_id = user[0][0]
resp = admin.post(WEB + '/admin/users/new', data={
    'name': 'dup', 'email': email, 'institution': str(inst_id), 'roles[]': ['station_operator'],
    'password': pwd, 'confirm_password': pwd}, allow_redirects=False)
check('usuario con email duplicado -> 409', resp.status_code == 409, resp.status_code)
resp = admin.post(WEB + '/admin/users/new', data={
    'name': 'x', 'email': f'x{TAG}@a.com', 'institution': str(inst_id), 'roles[]': ['station_operator'],
    'password': 'short', 'confirm_password': 'short'}, allow_redirects=False)
check('usuario con contraseña corta -> 400', resp.status_code == 400, resp.status_code)
newop, r = login(email, pwd)
check('usuario creado en Admin inicia sesión en AuthService', r.status_code == 200, r.status_code)
resp = newop.get(WEB + '/operator/sessions', allow_redirects=False)
check('usuario nuevo entra a /operator/sessions', resp.status_code == 200, resp.status_code)
resp = admin.post(WEB + f'/admin/users/{user_id}/password', data={
    'new_password': pwd + 'X', 'confirm_new_password': pwd + 'X'}, allow_redirects=False)
_, r_old = login(email, pwd)
_, r_new = login(email, pwd + 'X')
check('reset de contraseña: vieja rechazada, nueva aceptada', r_old.status_code == 401 and r_new.status_code == 200,
      (r_old.status_code, r_new.status_code))
resp = admin.post(WEB + f'/admin/users/{user_id}/deactivate', allow_redirects=False)
check('desactivar usuario', q('SELECT active FROM "user" WHERE id=%s', user_id)[0][0] is False)
resp = newop.get(WEB + '/operator/sessions', allow_redirects=False)
check('sesión del usuario desactivado queda invalidada (redirige a sign-in)',
      resp.status_code == 302 and '/sign-in' in resp.headers.get('Location', ''), resp.status_code)
admin_id = q("SELECT id FROM \"user\" WHERE email='admin@instrumed.com'")[0][0]
resp = admin.post(WEB + f'/admin/users/{admin_id}/deactivate', allow_redirects=True)
check('admin no puede desactivarse a sí mismo', q('SELECT active FROM "user" WHERE id=%s', admin_id)[0][0] is True)
admin.post(WEB + f'/admin/users/{user_id}/activate')
check('reactivar usuario', q('SELECT active FROM "user" WHERE id=%s', user_id)[0][0] is True)

# 8. Modelos de visión
vtag = f'v-{TAG.lower()}'
resp = admin.post(WEB + '/admin/vision-models/new', data={
    'version_tag': vtag, 'model_asset_reference': f'models/{vtag}.pt', 'checksum': 'a' * 64,
    'yolo_class_id[]': ['0'], 'instrument_family[]': [str(fam_id)]}, allow_redirects=False)
model = q('SELECT id, active FROM yolo_model WHERE version_tag=%s', vtag)
check('registrar modelo de visión (inactivo)', resp.status_code == 302 and model and model[0][1] is False, resp.status_code)
resp = admin.post(WEB + '/admin/vision-models/new', data={
    'version_tag': f'{vtag}b', 'checksum': 'zz'}, allow_redirects=False)
check('modelo con checksum inválido -> 400', resp.status_code == 400, resp.status_code)
prev_active = q('SELECT id FROM yolo_model WHERE active')
resp = admin.post(WEB + f'/admin/vision-models/{model[0][0]}/activate', allow_redirects=False)
actives = q('SELECT id FROM yolo_model WHERE active')
check('activar modelo deja exactamente uno activo', actives == [(model[0][0],)], actives)
if prev_active:  # restaurar el modelo demo como activo para el flujo YOLO
    admin.post(WEB + f'/admin/vision-models/{prev_active[0][0]}/activate')
    check('restaurar modelo demo activo', q('SELECT id FROM yolo_model WHERE active') == prev_active)

# 9. Configuración
rcode2 = f'Q{TAG}'
resp = admin.post(WEB + '/admin/configuration/operating-rooms/new', data={
    'code': rcode2, 'name': f'Quirófano {TAG}', 'status': 'active'}, allow_redirects=False)
room = q('SELECT id FROM operating_room WHERE code=%s', rcode2)
check('crear quirófano', resp.status_code == 302 and len(room) == 1, resp.status_code)
resp = admin.post(WEB + '/admin/configuration/operating-rooms/new', data={
    'code': rcode2, 'name': 'dup', 'status': 'active'}, allow_redirects=False)
check('quirófano duplicado -> 409', resp.status_code == 409, resp.status_code)
resp = admin.post(WEB + '/admin/configuration/capture-stations/new', data={
    'name': f'Estación {TAG}', 'operating_room': str(room[0][0]), 'status': 'active'}, allow_redirects=False)
check('crear estación de captura', resp.status_code == 302 and
      q('SELECT count(*) FROM capture_station WHERE name=%s', f'Estación {TAG}')[0][0] == 1, resp.status_code)
inst_name = q('SELECT name FROM institution WHERE id=%s', inst_id)[0][0]
resp = admin.post(WEB + '/admin/configuration/institution/edit', data={'name': inst_name, 'status': 'inactive'},
                  allow_redirects=False)
check('no se puede desactivar la institución propia -> 400', resp.status_code == 400, resp.status_code)

# 10. Bitácora y permisos
resp = admin.get(WEB + '/admin/audit-log?search=' + TAG)
check('bitácora muestra eventos', resp.status_code == 200 and 'CREATE_' in resp.text)
sup, r = login('supervisor@instrumed.com', 'DemopwdSPD78!')
check('login supervisor', r.status_code == 200)
for path in ['/admin/users', '/admin/roles', '/admin/vision-models', '/admin/audit-log', '/admin/configuration',
             '/admin/dashboard', '/admin/instrument-families', '/admin/procedures']:
    resp = sup.get(WEB + path, allow_redirects=False)
    check(f'supervisor -> {path} = 403', resp.status_code == 403, resp.status_code)
for path in ['/admin/instruments', '/admin/kits', f'/admin/kits/{kit_v2}/edit']:
    resp = sup.get(WEB + path, allow_redirects=False)
    check(f'supervisor permitido {path}', resp.status_code == 200, resp.status_code)
op, r = login('operator@instrumed.com', 'DemopwdOP78!')
resp = op.get(WEB + '/admin/instruments', allow_redirects=False)
check('operador -> /admin/instruments = 403', resp.status_code == 403, resp.status_code)
anon = requests.get(WEB + '/admin/dashboard', allow_redirects=False)
check('anónimo -> redirige a sign-in', anon.status_code == 302 and '/sign-in' in anon.headers['Location'])
total_audit = q("SELECT count(*) FROM access_audit WHERE occurred_at > now() - interval '10 minutes'")[0][0]
check('access_audit registra la actividad', total_audit > 15, total_audit)

failed = [r for r in results if not r[1]]
print(f'\nRESUMEN ADMIN: {len(results) - len(failed)}/{len(results)} PASS')
sys.exit(1 if failed else 0)
