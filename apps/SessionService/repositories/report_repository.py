"""Read-only aggregates of the Supervisor reports (PEF-DS09 §1.3.4 complement v0.3.0).

Every query starts from the same filtered set of sessions (`_SESSIONS`): sessions whose operation
belongs to the caller's institution AND is scheduled inside the period, optionally restricted to a
kit, an operator and a status. Tenant isolation is therefore part of the WHERE clause of every
query, never a post-filter.

Definitions (also in GUIA_REPORTES.md):
- period: operation.scheduled_at in [from, to) — sessions without a scheduled operation are not
  reported (they have no date in the agenda);
- duration: ended_at - started_at, the Operator's counting (start -> report), over sessions that
  have both;
- reported session: ended_at IS NOT NULL (the Operator finished the count);
- % sessions with discrepancies: reported sessions with >= 1 discrepancy / reported sessions;
- discrepancy resolution time: resolved_at - COALESCE(origin count event, session start), the same
  formula as the Administration dashboard;
- human reported quantity of a family: the LAST manual_count of that session and family
  (detected_quantity, or the numeric payload.reported_quantity of legacy rows);
- AI-human pairs: the corrected rule of the Administration dashboard (services/admin_local/
  dashboard.py, AI_HUMAN_AGREEMENT_SQL): each vision manual_count compared with the YOLO suggestion
  it validated (payload.ai_suggested_quantity, or the last final auto_count lot before it) — never
  with the expected inventory. A pair whose quantities differ is a human correction.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

_SESSIONS = """
    s AS (
        SELECT ws.id, ws.user_id, ws.kit_id, ws.started_at, ws.ended_at, st.code AS status_code,
               op.scheduled_at,
               EXISTS (SELECT 1 FROM discrepancy d WHERE d.session_id = ws.id) AS has_discrepancy
        FROM work_session ws
        JOIN operation op ON op.id = ws.operation_id
        JOIN cat_session_status st ON st.id = ws.status_id
        WHERE op.institution_id = :institution_id
          AND op.scheduled_at >= :date_from AND op.scheduled_at < :date_to
          AND (CAST(:kit_id AS uuid) IS NULL OR ws.kit_id = :kit_id)
          AND (CAST(:operator_id AS uuid) IS NULL OR ws.user_id = :operator_id)
          AND (CAST(:status AS text) IS NULL OR st.code = :status)
    )"""

# Same pair selection as AI_HUMAN_AGREEMENT_SQL (Administration dashboard), restricted to `s`.
_PAIRS = """
    pairs AS (
        SELECT pair.session_id, pair.human_qty, pair.ai_qty FROM (
            SELECT ce.session_id,
                   COALESCE(ce.detected_quantity, (ce.payload ->> 'reported_quantity')::int) AS human_qty,
                   COALESCE(
                       (ce.payload ->> 'ai_suggested_quantity')::int,
                       CASE WHEN final_lot.payload IS NOT NULL THEN COALESCE((
                           SELECT (fam ->> 'detected_quantity')::int
                           FROM jsonb_array_elements(COALESCE(final_lot.payload -> 'board' -> 'families',
                                                              final_lot.payload -> 'counts', '[]'::jsonb)) fam
                           WHERE fam ->> 'family_id' = ce.family_id::text
                           LIMIT 1
                       ), 0) END
                   ) AS ai_qty
            FROM count_event ce
            JOIN s ON s.id = ce.session_id
            JOIN cat_event_type et ON et.id = ce.event_type_id
            LEFT JOIN LATERAL (
                SELECT ac.payload
                FROM count_event ac
                JOIN cat_event_type aet ON aet.id = ac.event_type_id
                WHERE aet.code = 'auto_count' AND ac.session_id = ce.session_id
                  AND ac.family_id IS NULL AND ac.occurred_at <= ce.occurred_at
                  AND ((ac.payload ->> 'trigger') = 'final'
                       OR (ac.payload ->> 'trigger' IS NULL AND (ac.payload ->> 'reason') = 'detection_close'))
                ORDER BY ac.occurred_at DESC
                LIMIT 1
            ) final_lot ON TRUE
            WHERE et.code = 'manual_count' AND ce.family_id IS NOT NULL
              AND (ce.payload ->> 'ai') = 'true'
        ) pair
        WHERE pair.human_qty IS NOT NULL AND pair.ai_qty IS NOT NULL
    )"""

# Last human count of each (session, family): the quantity the Operator reported.
_REPORTED = """
    reported AS (
        SELECT DISTINCT ON (ce.session_id, ce.family_id) ce.session_id, ce.family_id,
               COALESCE(ce.detected_quantity,
                        CASE WHEN (ce.payload ->> 'reported_quantity') ~ '^[0-9]+$'
                             THEN (ce.payload ->> 'reported_quantity')::int END) AS qty
        FROM count_event ce
        JOIN s ON s.id = ce.session_id
        JOIN cat_event_type et ON et.id = ce.event_type_id
        WHERE et.code = 'manual_count' AND ce.family_id IS NOT NULL
        ORDER BY ce.session_id, ce.family_id, ce.occurred_at DESC, ce.id DESC
    )"""


def _rows(connection: Connection, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(text(sql), params).mappings().all()]


def session_summary(connection: Connection, params: dict[str, Any]) -> dict[str, Any]:
    return dict(connection.execute(text(f"""
        WITH {_SESSIONS}
        SELECT count(*) AS total,
               count(*) FILTER (WHERE status_code = 'scheduled') AS scheduled,
               count(*) FILTER (WHERE status_code = 'in_progress') AS in_progress,
               count(*) FILTER (WHERE status_code = 'awaiting_spd_review') AS awaiting_spd_review,
               count(*) FILTER (WHERE status_code = 'correction_required') AS correction_required,
               count(*) FILTER (WHERE status_code = 'closed') AS closed,
               count(*) FILTER (WHERE status_code = 'aborted') AS aborted,
               count(*) FILTER (WHERE ended_at IS NOT NULL) AS reported,
               count(*) FILTER (WHERE ended_at IS NOT NULL AND has_discrepancy) AS reported_with_discrepancies,
               count(*) FILTER (WHERE started_at IS NOT NULL AND ended_at IS NOT NULL AND ended_at >= started_at)
                   AS timed,
               avg(EXTRACT(EPOCH FROM (ended_at - started_at)) / 60.0)
                   FILTER (WHERE started_at IS NOT NULL AND ended_at IS NOT NULL AND ended_at >= started_at)
                   AS avg_duration_minutes
        FROM s
    """), params).mappings().one())


def activity(connection: Connection, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Sessions per day of their scheduled date (in the requested time zone)."""
    return _rows(connection, f"""
        WITH {_SESSIONS}
        SELECT (scheduled_at AT TIME ZONE :tz)::date AS day,
               count(*) AS sessions,
               count(*) FILTER (WHERE ended_at IS NOT NULL) AS reported,
               count(*) FILTER (WHERE has_discrepancy) AS with_discrepancies
        FROM s GROUP BY 1 ORDER BY 1
    """, params)


