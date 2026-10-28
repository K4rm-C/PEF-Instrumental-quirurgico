"""NotificationsService end to end (live services; see GUIA_NOTIFICATIONS.md).

Needs: BackendWebFlask (:5000) with NOTIFICATIONS_SERVICE_URL (and CATALOG_SERVICE_URL, SESSION_SERVICE_URL),
AuthService (:5001), VisionWorker (:5002, weights/best.pt), compose postgres, redis, catalog, session,
notifications and notifications-worker. Steps 14-15 stop and start the compose `notifications` and
`notifications-worker` services. Every notification checked here comes from the real flows (RF screens,
SessionService, the Operator's counting in the monolith); nothing is inserted by hand.

usage: python scripts/e2e_notifications.py <videos_dir>
env:   DATABASE_URL, NOTIFICATIONS_SERVICE_URL (as seen from this machine), JWT_SECRET_KEY (only to forge an
       expired token), COMPOSE_ENV_FILE, COMPOSE_PROJECT
"""
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import psycopg
import requests
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
WEB, AUTH = os.environ.get('PEF_WEB_URL', 'http://127.0.0.1:5000'), os.environ.get('PEF_AUTH_URL', 'http://127.0.0.1:5001')
SERVICE = os.environ['NOTIFICATIONS_SERVICE_URL'].rstrip('/')
API = f'{SERVICE}/api/v1/notifications'
DSN = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')
VIDEOS = Path(sys.argv[1])
COMPOSE = ['docker', 'compose', '--env-file', os.environ.get('COMPOSE_ENV_FILE', '.env')] + (
    ['-p', os.environ['COMPOSE_PROJECT']] if os.environ.get('COMPOSE_PROJECT') else [])
XSD = etree.XMLSchema(etree.parse(str(ROOT / 'apps/NotificationsService/schemas/notifications.xsd')))
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


def login(email, password):
    session = requests.Session()
    response = session.post(f'{AUTH}/login', json={'email': email, 'password': password}, timeout=10)
    assert response.status_code == 200, (email, response.status_code)
    return session


def bearer(session, **extra):
    return {'Authorization': f'Bearer {session.cookies.get("access_token")}', **extra}


def api(session, path='', method='GET', **kwargs):
    headers = {**bearer(session), **kwargs.pop('headers', {})}
    return requests.request(method, f'{API}{path}', headers=headers, timeout=10, **kwargs)


def mine(session, sid=None, **params):
    items = api(session, params={'per_page': 100, **params}).json()['items']
    return [i for i in items if sid is None or i['session_id'] == sid]


def types(session, sid):
    return sorted(i['type'] for i in mine(session, sid))


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
    return q('SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = %s', sid)[0][0]


def label(sid):
    return 'WS-' + sid.replace('-', '')[:8].upper()


