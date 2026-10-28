"""Auth / Identity integration end to end (live services; see GUIA_AUTH.md).

Needs: BackendWebFlask (:5000), BackendAuthService (:5001, local or `docker compose up -d auth`), the
compose services postgres and redis (and catalog when the web runs with CATALOG_SERVICE_URL).
Everything is real: RF screens over HTTP, AuthService /api/v1/auth, PostgreSQL rows, Redis keys.
It creates its own users (and one extra institution for the last-administrator rule); demo
accounts keep their passwords.

usage: python scripts/e2e_identity.py
env:   DATABASE_URL, REDIS_URL (as seen from this machine), JWT_SECRET_KEY, JWT_ALGORITHM (optional)
"""
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import psycopg
import redis
import requests
from lxml import etree

WEB, AUTH = os.environ.get('PEF_WEB_URL', 'http://127.0.0.1:5000'), os.environ.get('PEF_AUTH_URL', 'http://127.0.0.1:5001')
API = f'{AUTH}/api/v1/auth'
DSN = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')
REDIS = redis.Redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
SECRET, ALGORITHM = os.environ['JWT_SECRET_KEY'], os.environ.get('JWT_ALGORITHM', 'HS256')
XSD = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parents[1] / 'apps/BackendAuthService/schemas/identity.xsd')))
TAG = uuid.uuid4().hex[:6].lower()
PASSWORD, NEW_PASSWORD = f'Inicial-{TAG}-1', f'Renovada-{TAG}-2'
DEMO = {
    'admin@instrumed.com': 'DemopwdADM78!',
    'supervisor@instrumed.com': 'DemopwdSPD78!',
    'operator@instrumed.com': 'DemopwdOP78!',
    'operador2@instrumed.com': 'DemopwdOP278!',
}
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail != '' else ''), flush=True)


def q(sql, *args):
    with psycopg.connect(DSN, autocommit=True) as conn:
        cursor = conn.execute(sql, args)
        return cursor.fetchall() if cursor.description else []


def portal_login(email, password):
    """Same as the sign-in page: AuthService cookie route, then the portal uses the cookies."""
    session = requests.Session()
    response = session.post(f'{AUTH}/login', json={'email': email, 'password': password}, timeout=10)
    return session, response


def api_login(email, password):
    return requests.post(f'{API}/login', json={'email': email, 'password': password}, timeout=10)


def bearer(token, **extra):
    return {'Authorization': f'Bearer {token}', **extra}


def jti(token):
    return jwt.decode(token, options={'verify_signature': False})['jti']


def redis_session(token):
    raw = REDIS.get(f'auth:jwt:{jti(token)}')
    return json.loads(raw) if raw else None


def page_ok(session, path):
    response = session.get(f'{WEB}{path}', allow_redirects=False, timeout=10)
    return response.status_code == 200, response.status_code


def flashed(session, response):
    return session.get(f'{WEB}{response.headers["Location"]}', timeout=10).text if response.status_code == 302 else response.text


def audit(resource_id):
    return [row[0] for row in q('SELECT action FROM access_audit WHERE resource_id=%s ORDER BY occurred_at, action',
                                str(resource_id))]


institution = q("SELECT institution_id FROM \"user\" WHERE email='admin@instrumed.com'")[0][0]
admin_id = str(q("SELECT id FROM \"user\" WHERE email='admin@instrumed.com'")[0][0])

print('== 1. Login of the existing users (portal cookies and API v1 JSON)')
sessions = {}
for email, password in DEMO.items():
    session, response = portal_login(email, password)
    sessions[email] = session
    v1 = api_login(email, password)
    check(f'login {email}', response.status_code == 200 and v1.status_code == 200 and v1.json()['access_token'],
          (response.status_code, v1.status_code, v1.json().get('user', {}).get('roles')))
admin = sessions['admin@instrumed.com']
admin_token = api_login('admin@instrumed.com', DEMO['admin@instrumed.com']).json()['access_token']
check('pantalla RF de usuarios (admin) 200', page_ok(admin, '/admin/users')[0])

