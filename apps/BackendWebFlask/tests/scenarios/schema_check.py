"""Offline check: SQLAlchemy models vs data/migrations/001_init.sql (no DB connection)."""
import os
import re
import sys
import warnings
import sys as _sys
_sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from pathlib import Path
ROOT = str(Path(__file__).resolve().parents[4])  # repository root
APP = os.environ.get("APP_DIR") or os.path.join(ROOT, "apps", "BackendWebFlask")
sys.path.insert(0, APP)
if not os.environ.get("LENIENT"):
    warnings.simplefilter("error")  # turn SAWarnings (e.g. relationship overlaps) into failures
    warnings.simplefilter("ignore", ResourceWarning)

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateIndex, CreateTable

# ---------------------------------------------------------------- 1. import + mappers
import models  # noqa: F401  (imports every model file)
from extensions import db

try:
    configure_mappers()
except Exception as exc:
    if not os.environ.get("LENIENT"):
        raise
    print("[FAIL] configure_mappers:", type(exc).__name__, str(exc)[:400])
meta = db.Model.metadata
print(f"[ok] imported models, configure_mappers() passed, {len(meta.tables)} tables in metadata")

# ---------------------------------------------------------------- 2. DDL compiles for PG
dialect = postgresql.dialect()
for table in meta.sorted_tables:
    str(CreateTable(table).compile(dialect=dialect))
    for index in table.indexes:
        str(CreateIndex(index).compile(dialect=dialect))
print("[ok] CREATE TABLE / CREATE INDEX compile against the postgresql dialect")

# ---------------------------------------------------------------- 3. parse SQL
sql = open(os.path.join(ROOT, "data", "migrations", "001_init.sql"), encoding="utf-8").read()
sql = re.sub(r"--[^\n]*", "", sql)

sql_tables = {}
for m in re.finditer(r'CREATE TABLE "?(\w+)"? \((.*?)\n\);', sql, re.S):
    name, body = m.group(1), m.group(2)
    cols, uniques, fks, checks = {}, set(), {}, set()
    for part in re.split(r",\s*\n(?=\s*(?:CONSTRAINT|\w+\s))", body):
        part = " ".join(part.split())
        if part.startswith("CONSTRAINT"):
            cname = part.split()[1]
            if " UNIQUE " in part:
                uniques.add((cname, tuple(c.strip() for c in re.search(r"UNIQUE \((.*?)\)", part).group(1).split(","))))
            elif "FOREIGN KEY" in part:
                col = re.search(r"FOREIGN KEY \((\w+)\)", part).group(1)
                ref = re.search(r'REFERENCES "?(\w+)"? \((\w+)\)', part)
                ondel = re.search(r"ON DELETE (SET NULL|CASCADE|RESTRICT)", part)
                fks[col] = (f"{ref.group(1)}.{ref.group(2)}", ondel.group(1) if ondel else None)
            elif " CHECK " in part:
                checks.add(cname)
            continue
        cm = re.match(r"(\w+)\s+(.*)", part)
        col, rest = cm.group(1), cm.group(2)
        ctype = re.match(r"([A-Z]+(?:\(\d+\))?)", rest).group(1)
        dflt = re.search(r"DEFAULT (.+)$", rest)
        cols[col] = {"type": ctype, "nullable": "NOT NULL" not in rest, "default": dflt.group(1).strip() if dflt else None}
    sql_tables[name] = {"cols": cols, "uniques": uniques, "fks": fks, "checks": checks}

for m in re.finditer(r"ALTER TABLE (\w+)\s+ADD CONSTRAINT \w+\s+FOREIGN KEY \((\w+)\) REFERENCES (\w+) \((\w+)\) ON DELETE (SET NULL|CASCADE|RESTRICT)", sql):
    sql_tables[m.group(1)]["fks"][m.group(2)] = (f"{m.group(3)}.{m.group(4)}", m.group(5))

sql_unique_idx = {m.group(1): (m.group(2), tuple(c.strip() for c in m.group(3).split(",")))
                  for m in re.finditer(r'CREATE UNIQUE INDEX (\w+)\s+ON "?(\w+)"? \((.*?)\)', sql)}