def wait_for(predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    return predicate()


def pending(sid):
    return q('SELECT count(*) FROM notification_outbox WHERE session_id = %s AND processed_at IS NULL', sid)[0][0]


def pg_notifications(sid):
    return sorted((r[0], str(r[1])) for r in q('SELECT type, recipient_user_id FROM notification WHERE session_id = %s', sid))


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


def schedule(spd, operator_id, privacy=False, minutes=60):
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


def expected(sid):
    return {str(f): int(n) for f, n in q('SELECT family_id, expected_quantity FROM expected_inventory WHERE session_id = %s', sid)}


def report(op, sid, short=False):
    op.post(f'{WEB}/operator/sessions/{sid}/begin', data={'instruments_ready': '1'}, allow_redirects=False)
    lines = expected(sid)
    first = next(iter(lines))
    form = {f'reported_{f}': str(n - 1 if short and f == first else n) for f, n in lines.items()}
    if short:
        form.update({f'reason_{first}': 'shortage', f'notes_{first}': 'One instrument missing on the tray'})
    return op.post(f'{WEB}/operator/sessions/{sid}/manual-report', data=form, allow_redirects=False)


def resolve_and_close(spd, sid):
    for (disc,) in q('SELECT id FROM discrepancy WHERE session_id = %s AND NOT resolved', sid):
        spd.post(f'{WEB}/supervisor/discrepancies/{sid}/review', allow_redirects=False,
                 data={'discrepancy_id': str(disc), 'notes': 'Piece not found after search',
                       'resolution_outcome': outcome_for(disc)})
    if has_physical_shortage(sid):
        return spd.post(f'{WEB}/supervisor/sessions/{sid}/close', allow_redirects=False,
                        data={'material_recovered': '1', 'contingency_reviewed': '1',
                              'unreconciled_shortage_acknowledged': '1'})
    return spd.post(f'{WEB}/supervisor/sessions/{sid}/close', allow_redirects=False,
                    data={'material_recovered': '1', 'contingency_reviewed': '1'})


def compose(*args):
    return subprocess.run(COMPOSE + list(args), cwd=ROOT, check=True, capture_output=True, text=True)


user_id = {email: str(q('SELECT id FROM "user" WHERE email = %s', email)[0][0]) for email in
           ('operator@instrumed.com', 'operador2@instrumed.com', 'supervisor@instrumed.com', 'supervisor2@instrumed.com',
            'admin@instrumed.com')}
OP, OP2, SPD, SPD2 = (user_id[e] for e in ('operator@instrumed.com', 'operador2@instrumed.com', 'supervisor@instrumed.com',
                                           'supervisor2@instrumed.com'))
op = login('operator@instrumed.com', 'DemopwdOP78!')
op2 = login('operador2@instrumed.com', 'DemopwdOP278!')
spd = login('supervisor@instrumed.com', 'DemopwdSPD78!')
spd2 = login('supervisor2@instrumed.com', 'DemopwdSPD278!')
admin = login('admin@instrumed.com', 'DemopwdADM78!')
health = requests.get(f'{SERVICE}/health', timeout=5)
check('NotificationsService y su worker en marcha', health.status_code == 200 and 'X-Outbox-Pending' in health.headers,
      health.headers.get('X-Outbox-Pending'))
op_unread_before = api(op, '/unread-count').json()['unread']

# ====================================================================================================
print('== 1. Session scheduled -> the assigned Operator is notified')
sid = schedule(spd, OP)
got = wait_for(lambda: [i for i in mine(op, sid) if i['type'] == 'session_scheduled'])
item = got[0] if got else {}
check('session_scheduled para operator@ (generado por el worker desde access_audit)', len(got) == 1
      and item['status'] == 'unread' and item['data']['session_label'] == label(sid) and item['data'].get('scheduled_at'), item)
check('el Supervisor que la programó no recibe aviso de su propia acción', mine(spd, sid) == [])
check('otro Operador no la ve', mine(op2, sid) == [])

print('== 2-3. Report with a shortage -> discrepancy pending (Supervisor) + correction required (Operator)')
r = report(op, sid, short=True)
check('reporte del Operador con faltante -> correction_required', r.status_code == 302 and status(sid) == 'correction_required',
      status(sid))
pend = wait_for(lambda: [i for i in mine(spd, sid) if i['type'] == 'discrepancy_pending'])
check('discrepancy_pending al Supervisor que programó la sesión (con discrepancias abiertas)',
      len(pend) == 1 and pend[0]['data'].get('open_discrepancies', 0) >= 1, pend and pend[0]['data'])
check('supervisor2@ (no la programó) no recibe el aviso', mine(spd2, sid) == [])
corr = wait_for(lambda: [i for i in mine(op, sid) if i['type'] == 'correction_required'])
check('correction_required al Operador asignado', len(corr) == 1, types(op, sid))

print('== 4-5. Supervisor resolves and confirms the safe close -> Operator told')
r = resolve_and_close(spd, sid)
check('resolución y cierre seguro por SessionService (RF)', r.status_code == 302 and status(sid) == 'closed', status(sid))
done = wait_for(lambda: {'discrepancy_resolved', 'session_closed'} <= set(types(op, sid)))
check('discrepancy_resolved y session_closed al Operador', done, types(op, sid))
check('quien cerró (supervisor@) no recibe session_closed', 'session_closed' not in types(spd, sid), types(spd, sid))
check('PostgreSQL: exactamente los avisos esperados de la sesión (una resolución = un aviso aunque haya varias)',
      pg_notifications(sid) == sorted([('session_scheduled', OP), ('correction_required', OP), ('discrepancy_pending', SPD),
                                       ('discrepancy_resolved', OP), ('session_closed', OP)]), pg_notifications(sid))

print('== 6. Listing, filters, pagination and unread count')
unread = api(op, '/unread-count').json()['unread']
check('unread-count = PostgreSQL', unread == q("SELECT count(*) FROM notification WHERE recipient_user_id = %s AND status = 'unread' "
                                               "AND institution_id = %s", OP, INSTITUTION)[0][0], unread)
check('unread-count creció con los avisos de la sesión', unread >= op_unread_before + 4, (op_unread_before, unread))
by_type = api(op, params={'type': 'session_closed', 'per_page': 100}).json()
check('filtro por tipo', by_type['items'] and all(i['type'] == 'session_closed' for i in by_type['items']))
today = datetime.now(timezone.utc).date().isoformat()
check('filtro por fecha', any(i['session_id'] == sid for i in api(op, params={'from': today, 'per_page': 100}).json()['items']))
p1 = api(op, params={'per_page': 2, 'page': 1}).json()
p2 = api(op, params={'per_page': 2, 'page': 2}).json()
check('paginación sin duplicados', p1['total'] >= 4 and len(p1['items']) == 2 and
      not {i['id'] for i in p1['items']} & {i['id'] for i in p2['items']}, (p1['total'], p1['total_pages']))
check('orden: más recientes primero', [i['created_at'] for i in p1['items']] == sorted([i['created_at'] for i in p1['items']], reverse=True))
check('parámetro inválido -> 400 con campo', api(op, params={'status': 'deleted'}).json()['details']['field'] == 'status')

print('== 7. Mark one as read (idempotent, persisted)')
target = next(i for i in mine(op, sid) if i['type'] == 'session_scheduled')
first = api(op, f'/{target["id"]}/read', 'PATCH').json()
second = api(op, f'/{target["id"]}/read', 'PATCH').json()
row = q('SELECT status, read_at FROM notification WHERE id = %s', target['id'])[0]
check('marcar como leída: read en PostgreSQL, read_at del primer marcado', first['status'] == 'read' and row[0] == 'read'
      and second['read_at'] == first['read_at'] and row[1] is not None, row)
check('el contador baja en 1', api(op, '/unread-count').json()['unread'] == unread - 1)

print('== 8. Mark all as read (JSON and XML body)')
r = api(op, '/read-all', 'PATCH', json={'type': 'correction_required'})
check('read-all por tipo (JSON)', r.status_code == 200 and r.json()['updated'] >= 1 and
      not [i for i in mine(op, status='unread') if i['type'] == 'correction_required'], r.json())
xml = api(op, '/read-all', 'PATCH', data=b'<read_all_request><type>discrepancy_resolved</type></read_all_request>',
          headers={'Content-Type': 'application/xml', 'Accept': 'application/xml'})
doc = etree.fromstring(xml.content)
check('read-all con cuerpo XML validado contra el XSD', xml.status_code == 200 and XSD.validate(doc) and doc.tag == 'read_all_result')
bad = api(op, '/read-all', 'PATCH', data=b'<read_all_request><type>party</type></read_all_request>',
          headers={'Content-Type': 'application/xml'})
check('XML inválido -> 400 xsd_validation_failed (sin cambios)', bad.status_code == 400 and
      bad.json()['details']['reason'] == 'xsd_validation_failed')
r = api(op2, '/read-all', 'PATCH')
check('read-all de operador2 no toca las de operator', r.status_code == 200 and
      q("SELECT count(*) FROM notification WHERE recipient_user_id = %s AND type = 'session_closed' AND session_id = %s AND status = 'unread'",
        OP, sid)[0][0] == 1)

print('== 9. Another user cannot read or mark a notification')
closed_item = next(i for i in mine(op, sid) if i['type'] == 'session_closed')
codes = [api(s, f'/{closed_item["id"]}').status_code for s in (op2, spd, admin)] + \
        [api(s, f'/{closed_item["id"]}/read', 'PATCH').status_code for s in (op2, spd, admin)]
check('GET y PATCH ajenos -> 404 (no revela existencia)', set(codes) == {404}, codes)
check('sigue sin leer en PostgreSQL', q('SELECT status FROM notification WHERE id = %s', closed_item['id'])[0][0] == 'unread')
check('todas las notificaciones de operator son de su institución',
      q('SELECT count(*) FROM notification WHERE recipient_user_id = %s AND institution_id <> %s', OP, INSTITUTION)[0][0] == 0)

print('== 10. Invalid, expired, revoked tokens and header identities')
check('sin token -> 401', requests.get(API, timeout=10).status_code == 401)
check('token inválido -> 401', requests.get(API, headers={'Authorization': 'Bearer x.y.z'}, timeout=10).status_code == 401)
now = datetime.now(timezone.utc)
expired = jwt.encode({'sub': OP, 'jti': str(uuid.uuid4()), 'type': 'access', 'iat': now - timedelta(hours=2),
                      'exp': now - timedelta(hours=1)}, os.environ['JWT_SECRET_KEY'], algorithm=os.environ.get('JWT_ALGORITHM', 'HS256'))
check('token expirado -> 401', requests.get(API, headers={'Authorization': f'Bearer {expired}'}, timeout=10).status_code == 401)
temp = login('operator@instrumed.com', 'DemopwdOP78!')
token = temp.cookies.get('access_token')
temp.post(f'{AUTH}/logout', timeout=10)
check('token revocado por logout -> 401', requests.get(API, headers={'Authorization': f'Bearer {token}'}, timeout=10).status_code == 401)
check('identidad por header personalizado -> 401', requests.get(API, headers={'X-User-Id': OP, 'X-Institution-Id': INSTITUTION},
                                                                 timeout=10).status_code == 401)

print('== 11. JSON / XML, correlation ID, no creation endpoint')
xml_page = api(op, headers={'Accept': 'application/xml', 'X-Correlation-ID': f'e2e-notif-{TAG}'})
doc = etree.fromstring(xml_page.content)
check('página XML válida contra notifications.xsd', XSD.validate(doc) and doc.tag == 'notification_page', XSD.error_log)
check('X-Correlation-ID devuelto', xml_page.headers.get('X-Correlation-ID') == f'e2e-notif-{TAG}')
one = etree.fromstring(api(op, f'/{closed_item["id"]}', headers={'Accept': 'application/xml'}).content)
check('detalle XML = JSON', XSD.validate(one) and one.findtext('id') == closed_item['id'] and one.findtext('type') == 'session_closed')
check('sin endpoint de creación: POST -> 405', api(admin, '', 'POST', json={'type': 'session_closed'}).status_code == 405)
check('Swagger y OpenAPI publicados', requests.get(f'{API}/docs', timeout=10).status_code == 200 and
      b'/api/v1/notifications/read-all' in requests.get(f'{API}/openapi.yaml', timeout=10).content)

print('== 12. Portal: bell, page, link to the authorized session detail')
page = op.get(f'{WEB}/notifications')
check('página /notifications del Operador lista sus avisos', page.status_code == 200 and label(sid) in page.text
      and f'/operator/sessions/{sid}' in page.text)
check('campana: contador JSON = servicio', op.get(f'{WEB}/notifications/unread-count').json() ==
      {'available': True, 'enabled': True, 'unread': api(op, '/unread-count').json()['unread']})
detail = op.get(f'{WEB}/operator/sessions/{sid}', allow_redirects=False)
check('el enlace abre el detalle autorizado de su sesión', detail.status_code == 200)
check('operador2 no puede abrir ese detalle', op2.get(f'{WEB}/operator/sessions/{sid}', allow_redirects=False).status_code == 404)
sup_page = spd.get(f'{WEB}/notifications')
check('página del Supervisor enlaza /supervisor/sessions/<id>', sup_page.status_code == 200 and f'/supervisor/sessions/{sid}' in sup_page.text)
r = op.post(f'{WEB}/notifications/{closed_item["id"]}/read', data={'open': f'/operator/sessions/{sid}'}, allow_redirects=False)
check('marcar y abrir desde el portal', r.status_code == 302 and r.headers['Location'].endswith(f'/operator/sessions/{sid}')
      and q('SELECT status FROM notification WHERE id = %s', closed_item['id'])[0][0] == 'read')
check('Administrador: página disponible (sin avisos de sesión)', admin.get(f'{WEB}/notifications').status_code == 200)

print('== 13. Deduplication and replay (PostgreSQL)')
dups = q('SELECT source_table, source_id, recipient_user_id, type, count(*) FROM notification GROUP BY 1,2,3,4 HAVING count(*) > 1')
check('ningún (evento, destinatario, tipo) duplicado en toda la tabla', dups == [], dups)
before = pg_notifications(sid)
q('UPDATE notification_outbox SET processed_at = NULL WHERE session_id = %s', sid)
wait_for(lambda: pending(sid) == 0)
check('reprocesar todos los eventos de la sesión no duplica nada', pending(sid) == 0 and pg_notifications(sid) == before,
      (len(before), len(pg_notifications(sid))))
out_rows = q('SELECT source_table, source_id, count(*) FROM notification_outbox WHERE session_id = %s GROUP BY 1,2 HAVING count(*) > 1', sid)
check('un solo registro de outbox por evento de origen', out_rows == [])

# ====================================================================================================
print('== 14. NotificationsService (API + worker) stopped: the Operator keeps working')
compose('stop', 'notifications', 'notifications-worker')
try:
    requests.get(f'{SERVICE}/health', timeout=2)
    down = False
except requests.RequestException:
    down = True
check('contenedores notifications y notifications-worker detenidos', down)
t0 = time.monotonic()
sid2 = schedule(spd, OP, privacy=True, minutes=90)       # vision session: real YOLO counting
check('el Supervisor programa durante la caída (SessionService)', len(sid2) == 36 and status(sid2) == 'scheduled', sid2)
op.post(f'{WEB}/operator/sessions/{sid2}/begin', data={'instruments_ready': '1'}, allow_redirects=False)
loc1 = capture(op, sid2, 'tray_full.mp4')
loc2 = capture(op, sid2, 'tray_full.mp4')
form = {f'validated_{f}': str(n) for f, n in expected(sid2).items()}
form.update({f'notes_{f}': 'Verified on the physical tray' for f in expected(sid2)})
form['extra_instruments'] = '0'
r = op.post(f'{WEB}/operator/sessions/{sid2}/vision-validation', data=form, allow_redirects=False)
check('Operador: inicio, conteo YOLO inicial y final y validación humana sin NotificationsService',
      loc1.endswith('/capture') and loc2.endswith('/detection-summary') and r.status_code == 302
      and status(sid2) in ('awaiting_spd_review', 'correction_required'), (loc1, loc2, status(sid2)))
sid3 = schedule(spd, OP2, minutes=95)
r3 = report(op2, sid3, short=True)
check('Operador 2: reporte con faltante durante la caída', r3.status_code == 302 and status(sid3) == 'correction_required')
print(f'     (flujo del Operador completo en {time.monotonic() - t0:.1f}s con el servicio detenido)')
waiting = q('SELECT session_id, event_code FROM notification_outbox WHERE session_id IN (%s, %s) AND processed_at IS NULL', sid2, sid3)
check('los eventos esperan en notification_outbox (capturados por los triggers en la transacción del productor)',
      {(str(s), e) for s, e in waiting} >= {(sid2, 'session.scheduled'), (sid3, 'session.scheduled'), (sid3, 'manual_close')}, waiting)
check('ninguna notificación generada todavía', q('SELECT count(*) FROM notification WHERE session_id IN (%s, %s)', sid2, sid3)[0][0] == 0)
page = op.get(f'{WEB}/notifications')
check('portal: aviso controlado en /notifications (503)', page.status_code == 503 and
      'Notifications are temporarily unavailable' in page.text, page.status_code)
check('portal: la campana responde available=false sin error', op.get(f'{WEB}/notifications/unread-count').json()
      == {'available': False, 'enabled': True, 'unread': 0})
check('las demás pantallas siguen funcionando', op.get(f'{WEB}/operator/sessions').status_code == 200 and
      spd.get(f'{WEB}/supervisor/sessions').status_code == 200)
# Simulated trigger miss: the reconciliation sweep at start-up must recover it.
missed = q("DELETE FROM notification_outbox WHERE session_id = %s AND event_code = 'session.scheduled' RETURNING source_id", sid2)
check('(simulación) se elimina del outbox el evento session.scheduled de la sesión 2', len(missed) == 1)

print('== 15. Restart: pending events processed once, missed event reconciled')
compose('start', 'notifications', 'notifications-worker')


def _health_ok():
    try:
        return requests.get(f'{SERVICE}/health/ready', timeout=2).status_code == 200
    except requests.RequestException:
        return False


check('servicio de nuevo disponible', wait_for(_health_ok, 90))
caught_up = wait_for(lambda: pending(sid2) == 0 and pending(sid3) == 0
                     and q("SELECT count(*) FROM notification_outbox WHERE session_id = %s AND event_code = 'session.scheduled'", sid2)[0][0] == 1,
                     60)
check('el worker procesó los eventos pendientes (y reencoló el perdido)', caught_up)
n2 = wait_for(lambda: [i for i in mine(op, sid2) if i['type'] == 'session_scheduled'], 30)
check('session_scheduled de la caída entregado (recuperado por la reconciliación)', len(n2) == 1)
check('operador2: session_scheduled + correction_required de la caída', types(op2, sid3) == ['correction_required', 'session_scheduled'],
      types(op2, sid3))
check('supervisor@: discrepancy_pending de la sesión 3', [i['type'] for i in mine(spd, sid3)] == ['discrepancy_pending'])
before2, before3 = pg_notifications(sid2), pg_notifications(sid3)
compose('restart', 'notifications-worker')
time.sleep(6)
check('reiniciar otra vez el worker no duplica (reconciliación + outbox idempotentes)',
      pg_notifications(sid2) == before2 and pg_notifications(sid3) == before3, (before2, before3))
logs = compose('logs', '--since', '10m', 'notifications-worker').stdout
check('el worker deja evidencia en sus logs', 'worker started' in logs and 'reconciliation enqueued' in logs
      and f'session={sid3}' in logs)
check('los avisos anteriores a la caída siguen ahí', pg_notifications(sid) == before)

print('== 16. Regression: the three roles keep their screens')
check('Operador: lista de sesiones', op.get(f'{WEB}/operator/sessions').status_code == 200)
check('Supervisor: sesiones, discrepancias y auditoría', all(spd.get(f'{WEB}{p}').status_code == 200 for p in
      ('/supervisor/sessions', '/supervisor/discrepancies', f'/supervisor/sessions/{sid3}')))
check('Administrador: usuarios y familias', all(admin.get(f'{WEB}{p}').status_code == 200 for p in
      ('/admin/users', '/admin/instrument-families')))

print('== Cleanup: the outage sessions are finished through the real flows (open sessions would pile up)')
for open_sid in (sid2, sid3):
    resolve_and_close(spd, open_sid)
check('sesiones de la caída cerradas por el Supervisor', status(sid2) == 'closed' and status(sid3) == 'closed',
      (status(sid2), status(sid3)))
check('y sus cierres también notificados al Operador', wait_for(lambda: 'session_closed' in types(op, sid2)
                                                               and 'session_closed' in types(op2, sid3)))

print(f'\n{sum(results)}/{len(results)} checks passed')
sys.exit(0 if all(results) else 1)