print('== 2. Create a user from Administration RF')
email = f'nuevo.{TAG}@instrumed.com'
form = {'name': f'Usuario {TAG}', 'email': email.upper(), 'institution': str(institution), 'roles[]': ['station_operator'],
        'status': 'active', 'password': PASSWORD, 'confirm_password': PASSWORD}
response = admin.post(f'{WEB}/admin/users/new', data=form, allow_redirects=False)
row = q('SELECT id, name, email, active, password_hash FROM "user" WHERE email=%s', email)
check('alta por la pantalla RF (302) y fila en PostgreSQL', response.status_code == 302 and row and row[0][3] is True,
      (response.status_code, row and row[0][1:4]))
user_id = str(row[0][0])
check('correo guardado en minúsculas y contraseña sólo como hash', row[0][2] == email and PASSWORD not in row[0][4])
check('mensaje "User saved." en la lista', 'User saved.' in flashed(admin, response))
duplicate = admin.post(f'{WEB}/admin/users/new', data=form, allow_redirects=False)
check('correo duplicado: 409 con el mensaje de Admin', duplicate.status_code == 409 and
      'A user with this email already exists.' in duplicate.text, duplicate.status_code)
short = admin.post(f'{WEB}/admin/users/new', allow_redirects=False,
                   data={**form, 'email': f'otro.{TAG}@instrumed.com', 'password': 'x', 'confirm_password': 'x'})
check('contraseña corta: 400 con el mensaje de Admin', short.status_code == 400 and
      'Password must contain at least 8 characters.' in short.text, short.status_code)
mismatch = admin.post(f'{WEB}/admin/users/new', allow_redirects=False,
                      data={**form, 'email': f'otro.{TAG}@instrumed.com', 'confirm_password': 'distinta-123'})
check('confirmación distinta: 400 "Passwords do not match."', mismatch.status_code == 400 and 'Passwords do not match.' in mismatch.text)

print('== 3. Sign in with the new user')
user, response = portal_login(email, PASSWORD)
check('login del usuario creado (portal)', response.status_code == 200, response.status_code)
check('el usuario creado entra a su pantalla de Operador', page_ok(user, '/operator/sessions')[0])
user_api = api_login(email, PASSWORD).json()
user_access, user_refresh = user_api['access_token'], user_api['refresh_token']
check('API v1: roles del usuario creado', user_api['user']['roles'] == ['station_operator'], user_api['user']['roles'])

print('== 4. Modify their data')
edit = {'name': f'Usuario {TAG} editado', 'email': email, 'institution': str(institution), 'roles[]': ['station_operator'],
        'status': 'active'}
response = admin.post(f'{WEB}/admin/users/{user_id}/edit', data=edit, allow_redirects=False)
check('edición RF (302) y nombre en PostgreSQL', response.status_code == 302 and
      q('SELECT name FROM "user" WHERE id=%s', user_id)[0][0] == f'Usuario {TAG} editado', response.status_code)
check('perfil /me refleja el cambio sin nuevo login',
      requests.get(f'{API}/me', headers=bearer(user_access), timeout=10).json()['name'] == f'Usuario {TAG} editado')

print('== 5. Assign and modify roles')
response = admin.post(f'{WEB}/admin/users/{user_id}/edit', data={**edit, 'roles[]': ['spd_supervisor', 'station_operator']},
                      allow_redirects=False)
roles = [r[0] for r in q('SELECT r.code FROM user_role ur JOIN role r ON r.id=ur.role_id WHERE ur.user_id=%s ORDER BY r.code', user_id)]
check('roles asignados en PostgreSQL', response.status_code == 302 and roles == ['spd_supervisor', 'station_operator'], roles)
permissions = requests.get(f'{API}/permissions', headers=bearer(user_access), timeout=10).json()
check('GET /permissions con el mismo token refleja el cambio', permissions['roles'] == ['spd_supervisor', 'station_operator'], permissions)
check('la sesión del portal del usuario ya ve la pantalla de Supervisor', page_ok(user, '/supervisor/sessions')[0])
response = requests.put(f'{API}/users/{user_id}/roles', json={'role_codes': ['station_operator']}, headers=bearer(admin_token), timeout=10)
check('PUT /users/{id}/roles (API) vuelve a Operador', response.status_code == 200 and
      [r['code'] for r in response.json()['roles']] == ['station_operator'])
