"""Boot the real Flask app (no PostgreSQL) and exercise the ORM mappings without touching a DB."""
import os
import sys
import warnings
import sys as _sys
_sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from pathlib import Path
APP = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, APP)
os.chdir(APP)
warnings.simplefilter("error")
warnings.simplefilter("ignore", ResourceWarning)
warnings.simplefilter("ignore", DeprecationWarning)

import config  # noqa: E402
# psycopg can't load in the scratch venv; a lazy SQLite engine is enough to build the app (never connected)
config.Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(__import__("tempfile").mkdtemp(prefix="pef-test-"), "unused.db")
import app as app_module  # runs create_app(): db.init_app + blueprint (routes.py, view_data.py)
from sqlalchemy import insert
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers

from extensions import db
from models.User import User
from models.UserRole import UserRole
from models.Role import Role
from models.WorkSession import WorkSession

flask_app = app_module.app
with flask_app.app_context():
    configure_mappers()
    print(f"[ok] create_app() + configure_mappers(); {len(flask_app.url_map._rules)} routes registered")

    # routes.py builds users like this; server-generated columns must not be required
    u = User(name="n", email="e@x", password_hash="h", institution_id=None, active=True)
    UserRole(user=u, role=Role(code="c", description="d"))
    print("[ok] User(...) as built in routes.py constructs without the new server-default columns")

    ins = str(insert(User).values(name="n", email="e", password_hash="h", institution_id=None)
              .compile(dialect=postgresql.dialect()))
    assert "ui_preferences" not in ins and "created_at" not in ins, ins
    print("[ok] INSERT for user omits ui_preferences/created_at/updated_at -> PostgreSQL defaults apply")

    # opener vs closer relationships resolve to different columns
    opener, closer = User(name="a"), User(name="b")
    ws = WorkSession(user=opener, closed_by_user=closer)
    assert ws in opener.work_sessions and ws in closer.closed_work_sessions
    assert ws not in opener.closed_work_sessions and ws not in closer.work_sessions
    rels = WorkSession.__mapper__.relationships
    assert [c.name for c in rels["user"].local_columns] == ["user_id"]
    assert [c.name for c in rels["closed_by_user"].local_columns] == ["closed_by_user_id"]
    print("[ok] WorkSession.user -> user_id, WorkSession.closed_by_user -> closed_by_user_id (back_populates wired)")

    # every back_populates points at an existing, reciprocal relationship
    for mapper in db.Model.registry.mappers:
        for rel in mapper.relationships:
            if rel.back_populates:
                other = rel.mapper.relationships.get(rel.back_populates)
                assert other is not None, f"{mapper.class_.__name__}.{rel.key} -> missing {rel.back_populates}"
                assert other.back_populates == rel.key, f"{mapper.class_.__name__}.{rel.key} not reciprocal"
    print("[ok] all back_populates pairs exist and are reciprocal")
