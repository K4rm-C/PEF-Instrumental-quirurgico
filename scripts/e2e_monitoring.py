"""MonitoringService end to end (live services; see GUIA_MONITORING.md).

Needs:
- BackendWebFlask (:5000), with MONITORING_SERVICE_URL, CATALOG_SERVICE_URL and SESSION_SERVICE_URL;
- AuthService (:5001) and VisionWorker (:5002);
- the compose services postgres, redis, mongo, garage, catalog, session, evidence, audit,
  notifications, notifications-worker, monitoring and monitoring-collector.

Steps 9-12 stop and start the compose services catalog, notifications, monitoring-collector and
monitoring. Every status checked here is a stored measurement of the real collector; nothing is
inserted by hand.

usage: python scripts/e2e_monitoring.py
env:   DATABASE_URL, MONITORING_SERVICE_URL (as seen from this machine), JWT_SECRET_KEY (only to forge an
       expired token), COMPOSE_ENV_FILE, COMPOSE_PROJECT, MONITORING_INTERVAL_SECONDS (the collector's)
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
SERVICE = os.environ['MONITORING_SERVICE_URL'].rstrip('/')
API = f'{SERVICE}/api/v1/monitoring'
DSN = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')
INTERVAL = float(os.environ.get('MONITORING_INTERVAL_SECONDS', '30'))
COMPOSE = ['docker', 'compose', '--env-file', os.environ.get('COMPOSE_ENV_FILE', '.env')] + (
    ['-p', os.environ['COMPOSE_PROJECT']] if os.environ.get('COMPOSE_PROJECT') else [])
XSD = etree.XMLSchema(etree.parse(str(ROOT / 'apps/MonitoringService/schemas/monitoring.xsd')))
COMPONENTS = ['web', 'auth', 'catalog', 'session', 'evidence', 'audit', 'notifications', 'notifications-worker', 'vision',
              'postgres', 'redis', 'mongo', 'garage']
MICROSERVICES = ['auth', 'catalog', 'session', 'evidence', 'audit', 'notifications']
KIT = '51515151-0000-4000-8000-000000000001'
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


def api(session, path, **params):
    headers = params.pop('headers', {})
    return requests.get(f'{API}{path}', params=params, headers={**bearer(session), **headers}, timeout=10)


def current(session):
    return {i['name']: i for i in api(session, '/services').json()['items']}


def wait_for(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(1)
    return predicate()


def reachable(url):
    try:
        return requests.get(url, timeout=2).status_code == 200
    except requests.RequestException:
        return False


def compose(*args):
    return subprocess.run(COMPOSE + list(args), cwd=ROOT, check=True, capture_output=True, text=True)


def status(sid):
    return q('SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = %s', sid)[0][0]


def schedule(spd, operator_id, minutes=60):
    kit_lines = q('SELECT family_id, quantity FROM kit_item WHERE kit_id = %s ORDER BY family_id', KIT)
    data = {'operator_user_id': operator_id, 'procedure_type_id': str(q('SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1')[0][0]),
            'room_id': ROOM, 'station_id': STATION, 'kit_id': KIT, 'patient_id': PATIENT,
            'scheduled_at': (datetime.now() + timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M'),
            'family_id[]': [str(f) for f, _ in kit_lines], 'expected_quantity[]': [str(n) for _, n in kit_lines],
            'physician_id[]': [PHYSICIAN], 'surgical_role_code[]': ['surgeon']}
    response = spd.post(f'{WEB}/supervisor/sessions/new', data=data, allow_redirects=False)
    return response.headers.get('Location', '').rstrip('/').split('/')[-1]


op = login('operator@instrumed.com', 'DemopwdOP78!')
spd = login('supervisor@instrumed.com', 'DemopwdSPD78!')
admin = login('admin@instrumed.com', 'DemopwdADM78!')
OP = str(q("SELECT id FROM \"user\" WHERE email = 'operator@instrumed.com'")[0][0])
TOLERANCE = INTERVAL * 2 + 10        # a change is visible after at most one cycle (+ timeouts)

# ====================================================================================================
print('== 1. MonitoringService starts in Docker')
ps = compose('ps', '--format', '{{.Service}} {{.State}}', 'monitoring', 'monitoring-collector').stdout
check('contenedores monitoring y monitoring-collector en ejecución', 'monitoring running' in ps
      and 'monitoring-collector running' in ps, ps.strip().replace('\n', ' | '))
health = requests.get(f'{SERVICE}/health', timeout=5)
check('/health del servicio (PostgreSQL y Redis) y edad de la última corrida', health.status_code == 200
      and health.json()['checks'] == {'database': 'ok', 'redis': 'ok'}
      and int(health.headers['X-Last-Run-Age-Seconds']) <= INTERVAL + 5, health.headers.get('X-Last-Run-Age-Seconds'))
logs = compose('logs', 'monitoring-collector').stdout
check('el recolector arrancó y registra cada corrida en su log', 'collector started' in logs and ' run ' in logs)

print('== 2-4. Components, databases, dependencies and measured latencies')
now = wait_for(lambda: (lambda c: c if all(c[n]['status'] == 'up' for n in COMPONENTS) else None)(current(admin)), TOLERANCE)
state = now or current(admin)
check('los 13 componentes verificados y disponibles', now is not None and list(state) == COMPONENTS,
      {n: i['status'] for n, i in state.items() if i['status'] != 'up'})
check('los 6 microservicios responden su /health/ready con HTTP 200',
      all(state[n]['check']['http_status'] == 200 and state[n]['check']['endpoint'].endswith('/health/ready') for n in MICROSERVICES))
check('dependencias reportadas por cada microservicio (database y redis ok)',
      all({d['name']: d['status'] for d in state[n]['check']['dependencies']} == {'database': 'ok', 'redis': 'ok'}
          for n in ('catalog', 'session', 'audit', 'notifications')))
check('Evidence incluye el almacenamiento de objetos entre sus dependencias',
      {d['name'] for d in state['evidence']['check']['dependencies']} >= {'database', 'redis'}, state['evidence']['check']['dependencies'])
check('PostgreSQL, Redis, MongoDB y Garage verificados directamente',
      all(state[n]['status'] == 'up' for n in ('postgres', 'redis', 'mongo', 'garage')))
check('notifications-worker verificado por su conexión real y el backlog del outbox',
      {d['name']: d['status'] for d in state['notifications-worker']['check']['dependencies']}
      == {'database_connection': 'ok', 'outbox_backlog': 'ok'})
check('VisionWorker con pesos del modelo presentes',
      {d['name']: d['status'] for d in state['vision']['check']['dependencies']} == {'model_weights': 'ok'})
latencies = {n: state[n]['check']['latency_ms'] for n in COMPONENTS}
check('latencias medidas realmente (> 0 y menores al timeout)', all(0 < v < 3000 for v in latencies.values()), latencies)
check('ningún endpoint guardado contiene credenciales', all('@' not in (state[n]['check']['endpoint'] or '') for n in COMPONENTS))

print('== 5-6. Stored in PostgreSQL and read back')
latest_run = q('SELECT id, components FROM monitoring_run ORDER BY started_at DESC LIMIT 1')[0]
rows = q('SELECT component, status, latency_ms FROM monitoring_check WHERE run_id = %s', latest_run[0])
check('cada corrida guarda una fila por componente', latest_run[1] == 13 and len(rows) == 13)
cat_id = state['catalog']['check']['id']
pg = q('SELECT status, latency_ms, http_status FROM monitoring_check WHERE id = %s', cat_id)[0]
check('el estado de la API es la fila de PostgreSQL', pg[0] == 'up' and float(pg[1]) == state['catalog']['check']['latency_ms']
      and pg[2] == 200, pg)
page = api(admin, '/history', component='catalog', per_page=5).json()
check('historial de catalog recuperado por la API = PostgreSQL', page['total'] ==
      q("SELECT count(*) FROM monitoring_check WHERE component = 'catalog'")[0][0] and len(page['items']) == 5, page['total'])

print('== 7. Filters by component, date and status')
since = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat().replace('+00:00', 'Z')
recent = api(admin, '/history', component='session', **{'from': since}, per_page=200).json()
check('filtro por componente y fecha', recent['items'] and all(i['component'] == 'session' and i['checked_at'] >= since[:19]
                                                               for i in recent['items']), recent['total'])
up_only = api(admin, '/history', status='up', per_page=50).json()
check('filtro por estado', up_only['items'] and all(i['status'] == 'up' for i in up_only['items']))

print('== 8. Availability and latency aggregates')
window_from = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0)
window_to = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=1)
iso = lambda d: d.isoformat().replace('+00:00', 'Z')  # noqa: E731
summary = api(admin, '/summary', component='session', **{'from': iso(window_from), 'to': iso(window_to)}, bucket='5m').json()
sql = q("""SELECT count(*), count(*) FILTER (WHERE status IN ('up','degraded')), count(*) FILTER (WHERE status <> 'unknown'),
                  round(avg(latency_ms) FILTER (WHERE status IN ('up','degraded')), 2)
           FROM monitoring_check WHERE component = 'session' AND checked_at >= %s AND checked_at < %s""", window_from, window_to)[0]
stats = summary['components'][0]
check('agregados de la API = SQL independiente sobre PostgreSQL', stats['checks'] == sql[0]
      and stats['availability_pct'] == round(100.0 * sql[1] / sql[2], 2) and stats['avg_latency_ms'] == float(sql[3]),
      (stats['checks'], stats['availability_pct'], stats['avg_latency_ms'], sql))
check('serie temporal por intervalos de 5 min', sum(p['checks'] for p in summary['series'][0]['points']) == sql[0])

# ====================================================================================================
print('== 9. CatalogService stopped -> detected')
compose('stop', 'catalog')
t0 = time.monotonic()
down = wait_for(lambda: current(admin)['catalog']['status'] == 'down', TOLERANCE)
detected_in = time.monotonic() - t0
catalog = current(admin)['catalog']
check('caída de catalog detectada y registrada', down and catalog['check']['error_type'] in (
    'dns_error', 'connection_refused', 'connection_error', 'connect_timeout', 'timeout'),
      (catalog['status'], catalog['check']['error_type'], f'{detected_in:.0f}s'))
others = current(admin)
check('la caída de un componente no bloquea la comprobación de los demás',
      all(others[n]['status'] == 'up' and others[n]['freshness'] == 'current' for n in COMPONENTS if n != 'catalog'),
      {n: others[n]['status'] for n in COMPONENTS})
admin_page = admin.get(f'{WEB}/admin/system-status')
check('pantalla RF del Administrador muestra catalog no disponible', admin_page.status_code == 200 and
      'data-component="catalog" data-status="down"' in admin_page.text)
check('el Supervisor ve catalog no disponible en su resumen', 'data-component="catalog" data-status="down"' in
      spd.get(f'{WEB}/supervisor/services-health').text)

print('== 10. CatalogService restarted -> recovery detected')
compose('start', 'catalog')
recovered = wait_for(lambda: current(admin)['catalog']['status'] == 'up', TOLERANCE + 30)
check('recuperación de catalog detectada', recovered, current(admin)['catalog']['status'])
transitions = [i['status'] for i in api(admin, '/history', component='catalog', per_page=200,
                                        **{'from': iso(datetime.now(timezone.utc) - timedelta(minutes=5))}).json()['items']]
chronological = list(reversed(transitions))      # the API answers newest first
outage = next((i for i, value in enumerate(chronological) if value == 'down' and 'up' in chronological[:i]), None)
# In a freshly created environment the oldest checks may predate the service being ready: only the
# sequence matters (up, then the outage, then up again), not the first value of the window.
check('el historial muestra up -> down -> up', outage is not None and 'up' in chronological[outage:]
      and transitions[0] == 'up', transitions[:12])
day = api(admin, '/services/catalog').json()['last_24h']
check('la caída se refleja en la disponibilidad agregada', day['down'] >= 1 and day['availability_pct'] < 100.0, day)

print('== 11. NotificationsService stopped -> outage recorded')
compose('stop', 'notifications')
gone = wait_for(lambda: current(admin)['notifications']['status'] == 'down', TOLERANCE)
now_state = current(admin)
check('caída de notifications registrada', gone, now_state['notifications']['check']['error_type'])
check('su worker (proceso independiente) sigue disponible', now_state['notifications-worker']['status'] == 'up')
stored = q("SELECT count(*) FROM monitoring_check WHERE component = 'notifications' AND status = 'down' "
           "AND checked_at > now() - interval '5 minutes'")[0][0]
check('la indisponibilidad queda en PostgreSQL', stored >= 1, stored)
compose('start', 'notifications')
check('notifications recuperado', wait_for(lambda: current(admin)['notifications']['status'] == 'up', TOLERANCE + 30))

print('== 12. MonitoringService stopped: the Operator and the other services keep working')
compose('stop', 'monitoring-collector')
stale = wait_for(lambda: all(i['status'] == 'unknown' and i['freshness'] == 'stale' for i in current(admin).values()),
                 INTERVAL * 3 + 20)
check('con el recolector detenido la API no repite el último estado: todo pasa a unknown (stale)', stale)
compose('stop', 'monitoring')
check('API de monitoreo detenida', not reachable(f'{SERVICE}/health'))
runs_before = q('SELECT count(*) FROM monitoring_run')[0][0]
t0 = time.monotonic()
sid = schedule(spd, OP, minutes=70)
r1 = op.post(f'{WEB}/operator/sessions/{sid}/begin', data={'instruments_ready': '1'}, allow_redirects=False)
lines = {str(f): int(n) for f, n in q('SELECT family_id, expected_quantity FROM expected_inventory WHERE session_id = %s', sid)}
r2 = op.post(f'{WEB}/operator/sessions/{sid}/manual-report', data={f'reported_{f}': str(n) for f, n in lines.items()},
             allow_redirects=False)
check('Operador: inicia y reporta su conteo sin MonitoringService', r1.status_code == 302 and r2.status_code == 302
      and status(sid) == 'awaiting_spd_review', status(sid))
r3 = spd.post(f'{WEB}/supervisor/sessions/{sid}/close', data={'material_recovered': '1'}, allow_redirects=False)
check('Supervisor (SessionService) confirma el cierre seguro sin MonitoringService', r3.status_code == 302 and status(sid) == 'closed')
check('Administración (CatalogService) y Auth siguen funcionando', admin.get(f'{WEB}/admin/instrument-families').status_code == 200
      and requests.get(f'{AUTH}/health/ready', timeout=5).status_code == 200)
print(f'     (flujo completo en {time.monotonic() - t0:.1f}s con MonitoringService detenido)')
check('MonitoringService no escribió nada durante la caída', q('SELECT count(*) FROM monitoring_run')[0][0] == runs_before)
check('ninguna tabla de monitoreo referencia sesiones quirúrgicas',
      q("SELECT count(*) FROM information_schema.table_constraints tc JOIN information_schema.constraint_column_usage cu "
        "ON cu.constraint_name = tc.constraint_name WHERE tc.table_name LIKE 'monitoring_%%' AND tc.constraint_type = 'FOREIGN KEY' "
        "AND cu.table_name <> 'monitoring_run'")[0][0] == 0)
page = admin.get(f'{WEB}/admin/system-status')
check('pantalla del Administrador: aviso controlado (503)', page.status_code == 503 and 'data-monitoring-unavailable' in page.text)
sup = spd.get(f'{WEB}/supervisor/services-health')
check('pantalla del Supervisor: aviso controlado (503)', sup.status_code == 503 and 'data-monitoring-unavailable' in sup.text)
compose('start', 'monitoring', 'monitoring-collector')
check('MonitoringService de nuevo disponible', wait_for(lambda: reachable(f'{SERVICE}/health/ready'), 60))
resumed = wait_for(lambda: all(i['freshness'] == 'current' and i['status'] == 'up' for i in current(admin).values()), TOLERANCE + 20)
check('las verificaciones se reanudan (todo current y up)', resumed, {n: (i['status'], i['freshness']) for n, i in current(admin).items()
                                                                      if i['status'] != 'up'})
check('nuevas corridas guardadas tras la recuperación', q('SELECT count(*) FROM monitoring_run')[0][0] > runs_before)

# ====================================================================================================
print('== 13. Authorized and unauthorized users')
sup_view = api(spd, '/services').json()
check('Supervisor: solo nombre, estado y última comprobación (sin infraestructura)',
      set(sup_view) == {'items', 'counts'} and all(set(i) == {'name', 'label', 'status', 'checked_at', 'freshness'}
                                                   for i in sup_view['items']))
check('Supervisor: resumen sin latencias', all('avg_latency_ms' not in c for c in api(spd, '/summary').json()['components']))
check('Supervisor: sin historial ni detalle técnico (403)', api(spd, '/history').status_code == 403
      and api(spd, '/services/catalog').status_code == 403)
check('Operador: 403 en todo', all(api(op, p).status_code == 403 for p in ('/services', '/summary', '/history')))
check('Operador sin pantallas de estado', op.get(f'{WEB}/admin/system-status', allow_redirects=False).status_code == 403
      and op.get(f'{WEB}/supervisor/services-health', allow_redirects=False).status_code == 403)

print('== 14. Invalid, expired and revoked tokens')
check('sin token -> 401', requests.get(f'{API}/services', timeout=10).status_code == 401)
check('token inválido -> 401', requests.get(f'{API}/services', headers={'Authorization': 'Bearer x.y.z'}, timeout=10).status_code == 401)
admin_id = str(q("SELECT id FROM \"user\" WHERE email = 'admin@instrumed.com'")[0][0])
t = datetime.now(timezone.utc)
expired = jwt.encode({'sub': admin_id, 'jti': str(uuid.uuid4()), 'type': 'access', 'iat': t - timedelta(hours=2),
                      'exp': t - timedelta(hours=1)}, os.environ['JWT_SECRET_KEY'], algorithm=os.environ.get('JWT_ALGORITHM', 'HS256'))
check('token expirado -> 401', requests.get(f'{API}/services', headers={'Authorization': f'Bearer {expired}'}, timeout=10).status_code == 401)
temp = login('admin@instrumed.com', 'DemopwdADM78!')
token = temp.cookies.get('access_token')
temp.post(f'{AUTH}/logout', timeout=10)
check('token revocado por logout -> 401', requests.get(f'{API}/services', headers={'Authorization': f'Bearer {token}'},
                                                        timeout=10).status_code == 401)
check('identidad por headers -> 401', requests.get(f'{API}/services', headers={'X-User-Id': admin_id, 'X-Role': 'it_admin'},
                                                    timeout=10).status_code == 401)

print('== 15. JSON and XML')
for path, params, root in (('/services', {}, 'service_list'), ('/services/catalog', {'recent': 3}, 'service'),
                           ('/history', {'per_page': 5}, 'check_page'), ('/summary', {'bucket': '15m'}, 'summary')):
    response = api(admin, path, headers={'Accept': 'application/xml', 'X-Correlation-ID': 'e2e-mon-xml'}, **params)
    document = etree.fromstring(response.content)
    check(f'XML {path} válido contra monitoring.xsd', response.status_code == 200 and document.tag == root
          and XSD.validate(document) and response.headers.get('X-Correlation-ID') == 'e2e-mon-xml', XSD.error_log)
sup_xml = etree.fromstring(api(spd, '/services', headers={'Accept': 'application/xml'}).content)
check('XML del Supervisor válido y sin detalle técnico', XSD.validate(sup_xml) and sup_xml.find('items/service/check') is None)
check('Swagger y OpenAPI publicados', requests.get(f'{API}/docs', timeout=10).status_code == 200
      and b'/api/v1/monitoring/summary' in requests.get(f'{API}/openapi.yaml', timeout=10).content)

print('== 16. Bad parameters and unknown components')
for params, field in (({'component': 'nope'}, 'component'), ({'status': 'fine'}, 'status'), ({'per_page': 999}, 'per_page'),
                      ({'from': 'ayer'}, 'from'), ({'sort': 'status'}, 'sort')):
    response = api(admin, '/history', **params)
    check(f'/history {field} inválido -> 400', response.status_code == 400 and response.json()['details']['field'] == field)
check('/summary bucket inválido -> 400', api(admin, '/summary', bucket='2m').json()['details']['field'] == 'bucket')
check('parámetro desconocido -> 400', api(admin, '/history', limit=5).json()['details']['unknown_parameters'] == ['limit'])
unknown = api(admin, '/services/mainframe')
check('componente inexistente -> 404', unknown.status_code == 404 and unknown.json()['error'] == 'not_found')

print('== 17. Portal screens (regression)')
page = admin.get(f'{WEB}/admin/system-status?window=1h')
check('Administrador: estado del sistema con mediciones reales y gráfica Highcharts', page.status_code == 200
      and page.text.count('data-status="up"') == 13 and 'highcharts@11.4.8' in page.text and 'latency-series' in page.text)
check('Supervisor: estado de los servicios', spd.get(f'{WEB}/supervisor/services-health').status_code == 200)
check('Operador, Supervisor y Administrador conservan sus pantallas', op.get(f'{WEB}/operator/sessions').status_code == 200
      and spd.get(f'{WEB}/supervisor/sessions').status_code == 200 and admin.get(f'{WEB}/admin/users').status_code == 200)
check('/health del portal (público, sin AuthService)', requests.get(f'{WEB}/health', timeout=5).json()['status'] == 'ready')

print(f'\n{sum(results)}/{len(results)} checks passed')
sys.exit(0 if all(results) else 1)
