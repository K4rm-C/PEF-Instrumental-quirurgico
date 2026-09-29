"""Canonical English data: 002/004 seeds are English and 005_canonical_english_data.sql converts
databases seeded with the former Spanish values (idempotent, conservative, keyed by code/UUID).

The migration file itself is executed against a throwaway SQLite database built from the models.
Only two mechanical adaptations are applied for SQLite: `ON COMMIT DROP` is removed from the temp
table and UUID literals are written in SQLite's storage format (32 hex chars, no dashes).
"""
import re
import sqlite3
import uuid

from _common import APP, check, finish, setup_app

from pathlib import Path

ROOT = Path(APP).parents[1]
MIGRATION = (ROOT / 'data' / 'migrations' / '005_canonical_english_data.sql').read_text(encoding='utf-8')
CATALOGS = (ROOT / 'data' / 'seeds' / '002_seed_catalogs.sql').read_text(encoding='utf-8')
E2E = (ROOT / 'data' / 'seeds' / '004_seed_e2e.sql').read_text(encoding='utf-8')


def strip_comments(sql):
    return re.sub(r'--[^\n]*', '', sql)


# ------------------------------------------------------------------ static consistency
rows = re.findall(r"\(\s*'([\w.]+)',\s*'([\w]+)',\s*'((?:[^']|'')*)',\s*'((?:[^']|'')*)'\s*\)", strip_comments(MIGRATION))
catalog_map = {(t, c): (old, new) for t, c, old, new in rows if t.startswith('cat_')}
family_map = [(t, c, old, new) for t, c, old, new in rows if t.startswith('family.')]
check(len(catalog_map) == 78 and len(family_map) == 22, f'migration mapping rows ({len(catalog_map)} catalog, {len(family_map)} family)')

seed_pairs = {}
for block in re.findall(r'INSERT INTO (cat_\w+) \([^)]*\) VALUES(.*?)ON CONFLICT', strip_comments(CATALOGS), re.S):
    table, values = block
    for code, name in re.findall(r"\(\s*'(\w+)',\s*'([^']*)'", values):
        seed_pairs[(table, code)] = name
check(set(seed_pairs) == set(catalog_map), f'migration covers every 002 catalog row ({len(seed_pairs)})')
check(all(catalog_map[key][1] == name for key, name in seed_pairs.items()), '002 names == migration target values')

spanish = {old for old, _ in catalog_map.values()} | {old for _, _, old, _ in family_map} | {
    'Quirofano E2E 1', 'Estacion cenital E2E-OR-1', 'Administrador Tecnico (E2E)', 'Operador CDE (E2E)',
    'Supervisor de Calidad (E2E)', 'Kit Cirugia General E2E', 'Abierta (E2E)', 'Paciente E2E 001', 'Dra. E2E Cirujana',
    'https://pef.udem/conteo-cenital/aviso-privacidad/v1.0'}
literals = set(re.findall(r"'([^']*)'", strip_comments(CATALOGS) + strip_comments(E2E)))
check(not (literals & spanish), f'no former Spanish value left in 002/004 literals {sorted(literals & spanish)[:5]}')
check(not re.search(r'[áéíóúñÁÉÍÓÚÑ]', strip_comments(CATALOGS) + strip_comments(E2E)), 'no accented Spanish characters in 002/004 data')
for new in ('Kelly Forceps', 'Mosquito Forceps', 'Metzenbaum Scissors', 'Mayo-Hegar Needle Holder', 'Farabeuf Retractor',
            'General Surgery Kit E2E', 'Overhead Capture Station E2E-OR-1', 'Dr. E2E Surgeon', 'E2E Operating Room 1',
            'IT Administrator', 'Operator CDE', 'Supervisor CDE / Quality', 'Open (E2E)', 'E2E Patient 001'):
    check(f"'{new}'" in E2E and f"'{new}'" in MIGRATION, f'004 and 005 agree on "{new}"')

# ------------------------------------------------------------------ run the migration on old Spanish data
flask_app, db = setup_app('english_migration.db')
from models.CatInstrumentCategory import CatInstrumentCategory  # noqa: E402
from models.CatProcedureType import CatProcedureType  # noqa: E402
from sqlalchemy import text  # noqa: E402

E2E_IDS = {k: uuid.UUID(f'e2e00000-0000-4000-8000-0000000000{k}') for k in ('01', '11', '12', '21', '22', '23', '41', '51', '52')}
TABLES = sorted({t for t, _ in catalog_map})

