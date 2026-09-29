"""Shared SQLite harness for the BackendWebFlask scenario suites.

PostgreSQL-only DDL (JSONB, INET, gen_random_uuid(), now(), partial indexes, jsonb_typeof) is
adapted for SQLite here only. Authentication is simulated by replacing routes._current_user.
"""
import os
import sys
import tempfile
import warnings
from pathlib import Path

APP = str(Path(__file__).resolve().parents[2])  # apps/BackendWebFlask
# every scenario process gets its own throwaway work dir (SQLite DB, evidence files)
HERE = os.environ.get('PEF_TEST_WORKDIR') or tempfile.mkdtemp(prefix='pef-test-')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
FAILS = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILS.append(label)


def finish():
    print(f"\n{'ALL PASSED' if not FAILS else f'{len(FAILS)} FAILED: ' + '; '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)


def setup_app(db_name):
    db_file = os.path.join(HERE, db_name)
    if os.path.exists(db_file):
        os.remove(db_file)
    sys.path.insert(0, APP)
    os.chdir(APP)
    warnings.simplefilter("ignore", DeprecationWarning)

    import config
    config.Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + db_file
    config.Config.SQLALCHEMY_ENGINE_OPTIONS = {}

    from sqlalchemy import event, text
    from sqlalchemy.dialects.postgresql import INET, JSONB
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _jsonb(element, compiler, **kw):
        return "JSON"

    @compiles(INET, "sqlite")
    def _inet(element, compiler, **kw):
        return "TEXT"

    import app as app_module
    from extensions import db
    from sqlalchemy.orm import configure_mappers
    import models  # noqa: F401

    flask_app = app_module.app
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        configure_mappers()
        for table in db.metadata.tables.values():
            for column in table.columns:
                sd = column.server_default
                if sd is None:
                    continue
                txt = str(getattr(sd.arg, "text", sd.arg))
                if "gen_random_uuid" in txt:
                    column.server_default = None
                elif "now()" in txt:
                    column.server_default = type(sd)(text("CURRENT_TIMESTAMP"))
                elif "::jsonb" in txt:
                    column.server_default = type(sd)(text(txt.replace("::jsonb", "")))
            for index in table.indexes:
                where = index.dialect_options["postgresql"].get("where")
                if where is not None:
                    index.dialect_options["sqlite"]["where"] = where

        def _on_connect(con, rec):
            con.execute("PRAGMA foreign_keys=ON")
            con.create_function("jsonb_typeof", 1, lambda v: "object" if v and str(v).lstrip().startswith("{") else "other")

        event.listen(db.engine, "connect", _on_connect)
        db.engine.dispose()
        db.create_all()
    return flask_app, db


def fake_user(user_id, institution_id, role_code, name="Test"):
    labels = {"it_admin": "Administrator", "operator_cde": "Operator", "supervisor_quality": "Supervisor"}
    return {"id": str(user_id), "name": name, "email": "t@x.org", "institution_id": str(institution_id),
            "roles": [{"code": role_code}], "role_code": role_code, "role_label": labels[role_code],
            "profile_role_label": labels[role_code], "avatar_url": None}
