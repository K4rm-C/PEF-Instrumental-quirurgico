"""MS-12 (ms-auditoria): consistency of the audit trail written by EVERY service into access_audit.

Read-only. Checks the rows of a time window (default: last 24 h) and prints a JSON report; exit code 1 when a HARD
rule is violated, 0 otherwise (warnings never fail):

HARD (a broken trail):
  actor_institution     the actor (user or machine client) belongs to the institution recorded on the row;
  resource_institution  for resources owned by an institution (session, kit, tray, camera, room, export batch,
                        kit equivalence, FHIR endpoint / delivery) the resource belongs to that institution;
  duplicates            no two identical rows (action, resource, actor, correlation id, instant).
WARN (to review, not a fault by itself):
  missing_institution   rows without institution (e.g. failed sign-ins of unknown users);
  missing_resource      the audited resource no longer exists (deleted afterwards, or never existed);
  action_styles         actions per naming style: UPPER_SNAKE (Administration, D-5) and dotted (session.*); the
                        unification of the naming is pending decision D-5, so both are accepted and only counted.

    python scripts/check_audit_consistency.py --database-url postgresql+psycopg://... [--since 2026-11-10T00:00:00Z]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text

# resource_type -> SQL giving the institution of :rid (only resources owned by ONE institution).
OWNED = {
    "work_session": "SELECT o.institution_id FROM work_session w JOIN operation o ON o.id = w.operation_id WHERE w.id = :rid",
    "kit": "SELECT institution_id FROM kit WHERE id = :rid",
    "surgical_tray": "SELECT institution_id FROM surgical_tray WHERE id = :rid",
    "capture_camera": "SELECT institution_id FROM capture_camera WHERE id = :rid",
    "operating_room": "SELECT institution_id FROM operating_room WHERE id = :rid",
    "kit_equivalence": "SELECT institution_id FROM kit_item_equivalence WHERE id = :rid",
    "integration_export_batch": "SELECT institution_id FROM integration_export_batch WHERE id = :rid",
    "integration_endpoint": "SELECT institution_id FROM integration_endpoint WHERE id = :rid",
    "integration_delivery": "SELECT institution_id FROM integration_delivery WHERE id = :rid",
}
NIL = "00000000-0000-0000-0000-000000000000"
UPPER = re.compile(r"^[A-Z][A-Z0-9_]+$")
DOTTED = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


def check(engine, since: datetime) -> dict:
    report = {"since": since.isoformat(), "rows": 0, "hard": {}, "warn": {}, "action_styles": {}}
    with engine.connect() as c:
        exists = {t: bool(c.execute(text("SELECT to_regclass(:t)"), {"t": t}).scalar())
                  for t in ("kit_item_equivalence", "integration_export_batch", "integration_endpoint",
                            "integration_delivery", "surgical_tray", "capture_camera")}
        report["rows"] = c.execute(text("SELECT count(*) FROM access_audit WHERE occurred_at >= :s"), {"s": since}).scalar()
        report["hard"]["actor_institution"] = [dict(r) for r in c.execute(text("""
            SELECT a.id::text, a.action, a.actor_type, a.institution_id::text AS row_institution,
                   COALESCE(u.institution_id, ic.institution_id)::text AS actor_institution
            FROM access_audit a
            LEFT JOIN "user" u ON a.actor_type = 'user' AND u.id = a.actor_user_id
            LEFT JOIN integration_client ic ON a.actor_type = 'client' AND ic.id = a.actor_client_id
            WHERE a.occurred_at >= :s AND a.institution_id IS NOT NULL
              AND COALESCE(u.institution_id, ic.institution_id) IS NOT NULL
              AND COALESCE(u.institution_id, ic.institution_id) <> a.institution_id
            LIMIT 50"""), {"s": since}).mappings()]
        report["hard"]["duplicates"] = [dict(r) for r in c.execute(text("""
            SELECT action, resource_id::text, correlation_id, occurred_at::text, count(*) AS n FROM access_audit
            WHERE occurred_at >= :s
            GROUP BY action, resource_type, resource_id, actor_type, actor_user_id, actor_client_id, correlation_id,
                     occurred_at, outcome
            HAVING count(*) > 1 LIMIT 50"""), {"s": since}).mappings()]
        mismatched, missing = [], []
        rows = c.execute(text("""
            SELECT id::text, action, resource_type, resource_id::text, institution_id::text FROM access_audit
            WHERE occurred_at >= :s AND resource_type = ANY(:types) AND resource_id <> CAST(:nil AS uuid)"""),
                         {"s": since, "types": list(OWNED), "nil": NIL}).mappings().all()
        for row in rows:
            table = {"kit_equivalence": "kit_item_equivalence"}.get(row["resource_type"], row["resource_type"])
            if table in exists and not exists[table]:
                continue
            owner = c.execute(text(OWNED[row["resource_type"]]), {"rid": row["resource_id"]}).first()
            if owner is None:
                missing.append(dict(row))
            elif row["institution_id"] is not None and str(owner[0]) != row["institution_id"]:
                mismatched.append({**dict(row), "resource_institution": str(owner[0])})
        report["hard"]["resource_institution"] = mismatched[:50]
        report["warn"]["missing_resource"] = {"count": len(missing), "sample": missing[:10]}
        report["warn"]["missing_institution"] = c.execute(text(
            "SELECT count(*) FROM access_audit WHERE occurred_at >= :s AND institution_id IS NULL"), {"s": since}).scalar()
        styles = {"upper_snake": 0, "dotted": 0, "other": []}
        for action, n in c.execute(text("SELECT action, count(*) FROM access_audit WHERE occurred_at >= :s GROUP BY action"),
                                   {"s": since}):
            if UPPER.match(action):
                styles["upper_snake"] += n
            elif DOTTED.match(action):
                styles["dotted"] += n
            else:
                styles["other"].append(action)
        report["action_styles"] = styles
    report["ok"] = not any(report["hard"].values())
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--since", help="ISO date-time (default: 24 h ago)")
    args = parser.parse_args()
    since = (datetime.fromisoformat(args.since.replace("Z", "+00:00")) if args.since
             else datetime.now(timezone.utc) - timedelta(hours=24))
    engine = create_engine(args.database_url, future=True)
    try:
        report = check(engine, since)
    finally:
        engine.dispose()
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
