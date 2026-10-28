"""Supervisor reports end to end (live services; see GUIA_REPORTES.md).

Needs: BackendWebFlask (:5000) with SESSION_SERVICE_URL and CATALOG_SERVICE_URL, AuthService (:5001),
VisionWorker (:5002, weights/best.pt) and compose postgres, redis, catalog and session.

1. Every figure of GET /api/v1/sessions/reports/* is recomputed here in Python from raw PostgreSQL rows
   (an implementation independent of the service's SQL) for the real sessions of the institution.
2. A new session goes through the real flows (Supervisor schedules, Operator counts with YOLO and
   validates with one shortage, Supervisor resolves and closes) and the reports move by exactly the
   expected amounts.
3. Isolation (a supervisor of another institution, created through Administration), permissions,
   invalid parameters, JSON/XML (XSD) and the RF screens (charts fed with the same numbers).

usage: python scripts/e2e_reports.py <videos_dir>
env:   DATABASE_URL, SESSION_SERVICE_URL (as seen from this machine), COMPOSE_ENV_FILE, COMPOSE_PROJECT (step 18 stops
       and starts the compose `session` service)
"""
import json
import os
import re
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import requests
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
WEB, AUTH = os.environ.get('PEF_WEB_URL', 'http://127.0.0.1:5000'), os.environ.get('PEF_AUTH_URL', 'http://127.0.0.1:5001')
API = os.environ['SESSION_SERVICE_URL'].rstrip('/') + '/api/v1/sessions'
DSN = os.environ['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://')
VIDEOS = Path(sys.argv[1])
XSD = etree.XMLSchema(etree.parse(str(ROOT / 'apps/SessionService/schemas/session-reports.xsd')))
INSTITUTION = '11111111-1111-1111-1111-111111111111'
KIT = '51515151-0000-4000-8000-000000000001'
ROOM, STATION = '12121212-0000-4000-8000-000000000001', '13131313-0000-4000-8000-000000000001'
PHYSICIAN, PATIENT = '32323232-0000-4000-8000-000000000001', '31313131-0000-4000-8000-000000000001'
TAG = uuid.uuid4().hex[:6].upper()
STATUSES = ('scheduled', 'in_progress', 'awaiting_spd_review', 'correction_required', 'closed', 'aborted')
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


def report(session, path, **params):
    return requests.get(f'{API}/reports/{path}', params=params, headers=bearer(session, **params.pop('headers', {})), timeout=20)


def all_pages(session, path, key, **params):
    items, page = [], 1
    while True:
        body = report(session, path, per_page=100, page=page, **params).json()
        items += body[key]
        if page >= body['total_pages']:
            return items
        page += 1


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


# --- independent recomputation from raw rows --------------------------------------------------------