check('y el portal deja de mostrarle Supervisor (403)', page_ok(user, '/supervisor/sessions')[1] == 403)

print('== 6. Permission restrictions')
supervisor = sessions['supervisor@instrumed.com']
check('Supervisor en /admin/users: 403', page_ok(supervisor, '/admin/users')[1] == 403)
check('Operador en /admin/roles: 403', page_ok(sessions['operator@instrumed.com'], '/admin/roles')[1] == 403)
spd_token = api_login('supervisor@instrumed.com', DEMO['supervisor@instrumed.com']).json()['access_token']
response = requests.post(f'{API}/users/{user_id}/deactivate', headers=bearer(spd_token), timeout=10)
check('Supervisor directo a la API: 403 forbidden', response.status_code == 403 and response.json()['error'] == 'forbidden')
check('intento denegado auditado (DENIED_POST_USER)', 'DENIED_POST_USER' in audit(user_id), audit(user_id))
response = admin.post(f'{WEB}/admin/users/{admin_id}/deactivate', allow_redirects=False)
check('admin no puede desactivarse a sí mismo', 'You cannot deactivate your own account.' in flashed(admin, response))
response = admin.post(f'{WEB}/admin/users/{admin_id}/edit', allow_redirects=False, data={
    'name': 'Admin', 'email': 'admin@instrumed.com', 'institution': str(institution), 'roles[]': ['station_operator'],
    'status': 'active'})
check('admin no puede quitarse el rol de administrador', response.status_code == 400 and
      'You cannot remove the administrator role from your own account.' in response.text, response.status_code)
# Last administrator of another institution (created for this run).
other_inst, other_role = str(uuid.uuid4()), str(uuid.uuid4())
q('INSERT INTO institution (id, name, active) VALUES (%s, %s, TRUE)', other_inst, f'Hospital E2E {TAG}')
q("INSERT INTO role (id, code, description, institution_id) VALUES (%s, 'it_admin', 'IT administrator', %s)", other_role, other_inst)
response = admin.post(f'{WEB}/admin/users/new', allow_redirects=False, data={
    'name': f'Admin E2E {TAG}', 'email': f'admin.e2e.{TAG}@instrumed.com', 'institution': other_inst,
    'roles[]': ['it_admin'], 'status': 'active', 'password': PASSWORD, 'confirm_password': PASSWORD})
only_admin = str(q('SELECT id FROM "user" WHERE email=%s', f'admin.e2e.{TAG}@instrumed.com')[0][0])
response = admin.post(f'{WEB}/admin/users/{only_admin}/deactivate', allow_redirects=False)
check('no se desactiva al último administrador activo de una institución',
      'At least one active administrator must remain.' in flashed(admin, response) and
      q('SELECT active FROM "user" WHERE id=%s', only_admin)[0][0] is True)

print('== 7. Deactivate a user: their session ends')
response = admin.post(f'{WEB}/admin/users/{user_id}/deactivate', allow_redirects=False)
check('desactivación RF (mensaje) y active=false', 'User deactivated. Their history is kept.' in flashed(admin, response)
      and q('SELECT active FROM "user" WHERE id=%s', user_id)[0][0] is False)
response = user.get(f'{WEB}/operator/sessions', allow_redirects=False, timeout=10)
check('la sesión del portal del usuario termina (redirige a iniciar sesión)', response.status_code == 302 and
      '/sign-in' in response.headers.get('Location', ''), (response.status_code, response.headers.get('Location')))
check('API: access anterior -> 401', requests.get(f'{API}/me', headers=bearer(user_access), timeout=10).status_code == 401)
check('API: refresh anterior -> 401', requests.post(f'{API}/refresh', json={'refresh_token': user_refresh}, timeout=10).status_code == 401)
check('Redis: sesiones del usuario marcadas revoked=true', redis_session(user_access)['revoked'] is True and
      redis_session(user_refresh)['revoked'] is True, (redis_session(user_access), redis_session(user_refresh)))
check('login con usuario desactivado: 401', portal_login(email, PASSWORD)[1].status_code == 401)