def discrepancy_summary(connection: Connection, params: dict[str, Any]) -> dict[str, Any]:
    return dict(connection.execute(text(f"""
        WITH {_SESSIONS}
        SELECT count(*) AS total,
               count(*) FILTER (WHERE NOT d.resolved) AS open,
               count(*) FILTER (WHERE d.resolved) AS resolved,
               count(*) FILTER (WHERE d.resolved AND d.resolved_at IS NOT NULL
                                AND COALESCE(ce.occurred_at, s.started_at) IS NOT NULL) AS timed,
               avg(EXTRACT(EPOCH FROM (d.resolved_at - COALESCE(ce.occurred_at, s.started_at))) / 60.0)
                   FILTER (WHERE d.resolved AND d.resolved_at IS NOT NULL
                           AND COALESCE(ce.occurred_at, s.started_at) IS NOT NULL) AS avg_resolution_minutes
        FROM discrepancy d
        JOIN s ON s.id = d.session_id
        LEFT JOIN count_event ce ON ce.id = d.origin_event_id
    """), params).mappings().one())


def discrepancies_by_reason(connection: Connection, params: dict[str, Any]) -> list[dict[str, Any]]:
    return _rows(connection, f"""
        WITH {_SESSIONS}
        SELECT r.code, r.name, count(*) AS total, count(*) FILTER (WHERE NOT d.resolved) AS open
        FROM discrepancy d
        JOIN s ON s.id = d.session_id
        LEFT JOIN cat_discrepancy_reason r ON r.id = d.reason_id
        GROUP BY r.code, r.name
        ORDER BY count(*) DESC, r.code NULLS LAST
    """, params)


def discrepancies_by_family(connection: Connection, params: dict[str, Any]) -> list[dict[str, Any]]:
    return _rows(connection, f"""
        WITH {_SESSIONS}
        SELECT f.id AS family_id, f.code, f.name, count(*) AS total, count(*) FILTER (WHERE NOT d.resolved) AS open
        FROM discrepancy d
        JOIN s ON s.id = d.session_id
        LEFT JOIN instrument_family f ON f.id = d.family_id
        GROUP BY f.id, f.code, f.name
        ORDER BY count(*) DESC, f.name NULLS LAST
    """, params)


def ai_agreement(connection: Connection, params: dict[str, Any]) -> dict[str, Any]:
    return dict(connection.execute(text(f"""
        WITH {_SESSIONS}, {_PAIRS}
        SELECT count(*) AS pairs, count(*) FILTER (WHERE human_qty = ai_qty) AS agreed FROM pairs
    """), params).mappings().one())


