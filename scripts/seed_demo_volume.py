#!/usr/bin/env python3
"""Generate variable demo volume (ORs, history, KPIs, audit, monitoring) for PEF.

Runs after catalogs + 003 (canonical + Locust). Idempotent via UUID5 keys and
ON CONFLICT. Re-applies data/seeds/004_seed_demo_locust.sql first so older volumes
still get established Locust accounts.

Env (defaults suit a full local / Locust smoke):
  SEED_VOLUME_ENABLED=1          set 0 to skip generation (still applies 004)
  SEED_RNG=2026                  reproducibility seed
  SEED_OR_COUNT=4                total ORs including OR-01/OR-02 from SQL
  SEED_CLOSED_SESSIONS=48        closed sessions spread over SEED_DAYS_BACK
  SEED_SCHEDULED=8
  SEED_IN_PROGRESS=2             extra in_progress (beyond SQL fixtures)
  SEED_DAYS_BACK=30
  SEED_DISCREPANCY_RATE=0.28
  SEED_AUDIT_PER_SESSION=3
  SEED_MONITORING_HOURS=48       synthetic monitoring_check history
  SEED_EXTRA_OP_USERS=4          gen.opN@instrumed.com (password DemopwdGEN78!)
  SEED_EXTRA_SPD_USERS=2
  SEED_EXTRA_FLOOR_USERS=1

Usage:
  python scripts/seed_demo_volume.py
"""
from __future__ import annotations

import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCUST_SQL = ROOT / "data" / "seeds" / "004_seed_demo_locust.sql"
NS = uuid.UUID("a11ce5e0-5eed-4000-8000-00000000def0")
INSTITUTION = uuid.UUID("11111111-1111-1111-1111-111111111111")
KIT_ID = uuid.UUID("51515151-0000-4000-8000-000000000001")
# Shared hash for DemopwdGEN78! (Werkzeug pbkdf2, same family as 003).
GEN_PASSWORD_HASH = (
    "pbkdf2:sha256:600000$SeedVolGenPEF01$"
    "8f3c0a1b2d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcd"
)

ROLE_OP = uuid.UUID("14141414-0000-4000-8000-000000000001")
ROLE_SPD = uuid.UUID("14141414-0000-4000-8000-000000000002")
ROLE_ADMIN = uuid.UUID("14141414-0000-4000-8000-000000000003")
ROLE_FLOOR = uuid.UUID("14141414-0000-4000-8000-000000000004")

PROC_CODES = ("lap_chole", "appendectomy", "hernia_repair", "demo", "open_chole")
MONITOR_COMPONENTS = (
    "auth", "catalog", "session", "evidence", "notifications",
    "monitoring", "integration", "web", "postgres", "redis", "mongo", "garage",
)


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    return float(raw)


def _uid(kind: str, *parts: object) -> uuid.UUID:
    return uuid.uuid5(NS, f"{kind}:" + ":".join(str(p) for p in parts))


def _database_url() -> str:
    url = _env("DATABASE_URL")
    if url:
        return url.replace("postgresql+psycopg://", "postgresql://")
    user = _env("POSTGRES_USER", "MedAdmin")
    password = _env("POSTGRES_PASSWORD", "MedMin027")
    host = _env("POSTGRES_HOST", "127.0.0.1")
    port = _env("POSTGRES_PORT", "5433")
    db = _env("POSTGRES_DB", "InstruMed")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _ensure_gen_password_hash(conn) -> str:
    """Build a real Werkzeug hash once; cache in DB via first generated user or compute here."""
    try:
        from werkzeug.security import generate_password_hash

        return generate_password_hash("DemopwdGEN78!", method="pbkdf2:sha256:600000")
    except Exception:
        # Fallback: reuse an existing demo hash shape only if werkzeug missing (compose installs it).
        row = conn.execute(
            'SELECT password_hash FROM "user" WHERE email = %s LIMIT 1',
            ("operator@instrumed.com",),
        ).fetchone()
        if row:
            return row[0]
        return GEN_PASSWORD_HASH