# ---------------------------------------------------------------- 4. compare
TYPE_MAP = {"UUID": "UUID", "TEXT": "TEXT", "BOOLEAN": "BOOLEAN", "TIMESTAMPTZ": "TIMESTAMP WITH TIME ZONE",
            "JSONB": "JSONB", "SMALLINT": "SMALLINT", "BIGINT": "BIGINT", "DATE": "DATE", "INET": "INET"}

def sql_type(t):
    if t.startswith("VARCHAR"):
        return t
    if t.startswith("CHAR"):
        return t
    return TYPE_MAP[t]

def norm_default(d):
    if d is None:
        return None
    d = d.lower().replace(" ", "")
    return {"gen_random_uuid()": "uuid", "now()": "now"}.get(d, d)

def model_default(col):
    sd = col.server_default
    if sd is None:
        return None
    arg = sd.arg
    txt = str(arg.compile(dialect=dialect)) if hasattr(arg, "compile") else str(arg)
    return norm_default(txt)

problems = []
model_tables = set(meta.tables)
if set(sql_tables) != model_tables:
    problems.append(f"table set differs: only SQL={set(sql_tables) - model_tables}, only models={model_tables - set(sql_tables)}")

for tname, spec in sql_tables.items():
    table = meta.tables.get(tname)
    if table is None:
        continue
    mcols = {c.name: c for c in table.columns}
    if set(mcols) != set(spec["cols"]):
        problems.append(f"{tname}: columns only in SQL={set(spec['cols']) - set(mcols)}, only in model={set(mcols) - set(spec['cols'])}")
    for cname, cs in spec["cols"].items():
        col = mcols.get(cname)
        if col is None:
            continue
        mt = col.type.compile(dialect=dialect)
        if mt != sql_type(cs["type"]):
            problems.append(f"{tname}.{cname}: type model={mt} sql={cs['type']}")
        is_pk = cname == "id"
        if not is_pk and col.nullable != cs["nullable"]:
            problems.append(f"{tname}.{cname}: nullable model={col.nullable} sql={cs['nullable']}")
        if model_default(col) != norm_default(cs["default"]):
            problems.append(f"{tname}.{cname}: default model={model_default(col)} sql={norm_default(cs['default'])}")
    # PK
    if [c.name for c in table.primary_key.columns] != ["id"]:
        problems.append(f"{tname}: primary key is {[c.name for c in table.primary_key.columns]}")
    # FKs
    mfks = {}
    for fkc in table.constraints:
        if isinstance(fkc, ForeignKeyConstraint):
            for el in fkc.elements:
                mfks[el.parent.name] = (el.target_fullname, fkc.ondelete)
    if mfks != spec["fks"]:
        problems.append(f"{tname}: FK mismatch model={mfks} sql={spec['fks']}")
    # UNIQUE constraints (by name + columns)
    muniq = {(c.name, tuple(col.name for col in c.columns)) for c in table.constraints if isinstance(c, UniqueConstraint)}
    if muniq != spec["uniques"]:
        problems.append(f"{tname}: UNIQUE mismatch model={muniq} sql={spec['uniques']}")
    unnamed_unique = [c.name for c in table.columns if c.unique]
    if unnamed_unique:
        problems.append(f"{tname}: extra column-level unique=True on {unnamed_unique}")
    # CHECK constraints (by name)
    mchk = {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
    if mchk != spec["checks"]:
        problems.append(f"{tname}: CHECK mismatch model={mchk} sql={spec['checks']}")

# partial unique indexes
m_unique_idx = {i.name: (i.table.name, tuple(c.name for c in i.columns))
                for t in meta.tables.values() for i in t.indexes if i.unique}
if m_unique_idx != sql_unique_idx:
    problems.append(f"UNIQUE INDEX mismatch model={m_unique_idx} sql={sql_unique_idx}")

print(f"[..] compared {len(sql_tables)} SQL tables against {len(model_tables)} model tables")
if problems:
    print(f"[FAIL] {len(problems)} differences:")
    for p in problems:
        print("   -", p)
    sys.exit(1)
print("[ok] columns, types, nullability, PKs, FKs (+ON DELETE), UNIQUE, CHECK names and server defaults match 001_init.sql")

sys.exit(0)
