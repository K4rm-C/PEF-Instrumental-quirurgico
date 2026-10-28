"""Integral Docker deployment, end to end (GUIA_DOCKER_INTEGRAL.md, "Pruebas del despliegue").

Runs against the deployment started from the repository root with `docker compose up -d --build`
(root .env, default compose project): every component in a container, the portal and VisionWorker
included. It checks the images, start-up and healthchecks, the three roles, Admin / Supervisor /
Operator flows, the real YOLO model loaded inside the vision container (best.pt mounted read-only),
PostgreSQL, MongoDB checkpoints and telemetry, Redis (JWT, revocation, AOF persistence), audit,
notifications, MonitoringService by internal DNS name, the Operator with Session / Catalog / Audit /
Notifications / Monitoring stopped, restart and recovery, the durable queues across container
recreation, and that logs / images carry no secrets.

It stops and starts containers of THIS project only (never `down`, never volumes). It writes test data
(a family, sessions, lost pieces are NOT created: every count matches the expected inventory).

usage: python scripts/e2e_docker.py <videos_dir>      (videos: scripts/make_test_tray_videos.py)
env:   PEF_DOCKER_ENV_FILE (default .env of the repository root)

The tray videos are synthetic (generated from a reference photo): they show the technical path only,
never the clinical accuracy of the model.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
import requests
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = Path(os.environ.get('PEF_DOCKER_ENV_FILE', ROOT / '.env'))
VIDEOS = Path(sys.argv[1])
CFG = {k: v for k, _, v in (line.partition('=') for line in ENV_FILE.read_text(encoding='utf-8').splitlines()
                              if line and not line.startswith('#') and '=' in line)}
PORT = lambda key, default: CFG.get(key) or default  # noqa: E731
WEB = f"http://127.0.0.1:{PORT('WEB_PORT', '5000')}"
AUTH = f"http://127.0.0.1:{PORT('AUTH_PORT', '5001')}"
VISION = f"http://127.0.0.1:{PORT('VISION_PORT', '5002')}"
AUDIT = f"http://127.0.0.1:{PORT('AUDIT_PORT', '5006')}/api/v1/audit"
NOTIFY = f"http://127.0.0.1:{PORT('NOTIFICATIONS_PORT', '5007')}/api/v1/notifications"
DSN = (f"postgresql://{CFG['POSTGRES_USER']}:{CFG['POSTGRES_PASSWORD']}@127.0.0.1:{CFG['POSTGRES_PORT']}/"
       f"{CFG['POSTGRES_DB']}")
mongo = MongoClient(f"mongodb://{CFG['MONGO_USER']}:{CFG['MONGO_PASSWORD']}@127.0.0.1:{CFG['MONGO_PORT']}/?authSource=admin",
                    serverSelectionTimeoutMS=3000, tz_aware=True)[CFG.get('MONGO_DB') or 'pef']
COMPOSE = ['docker', 'compose', '--env-file', str(ENV_FILE)]
ALL = ['postgres', 'redis', 'mongo', 'garage', 'auth', 'catalog', 'session', 'evidence', 'audit', 'notifications',
       'notifications-worker', 'monitoring', 'monitoring-collector', 'web', 'vision']
OFFLINE = ['session', 'catalog', 'audit', 'notifications', 'notifications-worker', 'monitoring', 'monitoring-collector']
KIT = '51515151-0000-4000-8000-000000000001'
ROOM, STATION = '12121212-0000-4000-8000-000000000001', '13131313-0000-4000-8000-000000000001'
PHYSICIAN, PATIENT = '32323232-0000-4000-8000-000000000001', '31313131-0000-4000-8000-000000000001'
PASSWORDS = {'operator@instrumed.com': 'DemopwdOP78!', 'supervisor@instrumed.com': 'DemopwdSPD78!',
             'admin@instrumed.com': 'DemopwdADM78!'}
TAG = uuid.uuid4().hex[:6].upper()
results, tokens = [], []


def check(name, ok, detail=''):
    results.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail != '' else ''), flush=True)


def section(title):
    print(f'\n== {title}', flush=True)


def q(sql, *args):
    with psycopg.connect(DSN, autocommit=True) as conn:
        cursor = conn.execute(sql, args)
        return cursor.fetchall() if cursor.description else []


def compose(*args, check_rc=True):
    return subprocess.run(COMPOSE + list(args), cwd=ROOT, check=check_rc, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')


def ps():
    """service -> (state, health, container id)."""
    out = compose('ps', '-a', '--format', 'json').stdout.strip()
    rows = json.loads(out) if out.startswith('[') else [json.loads(line) for line in out.splitlines() if line]
    return {r['Service']: (r['State'], r.get('Health', ''), r['ID']) for r in rows}


def wait_for(predicate, seconds=60, step=1.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = predicate()
        except Exception:  # noqa: BLE001 - a component still starting
            value = None
        if value:
            return value
        time.sleep(step)
    try:
        return predicate()
    except Exception:  # noqa: BLE001
        return None


def http_status(url, **kwargs):
    try:
        return requests.get(url, timeout=5, **kwargs).status_code
    except requests.RequestException:
        return None


def healthy(services):
    state = ps()
    return all(state.get(s, ('', '', ''))[0] == 'running' and state[s][1] in ('', 'healthy') for s in services)


def login(email):
    session = requests.Session()
    response = session.post(f'{AUTH}/login', json={'email': email, 'password': PASSWORDS[email]}, timeout=10)
    if session.cookies.get('access_token'):
        tokens.append(session.cookies.get('access_token'))
    return session, response


def bearer(session):
    return {'Authorization': f'Bearer {session.cookies.get("access_token")}'}


def status(sid):
    return q('SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = %s', sid)[0][0]


def schedule(spd, operator_id):
    """Vision session (privacy notice confirmed), scheduled by the Supervisor through the portal."""
    kit_lines = q('SELECT family_id, quantity FROM kit_item WHERE kit_id = %s ORDER BY family_id', KIT)
    notice = str(q('SELECT id FROM privacy_notice_version WHERE active ORDER BY effective_at DESC LIMIT 1')[0][0])
    data = {'action': 'schedule', 'operator_user_id': operator_id,
            'procedure_type_id': str(q('SELECT procedure_type_id FROM procedure_kit WHERE kit_id = %s LIMIT 1', KIT)[0][0]),
            'room_id': ROOM, 'station_id': STATION, 'kit_id': KIT, 'patient_id': PATIENT,
            'scheduled_at': (datetime.now() + timedelta(minutes=60)).strftime('%Y-%m-%dT%H:%M'),
            'family_id[]': [str(f) for f, _ in kit_lines], 'expected_quantity[]': [str(n) for _, n in kit_lines],
            'physician_id[]': [PHYSICIAN], 'surgical_role_code[]': ['surgeon'],
            'confirm_privacy': '1', 'privacy_notice_version_id': notice, 'privacy_confirm_checkbox': '1'}
    response = spd.post(f'{WEB}/supervisor/sessions/new', data=data, allow_redirects=False)
    return response, response.headers.get('Location', '').rstrip('/').split('/')[-1]


def run_video(op, sid, video):
    capture = f'{WEB}/operator/sessions/{sid}/capture'
    op.get(capture)
    with open(VIDEOS / video, 'rb') as fh:
        code = op.post(f'{capture}/upload', files={'video': (video, fh, 'video/mp4')}).status_code
    state = {}
    for _ in range(600):
        state = op.get(f'{capture}/state').json()['session']
        if state['status'] != 'processing':
            break
        time.sleep(1)
    return code, state


def finish(op, sid):
    return op.post(f'{WEB}/operator/sessions/{sid}/capture/finish', allow_redirects=False).headers.get('Location', '')


def validate(op, sid):
    form = {}
    for family_id, quantity in q('SELECT family_id, expected_quantity FROM expected_inventory WHERE session_id = %s', sid):
        form.update({f'validated_{family_id}': str(quantity), f'notes_{family_id}': 'Verified on the physical tray'})
    # The synthetic tray shows pieces outside the kit: the Operator confirms none on the physical tray
    # (otherwise the session correctly goes to correction_required with a `surplus` discrepancy).
    form['extra_instruments'] = '0'
    return op.post(f'{WEB}/operator/sessions/{sid}/vision-validation', data=form, allow_redirects=False)


def vision_count(op, sid):
    """Operator: begin, initial count, final count (same tray), human validation."""
    op.post(f'{WEB}/operator/sessions/{sid}/begin', data={'instruments_ready': '1'}, allow_redirects=False)
    code, state = run_video(op, sid, 'tray_full.mp4')
    first = finish(op, sid)
    run_video(op, sid, 'tray_full.mp4')
    second = finish(op, sid)
    return code, state, first, second, validate(op, sid)


def container_ls(service, path):
    out = compose('exec', '-T', service, 'sh', '-c', f'ls {path} 2>/dev/null | grep -c json || true', check_rc=False)
    return int((out.stdout or '0').strip() or 0)


def redis_cli(*args):
    # The password goes in the exec environment (REDISCLI_AUTH), never on a command line or in a log.
    return compose('exec', '-T', '-e', f"REDISCLI_AUTH={CFG['REDIS_PASSWORD']}", 'redis', 'redis-cli', *args).stdout.strip()


def jti(token):
    import base64
    payload = token.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))['jti']


# ---------------------------------------------------------------------------------------------------
section('1. Images')
listing = json.loads(compose('images', '--format', 'json').stdout or '[]')
web_image = next((f"{r['Repository']}:{r['Tag']}" for r in listing if r['Repository'].endswith('-web')), None)
vision_image = next((f"{r['Repository']}:{r['Tag']}" for r in listing if r['Repository'].endswith('-vision')), None)
check('imagen del portal construida', web_image, web_image)
check('imagen de VisionWorker construida', vision_image, vision_image)
found = subprocess.run(['docker', 'run', '--rm', '--entrypoint', 'sh', vision_image, '-c',
                        "find / -xdev \\( -name '*.pt' -o -name '.env' \\) -not -path '/proc/*' "
                        "-not -path '*/site-packages/*' 2>/dev/null"], capture_output=True, text=True).stdout.split()
check('best.pt y .env NO están dentro de la imagen de vision', found == [], found)
found = subprocess.run(['docker', 'run', '--rm', '--entrypoint', 'sh', web_image, '-c',
                        "find /app -name '.env*' -o -name 'tests' -maxdepth 2"], capture_output=True, text=True).stdout.split()
check('.env y tests NO están dentro de la imagen del portal', found == [], found)
for image in (web_image, vision_image):
    config = json.loads(subprocess.run(['docker', 'image', 'inspect', image], capture_output=True, text=True).stdout)[0]['Config']
    leaked = [k for k, v in CFG.items() if ('PASSWORD' in k or 'SECRET' in k or k == 'S3_ACCESS_KEY') and v
              and v in json.dumps(config)]
    check(f'sin secretos en la configuración de {image.split(":")[0]}', not leaked and config.get('User'), (leaked, config.get('User')))

section('2. Startup of every component')
state = ps()
check('los 15 servicios existen y están en ejecución', all(state.get(s, ('',))[0] == 'running' for s in ALL),
      {s: state.get(s, ('missing',))[0] for s in ALL if state.get(s, ('missing',))[0] != 'running'})

section('3. Healthchecks')
check('healthchecks de Docker en healthy', wait_for(lambda: healthy(ALL), 240, 3),
      {s: v[1] for s, v in ps().items() if v[1] not in ('', 'healthy')})
for name, url in (('web', f'{WEB}/health/ready'), ('auth', f'{AUTH}/health/ready'), ('vision', f'{VISION}/health')):
    check(f'{name} responde 200 en {url.split("127.0.0.1")[1]}', http_status(url) == 200)
vision_health = requests.get(f'{VISION}/health', timeout=5).json()
check('vision: best.pt presente en /models (montaje)', vision_health['weights_present'] and
      vision_health['weights_path'] == '/models/best.pt', vision_health['weights_path'])
mounts = json.loads(subprocess.run(['docker', 'inspect', ps()['vision'][2]], capture_output=True, text=True).stdout)[0]['Mounts']
models = [m for m in mounts if m['Destination'] == '/models']
check('vision: /models es un bind mount de SOLO LECTURA', models and models[0]['RW'] is False and
      models[0]['Source'].replace('\\', '/').endswith('VisionWorker/weights'), models and models[0]['Source'])
write = compose('exec', '-T', 'vision', 'sh', '-c', 'touch /models/x', check_rc=False)
check('vision no puede escribir en /models', write.returncode != 0, write.stderr.strip()[-60:])
users = {s: compose('exec', '-T', s, 'id', '-u').stdout.strip() for s in ('web', 'vision')}
check('web y vision corren con usuario no root', all(u and u != '0' for u in users.values()), users)

section('4-5. Login and the three roles')
op, r_op = login('operator@instrumed.com')
spd, r_spd = login('supervisor@instrumed.com')
admin, r_adm = login('admin@instrumed.com')
check('login de los tres roles en AuthService (contenedor)', [r.status_code for r in (r_op, r_spd, r_adm)] == [200] * 3)
check('cookie access_token emitida', all(s.cookies.get('access_token') for s in (op, spd, admin)))
for who, session, path in (('Operador', op, '/operator/sessions'), ('Supervisor', spd, '/supervisor/sessions'),
                           ('Administrador', admin, '/admin/users')):
    check(f'{who}: {path} 200', session.get(f'{WEB}{path}', allow_redirects=False).status_code == 200)
check('Operador en pantalla de Supervisor: 403', op.get(f'{WEB}/supervisor/sessions', allow_redirects=False).status_code == 403)
check('Supervisor en pantalla de Admin: 403', spd.get(f'{WEB}/admin/users', allow_redirects=False).status_code == 403)
check('sin sesión: redirección a inicio de sesión', requests.get(f'{WEB}/operator/sessions', allow_redirects=False,
                                                               timeout=5).status_code in (302, 401))

section('6. Administration: create and edit')
category = q('SELECT id FROM cat_instrument_category ORDER BY code LIMIT 1')[0][0]
code = f'DOCKER_{TAG}'
r = admin.post(f'{WEB}/admin/instrument-families/new', allow_redirects=False, data={
    'code': code, 'name': f'Familia Docker {TAG}', 'category': str(category), 'how_to_identify': 'x',
    'classification_characteristics': 'y', 'function': 'z', 'status': 'active'})
family = q('SELECT id FROM instrument_family WHERE code = %s', code)
check('crear familia (portal -> CatalogService)', r.status_code == 302 and family, r.status_code)
r = admin.post(f'{WEB}/admin/instrument-families/{family[0][0]}/edit', allow_redirects=False, data={
    'name': f'Familia Docker {TAG} editada', 'category': str(category), 'status': 'active'})
check('editar familia', r.status_code == 302 and q('SELECT name FROM instrument_family WHERE id = %s', family[0][0])[0][0]
      == f'Familia Docker {TAG} editada')

section('7-8. Supervisor schedules, Operator starts')
operator_id = str(q("SELECT id FROM \"user\" WHERE email = 'operator@instrumed.com'")[0][0])
r, sid = schedule(spd, operator_id)
check('Supervisor programa sesión con visión', r.status_code == 302 and status(sid) == 'scheduled', (r.status_code, sid))
check('inventario esperado registrado', q('SELECT count(*) FROM expected_inventory WHERE session_id = %s', sid)[0][0] > 0)
_, sid_offline = schedule(spd, operator_id)          # for step 17 (scheduled while SessionService is up)
_, sid_queue = schedule(spd, operator_id)            # for step 19
r = op.post(f'{WEB}/operator/sessions/{sid}/begin', data={'instruments_ready': '1'}, allow_redirects=False)
check('Operador inicia la sesión', r.status_code == 302 and status(sid) == 'in_progress', status(sid))

section('9. Video processed by the containerized VisionWorker (real model)')
started = time.monotonic()
code, st = run_video(op, sid, 'tray_full.mp4')
boxes = len(st.get('boxes') or [])
check('vision (contenedor) procesó el video con best.pt', code == 202 and st['status'] == 'ready' and boxes > 0,
      (st['status'], boxes, st.get('model_version'), f'{time.monotonic() - started:.0f}s'))
logs = compose('logs', 'vision').stdout
check('el modelo se cargó dentro del contenedor (sin errores de pesos)', 'Weights not found' not in logs and
      'Traceback' not in logs)
check('conteo inicial registrado', finish(op, sid).endswith('/capture'))
run_video(op, sid, 'tray_full.mp4')
check('conteo final coincidente', finish(op, sid).endswith('/detection-summary'))
with tempfile.TemporaryDirectory() as empty:
    name = f'pef-vision-noweights-{TAG.lower()}'
    subprocess.run(['docker', 'run', '-d', '--rm', '--name', name, '-p', '127.0.0.1:56992:5002', '-v', f'{empty}:/models:ro',
                    '-e', 'MONGO_URL=', vision_image], capture_output=True, check=True)
    try:
        health = wait_for(lambda: requests.get('http://127.0.0.1:56992/health', timeout=3).json(), 120, 2)
        check('sin best.pt: el worker arranca y lo informa (weights_present=false)', health and health['weights_present'] is False)
        requests.post('http://127.0.0.1:56992/sessions/nw/bind', json={'expected': [], 'class_map': []}, timeout=5)
        with open(VIDEOS / 'tray_full.mp4', 'rb') as fh:
            up = requests.post('http://127.0.0.1:56992/sessions/nw/process-video', files={'video': ('t.mp4', fh, 'video/mp4')}, timeout=30)
        state_nw = wait_for(lambda: (lambda s: s if s.get('status') not in ('processing', 'idle') else None)(
            requests.get('http://127.0.0.1:56992/sessions/nw/state', timeout=3).json().get('session', {})), 60)
        check('sin best.pt: error controlado, sin caída del proceso', state_nw and state_nw.get('status') == 'error'
              and 'Weights not found' in (state_nw.get('last_error') or '') and http_status('http://127.0.0.1:56992/health') == 200,
              (up.status_code, state_nw and state_nw.get('status')))
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)

section('10-11. Human validation and PostgreSQL')
r = validate(op, sid)
check('validación humana', r.status_code == 302 and status(sid) == 'awaiting_spd_review', status(sid))
r = spd.post(f'{WEB}/supervisor/sessions/{sid}/close', data={'material_recovered': '1'}, allow_redirects=False)
check('Supervisor cierra la sesión', r.status_code == 302 and status(sid) == 'closed', status(sid))
events = dict(q('SELECT t.code, count(*) FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id '
                'WHERE ce.session_id = %s GROUP BY 1', sid))
check('PostgreSQL: eventos de conteo de la sesión', events.get('auto_count', 0) >= 2, events)
check('PostgreSQL: sin faltante físico (todo conteo coincide)', not q(
    "SELECT 1 FROM discrepancy d JOIN cat_discrepancy_reason r ON r.id = d.reason_id WHERE d.session_id = %s "
    "AND r.code = 'shortage'", sid))

section('12. MongoDB: checkpoints and telemetry')
checkpoints = wait_for(lambda: list(mongo.checkpoint_frame.find({'session_id': sid}).sort('session_checkpoint_seq', 1)), 60)
check('checkpoints escritos por web (contenedor)', checkpoints and checkpoints[0]['reason'] == 'start' and
      checkpoints[-1]['reason'] == 'close', [d['reason'] for d in checkpoints or []])
telemetry = wait_for(lambda: mongo.session_telemetry.count_documents({'session_id': sid}), 60)
check('telemetría escrita por vision (contenedor)', telemetry and telemetry > 0, telemetry)
check('evidencia JPEG de los checkpoints en EvidenceService', all(d.get('media_asset_id') for d in checkpoints or [{}]))

section('13. Redis: JWT, revocation, AOF persistence')
check('Redis con AOF activo', redis_cli('CONFIG', 'GET', 'appendonly').split()[-1] == 'yes')
op_token = op.cookies.get('access_token')
check('auth:jwt:{jti} del Operador en Redis', redis_cli('EXISTS', f'auth:jwt:{jti(op_token)}') == '1')
victim, _ = login('operator@instrumed.com')
victim_token = victim.cookies.get('access_token')
victim.post(f'{AUTH}/logout', headers=bearer(victim), timeout=10)
check('logout: token revocado (401 en /verify)', requests.get(f'{AUTH}/verify', headers={'Authorization': f'Bearer {victim_token}'},
                                                             timeout=5).status_code == 401)
compose('restart', 'redis')
wait_for(lambda: healthy(['redis']), 60, 2)
check('tras reiniciar Redis: la revocación persiste', wait_for(lambda: requests.get(
    f'{AUTH}/verify', headers={'Authorization': f'Bearer {victim_token}'}, timeout=5).status_code == 401, 30))
check('tras reiniciar Redis: la sesión activa sigue válida', wait_for(lambda: requests.get(
    f'{AUTH}/verify', headers=bearer(op), timeout=5).status_code == 200, 30))
check('Operador sigue en su pantalla tras reiniciar Redis', op.get(f'{WEB}/operator/sessions', allow_redirects=False).status_code == 200)

section('14. Audit')
audit = requests.get(f'{AUDIT}/events', params={'session_id': sid}, headers=bearer(spd), timeout=10)
check('AuditService (contenedor) entrega los eventos de la sesión', audit.status_code == 200 and audit.json().get('items'),
      audit.status_code)
check('auditoría de la familia creada en Admin', q('SELECT count(*) FROM access_audit WHERE action = %s AND resource_id = %s',
                                                  'CREATE_INSTRUMENT_FAMILY', family[0][0])[0][0] == 1)
check('Admin: bitácora de auditoría 200', admin.get(f'{WEB}/admin/audit-log', allow_redirects=False).status_code == 200)

section('15. Notifications and outbox')
check('outbox de notificaciones procesado por el worker', wait_for(lambda: q(
    'SELECT count(*) FROM notification_outbox WHERE session_id = %s AND processed_at IS NULL', sid)[0][0] == 0, 60))
notes = q('SELECT type FROM notification WHERE session_id = %s', sid)
check('notificaciones generadas para la sesión', notes, sorted({n[0] for n in notes}))
mine = requests.get(NOTIFY, headers=bearer(op), params={'per_page': 100}, timeout=10)
check('NotificationsService entrega las del Operador', mine.status_code == 200 and any(
    i['session_id'] == sid for i in mine.json()['items']))

section('16. MonitoringService: web and vision by internal name')
latest = lambda: {c: (s, e) for c, s, e in q(  # noqa: E731
    "SELECT DISTINCT ON (component) component, status, endpoint FROM monitoring_check "
    "ORDER BY component, checked_at DESC")}
mon = wait_for(lambda: (lambda m: m if m.get('web', ('',))[0] == 'up' and m.get('vision', ('',))[0] == 'up' else None)(latest()),
               120, 3) or latest()
check('web up vía http://web:5000', mon.get('web', ('', ''))[0] == 'up' and 'web:5000' in (mon['web'][1] or ''), mon.get('web'))
check('vision up vía http://vision:5002', mon.get('vision', ('', ''))[0] == 'up' and 'vision:5002' in (mon['vision'][1] or ''),
      mon.get('vision'))
check('sin host.docker.internal en los destinos', not any('host.docker.internal' in (e or '') for _, e in mon.values()))
check('todos los componentes up', all(s == 'up' for s, _ in mon.values()), {c: s for c, (s, _) in mon.items() if s != 'up'})

section('17. Operator with Session / Catalog / Audit / Notifications / Monitoring stopped')
compose('stop', *OFFLINE)
stopped = ps()
check('servicios detenidos', all(stopped[s][0] == 'exited' for s in OFFLINE), {s: stopped[s][0] for s in OFFLINE})
check('Operador: lista de sesiones', op.get(f'{WEB}/operator/sessions', allow_redirects=False).status_code == 200)
code, st, first, second, r = vision_count(op, sid_offline)
check('Operador cuenta con visión y valida', st['status'] == 'ready' and first.endswith('/capture') and
      second.endswith('/detection-summary') and r.status_code == 302 and status(sid_offline) == 'awaiting_spd_review',
      (st['status'], status(sid_offline)))
compose('start', *OFFLINE)
check('servicios de nuevo healthy', wait_for(lambda: healthy(OFFLINE), 180, 3))
check('notificaciones pendientes entregadas al volver el worker', wait_for(lambda: q(
    'SELECT count(*) FROM notification_outbox WHERE session_id = %s AND processed_at IS NULL', sid_offline)[0][0] == 0, 90))
r = spd.post(f'{WEB}/supervisor/sessions/{sid_offline}/close', data={'material_recovered': '1'}, allow_redirects=False)
check('Supervisor cierra la sesión al volver SessionService', status(sid_offline) == 'closed', status(sid_offline))

section('18. Restart and recovery')
before = ps()
compose('restart', 'web', 'vision')
check('web y vision healthy tras reiniciar', wait_for(lambda: healthy(['web', 'vision']), 240, 3))
check('la sesión del Operador sobrevive al reinicio (JWT en Redis)',
      op.get(f'{WEB}/operator/sessions', allow_redirects=False).status_code == 200)
check('reinicio sin recrear (mismo contenedor)', ps()['web'][2] == before['web'][2])

section('19. Durable queues across container recreation')
op.post(f'{WEB}/operator/sessions/{sid_queue}/begin', data={'instruments_ready': '1'}, allow_redirects=False)
compose('stop', 'mongo')
code, st = run_video(op, sid_queue, 'tray_full.mp4')
finish(op, sid_queue)
run_video(op, sid_queue, 'tray_full.mp4')
finish(op, sid_queue)
r = validate(op, sid_queue)
check('MongoDB detenido: el Operador termina igual', r.status_code == 302 and status(sid_queue) == 'awaiting_spd_review')
time.sleep(3)
outbox_before = container_ls('web', '/data/outbox')
spool_before = requests.get(f'{VISION}/health', timeout=5).json()['telemetry']['pending']
check('checkpoints pendientes en web_outbox', outbox_before >= 2, outbox_before)
check('telemetría pendiente en el spool de vision_data', spool_before > 0, spool_before)
ids_before = {s: ps()[s][2] for s in ('web', 'vision')}
compose('up', '-d', '--force-recreate', '--no-deps', 'web', 'vision')
wait_for(lambda: healthy(['web', 'vision']), 240, 3)
ids_after = {s: ps()[s][2] for s in ('web', 'vision')}
check('web y vision recreados (contenedores nuevos)', all(ids_before[s] != ids_after[s] for s in ids_before))
outbox_after = container_ls('web', '/data/outbox')
spool_after = requests.get(f'{VISION}/health', timeout=5).json()['telemetry']['pending']
check('la cola de checkpoints sobrevive a la recreación', outbox_after == outbox_before, (outbox_before, outbox_after))
check('el spool de telemetría sobrevive a la recreación', spool_after == spool_before, (spool_before, spool_after))
compose('start', 'mongo')
wait_for(lambda: healthy(['mongo']), 90, 2)
check('cola de checkpoints entregada al volver MongoDB', wait_for(lambda: container_ls('web', '/data/outbox') == 0, 120, 2))
check('spool de telemetría entregado', wait_for(lambda: requests.get(f'{VISION}/health', timeout=5).json()
                                                ['telemetry']['pending'] == 0, 120, 2))
docs = list(mongo.checkpoint_frame.find({'session_id': sid_queue}).sort('session_checkpoint_seq', 1))
check('checkpoints de la sesión en MongoDB, sin duplicados', [d['reason'] for d in docs] == ['start', 'close'],
      [d['reason'] for d in docs])
check('telemetría de la sesión en MongoDB', mongo.session_telemetry.count_documents({'session_id': sid_queue}) > 0)
spd.post(f'{WEB}/supervisor/sessions/{sid_queue}/close', data={'material_recovered': '1'}, allow_redirects=False)

section('Security: logs without passwords, tokens or secrets')
logs = compose('logs', '--no-color', *ALL).stdout
secrets_found = [k for k, v in CFG.items() if ('PASSWORD' in k or 'SECRET' in k) and len(v) >= 8 and v in logs]
check('logs sin secretos del .env', not secrets_found, secrets_found)
check('logs sin contraseñas de usuarios', not [e for e, p in PASSWORDS.items() if p in logs])
leaked = [t for t in tokens if t in logs or t.split('.')[2] in logs]
check('logs sin JWT completos', not leaked, len(leaked))
check('logs sin access_token en query strings', 'access_token=' not in logs)
check('todo healthy al final', wait_for(lambda: healthy(ALL), 180, 3), {s: v[:2] for s, v in ps().items() if v[1] not in ('', 'healthy')})

passed = sum(ok for _, ok in results)
print(f'\nRESUMEN DOCKER: {passed}/{len(results)} PASS')
sys.exit(0 if passed == len(results) else 1)