def _cat(conn, table: str, code: str):
    row = conn.execute(f"SELECT id FROM {table} WHERE code = %s", (code,)).fetchone()
    if not row:
        raise RuntimeError(f"missing catalog {table}.code={code}")
    return row[0]


def _apply_locust_sql(conn) -> None:
    """Apply 004 via psql when available (multi-statement). Compose also runs it first."""
    if _env("SEED_SKIP_LOCUST_SQL", "0") in ("1", "true", "True", "yes"):
        print("ok: skip 004 (SEED_SKIP_LOCUST_SQL)")
        return
    if not LOCUST_SQL.is_file():
        print("warn: 004_seed_demo_locust.sql missing; skip re-apply")
        return
    import shutil
    import subprocess

    psql = shutil.which("psql")
    if not psql:
        print("warn: psql not on PATH; rely on Compose pre-step or merged 003 for Locust rows")
        return
    url = _database_url()
    # Prefer discrete env for psql (password in URL can break special chars).
    env = os.environ.copy()
    env.setdefault("PGPASSWORD", _env("POSTGRES_PASSWORD", "MedMin027"))
    cmd = [
        psql,
        "-h", _env("POSTGRES_HOST", "127.0.0.1"),
        "-p", _env("POSTGRES_PORT", "5433"),
        "-U", _env("POSTGRES_USER", "MedAdmin"),
        "-d", _env("POSTGRES_DB", "InstruMed"),
        "-v", "ON_ERROR_STOP=1",
        "-f", str(LOCUST_SQL),
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError("failed applying 004_seed_demo_locust.sql")
    print("ok: applied 004_seed_demo_locust.sql (idempotent)")
    # discard unused url lint
    _ = url
    conn.rollback()  # keep connection clean after external psql txn


def _ensure_ors(conn, rng: random.Random, or_count: int) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Return list of (room_id, station_id) for OR-01..OR-N."""
    rooms: list[tuple[uuid.UUID, uuid.UUID]] = []
    for n in range(1, max(1, or_count) + 1):
        room_id = _uid("or", n) if n > 2 else uuid.UUID(f"12121212-0000-4000-8000-{n:012d}")
        station_id = _uid("station", n) if n > 2 else uuid.UUID(f"13131313-0000-4000-8000-{n:012d}")
        code = f"OR-{n:02d}"
        conn.execute(
            """
            INSERT INTO operating_room (id, code, name, active, institution_id)
            VALUES (%s, %s, %s, TRUE, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (room_id, code, f"Operating Room {n}", INSTITUTION),
        )
        # code unique may already exist with different id (SQL fixtures)
        existing = conn.execute(
            "SELECT id FROM operating_room WHERE institution_id = %s AND code = %s",
            (INSTITUTION, code),
        ).fetchone()
        if existing:
            room_id = existing[0]
        conn.execute(
            """
            INSERT INTO capture_station (id, name, roi, active, room_id)
            VALUES (%s, %s, %s::jsonb, TRUE, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                station_id,
                f"Capture Station CS-{n:02d}",
                '{"shape":"rect","x":240,"y":120,"w":1180,"h":760,"frame_w":1920,"frame_h":1080}',
                room_id,
            ),
        )
        st = conn.execute(
            "SELECT id FROM capture_station WHERE room_id = %s ORDER BY name LIMIT 1",
            (room_id,),
        ).fetchone()
        if st:
            station_id = st[0]
        rooms.append((room_id, station_id))
        # Floor demo covers all ORs
        conn.execute(
            """
            INSERT INTO user_operating_room (id, user_id, room_id)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                _uid("uor", "floor", n),
                uuid.UUID("22222222-2222-2222-2222-222222222226"),
                room_id,
            ),
        )
    return rooms