def iso(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def expected_report(window_from, window_to, *, kit=None, operator=None, state=None, tz='UTC'):
    sessions = [dict(zip(('id', 'user_id', 'kit_id', 'status', 'started', 'ended', 'scheduled'), r)) for r in q("""
        SELECT w.id::text, w.user_id::text, w.kit_id::text, s.code, w.started_at, w.ended_at, o.scheduled_at
        FROM work_session w JOIN operation o ON o.id = w.operation_id JOIN cat_session_status s ON s.id = w.status_id
        WHERE o.institution_id = %s AND o.scheduled_at >= %s AND o.scheduled_at < %s""", INSTITUTION, window_from, window_to)]
    sessions = [s for s in sessions if (kit is None or s['kit_id'] == kit) and (operator is None or s['user_id'] == operator)
                and (state is None or s['status'] == state)]
    ids = {s['id'] for s in sessions}
    disc = [dict(zip(('session', 'resolved', 'resolved_at', 'reason', 'family', 'origin_at'), r)) for r in q("""
        SELECT d.session_id::text, d.resolved, d.resolved_at, r.code, d.family_id::text, ce.occurred_at
        FROM discrepancy d LEFT JOIN cat_discrepancy_reason r ON r.id = d.reason_id
        LEFT JOIN count_event ce ON ce.id = d.origin_event_id""") if r[0] in ids]
    events = [dict(zip(('id', 'session', 'type', 'family', 'detected', 'payload', 'at'), r)) for r in q("""
        SELECT ce.id::text, ce.session_id::text, t.code, ce.family_id::text, ce.detected_quantity, ce.payload, ce.occurred_at
        FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id
        WHERE t.code IN ('manual_count', 'auto_count')""") if r[1] in ids]
    expected_rows = [r for r in q('SELECT session_id::text, expected_quantity FROM expected_inventory') if r[0] in ids]
    by_session = {s['id']: s for s in sessions}
    has_disc = {d['session'] for d in disc}
    reported = [s for s in sessions if s['ended']]
    timed = [s for s in sessions if s['started'] and s['ended'] and s['ended'] >= s['started']]

    # AI-human pairs: the corrected rule (human validation vs the YOLO suggestion it validated).
    lots = defaultdict(list)
    for e in events:
        trig = e['payload'].get('trigger')
        if e['type'] == 'auto_count' and e['family'] is None and (trig == 'final' or (trig is None and e['payload'].get('reason') == 'detection_close')):
            lots[e['session']].append(e)
    pairs = []
    for e in events:
        if e['type'] != 'manual_count' or e['family'] is None or e['payload'].get('ai') is not True:
            continue
        human = e['detected'] if e['detected'] is not None else (
            int(e['payload']['reported_quantity']) if e['payload'].get('reported_quantity') is not None else None)
        ai = e['payload'].get('ai_suggested_quantity')
        if ai is None:
            before = [lot for lot in lots[e['session']] if lot['at'] <= e['at']]
            if before:
                lot = max(before, key=lambda lot: lot['at'])['payload']
                families = (lot.get('board') or {}).get('families') or lot.get('counts') or []
                ai = next((int(f['detected_quantity']) for f in families if f.get('family_id') == e['family']), 0)
        if human is not None and ai is not None:
            pairs.append((e['session'], int(human) == int(ai)))

    last_counts = {}
    for e in sorted((e for e in events if e['type'] == 'manual_count' and e['family']), key=lambda e: (e['at'], e['id'])):
        payload_qty = e['payload'].get('reported_quantity')
        qty = e['detected'] if e['detected'] is not None else (
            int(payload_qty) if isinstance(payload_qty, (int, str)) and str(payload_qty).isdigit() else None)
        last_counts[(e['session'], e['family'])] = qty
    reported_total = defaultdict(int)
    counted_sessions = set()
    for (sid, _), qty in last_counts.items():
        counted_sessions.add(sid)
        if qty is not None:
            reported_total[sid] += qty
    expected_total = defaultdict(int)
    for sid, n in expected_rows:
        expected_total[sid] += n

    def resolution(d):
        start = d['origin_at'] or by_session[d['session']]['started']
        return (d['resolved_at'] - start).total_seconds() / 60 if d['resolved'] and d['resolved_at'] and start else None
    resolutions = [m for m in (resolution(d) for d in disc) if m is not None]
    out = {
        'total': len(sessions), 'by_status': {c: sum(1 for s in sessions if s['status'] == c) for c in STATUSES},
        'reported': len(reported), 'timed': len(timed),
        'avg_duration': round(sum((s['ended'] - s['started']).total_seconds() / 60 for s in timed) / len(timed), 1) if timed else None,
        'reported_with_disc': sum(1 for s in reported if s['id'] in has_disc),
        'disc_total': len(disc), 'disc_open': sum(1 for d in disc if not d['resolved']),
        'avg_resolution': round(sum(resolutions) / len(resolutions), 1) if resolutions else None,
        'by_reason': sorted((d['reason'], sum(1 for x in disc if x['reason'] == d['reason'])) for d in {d['reason']: d for d in disc}.values()),
        'pairs': len(pairs), 'agreed': sum(1 for _, ok in pairs if ok),
        'kits': {}, 'operators': {},
    }
    for s in sessions:
        k = out['kits'].setdefault(s['kit_id'], {'sessions': 0, 'with_disc': 0, 'discrepancies': 0, 'expected': 0,
                                                 'expected_counted': 0, 'reported': 0, 'any_counted': False})
        k['sessions'] += 1
        k['with_disc'] += s['id'] in has_disc
        k['discrepancies'] += sum(1 for d in disc if d['session'] == s['id'])
        k['expected'] += expected_total[s['id']]
        if s['id'] in counted_sessions:
            k['any_counted'] = True
            k['expected_counted'] += expected_total[s['id']]
            k['reported'] += reported_total[s['id']]
        o = out['operators'].setdefault(s['user_id'], {'sessions': 0, 'completed': 0, 'families': 0, 'pairs': 0,
                                                       'agreed': 0, 'discrepancies': 0})
        o['sessions'] += 1
        o['completed'] += s['ended'] is not None
        o['families'] += sum(1 for (sid, _) in last_counts if sid == s['id'])
        o['pairs'] += sum(1 for sid, _ in pairs if sid == s['id'])
        o['agreed'] += sum(1 for sid, ok in pairs if sid == s['id'] and ok)
        o['discrepancies'] += sum(1 for d in disc if d['session'] == s['id'])
    return out


def compare(name, api_summary, kits, operators, exp):
    s, d, a = api_summary['sessions'], api_summary['discrepancies'], api_summary['ai_agreement']
    got = {
        'total': s['total'], 'by_status': {i['code']: i['count'] for i in s['by_status']}, 'reported': s['reported'],
        'timed': s['timed'], 'avg_duration': s['avg_duration_minutes'], 'reported_with_disc': s['reported_with_discrepancies'],
        'disc_total': d['total'], 'disc_open': d['open'], 'avg_resolution': d['avg_resolution_minutes'],
        'by_reason': sorted((r['code'], r['total']) for r in d['by_reason']), 'pairs': a['pairs'], 'agreed': a['agreed'],
    }
    mismatch = {k: (got[k], exp[k]) for k in got if not (
        got[k] == exp[k] or (isinstance(got[k], float) and exp[k] is not None and abs(got[k] - exp[k]) <= 0.1))}
    check(f'{name}: resumen = recálculo independiente sobre PostgreSQL', not mismatch, mismatch or
          f"{got['total']} sesiones, {got['disc_total']} discrepancias, {got['pairs']} pares IA")
    kit_rows = {k['kit_id']: k for k in kits}
    kit_mismatch = {kid: (row, exp['kits'][kid]) for kid, row in kit_rows.items() if kid in exp['kits'] and (
        row['sessions'], row['sessions_with_discrepancies'], row['discrepancies'], row['expected_quantity'] or 0,
        row['expected_quantity_counted'] or 0, row['reported_quantity'] or 0) != (
        exp['kits'][kid]['sessions'], exp['kits'][kid]['with_disc'], exp['kits'][kid]['discrepancies'], exp['kits'][kid]['expected'],
        exp['kits'][kid]['expected_counted'], exp['kits'][kid]['reported'])}
    check(f'{name}: actividad por kit = recálculo', set(kit_rows) == set(exp['kits']) and not kit_mismatch, kit_mismatch or len(kit_rows))
    op_rows = {o['operator_id']: o for o in operators}
    op_mismatch = {oid: row for oid, row in op_rows.items() if (
        row['sessions'], row['counts_completed'], row['family_counts'], row['ai_pairs'], row['ai_agreed'], row['discrepancies'],
        row['human_corrections']) != (
        exp['operators'][oid]['sessions'], exp['operators'][oid]['completed'], exp['operators'][oid]['families'],
        exp['operators'][oid]['pairs'], exp['operators'][oid]['agreed'], exp['operators'][oid]['discrepancies'],
        exp['operators'][oid]['pairs'] - exp['operators'][oid]['agreed'])}
    check(f'{name}: actividad por Operador = recálculo', set(op_rows) == set(exp['operators']) and not op_mismatch,
          op_mismatch or len(op_rows))


# --- real flow helpers ---------------------------------------------------------------------------


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


def schedule(spd, operator_id):
    kit_lines = q('SELECT family_id, quantity FROM kit_item WHERE kit_id = %s ORDER BY family_id', KIT)
    notice = str(q('SELECT id FROM privacy_notice_version WHERE active ORDER BY effective_at DESC LIMIT 1')[0][0])
    data = {'operator_user_id': operator_id, 'procedure_type_id': str(q('SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1')[0][0]),
            'room_id': ROOM, 'station_id': STATION, 'kit_id': KIT, 'patient_id': PATIENT,
            'scheduled_at': datetime.now().strftime('%Y-%m-%dT%H:%M'),
            'family_id[]': [str(f) for f, _ in kit_lines], 'expected_quantity[]': [str(n) for _, n in kit_lines],
            'physician_id[]': [PHYSICIAN], 'surgical_role_code[]': ['surgeon'],
            'confirm_privacy': '1', 'privacy_notice_version_id': notice, 'privacy_confirm_checkbox': '1'}
    response = spd.post(f'{WEB}/supervisor/sessions/new', data=data, allow_redirects=False)
    return response.headers.get('Location', '').rstrip('/').split('/')[-1]


op = login('operator@instrumed.com', 'DemopwdOP78!')
spd = login('supervisor@instrumed.com', 'DemopwdSPD78!')
admin = login('admin@instrumed.com', 'DemopwdADM78!')
OP = str(q("SELECT id FROM \"user\" WHERE email = 'operator@instrumed.com'")[0][0])
WINDOW = {'from': '2026-01-01', 'to': (datetime.now(timezone.utc) + timedelta(days=60)).date().isoformat(), 'tz': 'UTC'}

# ====================================================================================================
print('== 1. Report of the real sessions of PostgreSQL')
summary = report(spd, 'summary', **WINDOW).json()
win_from, win_to = iso(summary['filters']['from']), iso(summary['filters']['to'])
kits = all_pages(spd, 'kits', 'kits', **WINDOW)
operators = all_pages(spd, 'operators', 'operators', **WINDOW)
check('hay sesiones reales en el periodo', summary['sessions']['total'] > 0, summary['sessions']['total'])
compare('todo el periodo', summary, kits, operators, expected_report(win_from, win_to))

print('== 2-5. Filters by period, kit, operator and status')
last7 = {'from': (datetime.now(timezone.utc) - timedelta(days=6)).date().isoformat(), 'to': datetime.now(timezone.utc).date().isoformat()}
s7 = report(spd, 'summary', **last7).json()
compare('últimos 7 días', s7, all_pages(spd, 'kits', 'kits', **last7), all_pages(spd, 'operators', 'operators', **last7),
        expected_report(iso(s7['filters']['from']), iso(s7['filters']['to'])))
for label, extra, kw in (('kit', {'kit_id': KIT}, {'kit': KIT}), ('Operador', {'operator_id': OP}, {'operator': OP}),
                         ('estado closed', {'status': 'closed'}, {'state': 'closed'})):
    body = report(spd, 'summary', **WINDOW, **extra).json()
    compare(f'filtro por {label}', body, all_pages(spd, 'kits', 'kits', **WINDOW, **extra),
            all_pages(spd, 'operators', 'operators', **WINDOW, **extra), expected_report(win_from, win_to, **kw))
check('el filtro por estado devuelve solo ese estado',
      all(i['count'] == 0 for i in report(spd, 'summary', **WINDOW, status='closed').json()['sessions']['by_status']
          if i['code'] != 'closed'))

print('== 6-8. A new session through the real flows moves the reports by exactly its facts')
before = report(spd, 'summary', **WINDOW).json()
before_op = next((o for o in all_pages(spd, 'operators', 'operators', **WINDOW) if o['operator_id'] == OP), None)
sid = schedule(spd, OP)
op.post(f'{WEB}/operator/sessions/{sid}/begin', data={'instruments_ready': '1'}, allow_redirects=False)
loc1, loc2 = capture(op, sid, 'tray_full.mp4'), capture(op, sid, 'tray_full.mp4')
lines = {str(f): int(n) for f, n in q('SELECT family_id, expected_quantity FROM expected_inventory WHERE session_id = %s', sid)}
short = sorted(lines)[0]
form = {f'validated_{f}': str(n - 1 if f == short else n) for f, n in lines.items()}
form.update({f'notes_{f}': 'Verified on the physical tray' for f in lines})
form[f'reason_{short}'] = 'shortage'
form['extra_instruments'] = '0'
r = op.post(f'{WEB}/operator/sessions/{sid}/vision-validation', data=form, allow_redirects=False)
check('Operador: conteo YOLO inicial y final y validación humana con un faltante', loc2.endswith('/detection-summary')
      and r.status_code == 302 and status(sid) == 'correction_required', (loc1, loc2, status(sid)))
facts = q("""SELECT count(*), count(*) FILTER (WHERE ce.detected_quantity = (ce.payload ->> 'ai_suggested_quantity')::int)
             FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id
             WHERE ce.session_id = %s AND t.code = 'manual_count' AND (ce.payload ->> 'ai') = 'true'""", sid)[0]
after = report(spd, 'summary', **WINDOW).json()
delta = {'total': after['sessions']['total'] - before['sessions']['total'],
         'correction_required': next(i['count'] for i in after['sessions']['by_status'] if i['code'] == 'correction_required')
         - next(i['count'] for i in before['sessions']['by_status'] if i['code'] == 'correction_required'),
         'discrepancies': after['discrepancies']['total'] - before['discrepancies']['total'],
         'open': after['discrepancies']['open'] - before['discrepancies']['open'],
         'pairs': after['ai_agreement']['pairs'] - before['ai_agreement']['pairs'],
         'corrections': after['ai_agreement']['corrections'] - before['ai_agreement']['corrections']}
disc_rows = q('SELECT count(*) FROM discrepancy WHERE session_id = %s', sid)[0][0]
check('reporte: +1 sesión, +1 correction_required, +discrepancias de la sesión', delta['total'] == 1 and
      delta['correction_required'] == 1 and delta['discrepancies'] == disc_rows == delta['open'] and disc_rows >= 1, delta)
check('acuerdo IA-humano: +1 par por familia validada, correcciones = familias donde el humano cambió la sugerencia YOLO',
      delta['pairs'] == facts[0] == len(lines) and delta['corrections'] == facts[0] - facts[1] and delta['corrections'] >= 1,
      (delta, facts))
after_op = next(o for o in all_pages(spd, 'operators', 'operators', **WINDOW) if o['operator_id'] == OP)
check('fila del Operador: +1 sesión, +1 conteo completado, +familias contadas',
      after_op['sessions'] - (before_op or {'sessions': 0})['sessions'] == 1 and
      after_op['counts_completed'] - (before_op or {'counts_completed': 0})['counts_completed'] == 1 and
      after_op['family_counts'] - (before_op or {'family_counts': 0})['family_counts'] == len(lines), after_op)
shortage_ids = [str(d) for (d,) in q('SELECT id FROM discrepancy WHERE session_id = %s AND NOT resolved', sid)
                if is_physical_shortage(d)]
note = spd.post(f'{WEB}/supervisor/discrepancies/{sid}/review', allow_redirects=False,
                data={'discrepancy_id': shortage_ids[0], 'notes': 'Counted again on the tray', 'resolution_outcome': 'notes_only'})
check('el faltante físico no se resuelve con una nota', note.status_code == 400 and
      'cannot be resolved with notes only' in note.text, note.status_code)
for (disc,) in q('SELECT id FROM discrepancy WHERE session_id = %s AND NOT resolved', sid):
    spd.post(f'{WEB}/supervisor/discrepancies/{sid}/review', allow_redirects=False,
             data={'discrepancy_id': str(disc), 'notes': 'Piece not found after search', 'resolution_outcome': outcome_for(disc)})
r = spd.post(f'{WEB}/supervisor/sessions/{sid}/close', allow_redirects=False,
             data={'material_recovered': '1', 'contingency_reviewed': '1', 'unreconciled_shortage_acknowledged': '1'})
payload = q("SELECT e.payload FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
            "WHERE e.session_id = %s AND t.code = 'session_close'", sid)[0][0]
check('Supervisor resuelve (pieza perdida) y cierra como faltante no conciliado, no como cierre seguro',
      status(sid) == 'closed' and payload['safe_close_confirmed'] is False
      and payload['unreconciled_physical_shortages'] == shortage_ids, (status(sid), payload.get('safe_close_confirmed')))
one = report(spd, 'summary', **WINDOW, operator_id=OP, status='closed').json()
compare('tras el cierre (Operador + closed)', one, all_pages(spd, 'kits', 'kits', **WINDOW, operator_id=OP, status='closed'),
        all_pages(spd, 'operators', 'operators', **WINDOW, operator_id=OP, status='closed'),
        expected_report(win_from, win_to, operator=OP, state='closed'))
duration = q("SELECT EXTRACT(EPOCH FROM (ended_at - started_at)) / 60 FROM work_session WHERE id = %s", sid)[0][0]
check('duración de la sesión nueva registrada (inicio -> reporte del Operador)', duration is not None and float(duration) > 0,
      f'{float(duration):.2f} min')

print('== 15. Consistency with the session detail')
detail = requests.get(f'{API}/{sid}', params={'expand': 'detail'}, headers=bearer(spd), timeout=10).json()
# Window around the session's real scheduled_at (not "today in UTC": the session is scheduled in local
# time, so after 18:00 in Mexico its UTC date is not the local date and a day filter would miss it).
scheduled = q('SELECT o.scheduled_at FROM work_session w JOIN operation o ON o.id = w.operation_id WHERE w.id = %s', sid)[0][0]
around = {'from': (scheduled - timedelta(hours=1)).isoformat(), 'to': (scheduled + timedelta(hours=1)).isoformat()}
just_this = report(spd, 'summary', **around, kit_id=KIT, operator_id=OP, status='closed').json()
check('detalle de sesión (SessionService) coherente con el reporte: cerrada y sin discrepancias abiertas',
      detail['status']['code'] == 'closed' and just_this['sessions']['closed'] >= 1 and
      all(dd.get('resolved') for dd in detail.get('discrepancies', [])), detail['status'])

print('== 9. Report without information')
empty = report(spd, 'summary', **{'from': '2020-01-01', 'to': '2020-01-31'}).json()
check('sin información: totales 0 y métricas null (no 0)', empty['sessions']['total'] == 0 and
      empty['sessions']['avg_duration_minutes'] is None and empty['sessions']['with_discrepancies_pct'] is None and
      empty['discrepancies']['avg_resolution_minutes'] is None and empty['ai_agreement']['agreement_pct'] is None
      and empty['activity'] == [])
empty_page = spd.get(f'{WEB}/supervisor/reports?date_range=today&session_status=aborted')
check('pantalla RF: estado vacío claro', empty_page.status_code == 200 and 'No sessions match these filters' in empty_page.text)

print('== 10. Isolation between institutions')
other_inst, other_role = str(uuid.uuid4()), str(uuid.uuid4())
q('INSERT INTO institution (id, name, active) VALUES (%s, %s, TRUE)', other_inst, f'Hospital Reportes {TAG}')
q("INSERT INTO role (id, code, description, institution_id) VALUES (%s, 'spd_supervisor', 'SPD supervisor', %s)", other_role, other_inst)
pwd = f'Reportes-{TAG}-1'
admin.post(f'{WEB}/admin/users/new', allow_redirects=False, data={
    'name': f'Supervisor Reportes {TAG}', 'email': f'spd.rep.{TAG}@instrumed.com', 'institution': other_inst,
    'roles[]': ['spd_supervisor'], 'status': 'active', 'password': pwd, 'confirm_password': pwd})
foreign = login(f'spd.rep.{TAG}@instrumed.com', pwd)
mine = report(foreign, 'summary', **WINDOW).json()
check('Supervisor de otra institución: 0 sesiones aunque la institución 1 tenga muchas',
      mine['sessions']['total'] == 0 and after['sessions']['total'] > 0, (mine['sessions']['total'], after['sessions']['total']))
r = report(foreign, 'summary', **WINDOW, kit_id=KIT)
check('kit de otra institución -> 400 Unknown kit (no revela datos)', r.status_code == 400 and r.json()['details']['field'] == 'kit_id')
r = report(foreign, 'operators', **WINDOW, operator_id=OP)
check('Operador de otra institución -> 400', r.status_code == 400 and r.json()['details']['field'] == 'operator_id')
check('sus opciones no listan kits ni operadores ajenos', report(foreign, 'options').json() == {'kits': [], 'operators': [],
                                                                                              'statuses': list(STATUSES)})
foreign_page = foreign.get(f'{WEB}/supervisor/reports?date_range=90d').text
check('su pantalla RF tampoco muestra datos de la institución 1', 'data-kit=' not in foreign_page
      and 'data-operator=' not in foreign_page and 'No sessions match these filters' in foreign_page)

print('== 11. Users without permission')
for who, session in (('Operador', op), ('Administrador', admin)):
    codes = [report(session, p).status_code for p in ('summary', 'kits', 'operators', 'options')]
    check(f'{who}: 403 en los 4 endpoints', set(codes) == {403}, codes)
check('Operador: pantallas RF de reportes e indicadores -> 403', op.get(f'{WEB}/supervisor/reports', allow_redirects=False).status_code == 403
      and op.get(f'{WEB}/supervisor/indicators', allow_redirects=False).status_code == 403)
denied = q("SELECT count(*) FROM access_audit WHERE actor_user_id = %s AND outcome = 'denied' "
           "AND occurred_at > now() - interval '2 minutes'", OP)[0][0]
check('intentos denegados auditados', denied >= 4, denied)
check('sin token -> 401', requests.get(f'{API}/reports/summary', timeout=10).status_code == 401)

print('== 12. Invalid parameters')
for params, field in (({'from': 'ayer'}, 'from'), ({'from': '2026-09-10', 'to': '2026-09-01'}, 'to'),
                      ({'from': '2024-01-01', 'to': '2026-01-01'}, 'from'), ({'kit_id': 'abc'}, 'kit_id'),
                      ({'status': 'done'}, 'status'), ({'tz': 'Mars/Base'}, 'tz'), ({'per_page': 500}, 'per_page')):
    r = report(spd, 'kits', **params)
    check(f'{field} inválido -> 400', r.status_code == 400 and r.json()['details']['field'] == field)
check('parámetro desconocido -> 400', report(spd, 'summary', institution_id=INSTITUTION).json()['details']['unknown_parameters']
      == ['institution_id'])

print('== 13. JSON and XML')
for path, root in (('summary', 'report_summary'), ('kits', 'kit_report'), ('operators', 'operator_report'), ('options', 'report_options')):
    params = {} if path == 'options' else WINDOW
    response = report(spd, path, headers={'Accept': 'application/xml'}, **params)
    document = etree.fromstring(response.content)
    check(f'XML /reports/{path} válido contra session-reports.xsd', response.status_code == 200 and document.tag == root
          and XSD.validate(document), XSD.error_log)
xml_total = int(etree.fromstring(report(spd, 'summary', headers={'Accept': 'application/xml'}, **WINDOW).content).findtext('sessions/total'))
check('XML = JSON', xml_total == report(spd, 'summary', **WINDOW).json()['sessions']['total'])
check('OpenAPI documenta los reportes', b'/api/v1/sessions/reports/summary' in requests.get(f'{API}/openapi.yaml', timeout=10).content)

print('== 14. Charts fed with real data')
page = spd.get(f'{WEB}/supervisor/reports?date_range=7d')
data = json.loads(re.search(r'<script type="application/json" id="report-charts">(.*?)</script>', page.text, re.S).group(1))
week = report(spd, 'summary', **{'from': (datetime.now().date() - timedelta(days=6)).isoformat(),
                                 'to': datetime.now().date().isoformat(), 'tz': 'America/Mexico_City'}).json()
check('pantalla RF con Highcharts', page.status_code == 200 and 'highcharts@11.4.8' in page.text and 'js/pages/reports.js' in page.text)
check('gráfica por estado = reporte', sum(p['y'] for p in data['status']) == week['sessions']['total'],
      (sum(p['y'] for p in data['status']), week['sessions']['total']))
check('gráfica por día = actividad del reporte', data['activity']['sessions'] == [d['sessions'] for d in week['activity']])
check('gráfica por motivo = reporte', data['reasons']['values'] == [r['total'] for r in week['discrepancies']['by_reason']])
check('tarjetas con valores del reporte', f'data-metric="avg-duration">{week["sessions"]["avg_duration_minutes"]:g} min' in page.text
      if week['sessions']['avg_duration_minutes'] is not None else 'data-metric="avg-duration"><span' in page.text)
indicators = spd.get(f'{WEB}/supervisor/indicators')
first_day = datetime.now().date().replace(day=1)
last_day = (first_day + timedelta(days=32)).replace(day=1) - timedelta(days=1)    # the screen reports the whole month
month = report(spd, 'summary', **{'from': first_day.isoformat(), 'to': last_day.isoformat(), 'tz': 'America/Mexico_City'}).json()
check('Indicadores con el mismo resumen (sesiones del mes)', indicators.status_code == 200 and
      f'<p class="stat-card__value">{month["sessions"]["total"]}</p>' in indicators.text)

print('== 16. Regression of the RF screens of the three roles')
check('Operador', op.get(f'{WEB}/operator/sessions').status_code == 200)
check('Supervisor', all(spd.get(f'{WEB}{p}').status_code == 200 for p in ('/supervisor/sessions', '/supervisor/discrepancies',
                                                                         f'/supervisor/sessions/{sid}', '/supervisor/reports',
                                                                         '/supervisor/indicators')))
check('Administrador', all(admin.get(f'{WEB}{p}').status_code == 200 for p in ('/admin/dashboard', '/admin/users', '/admin/kits')))

print('== 17. Administration dashboard: operational indicators of the administrator institution only')
dash = admin.get(f'{WEB}/admin/dashboard').text


def metric(label):
    found = re.search(r'<th scope="row">' + re.escape(label) + r'</th>\s*<td class="data-table__numeric-column">([^<]*)</td>', dash)
    return found.group(1).strip() if found else None


scoped = q("""SELECT count(*), (SELECT count(*) FROM discrepancy d JOIN work_session w ON w.id = d.session_id
                                  JOIN operation o ON o.id = w.operation_id WHERE o.institution_id = %s),
                     (SELECT count(*) FROM discrepancy d JOIN work_session w ON w.id = d.session_id
                                  JOIN operation o ON o.id = w.operation_id WHERE o.institution_id = %s AND NOT d.resolved)
              FROM work_session w JOIN operation o ON o.id = w.operation_id WHERE o.institution_id = %s""",
           INSTITUTION, INSTITUTION, INSTITUTION)[0]
everything = q('SELECT (SELECT count(*) FROM work_session), (SELECT count(*) FROM discrepancy)')[0]
check('dashboard: sesiones, discrepancias y abiertas = SQL acotado a su institución',
      (metric('Total Sessions'), metric('Total Discrepancies'), metric('Open Discrepancies')) == tuple(str(v) for v in scoped),
      (metric('Total Sessions'), metric('Total Discrepancies'), metric('Open Discrepancies'), scoped, 'global:', everything))

print('== 18. Indicators with SessionService stopped: controlled notice, never another source')
import subprocess  # noqa: E402
COMPOSE = ['docker', 'compose', '--env-file', os.environ.get('COMPOSE_ENV_FILE', '.env')] + (
    ['-p', os.environ['COMPOSE_PROJECT']] if os.environ.get('COMPOSE_PROJECT') else [])
subprocess.run(COMPOSE + ['stop', 'session'], cwd=ROOT, check=True, capture_output=True)
try:
    page = spd.get(f'{WEB}/supervisor/indicators')
    check('Indicadores: 503 con aviso y sin cifras parciales', page.status_code == 503 and 'data-report-unavailable' in page.text
          and 'stat-card__value' not in page.text, page.status_code)
    page = spd.get(f'{WEB}/supervisor/reports')
    check('Reportes: 503 con aviso', page.status_code == 503 and 'data-report-unavailable' in page.text, page.status_code)
finally:
    subprocess.run(COMPOSE + ['start', 'session'], cwd=ROOT, check=True, capture_output=True)
for _ in range(60):
    try:
        if requests.get(os.environ['SESSION_SERVICE_URL'].rstrip('/') + '/health/ready', timeout=2).status_code == 200:
            break
    except requests.RequestException:
        pass
    time.sleep(1)
check('SessionService de nuevo: Indicadores con cifras reales', spd.get(f'{WEB}/supervisor/indicators').status_code == 200)

print(f'\n{sum(results)}/{len(results)} checks passed')
sys.exit(0 if all(results) else 1)