print('== 8. Reactivate')
response = admin.post(f'{WEB}/admin/users/{user_id}/activate', allow_redirects=False)
check('reactivación RF (mensaje) y active=true', 'User reactivated.' in flashed(admin, response) and
      q('SELECT active FROM "user" WHERE id=%s', user_id)[0][0] is True)
check('el access anterior sigue revocado', requests.get(f'{API}/me', headers=bearer(user_access), timeout=10).status_code == 401)
user, response = portal_login(email, PASSWORD)
check('nuevo login tras reactivar', response.status_code == 200 and page_ok(user, '/operator/sessions')[0])

print('== 9. Password reset')
live = api_login(email, PASSWORD).json()['access_token']
response = admin.post(f'{WEB}/admin/users/{user_id}/password', allow_redirects=False,
                      data={'new_password': NEW_PASSWORD, 'confirm_new_password': NEW_PASSWORD})
check('restablecimiento RF (mensaje)', 'Password updated. The user must sign in with the new password.' in flashed(admin, response))
check('la contraseña anterior ya no autentica', portal_login(email, PASSWORD)[1].status_code == 401)
check('la nueva contraseña autentica', portal_login(email, NEW_PASSWORD)[1].status_code == 200)
check('sesiones previas revocadas tras el restablecimiento',
      requests.get(f'{API}/me', headers=bearer(live), timeout=10).status_code == 401 and redis_session(live)['revoked'] is True)

print('== 10. Logout: revocation in Redis')
pair = api_login(email, NEW_PASSWORD).json()
response = requests.post(f'{API}/logout', json={'refresh_token': pair['refresh_token']}, headers=bearer(pair['access_token']), timeout=10)
check('POST /api/v1/auth/logout 204', response.status_code == 204, response.status_code)
check('Redis: access y refresh revoked=true', redis_session(pair['access_token'])['revoked'] is True and
      redis_session(pair['refresh_token'])['revoked'] is True)
check('el access cerrado ya no sirve (Auth y Catalog)',
      requests.get(f'{API}/me', headers=bearer(pair['access_token']), timeout=10).status_code == 401)
user, _ = portal_login(email, NEW_PASSWORD)
cookie_access = user.cookies.get('access_token')
user.get(f'{WEB}/sign-out', timeout=10)
check('cierre de sesión del portal revoca su token en Redis', redis_session(cookie_access)['revoked'] is True)

print('== 11. Expired, invalid and unauthorized tokens')
now = datetime.now(timezone.utc)
expired = jwt.encode({'sub': admin_id, 'jti': str(uuid.uuid4()), 'type': 'access', 'iat': now - timedelta(hours=1),
                      'exp': now - timedelta(minutes=1)}, SECRET, algorithm=ALGORITHM)
forged = jwt.encode({'sub': admin_id, 'jti': jti(admin_token), 'type': 'access', 'exp': now + timedelta(minutes=5)},
                    'not-the-secret-0123456789abcdef0123', algorithm=ALGORITHM)
unregistered = jwt.encode({'sub': admin_id, 'jti': str(uuid.uuid4()), 'type': 'access', 'exp': now + timedelta(minutes=5)},
                          SECRET, algorithm=ALGORITHM)
for label, headers in (('expirado', bearer(expired)), ('firma inválida', bearer(forged)),
                       ('sin sesión en Redis', bearer(unregistered)), ('basura', bearer('abc.def')),
                       ('sin token', {}), ('cabeceras de rol falsas', {'X-User-Role': 'it_admin', 'X-User-Id': admin_id})):
    response = requests.get(f'{API}/users', headers=headers, timeout=10)
    check(f'token {label}: 401 unauthorized', response.status_code == 401 and response.json()['error'] == 'unauthorized',
          response.status_code)
response = requests.get(f'{API}/users/{uuid.uuid4()}', headers=bearer(admin_token), timeout=10)
check('usuario inexistente: 404 not_found', response.status_code == 404 and response.json()['details']['reason'] == 'user_not_found')
response = requests.post(f'{API}/users', json={'name': 'x', 'email': 'malo', 'institution_id': str(institution),
                                               'role_codes': ['station_operator'], 'password': PASSWORD}, headers=bearer(admin_token), timeout=10)