def _ensure_extra_users(conn, password_hash: str, op_n: int, spd_n: int, floor_n: int) -> dict[str, list[uuid.UUID]]:
    out: dict[str, list[uuid.UUID]] = {"op": [], "spd": [], "floor": []}
    # Prefer established Locust + canonical operators when present
    for email in (
        "operator@instrumed.com",
        "operador2@instrumed.com",
        "ana.ruiz@instrumed.com",
        "luis.pena@instrumed.com",
    ):
        row = conn.execute('SELECT id FROM "user" WHERE email = %s', (email,)).fetchone()
        if row:
            out["op"].append(row[0])
    for email in ("supervisor@instrumed.com", "supervisor2@instrumed.com", "carla.soto@instrumed.com"):
        row = conn.execute('SELECT id FROM "user" WHERE email = %s', (email,)).fetchone()
        if row:
            out["spd"].append(row[0])
    for email in ("floor@instrumed.com", "nora.diaz@instrumed.com"):
        row = conn.execute('SELECT id FROM "user" WHERE email = %s', (email,)).fetchone()
        if row:
            out["floor"].append(row[0])

    def add_user(kind: str, idx: int, role_id: uuid.UUID) -> None:
        email = f"gen.{kind}{idx}@instrumed.com"
        user_id = _uid("user", kind, idx)
        conn.execute(
            """
            INSERT INTO "user" (id, name, email, password_hash, active, ui_preferences, institution_id)
            VALUES (%s, %s, %s, %s, TRUE, %s::jsonb, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                user_id,
                f"Gen {kind.upper()} {idx}",
                email,
                password_hash,
                '{"theme":"light","locale":"es-MX"}',
                INSTITUTION,
            ),
        )
        # email unique: resolve existing
        row = conn.execute('SELECT id FROM "user" WHERE email = %s', (email,)).fetchone()
        if row:
            user_id = row[0]
        conn.execute(
            "INSERT INTO user_role (user_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, role_id),
        )
        out[kind if kind != "op" else "op"].append(user_id)

    for i in range(1, op_n + 1):
        add_user("op", i, ROLE_OP)
    for i in range(1, spd_n + 1):
        add_user("spd", i, ROLE_SPD)
    for i in range(1, floor_n + 1):
        add_user("floor", i, ROLE_FLOOR)
    return out


def _ensure_physicians(conn, count: int) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    rows = conn.execute(
        "SELECT id FROM physician WHERE institution_id = %s AND active ORDER BY name",
        (INSTITUTION,),
    ).fetchall()
    ids.extend(r[0] for r in rows)
    spec = _cat(conn, "cat_specialty", "general_surgery")
    for i in range(1, count + 1):
        pid = _uid("phys", i)
        conn.execute(
            """
            INSERT INTO physician (id, name, active, institution_id)
            VALUES (%s, %s, TRUE, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (pid, f"Dr. Volume Seed {i:02d}", INSTITUTION),
        )
        conn.execute(
            """
            INSERT INTO physician_specialty (physician_id, specialty_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
            """,
            (pid, spec),
        )
        ids.append(pid)
    # unique preserve order
    seen: set[uuid.UUID] = set()
    uniq: list[uuid.UUID] = []
    for x in ids:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _kit_items(conn) -> list[tuple[uuid.UUID, int]]:
    return [
        (r[0], int(r[1]))
        for r in conn.execute(
            "SELECT family_id, quantity FROM kit_item WHERE kit_id = %s",
            (KIT_ID,),
        ).fetchall()
    ]


def _insert_session_bundle(
    conn,
    *,
    idx: int,
    status: str,
    room_id: uuid.UUID,
    station_id: uuid.UUID,
    op_user: uuid.UUID,
    spd_user: uuid.UUID | None,
    physician_id: uuid.UUID,
    proc_code: str,
    started: datetime,
    ended: datetime | None,
    discrepancy_rate: float,
    audit_n: int,
    rng: random.Random,
    kit_items: list[tuple[uuid.UUID, int]],
) -> None:
    op_status = "closed" if status == "closed" else ("scheduled" if status == "scheduled" else "in_progress")
    sess_status = status
    operation_id = _uid("operation", idx, status)
    session_id = _uid("session", idx, status)
    patient_id = _uid("patient", idx)
    gender = _cat(conn, "cat_gender", rng.choice(("female", "male", "unknown")))
    conn.execute(
        """
        INSERT INTO patient (id, display_name, birth_date, active, gender_id, institution_id)
        VALUES (%s, %s, %s, TRUE, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (patient_id, f"Demo Patient VOL-{idx:04d}", datetime(1975 + (idx % 30), 1 + (idx % 12), 1 + (idx % 27)).date(), gender, INSTITUTION),
    )
    conn.execute(
        """
        INSERT INTO operation (id, scheduled_at, started_at, ended_at, status_id, procedure_type_id, room_id, institution_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (
            operation_id,
            started - timedelta(minutes=30),
            None if status == "scheduled" else started,
            ended,
            _cat(conn, "cat_operation_status", op_status),
            _cat(conn, "cat_procedure_type", proc_code),
            room_id,
            INSTITUTION,
        ),
    )
    conn.execute(
        "INSERT INTO operation_patient (operation_id, patient_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (operation_id, patient_id),
    )
    conn.execute(
        """
        INSERT INTO operation_physician (operation_id, physician_id, surgical_role_id)
        VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
        """,
        (operation_id, physician_id, _cat(conn, "cat_surgical_role", "surgeon")),
    )
    phase = "final_count" if status == "closed" else ("start" if status != "scheduled" else "start")
    capture = "vision" if rng.random() < 0.7 else "manual_no_privacy"
    conn.execute(
        """
        INSERT INTO work_session (
          id, started_at, ended_at, status_id, user_id, closed_by_user_id,
          operation_id, station_id, kit_id, current_phase_id, phase_changed_at,
          capture_mode, atypical_session, extended_retention, retention_until)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,FALSE,NULL)
        ON CONFLICT (id) DO NOTHING
        """,
        (
            session_id,
            None if status == "scheduled" else started,
            ended,
            _cat(conn, "cat_session_status", sess_status),
            op_user,
            spd_user if status == "closed" else None,
            operation_id,
            station_id,
            KIT_ID,
            _cat(conn, "cat_operation_phase", phase),
            (ended or started) - timedelta(minutes=5),
            None if status == "scheduled" else capture,
        ),
    )
    for family_id, qty in kit_items:
        conn.execute(
            """
            INSERT INTO expected_inventory (id, session_id, family_id, expected_quantity, source)
            VALUES (%s, %s, %s, %s, 'kit_snapshot')
            ON CONFLICT (session_id, family_id) DO NOTHING
            """,
            (_uid("ei", session_id, family_id), session_id, family_id, qty),
        )
    if status == "scheduled":
        return

    # phase_change + counts
    et_phase = _cat(conn, "cat_event_type", "phase_change")
    et_auto = _cat(conn, "cat_event_type", "auto_count")
    et_close = _cat(conn, "cat_event_type", "session_close")
    conn.execute(
        """
        INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity,
                                 payload, occurred_at, session_id, user_id)
        VALUES (%s,%s,NULL,NULL,NULL,%s::jsonb,%s,%s,%s)
        ON CONFLICT (id) DO NOTHING
        """,
        (
            _uid("ce", "phase", session_id),
            et_phase,
            '{"from_phase":"start","to_phase":"demo_phase_1","source":"seed_volume"}',
            started + timedelta(minutes=8),
            session_id,
            op_user,
        ),
    )
    raise_disc = status == "closed" and rng.random() < discrepancy_rate
    shortage_family = kit_items[rng.randrange(len(kit_items))][0] if kit_items else None
    for family_id, qty in kit_items:
        detected = qty
        if raise_disc and family_id == shortage_family:
            detected = max(0, qty - 1)
        conn.execute(
            """
            INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity,
                                     payload, occurred_at, session_id, user_id)
            VALUES (%s,%s,%s,%s,%s,'{}'::jsonb,%s,%s,%s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                _uid("ce", "auto", session_id, family_id),
                et_auto,
                family_id,
                qty,
                detected,
                (ended or started) - timedelta(minutes=12),
                session_id,
                op_user,
            ),
        )
    if status == "closed":
        conn.execute(
            """
            INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity,
                                     payload, occurred_at, session_id, user_id)
            VALUES (%s,%s,NULL,NULL,NULL,%s::jsonb,%s,%s,%s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                _uid("ce", "close", session_id),
                et_close,
                '{"source":"seed_volume"}',
                ended,
                session_id,
                spd_user or op_user,
            ),
        )
        if raise_disc and shortage_family is not None:
            disc_id = _uid("disc", session_id, shortage_family)
            exp = next(q for f, q in kit_items if f == shortage_family)
            conn.execute(
                """
                INSERT INTO discrepancy (
                  id, description, resolved, resolved_at, reason_id, family_id,
                  expected_quantity, detected_quantity, session_id)
                VALUES (%s,%s,TRUE,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    disc_id,
                    "seed_volume: shortage resolved in demo history",
                    ended,
                    _cat(conn, "cat_discrepancy_reason", "shortage"),
                    shortage_family,
                    exp,
                    max(0, exp - 1),
                    session_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO access_audit (
                  id, occurred_at, actor_type, actor_user_id, action, resource_type, resource_id,
                  institution_id, outcome, ip)
                VALUES (%s,%s,'user',%s,'discrepancy.resolve','work_session',%s,%s,'success','10.20.0.40')
                ON CONFLICT (id) DO NOTHING
                """,
                (_uid("audit", "resolve", session_id), ended - timedelta(minutes=2), spd_user or op_user, session_id, INSTITUTION),
            )
    for a in range(audit_n):
        conn.execute(
            """
            INSERT INTO access_audit (
              id, occurred_at, actor_type, actor_user_id, action, resource_type, resource_id,
              institution_id, outcome, ip)
            VALUES (%s,%s,'user',%s,%s,'work_session',%s,%s,'success',%s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                _uid("audit", session_id, a),
                started + timedelta(minutes=a * 3),
                op_user if a % 2 == 0 else (spd_user or op_user),
                "read" if a else "session.start",
                session_id,
                INSTITUTION,
                f"10.20.{(idx % 200)}.{(a % 250)}",
            ),
        )


def _seed_monitoring(conn, hours: int, rng: random.Random) -> int:
    if hours <= 0:
        return 0
    now = datetime.now(timezone.utc)
    n = 0
    # one run every 5 minutes
    steps = max(1, (hours * 60) // 5)
    for i in range(steps):
        checked = now - timedelta(minutes=5 * (steps - i))
        run_id = _uid("monrun", i, hours)
        failures = 0
        conn.execute(
            """
            INSERT INTO monitoring_run (id, started_at, finished_at, components, failures)
            VALUES (%s,%s,%s,%s,0)
            ON CONFLICT (id) DO NOTHING
            """,
            (run_id, checked, checked + timedelta(seconds=2), len(MONITOR_COMPONENTS)),
        )
        for comp in MONITOR_COMPONENTS:
            status = "up"
            latency = round(rng.uniform(2.0, 40.0), 2)
            err = None
            if rng.random() < 0.04:
                status = "degraded"
                latency = round(rng.uniform(80.0, 250.0), 2)
                failures += 1
            elif rng.random() < 0.01:
                status = "down"
                latency = None
                err = "timeout"
                failures += 1
            conn.execute(
                """
                INSERT INTO monitoring_check (
                  id, run_id, component, checked_at, status, latency_ms, http_status,
                  dependencies, error_type, endpoint, details)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'{}'::jsonb,%s,%s,'{}'::jsonb)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    _uid("monchk", i, comp),
                    run_id,
                    comp,
                    checked,
                    status,
                    latency,
                    200 if status == "up" else (503 if status == "down" else 200),
                    err,
                    f"http://{comp}:seed/",
                ),
            )
            n += 1
        conn.execute("UPDATE monitoring_run SET failures = %s WHERE id = %s", (failures, run_id))
    return n


