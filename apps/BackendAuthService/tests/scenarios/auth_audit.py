"""BackendAuthService scenario: /verify rejects inactive users, LOGIN / LOGOUT audit.

Runs the real Flask app of main.py against a throwaway SQLite database (DATABASE_URL is set
before import) with minimal "user" / role / user_role / access_audit tables.
"""
import importlib
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
SERVICE = Path(__file__).resolve().parents[2]  # apps/BackendAuthService
WORKDIR = os.environ.get('PEF_TEST_WORKDIR') or tempfile.mkdtemp(prefix='pef-auth-test-')
FAILS = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILS.append(label)


os.environ.update({"DATABASE_URL": "sqlite:///" + os.path.join(WORKDIR, "auth.db"),
                   "JWT_SECRET_KEY": "test-secret-key-at-least-32-bytes-long!", "AUTH_COOKIE_SECURE": "false"})
sys.path.insert(0, str(SERVICE))
auth = importlib.import_module("main")

from sqlalchemy import text  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

INSTITUTION = str(uuid.uuid4())
with auth.engine.begin() as con:
    con.execute(text('CREATE TABLE "user" (id TEXT PRIMARY KEY, name TEXT, email TEXT, password_hash TEXT, active BOOLEAN, '
                     'institution_id TEXT, last_login_at TEXT, password_updated_at TEXT)'))
    con.execute(text("CREATE TABLE role (id TEXT PRIMARY KEY, code TEXT, description TEXT, institution_id TEXT)"))
    con.execute(text("CREATE TABLE user_role (id TEXT PRIMARY KEY, user_id TEXT, role_id TEXT)"))
    con.execute(text("CREATE TABLE access_audit (id TEXT PRIMARY KEY, occurred_at TEXT DEFAULT CURRENT_TIMESTAMP, actor_type TEXT NOT NULL, "
                     "actor_user_id TEXT, actor_client_id TEXT, action TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT NOT NULL, "
                     "institution_id TEXT, outcome TEXT NOT NULL DEFAULT 'success', correlation_id TEXT, ip TEXT, "
                     "CHECK ((actor_type = 'user' AND actor_user_id IS NOT NULL) OR (actor_type = 'client' AND actor_client_id IS NOT NULL)))"))
    role_id = str(uuid.uuid4())
    con.execute(text("INSERT INTO role VALUES (:id, 'operator_cde', 'Operator', :i)"), {"id": role_id, "i": INSTITUTION})


def add_user(email):
    uid = str(uuid.uuid4())
    with auth.engine.begin() as con:
        con.execute(text('INSERT INTO "user" VALUES (:id, :n, :e, :h, 1, :i, NULL, NULL)'),
                    {"id": uid, "n": email, "e": email, "h": generate_password_hash("secret123"), "i": INSTITUTION})
        con.execute(text("INSERT INTO user_role VALUES (:id, :u, :r)"), {"id": str(uuid.uuid4()), "u": uid, "r": role_id})
    return uid


def access_token(response):
    return next((c.split("=", 1)[1].split(";")[0] for c in response.headers.getlist("Set-Cookie")
                 if c.startswith("access_token=")), None)


def audits(action=None, outcome=None):
    with auth.engine.connect() as con:
        rows = con.execute(text("SELECT action, outcome, actor_user_id, institution_id, resource_id FROM access_audit")).all()
    return [r for r in rows if (action is None or r[0] == action) and (outcome is None or r[1] == outcome)]


client = auth.app.test_client()

print("== /verify rejects inactive / deleted users")
verify_uid = add_user("verify@auth.org")
login = client.post("/login", json={"email": "verify@auth.org", "password": "secret123"})
check(login.status_code == 200, f"auth login -> {login.status_code}")
headers = {"Authorization": f"Bearer {access_token(login)}"}
check(client.get("/verify", headers=headers).status_code == 200, "verify active user -> 200")
with auth.engine.begin() as con:
    con.execute(text('UPDATE "user" SET active = 0 WHERE id = :id'), {"id": verify_uid})
check(client.get("/verify", headers=headers).status_code == 401, "verify deactivated user -> 401")
with auth.engine.begin() as con:
    con.execute(text('UPDATE "user" SET active = 1 WHERE id = :id'), {"id": verify_uid})
check(client.get("/verify", headers=headers).status_code == 401, "token revoked: stays 401 after reactivation")
with auth.engine.begin() as con:
    con.execute(text('DELETE FROM "user" WHERE id = :id'), {"id": verify_uid})
check(client.post("/login", json={"email": "verify@auth.org", "password": "secret123"}).status_code == 401,
      "deleted user cannot log in")

print("== LOGIN / LOGOUT audit")
uid = add_user("op@auth.org")
before = len(audits("LOGIN", "success"))
login = client.post("/login", json={"email": "op@auth.org", "password": "secret123"})
token = access_token(login)
rows = [r for r in audits("LOGIN", "success") if r[2] == uid]
check(login.status_code == 200 and len(audits("LOGIN", "success")) == before + 1 and len(rows) == 1
      and rows[0][3] == INSTITUTION and rows[0][4] == uid, "LOGIN audited once with actor/institution")
for _ in range(3):
    client.get("/verify", headers={"Authorization": f"Bearer {token}"})
check(len([r for r in audits("LOGIN") if r[2] == uid]) == 1, "/verify does not create LOGIN")
bad = client.post("/login", json={"email": "op@auth.org", "password": "wrong-pass"})
check(bad.status_code == 401 and len(audits("LOGIN", "denied")) == 1, "failed login -> 401 + denied LOGIN for the account")
unknown = client.post("/login", json={"email": "nobody@auth.org", "password": "whatever1"})
check(unknown.status_code == 401 and len(audits("LOGIN", "denied")) == 1, "unknown e-mail -> 401, not audited (no actor)")
login2 = client.post("/login", json={"email": "op@auth.org", "password": "secret123"})
token2 = access_token(login2)
check(login2.status_code == 200, "auth still works after failures")
out = client.post("/logout", headers={"Authorization": f"Bearer {token}"})
check(out.status_code == 200 and len(audits("LOGOUT")) == 1, "LOGOUT audited")
check(client.get("/verify", headers={"Authorization": f"Bearer {token}"}).status_code == 401, "token revoked after logout")
with auth.engine.begin() as con:
    con.execute(text("DROP TABLE access_audit"))
login3 = client.post("/login", json={"email": "op@auth.org", "password": "secret123"})
check(login3.status_code == 503, "LOGIN audit failure fails the login (same transaction, no unaudited login)")
check(client.post("/logout", headers={"Authorization": f"Bearer {token2}"}).status_code == 200,
      "LOGOUT still works when its audit fails")

print(f"\n{'ALL PASSED' if not FAILS else f'{len(FAILS)} FAILED: ' + '; '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