_KIT_ROWS = f"""
    WITH {_SESSIONS}, {_REPORTED},
    expected AS (
        SELECT ei.session_id, sum(ei.expected_quantity) AS qty
        FROM expected_inventory ei JOIN s ON s.id = ei.session_id GROUP BY ei.session_id
    ),
    reported_total AS (SELECT session_id, sum(qty) AS qty FROM reported GROUP BY session_id),
    disc AS (SELECT d.session_id, count(*) AS n FROM discrepancy d JOIN s ON s.id = d.session_id GROUP BY d.session_id),
    per_kit AS (
        SELECT s.kit_id,
               count(*) AS sessions,
               count(*) FILTER (WHERE s.ended_at IS NOT NULL) AS reported_sessions,
               count(*) FILTER (WHERE s.has_discrepancy) AS sessions_with_discrepancies,
               COALESCE(sum(disc.n), 0) AS discrepancies,
               sum(expected.qty) AS expected_quantity,
               sum(expected.qty) FILTER (WHERE rt.session_id IS NOT NULL) AS expected_quantity_counted,
               sum(rt.qty) AS reported_quantity
        FROM s
        LEFT JOIN expected ON expected.session_id = s.id
        LEFT JOIN reported_total rt ON rt.session_id = s.id
        LEFT JOIN disc ON disc.session_id = s.id
        GROUP BY s.kit_id
    )
"""


def kit_activity(connection: Connection, params: dict[str, Any], *, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
    total = connection.execute(text(f"{_KIT_ROWS} SELECT count(*) FROM per_kit"), params).scalar_one()
    rows = _rows(connection, f"""{_KIT_ROWS}
        SELECT pk.*, k.name AS kit_name, k.version AS kit_version, k.active AS kit_active
        FROM per_kit pk LEFT JOIN kit k ON k.id = pk.kit_id
        ORDER BY pk.sessions DESC, k.name NULLS LAST, pk.kit_id
        LIMIT :limit OFFSET :offset
    """, {**params, "limit": limit, "offset": offset})
    return total, rows


_OPERATOR_ROWS = f"""
    WITH {_SESSIONS}, {_PAIRS}, {_REPORTED},
    disc AS (SELECT d.session_id, count(*) AS n FROM discrepancy d JOIN s ON s.id = d.session_id GROUP BY d.session_id),
    family_counts AS (SELECT session_id, count(*) AS n FROM reported GROUP BY session_id),
    pair_stats AS (
        SELECT session_id, count(*) AS pairs, count(*) FILTER (WHERE human_qty = ai_qty) AS agreed
        FROM pairs GROUP BY session_id
    ),
    per_operator AS (
        SELECT s.user_id,
               count(*) AS sessions,
               count(*) FILTER (WHERE s.started_at IS NOT NULL) AS started_sessions,
               count(*) FILTER (WHERE s.ended_at IS NOT NULL) AS counts_completed,
               COALESCE(sum(fc.n), 0) AS family_counts,
               COALESCE(sum(ps.pairs), 0) AS ai_pairs,
               COALESCE(sum(ps.agreed), 0) AS ai_agreed,
               COALESCE(sum(disc.n), 0) AS discrepancies
        FROM s
        LEFT JOIN disc ON disc.session_id = s.id
        LEFT JOIN family_counts fc ON fc.session_id = s.id
        LEFT JOIN pair_stats ps ON ps.session_id = s.id
        GROUP BY s.user_id
    )
"""


def operator_activity(connection: Connection, params: dict[str, Any], *, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
    total = connection.execute(text(f"{_OPERATOR_ROWS} SELECT count(*) FROM per_operator"), params).scalar_one()
    rows = _rows(connection, f"""{_OPERATOR_ROWS}
        SELECT po.*, u.name AS operator_name
        FROM per_operator po JOIN "user" u ON u.id = po.user_id
        ORDER BY po.sessions DESC, u.name, po.user_id
        LIMIT :limit OFFSET :offset
    """, {**params, "limit": limit, "offset": offset})
    return total, rows


def filter_options(connection: Connection, institution_id) -> dict[str, list[dict[str, Any]]]:
    """Kits of the institution and its operators (active or not: history may include both)."""
    kits = _rows(connection, """
        SELECT id, name, version, active FROM kit WHERE institution_id = :i ORDER BY active DESC, name, id
    """, {"i": institution_id})
    operators = _rows(connection, """
        SELECT DISTINCT u.id, u.name, u.active
        FROM "user" u
        JOIN user_role ur ON ur.user_id = u.id
        JOIN role r ON r.id = ur.role_id AND r.institution_id = u.institution_id
        WHERE u.institution_id = :i AND r.code = 'station_operator'
        ORDER BY u.active DESC, u.name, u.id
    """, {"i": institution_id})
    return {"kits": kits, "operators": operators}


def kit_in_institution(connection: Connection, kit_id, institution_id) -> bool:
    return connection.execute(text("SELECT 1 FROM kit WHERE id = :k AND institution_id = :i"),
                              {"k": kit_id, "i": institution_id}).first() is not None


def user_in_institution(connection: Connection, user_id, institution_id) -> bool:
    return connection.execute(text('SELECT 1 FROM "user" WHERE id = :u AND institution_id = :i'),
                              {"u": user_id, "i": institution_id}).first() is not None


def valid_time_zone(connection: Connection, name: str) -> bool:
    return connection.execute(text("SELECT 1 FROM pg_timezone_names WHERE name = :n"), {"n": name}).first() is not None