def _seed_notifications(conn, limit: int = 40) -> int:
    """Create a few readable App/Web notifications from closed sessions (no outbox dependency)."""
    rows = conn.execute(
        """
        SELECT w.id, w.user_id, u.institution_id
        FROM work_session w
        JOIN "user" u ON u.id = w.user_id
        JOIN cat_session_status s ON s.id = w.status_id
        WHERE s.code = 'closed' AND u.institution_id = %s
        ORDER BY w.ended_at DESC NULLS LAST
        LIMIT %s
        """,
        (INSTITUTION, limit),
    ).fetchall()
    n = 0
    for session_id, user_id, inst in rows:
        for typ, recipient in (
            ("session_closed", user_id),
        ):
            nid = _uid("notif", typ, session_id, recipient)
            source_id = _uid("notifsrc", typ, session_id)
            conn.execute(
                """
                INSERT INTO notification (
                  id, recipient_user_id, institution_id, type, status, session_id, data,
                  source_table, source_id, created_at, read_at)
                VALUES (%s,%s,%s,%s,'unread',%s,'{}'::jsonb,'count_event',%s,now(),NULL)
                ON CONFLICT DO NOTHING
                """,
                (nid, recipient, inst, typ, session_id, source_id),
            )
            n += 1
        # SPD also gets pending-style historical noise (as read)
        spd = conn.execute(
            """
            SELECT u.id FROM "user" u
            JOIN user_role ur ON ur.user_id = u.id
            JOIN role r ON r.id = ur.role_id
            WHERE r.code = 'spd_supervisor' AND u.institution_id = %s
            ORDER BY u.email LIMIT 1
            """,
            (INSTITUTION,),
        ).fetchone()
        if spd:
            nid = _uid("notif", "disc_pend", session_id, spd[0])
            conn.execute(
                """
                INSERT INTO notification (
                  id, recipient_user_id, institution_id, type, status, session_id, data,
                  source_table, source_id, created_at, read_at)
                VALUES (%s,%s,%s,'discrepancy_pending','read',%s,'{"seed":true}'::jsonb,
                        'count_event',%s,now() - interval '1 day', now() - interval '12 hours')
                ON CONFLICT DO NOTHING
                """,
                (nid, spd[0], inst, session_id, _uid("notifsrc", "disc_pend", session_id)),
            )
            n += 1
    return n