check('valor inválido: 422 validation_error reason=invalid_email', response.status_code == 422 and
      response.json()['details']['reason'] == 'invalid_email', response.json())
response = requests.post(f'{API}/users', json={'name': 'x', 'password_hash': 'x'}, headers=bearer(admin_token), timeout=10)
check('campo desconocido: 400 invalid_request', response.status_code == 400 and
      response.json()['details']['unknown_fields'] == ['password_hash'])

print('== 12. JSON and XML')
xml_headers = bearer(admin_token, **{'Accept': 'application/xml', 'Content-Type': 'application/xml'})
xml_email = f'xml.{TAG}@instrumed.com'
body = (f'<user_create_request><name>XML {TAG}</name><email>{xml_email}</email><institution_id>{institution}</institution_id>'
        f'<role_codes><role_code>spd_supervisor</role_code></role_codes><active>true</active>'
        f'<password>{PASSWORD}</password><confirm_password>{PASSWORD}</confirm_password></user_create_request>')
response = requests.post(f'{API}/users', data=body.encode(), headers=xml_headers, timeout=10)
document = etree.fromstring(response.content)
check('POST /users en XML: 201 y respuesta válida contra identity.xsd', response.status_code == 201 and XSD.validate(document),
      response.status_code)
check('mismo usuario consultado en JSON', requests.get(f'{API}/users/{document.findtext("id")}', headers=bearer(admin_token),
                                                        timeout=10).json()['email'] == xml_email)
listing = requests.get(f'{API}/users', headers=bearer(admin_token, Accept='application/xml'), timeout=10)
check('GET /users en XML válido contra identity.xsd', XSD.validate(etree.fromstring(listing.content)))
roles_xml = requests.get(f'{API}/roles', headers=bearer(admin_token, Accept='application/xml'), timeout=10)
check('GET /roles en XML válido contra identity.xsd', XSD.validate(etree.fromstring(roles_xml.content)))
bad = requests.post(f'{API}/users', data=b'<user_create_request><name lang="es">x</name></user_create_request>',
                    headers=xml_headers, timeout=10)
bad_doc = etree.fromstring(bad.content)
check('XML que no cumple el XSD: 400 reason=xsd_validation_failed', bad.status_code == 400 and
      bad_doc.findtext('details/reason') == 'xsd_validation_failed' and XSD.validate(bad_doc))
check('ninguna respuesta administrativa expone hash o contraseña',
      all(secret not in (listing.text + response.text) for secret in ('password_hash', 'scrypt:', 'pbkdf2:', PASSWORD)))

print('== 13. Persistence and audit in PostgreSQL')
actions = audit(user_id)
for action in ('CREATE_USER', 'UPDATE_USER', 'CHANGE_USER_ROLES', 'DEACTIVATE_USER', 'ACTIVATE_USER', 'RESET_USER_PASSWORD',
               'DENIED_POST_USER'):
    check(f'access_audit {action}', action in actions)
rows = q("SELECT actor_user_id, outcome, correlation_id FROM access_audit WHERE resource_id=%s AND action='CREATE_USER'", user_id)
check('auditoría con actor = admin y correlation id del portal', rows and str(rows[0][0]) == admin_id and rows[0][1] == 'success'
      and len(rows[0][2] or '') == 32, rows)
check('rol de la institución en user_role (no hay escrituras locales duplicadas)',
      q('SELECT count(*) FROM user_role WHERE user_id=%s', user_id)[0][0] == 1)

print('== 14. Operator keeps working')
operator = sessions['operator@instrumed.com']
for path in ('/operator/profile', '/operator/sessions', '/operator/sessions/history'):
    check(f'Operador {path} 200', page_ok(operator, path)[0])
refreshed = operator.post(f'{AUTH}/refresh', timeout=10)
check('renovación de sesión del Operador (refresh por cookie)', refreshed.status_code == 200, refreshed.status_code)
check('Operador sigue autenticado tras renovar', page_ok(operator, '/operator/sessions')[0])

print(f'\n{sum(results)}/{len(results)} checks passed')
sys.exit(0 if all(results) else 1)