with flask_app.app_context():
    connection = db.engine.raw_connection()
    raw = connection.driver_connection

    def q(sql, *params):
        return raw.execute(sql, params).fetchall()

    def hexid(key):
        return E2E_IDS[key].hex

    for table in TABLES:
        for (t, code), (old, _) in catalog_map.items():
            if t == table:
                q(f'INSERT INTO {table} (id, code, name) VALUES (?, ?, ?)', uuid.uuid4().hex, code, old)
    # a catalog value edited by an administrator must be preserved
    q("UPDATE cat_procedure_type SET name = 'Custom hernia name' WHERE code = 'hernia_repair'")
    q("INSERT INTO privacy_notice_version (id, version, effective_at, document_uri, active) VALUES (?, 'v1.0', '2026-09-01', "
      "'https://pef.udem/conteo-cenital/aviso-privacidad/v1.0', 1)", uuid.uuid4().hex)
    cat_id = q("SELECT id FROM cat_instrument_category WHERE code = 'hemostasis'")[0][0]
    families = {}
    for t, code, old, _ in family_map:
        families.setdefault(code, {})[t.split('.')[1]] = families.get(code, {}).get(t.split('.')[1]) or old
    for code, cols in families.items():
        q('INSERT INTO instrument_family (id, code, name, category_id, identify_text, classify_text, function_text, active) '
          'VALUES (?, ?, ?, ?, ?, ?, ?, 1)', uuid.uuid4().hex, code, cols['name'], cat_id,
          cols.get('identify_text'), cols.get('classify_text'), cols.get('function_text'))
    q("INSERT INTO institution (id, name, active) VALUES (?, 'Hospital E2E (testing)', 1)", hexid('01'))
    q("INSERT INTO operating_room (id, code, name, active, institution_id) VALUES (?, 'E2E-OR-1', 'Quirofano E2E 1', 1, ?)", hexid('11'), hexid('01'))
    q("INSERT INTO capture_station (id, name, active, room_id) VALUES (?, 'Estacion cenital E2E-OR-1', 1, ?)", hexid('12'), hexid('11'))
    for key, code, desc in (('21', 'it_admin', 'Administrador Tecnico (E2E)'), ('22', 'operator_cde', 'Operador CDE (E2E)'),
                            ('23', 'supervisor_quality', 'Supervisor de Calidad (E2E)')):
        q('INSERT INTO role (id, code, description, institution_id) VALUES (?, ?, ?, ?)', hexid(key), code, desc, hexid('01'))
    q("INSERT INTO kit (id, name, version, active, institution_id) VALUES (?, 'Kit Cirugia General E2E', 1, 1, ?)", hexid('41'), hexid('01'))
    appx = q("SELECT id FROM cat_procedure_type WHERE code = 'appendectomy'")[0][0]
    q("INSERT INTO procedure_kit (id, procedure_type_id, kit_id, technique_label, is_default, active) VALUES (?, ?, ?, 'Abierta (E2E)', 1, 1)",
      uuid.uuid4().hex, appx, hexid('41'))
    q("INSERT INTO patient (id, display_name, active, institution_id) VALUES (?, 'Paciente E2E 001', 1, ?)", hexid('51'), hexid('01'))
    q("INSERT INTO physician (id, name, active, institution_id) VALUES (?, 'Dra. E2E Cirujana', 1, ?)", hexid('52'), hexid('01'))
    raw.commit()

    script = MIGRATION.replace(' ON COMMIT DROP', '')
    script = re.sub(r"'([0-9a-f]{8})-([0-9a-f]{4})-([0-9a-f]{4})-([0-9a-f]{4})-([0-9a-f]{12})'",
                    lambda m: "'" + ''.join(m.groups()) + "'", script)

    def run_migration():
        before = raw.total_changes
        raw.executescript(script + '\nDROP TABLE IF EXISTS canonical_english;')
        return raw.total_changes - before

    first = run_migration()
    check(first > 0, f'first run converts rows ({first} changes incl. temp table)')
    for (table, code), (old, new) in catalog_map.items():
        expected = 'Custom hernia name' if (table, code) == ('cat_procedure_type', 'hernia_repair') else new
        got = q(f'SELECT name FROM {table} WHERE code = ?', code)[0][0]
        if got != expected:
            check(False, f'{table}.{code} = {got!r}, expected {expected!r}')
    check(all(q(f'SELECT name FROM {t} WHERE code = ?', c)[0][0] in (n, 'Custom hernia name') for (t, c), (_, n) in catalog_map.items()),
          'every catalog name converted by code')
    check(q("SELECT name FROM cat_procedure_type WHERE code = 'hernia_repair'")[0][0] == 'Custom hernia name', 'user-edited value preserved')
    names = dict(q('SELECT code, name FROM instrument_family'))
    check(names == {'KELLY': 'Kelly Forceps', 'MOSQUITO': 'Mosquito Forceps', 'METZ': 'Metzenbaum Scissors',
                    'MAYOHEG': 'Mayo-Hegar Needle Holder', 'FARABEUF': 'Farabeuf Retractor'}, f'family names {names}')
    texts = q('SELECT identify_text, classify_text, function_text FROM instrument_family')
    check(not any(v and re.search(r'\b(de|la|el|para|con)\b', v) for row in texts for v in row), 'family texts converted')
    check(q('SELECT name FROM operating_room WHERE id = ?', hexid('11'))[0][0] == 'E2E Operating Room 1', 'operating room')
    check(q('SELECT name FROM capture_station WHERE id = ?', hexid('12'))[0][0] == 'Overhead Capture Station E2E-OR-1', 'capture station')
    check([r[0] for r in q('SELECT description FROM role ORDER BY code')] == ['IT Administrator', 'Operator CDE', 'Supervisor CDE / Quality'],
          'role descriptions (codes unchanged)')
    check([r[0] for r in q('SELECT code FROM role ORDER BY code')] == ['it_admin', 'operator_cde', 'supervisor_quality'], 'role codes unchanged')
    check(q('SELECT name FROM kit WHERE id = ?', hexid('41'))[0][0] == 'General Surgery Kit E2E', 'kit')
    check(q('SELECT technique_label FROM procedure_kit')[0][0] == 'Open (E2E)', 'procedure kit technique label')
    check(q('SELECT display_name FROM patient')[0][0] == 'E2E Patient 001', 'patient')
    check(q('SELECT name FROM physician')[0][0] == 'Dr. E2E Surgeon', 'physician')
    check(q('SELECT document_uri FROM privacy_notice_version')[0][0] == 'https://pef.udem/overhead-count/privacy-notice/v1.0',
          'privacy notice placeholder URI')

    snapshot = {t: q(f'SELECT * FROM {t} ORDER BY id') for t in TABLES + ['instrument_family', 'role', 'kit']}
    second = run_migration()
    check(snapshot == {t: q(f'SELECT * FROM {t} ORDER BY id') for t in snapshot}, 'second run changes nothing (idempotent)')
    connection.close()

finish()