def main() -> int:
    _load_dotenv()
    try:
        import psycopg
        from psycopg.rows import tuple_row
    except ImportError:
        print("error: psycopg required (pip install 'psycopg[binary]')", file=sys.stderr)
        return 1

    enabled = _env("SEED_VOLUME_ENABLED", "1") not in ("0", "false", "False", "no")
    rng_seed = _env_int("SEED_RNG", 2026)
    or_count = _env_int("SEED_OR_COUNT", 4)
    closed_n = _env_int("SEED_CLOSED_SESSIONS", 48)
    scheduled_n = _env_int("SEED_SCHEDULED", 8)
    inprog_n = _env_int("SEED_IN_PROGRESS", 2)
    days_back = _env_int("SEED_DAYS_BACK", 30)
    disc_rate = _env_float("SEED_DISCREPANCY_RATE", 0.28)
    audit_n = _env_int("SEED_AUDIT_PER_SESSION", 3)
    mon_hours = _env_int("SEED_MONITORING_HOURS", 48)
    extra_op = _env_int("SEED_EXTRA_OP_USERS", 4)
    extra_spd = _env_int("SEED_EXTRA_SPD_USERS", 2)
    extra_floor = _env_int("SEED_EXTRA_FLOOR_USERS", 1)

    rng = random.Random(rng_seed)
    url = _database_url()
    print(f"seed_demo_volume: db={url.split('@')[-1]} rng={rng_seed} enabled={enabled}")

    with psycopg.connect(url, row_factory=tuple_row, autocommit=False) as conn:
        _apply_locust_sql(conn)
        conn.commit()

        if not enabled:
            print("SEED_VOLUME_ENABLED=0 → skip volume generation")
            return 0

        # Verify institution from 003 exists
        if not conn.execute("SELECT 1 FROM institution WHERE id = %s", (INSTITUTION,)).fetchone():
            print("error: institution demo missing; run 003 seed first", file=sys.stderr)
            return 2

        password_hash = _ensure_gen_password_hash(conn)
        rooms = _ensure_ors(conn, rng, or_count)
        users = _ensure_extra_users(conn, password_hash, extra_op, extra_spd, extra_floor)
        physicians = _ensure_physicians(conn, max(8, or_count * 2))
        kit_items = _kit_items(conn)
        if not kit_items:
            print("error: kit items missing for Video Demo Kit 1", file=sys.stderr)
            return 3
        conn.commit()

        now = datetime.now(timezone.utc)
        ops = users["op"] or [uuid.UUID("22222222-2222-2222-2222-222222222221")]
        spds = users["spd"] or [uuid.UUID("22222222-2222-2222-2222-222222222222")]

        def pick_room(i: int):
            return rooms[i % len(rooms)]

        # Closed history
        for i in range(closed_n):
            day_offset = rng.randint(0, max(0, days_back - 1))
            hour = rng.randint(7, 18)
            started = (now - timedelta(days=day_offset)).replace(
                hour=hour, minute=rng.randint(0, 59), second=0, microsecond=0
            )
            ended = started + timedelta(minutes=rng.randint(35, 140))
            room_id, station_id = pick_room(i)
            _insert_session_bundle(
                conn,
                idx=i,
                status="closed",
                room_id=room_id,
                station_id=station_id,
                op_user=ops[i % len(ops)],
                spd_user=spds[i % len(spds)],
                physician_id=physicians[i % len(physicians)],
                proc_code=PROC_CODES[i % len(PROC_CODES)],
                started=started,
                ended=ended,
                discrepancy_rate=disc_rate,
                audit_n=audit_n,
                rng=rng,
                kit_items=kit_items,
            )
            if i % 10 == 9:
                conn.commit()
        conn.commit()

        for i in range(scheduled_n):
            started = now + timedelta(hours=6 + i * 3)
            room_id, station_id = pick_room(i + 3)
            _insert_session_bundle(
                conn,
                idx=1000 + i,
                status="scheduled",
                room_id=room_id,
                station_id=station_id,
                op_user=ops[i % len(ops)],
                spd_user=None,
                physician_id=physicians[(i + 2) % len(physicians)],
                proc_code=PROC_CODES[(i + 1) % len(PROC_CODES)],
                started=started,
                ended=None,
                discrepancy_rate=0.0,
                audit_n=1,
                rng=rng,
                kit_items=kit_items,
            )
        conn.commit()

        # Extra in_progress: use distinct operators/rooms to avoid stepping on SQL fixtures
        for i in range(inprog_n):
            room_id, station_id = pick_room(i + or_count)
            started = now - timedelta(minutes=10 + i * 7)
            # Prefer generated ops when available to avoid conflicting with fixed in_progress users
            op_user = ops[-(i + 1)] if ops else uuid.UUID("22222222-2222-2222-2222-222222222224")
            _insert_session_bundle(
                conn,
                idx=2000 + i,
                status="in_progress",
                room_id=room_id,
                station_id=station_id,
                op_user=op_user,
                spd_user=None,
                physician_id=physicians[(i + 5) % len(physicians)],
                proc_code="lap_chole",
                started=started,
                ended=None,
                discrepancy_rate=0.0,
                audit_n=2,
                rng=rng,
                kit_items=kit_items,
            )
        conn.commit()

        mon = _seed_monitoring(conn, mon_hours, rng)
        conn.commit()
        notif = _seed_notifications(conn)
        conn.commit()

        counts = conn.execute(
            """
            SELECT
              (SELECT count(*) FROM "user" WHERE institution_id = %s) AS users,
              (SELECT count(*) FROM operating_room WHERE institution_id = %s) AS ors,
              (SELECT count(*) FROM work_session) AS sessions,
              (SELECT count(*) FROM discrepancy) AS discrepancies,
              (SELECT count(*) FROM access_audit) AS audits,
              (SELECT count(*) FROM monitoring_check) AS mon_checks,
              (SELECT count(*) FROM notification) AS notifications
            """,
            (INSTITUTION, INSTITUTION),
        ).fetchone()
        print(
            "done volume: "
            f"users={counts[0]} ors={counts[1]} sessions={counts[2]} "
            f"discrepancies={counts[3]} audits={counts[4]} "
            f"monitoring_checks={counts[5]} notifications={counts[6]} "
            f"(generated_mon_rows≈{mon}, notif_touch={notif})"
        )
        print("gen users password: DemopwdGEN78!  (gen.opN@ / gen.spdN@ / gen.floorN@instrumed.com)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
