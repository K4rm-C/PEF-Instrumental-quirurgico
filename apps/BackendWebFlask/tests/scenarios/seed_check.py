"""Static checks of data/seeds/004_seed_e2e.sql (no database connection).

Column names are checked against the SQLAlchemy metadata (verified identical to 001_init.sql by
schema_check.py) and catalog codes against 002_seed_catalogs.sql. If the optional `pglast`
package (libpg_query, the real PostgreSQL parser) is installed, 001/002/004 are also parsed with
the PostgreSQL grammar; otherwise that part is skipped and a regex column check is used.
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
try:
    import pglast
    from pglast.visitors import Visitor
except ImportError:  # optional dependency
    pglast = None

ROOT = Path(__file__).resolve().parents[4]  # repository root
sys.path.insert(0, str(ROOT / 'apps' / 'BackendWebFlask'))
seed = (ROOT / 'data' / 'seeds' / '004_seed_e2e.sql').read_text(encoding='utf-8')
catalogs = (ROOT / 'data' / 'seeds' / '002_seed_catalogs.sql').read_text(encoding='utf-8')
fails = []


def check(cond, label):
    print(('  ok   ' if cond else '  FAIL ') + label)
    if not cond:
        fails.append(label)


# 1. PostgreSQL grammar
if pglast:
    for name in ('migrations/001_init.sql', 'seeds/002_seed_catalogs.sql', 'seeds/004_seed_e2e.sql',
                 'migrations/005_canonical_english_data.sql'):
        try:
            statements = pglast.parse_sql((ROOT / 'data' / name).read_text(encoding='utf-8'))
            check(True, f'{name}: parses with the PostgreSQL grammar ({len(statements)} statements)')
        except pglast.parser.ParseError as exc:
            check(False, f'{name}: {exc}')
else:
    print('  (pglast not installed: PostgreSQL grammar check skipped)')

# 2. INSERT/UPDATE target columns exist in the schema
import models  # noqa: E402,F401
from extensions import db  # noqa: E402

tables = db.Model.metadata.tables
targets = []
if pglast:
    class Collect(Visitor):
        def visit_InsertStmt(self, parent, node):
            targets.append((node.relation.relname, [t.name for t in node.cols]))

        def visit_UpdateStmt(self, parent, node):
            targets.append((node.relation.relname, [t.name for t in node.targetList]))

    for statement in pglast.parse_sql(seed):
        Collect()(statement)
else:
    body = re.sub(r'--[^\n]*', '', seed)
    for table, cols in re.findall(r'INSERT INTO "?(\w+)"? \(([^)]*)\)', body):
        targets.append((table, [c.strip() for c in cols.split(',')]))
    for table, sets in re.findall(r'UPDATE "?(\w+)"? SET ([^\n]*)', body):
        targets.append((table, [part.split('=')[0].strip() for part in sets.split(',')]))
for table, cols in targets:
    missing = [c for c in cols if table not in tables or c not in tables[table].c]
    check(table in tables and not missing, f'{table}: columns {cols} exist' + (f' MISSING {missing}' if missing else ''))

# 3. catalog codes referenced by the seed exist in 002
code_refs = {
    'cat_instrument_category': ['hemostasis', 'cutting', 'suturing', 'retraction'],
    'cat_procedure_type': ['appendectomy'],
    'cat_operation_phase': ['pre_incision', 'pre_closure', 'final_count'],
    'cat_gender': ['unknown'], 'cat_specialty': ['general_surgery'], 'cat_surgical_role': ['surgeon'],
    'cat_operation_status': ['scheduled'],
}
for table, codes in code_refs.items():
    block = catalogs[catalogs.index(f'INSERT INTO {table} '):]
    block = block[:block.index(';')]
    for code in codes:
        check(f"'{code}'" in block and f"'{code}'" in seed, f'{table}.{code} exists in 002 and is used by 004')
check("('low_confidence'" in catalogs and "('correction_requested'" in catalogs, 'low_confidence / correction_requested in 002')
check('low_confidence' not in seed and 'correction_requested' not in seed, '004 does not duplicate 002 catalogs')

# 4. scenario content
check("st.code = 'scheduled'" in seed and "'closed'" not in seed, 'operation is scheduled (not closed)')
inserted = {table for table, _ in targets}
for table in ('institution', 'operating_room', 'capture_station', 'role', 'user', 'user_role', 'instrument_family', 'kit',
              'kit_item', 'procedure_kit', 'procedure_phase', 'patient', 'physician', 'operation', 'operation_patient',
              'operation_physician', 'yolo_model', 'model_class'):
    check(table in inserted, f'seed writes {table}')
check("'e2e-controlled-dev', NULL, NULL, TRUE" in seed, 'active development YoloModel without weights')
roles = re.findall(r"'(it_admin|operator_cde|supervisor_quality)'", re.sub(r'--[^\n]*', '', seed))
check(sorted(roles) == ['it_admin', 'operator_cde', 'supervisor_quality'], 'the three application roles, once each')

# 5. password hashes are werkzeug hashes of the documented development password
from werkzeug.security import check_password_hash  # noqa: E402

hashes = re.findall(r"'(pbkdf2:sha256:[^']+)'", seed)
check(len(hashes) == 3 and all(check_password_hash(h, 'E2eTest#2026') for h in hashes), '3 werkzeug hashes of the dev password')
check('E2eTest#2026' not in re.sub(r'--[^\n]*', '', seed), 'no plaintext password outside SQL comments')

print('\nALL PASSED' if not fails else f'\n{len(fails)} FAILED')
sys.exit(1 if fails else 0)
