"""PEF-DS06 MongoDB end to end on live services (see GUIA_MONGODB.md, "Reproducir las pruebas").

Needs running: BackendWebFlask (:5000, MONGO_URL + EVIDENCE_SERVICE_URL), BackendAuthService (:5001),
VisionWorker (:5002, best.pt + MONGO_URL) and the compose services postgres, redis, mongo, garage,
evidence. It creates two vision sessions with the demo users, then checks the 10 required evidences;
step 9 stops and restarts the compose `mongo` service (data volume untouched).

usage: python scripts/e2e_mongo_ds06.py <videos_dir>       (videos: scripts/make_test_tray_videos.py)
env:   DATABASE_URL, MONGO_URL, MONGO_DB, EVIDENCE_SERVICE_URL, PEF_OUTBOX_DIR (same as the Web
       process), COMPOSE_ENV_FILE (.env), COMPOSE_PROJECT (optional), WEB_DIR (apps/BackendWebFlask)

The tray videos come from the UI reference photo (not an authentic capture): they demonstrate the
technical persistence only, never the accuracy of the YOLO model.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import requests
from pymongo import MongoClient

WEB, AUTH, WORKER, EVIDENCE = (os.environ.get('PEF_WEB_URL', 'http://127.0.0.1:5000'), os.environ.get('PEF_AUTH_URL', 'http://127.0.0.1:5001'), os.environ.get('PEF_VISION_URL', 'http://127.0.0.1:5002'),
                               os.environ['EVIDENCE_SERVICE_URL'])
DSN = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')
mongo = MongoClient(os.environ['MONGO_URL'], serverSelectionTimeoutMS=2000, tz_aware=True)[os.environ.get('MONGO_DB', 'pef')]
videos = Path(sys.argv[1])
OUTBOX = Path(os.environ['PEF_OUTBOX_DIR'])
COMPOSE = ['docker', 'compose', '--env-file', os.environ.get('COMPOSE_ENV_FILE', '.env')] + (
    ['-p', os.environ['COMPOSE_PROJECT']] if os.environ.get('COMPOSE_PROJECT') else [])
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail != '' else ''), flush=True)


def q(sql, *args):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, args).fetchall()


def show(title, value):
    print(f'---- {title}\n' + json.dumps(value, default=str, indent=2, ensure_ascii=False)[:2500], flush=True)


op = requests.Session()
op.post(f'{AUTH}/login', json={'email': 'operator@instrumed.com', 'password': 'DemopwdOP78!'})
token = op.cookies.get('access_token')


def run(session_id, video):
    capture = f'{WEB}/operator/sessions/{session_id}/capture'
    op.get(capture)
    with open(video, 'rb') as fh:
        code = op.post(f'{capture}/upload', files={'video': (video.name, fh, 'video/mp4')}).status_code
    st = {}
    for _ in range(300):
        st = op.get(f'{capture}/state').json()['session']
        if st['status'] != 'processing':
            break
        time.sleep(1)
    return code, st


def finish(session_id):
    r = op.post(f'{WEB}/operator/sessions/{session_id}/capture/finish', allow_redirects=False)
    return r.headers.get('Location', '')


def wait_outbox(seconds=40):
    for _ in range(seconds):
        if not list(OUTBOX.glob('*.json')):
            return True
        time.sleep(1)
    return False


def new_vision_session():
    """Supervisor schedules a session with the privacy notice (vision mode); the Operator starts it."""
    sup = requests.Session()
    sup.post(f'{AUTH}/login', json={'email': 'supervisor@instrumed.com', 'password': 'DemopwdSPD78!'})
    operator_id = q("SELECT id FROM \"user\" WHERE email='operator@instrumed.com'")[0][0]
    kit_id, proc_id = q('SELECT pk.kit_id, pk.procedure_type_id FROM procedure_kit pk JOIN kit k ON k.id=pk.kit_id '
                        'WHERE k.active AND pk.active ORDER BY pk.is_default DESC LIMIT 1')[0]
    room_id, station_id = q('SELECT r.id, cs.id FROM capture_station cs JOIN operating_room r ON r.id=cs.room_id '
                            'WHERE cs.active AND r.active LIMIT 1')[0]
    lines = q('SELECT family_id, quantity FROM kit_item WHERE kit_id=%s', kit_id)
    physician_id = q('SELECT id FROM physician ORDER BY id LIMIT 1')[0][0]
    privacy_id = q('SELECT id FROM privacy_notice_version ORDER BY version DESC LIMIT 1')[0][0]
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
    r = sup.post(f'{WEB}/supervisor/sessions/new', allow_redirects=False, data={
        'action': 'schedule', 'operator_user_id': str(operator_id), 'procedure_type_id': str(proc_id),
        'room_id': str(room_id), 'station_id': str(station_id), 'kit_id': str(kit_id), 'scheduled_at': when,
        'family_id[]': [str(f) for f, _ in lines], 'expected_quantity[]': [str(n) for _, n in lines],
        'physician_id[]': [str(physician_id)], 'surgical_role_code[]': ['surgeon'],
        'confirm_privacy': '1', 'privacy_notice_version_id': str(privacy_id), 'privacy_confirm_checkbox': '1'})
    session_id = r.headers['Location'].rstrip('/').split('/')[-1]
    op.post(f'{WEB}/operator/sessions/{session_id}/begin', data={'instruments_ready': '1'})
    return session_id


sid, sid2 = new_vision_session(), new_vision_session()
print('== 1. Process videos and generate checkpoints (session %s)' % sid)
code, st = run(sid, videos / 'tray_full.mp4')
full_total = len(st.get('boxes') or [])
check('worker real (best.pt) procesó la charola', code == 202 and st['status'] == 'ready' and full_total > 0,
      (st['status'], full_total, st.get('model_version')))
check('conteo inicial (start)', finish(sid).endswith('/capture'))
r = op.post(f'{WEB}/operator/sessions/{sid}/capture/phase', data={'phase_code': 'demo_phase_1'})
check('cambio de fase (state_change)', r.status_code == 200 and r.json().get('ok'), r.status_code)
code, st = run(sid, videos / 'tray_masked.mp4')
masked_total = len(st.get('boxes') or [])
print(f'   charola completa: {full_total} detecciones; con una pieza cubierta: {masked_total}')
if masked_total != full_total:
    check('final distinto rechazado (discrepancy)', finish(sid).endswith('/capture'), masked_total)
code, st = run(sid, videos / 'tray_full.mp4')
check('final coincidente (close)', finish(sid).endswith('/detection-summary'), len(st.get('boxes') or []))
expected = q('SELECT family_id, expected_quantity FROM expected_inventory WHERE session_id=%s', sid)
snapshot_counts = {}
form = {f'validated_{f}': str(n) for f, n in expected}
form.update({f'notes_{f}': 'Verified on the physical tray' for f, _ in expected})
r = op.post(f'{WEB}/operator/sessions/{sid}/vision-validation', data=form, allow_redirects=False)
check('validación humana enviada', r.status_code == 302, r.status_code)
check('cola de checkpoints vaciada', wait_outbox())

print('== 2. Checkpoints of the session')
docs = list(mongo.checkpoint_frame.find({'session_id': sid}).sort('session_checkpoint_seq', 1))
reasons = [d['reason'] for d in docs]
check('checkpoints reales por evento', reasons[:2] == ['start', 'state_change'] and reasons[-1] == 'close', reasons)
check('secuencia ascendente', [d['session_checkpoint_seq'] for d in docs] == list(range(1, len(docs) + 1)))
check('sin imágenes ni video en MongoDB', all('frame_jpeg_b64' not in d and 'boxes' not in d for d in docs))
show('documento checkpoint_frame (close)', {k: v for k, v in docs[-1].items() if k != 'detections'}
     | {'detections[:3]': docs[-1]['detections'][:3], 'detections_count': len(docs[-1]['detections'])})

print('== 3. Telemetry of the session')
for _ in range(20):
    tel = list(mongo.session_telemetry.find({'session_id': sid}).sort('ts', 1))
    if tel:
        break
    time.sleep(1)
check('telemetría medida por el worker', len(tel) >= 3 and all(t['fps_avg'] > 0 and t['latency_ms'] > 0 for t in tel), len(tel))
show('documento session_telemetry', tel[0])

print('== 4. Filters')
since = docs[0]['captured_at'] + timedelta(milliseconds=1)
by_reason = list(mongo.checkpoint_frame.find({'session_id': sid, 'reason': 'close'}))
by_date = list(mongo.checkpoint_frame.find({'session_id': sid, 'captured_at': {'$gte': since}}))
by_phase = list(mongo.checkpoint_frame.find({'phase_code': 'demo_phase_1', 'session_id': sid}))
op_id = q('SELECT operation_id FROM work_session WHERE id=%s', sid)[0][0]
by_operation = list(mongo.checkpoint_frame.find({'operation_id': str(op_id)}))
check('filtro por motivo', [d['reason'] for d in by_reason] == ['close'])
check('filtro por fecha', len(by_date) == len(docs) - 1, len(by_date))
check('filtro por fase', len(by_phase) >= 1, [d['reason'] for d in by_phase])
check('recuperación por operation_id de PostgreSQL', len(by_operation) == len(docs))
last_hour = list(mongo.session_telemetry.find({'session_id': sid, 'ts': {'$gte': datetime.now(timezone.utc) - timedelta(hours=1)},
                                               'latency_ms': {'$gt': 0}}))
check('filtro de telemetría por fecha y métrica', len(last_hour) == len(tel))

print('== 5. Aggregations')
agg_reason = list(mongo.checkpoint_frame.aggregate([
    {'$match': {'session_id': sid}},
    {'$group': {'_id': '$reason', 'checkpoints': {'$sum': 1}, 'detections': {'$sum': {'$size': '$detections'}},
                'with_evidence': {'$sum': {'$cond': [{'$ifNull': ['$media_asset_id', False]}, 1, 0]}}}},
    {'$sort': {'_id': 1}}]))
agg_tel = list(mongo.session_telemetry.aggregate([
    {'$match': {'session_id': sid}},
    {'$group': {'_id': '$session_id', 'samples': {'$sum': 1}, 'fps_avg': {'$avg': '$fps_avg'},
                'latency_ms_avg': {'$avg': '$latency_ms'}, 'latency_ms_max': {'$max': '$latency_ms'},
                'drops': {'$sum': '$drops'}}}]))
agg_family = list(mongo.checkpoint_frame.aggregate([
    {'$match': {'session_id': sid, 'reason': 'close'}}, {'$unwind': '$detections'},
    {'$group': {'_id': '$detections.family_code', 'n': {'$sum': 1}, 'confidence': {'$avg': '$detections.confidence'}}},
    {'$sort': {'n': -1}}]))
show('agregación checkpoints por motivo', agg_reason)
show('agregación de telemetría', agg_tel)
show('agregación de detecciones por familia (close)', agg_family)
check('agregaciones con datos reales', agg_reason and agg_tel and agg_tel[0]['samples'] == len(tel) and agg_family)

print('== 6. Evidence and controlled updates')
with_media = [d for d in docs if d.get('media_asset_id')]
check('evidencias almacenadas en EvidenceService', len(with_media) == len(docs), (len(with_media), len(docs)))
for d in with_media:
    row = q('SELECT session_id, gcs_uri, retention_until, sha256 FROM media_asset WHERE id=%s', d['media_asset_id'])
    ok = row and str(row[0][0]) == sid and row[0][1] == d['gcs_uri'] and abs((row[0][2] - d['retention_until']).total_seconds()) < 1
    check(f"media_asset {d['media_asset_id'][:8]} existe en PostgreSQL con el mismo URI y retención", ok)
evidence = requests.get(f"{EVIDENCE}/api/v1/evidence/{with_media[-1]['media_asset_id']}",
                        headers={'Authorization': f'Bearer {token}'}, timeout=5).json()
check('EvidenceService entrega la evidencia (URL firmada)', evidence.get('kind') == 'jpeg' and evidence.get('signed_url'))
image = requests.get(evidence['signed_url'], timeout=5)
check('el objeto JPEG existe en Garage', image.status_code == 200 and image.content[:3] == b'\xff\xd8\xff', len(image.content))
before = mongo.checkpoint_frame.find_one({'_id': docs[0]['_id']})
sys.path.insert(0, os.environ.get('WEB_DIR') or str(Path(__file__).resolve().parents[1] / 'apps' / 'BackendWebFlask'))
os.environ.setdefault('PEF_OUTBOX_DIR', str(OUTBOX))
from services import mongo_checkpoints as mc  # noqa: E402
updated = mc.extend_retention(sid, db=mongo)
after = mongo.checkpoint_frame.find_one({'_id': docs[0]['_id']})
check('actualización permitida: retention_until +180 días', updated == len(docs)
      and after['retention_until'] - after['captured_at'] == timedelta(days=180), updated)
check('contenido histórico intacto', {k: after[k] for k in ('detections', 'reason', 'captured_at', 'session_checkpoint_seq')}
      == {k: before[k] for k in ('detections', 'reason', 'captured_at', 'session_checkpoint_seq')})
check('una segunda vinculación de evidencia no sobrescribe', mc.attach_evidence(str(docs[0]['_id']),
                                                                               {'media_asset_id': '00000000-0000-4000-8000-000000000000'}, db=mongo) is False)

print('== 7. Indexes')
show('índices checkpoint_frame', mongo.checkpoint_frame.index_information())
show('índices session_telemetry', mongo.session_telemetry.index_information())
plan = mongo.checkpoint_frame.find({'session_id': sid}).sort('captured_at', 1).explain()['queryPlanner']['winningPlan']
plan_tel = mongo.session_telemetry.find({'session_id': sid}).sort('ts', 1).explain()['queryPlanner']['winningPlan']
check('consulta de checkpoints usa session_captured_at', 'session_captured_at' in json.dumps(plan, default=str))
check('consulta de telemetría usa session_ts', 'session_ts' in json.dumps(plan_tel, default=str))
info = mongo.command('listCollections', filter={'name': 'checkpoint_frame'})['cursor']['firstBatch'][0]['options']
check('validador DS06 activo (strict/error)', info.get('validationLevel') == 'strict' and '$jsonSchema' in info.get('validator', {}))

print('== 8. Identifiers vs PostgreSQL')
mongo_sessions = set(mongo.checkpoint_frame.distinct('session_id')) | set(mongo.session_telemetry.distinct('session_id'))
pg_sessions = {str(r[0]) for r in q('SELECT id FROM work_session WHERE id::text = ANY(%s)', list(mongo_sessions))}
check('todo session_id de MongoDB existe en work_session', mongo_sessions and mongo_sessions == pg_sessions,
      f'{len(mongo_sessions)} sesiones')
final_pg = q("SELECT ce.detected_quantity FROM count_event ce JOIN cat_event_type t ON t.id=ce.event_type_id "
             "WHERE ce.session_id=%s AND t.code='auto_count' AND ce.payload->>'trigger'='final' ORDER BY ce.occurred_at DESC LIMIT 1", sid)[0][0]
check('detecciones del checkpoint close = conteo final en PostgreSQL', len(docs[-1]['detections']) == final_pg, final_pg)
sup = requests.Session()
sup.post(f'{AUTH}/login', json={'email': 'supervisor@instrumed.com', 'password': 'DemopwdSPD78!'})
html = sup.get(f'{WEB}/supervisor/sessions/{sid}').text
check('panel del Supervisor muestra los checkpoints (lectura server-side)', 'data-checkpoints' in html and 'data-telemetry-summary' in html)

print('== 9. MongoDB stopped: the Operator keeps counting (session %s)' % sid2)
subprocess.run(COMPOSE + ['stop', 'mongo'], check=True, capture_output=True)
started = time.monotonic()
code, st = run(sid2, videos / 'tray_full.mp4')
check('procesamiento con MongoDB detenido', st['status'] == 'ready', st['status'])
check('conteo inicial', finish(sid2).endswith('/capture'))
code, st = run(sid2, videos / 'tray_full.mp4')
check('conteo final', finish(sid2).endswith('/detection-summary'))
expected2 = q('SELECT family_id, expected_quantity FROM expected_inventory WHERE session_id=%s', sid2)
form2 = {f'validated_{f}': str(n) for f, n in expected2}
form2.update({f'notes_{f}': 'Verified on the physical tray' for f, _ in expected2})
r = op.post(f'{WEB}/operator/sessions/{sid2}/vision-validation', data=form2, allow_redirects=False)
status2 = q('SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id=w.status_id WHERE w.id=%s', sid2)[0][0]
check('Operador termina su conteo (PostgreSQL)', r.status_code == 302 and status2 in ('awaiting_spd_review', 'correction_required'),
      (status2, f'{time.monotonic() - started:.1f}s'))
time.sleep(3)
pending_cp = len(list(OUTBOX.glob('*.json')))
worker_health = requests.get(f'{WORKER}/health', timeout=5).json()['telemetry']
check('checkpoints pendientes en la cola local', pending_cp >= 2, pending_cp)
check('telemetría pendiente en el spool del worker y fallo registrado',
      worker_health['pending'] > 0 and worker_health['failures'] > 0, worker_health)

print('== 10. MongoDB back: pending writes are delivered once')
subprocess.run(COMPOSE + ['start', 'mongo'], check=True, capture_output=True)
for _ in range(60):
    try:
        mongo.client.admin.command('ping')
        break
    except Exception:  # noqa: BLE001
        time.sleep(1)
check('cola de checkpoints entregada', wait_outbox(90), len(list(OUTBOX.glob('*.json'))))
for _ in range(90):
    if requests.get(f'{WORKER}/health', timeout=5).json()['telemetry']['pending'] == 0:
        break
    time.sleep(1)
docs2 = list(mongo.checkpoint_frame.find({'session_id': sid2}).sort('session_checkpoint_seq', 1))
tel2 = mongo.session_telemetry.count_documents({'session_id': sid2})
check('checkpoints de la sesión registrados tras la recuperación', [d['reason'] for d in docs2] == ['start', 'close'],
      [d['reason'] for d in docs2])
check('telemetría registrada tras la recuperación', tel2 > 0, tel2)
check('sin duplicados', len({d['session_checkpoint_seq'] for d in docs2}) == len(docs2))
print(f'\nRESUMEN MONGO: {sum(results)}/{len(results)} PASS')
sys.exit(0 if all(results) else 1)
