from sqlalchemy import text as sql

from .database import db


# AI-human agreement per family row: the human count of a vision manual_count compared with
# the AI suggestion it was validated against (DS04: the last auto_count with trigger=final).
# Not against the expected quantity, and not via human_correction (legacy portal rows record a
# correction whenever the report differs from the expected quantity). Rows written before
# payload.ai_suggested_quantity existed take the suggestion from the last final lot before them;
# rows without any AI suggestion are left out instead of counting as agreement.
# Restricted to the sessions of the administrator's institution (TENANT_SESSIONS); the pair rule itself
# is identical to SessionService's reports (apps/SessionService/tests/test_reports.py compares them).
AI_HUMAN_AGREEMENT_SQL = """
    SELECT count(*) AS total,
           count(*) FILTER (WHERE pair.human_qty = pair.ai_qty) AS agreed
    FROM (
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
      AND pair.session_id IN (TENANT_SESSIONS)
"""


# Operational data (sessions, counts, discrepancies) belongs to an institution through its operation;
# the administrator sees only their own, as in AuditService. Sessions without an operation have no
# tenant and are not counted. Users, roles, institutions, families, procedures and vision models stay
# global on purpose: the administrator manages them for every institution (GUIA_AUTH.md, GUIA_CATALOG.md).
TENANT_SESSIONS_SQL = (
    "SELECT ws.id FROM work_session ws JOIN operation op ON op.id = ws.operation_id "
    "WHERE op.institution_id = :inst"
)
AI_HUMAN_AGREEMENT_SQL = AI_HUMAN_AGREEMENT_SQL.replace("TENANT_SESSIONS", TENANT_SESSIONS_SQL)


def _scalar(query, **params):
    return db.execute(sql(query), params).scalar() or 0


def overview(actor):
    inst = {'inst': actor.institution_id}
    active_model = db.execute(sql(
        "SELECT id, version_tag FROM yolo_model WHERE active = TRUE LIMIT 1"
    )).first()

    vision_counts = db.execute(sql(AI_HUMAN_AGREEMENT_SQL), inst).mappings().first()
    agreement = None
    if vision_counts and vision_counts['total']:
        agreement = round(100.0 * vision_counts['agreed'] / vision_counts['total'], 1)

    avg_resolution = db.execute(sql("""
        SELECT avg(extract(epoch FROM (d.resolved_at - COALESCE(ce.occurred_at, ws.started_at))) / 60.0)
        FROM discrepancy d
        LEFT JOIN count_event ce ON ce.id = d.origin_event_id
        JOIN work_session ws ON ws.id = d.session_id
        JOIN operation op ON op.id = ws.operation_id
        WHERE d.resolved = TRUE AND d.resolved_at IS NOT NULL
          AND COALESCE(ce.occurred_at, ws.started_at) IS NOT NULL
          AND op.institution_id = :inst
    """), inst).scalar()

    return {
        'stats': {
            'active_users': _scalar('SELECT count(*) FROM "user" WHERE active = TRUE'),
            'active_families': _scalar('SELECT count(*) FROM instrument_family WHERE active = TRUE'),
            'registered_instruments': _scalar(
                'SELECT count(*) FROM instrument WHERE institution_id = :inst', **inst),
            'active_instruments': _scalar(
                'SELECT count(*) FROM instrument WHERE institution_id = :inst AND active = TRUE', **inst),
            'active_kits': _scalar(
                'SELECT count(*) FROM kit WHERE institution_id = :inst AND active = TRUE', **inst),
            'procedures': _scalar('SELECT count(*) FROM cat_procedure_type'),
            'capture_stations': _scalar(
                'SELECT count(*) FROM capture_station cs JOIN operating_room r ON r.id = cs.room_id '
                'WHERE r.institution_id = :inst', **inst),
            'active_capture_stations': _scalar(
                'SELECT count(*) FROM capture_station cs JOIN operating_room r ON r.id = cs.room_id '
                'WHERE r.institution_id = :inst AND cs.active = TRUE', **inst),
            'vision_models': _scalar('SELECT count(*) FROM yolo_model'),
            'active_vision_model': 1 if active_model else 0,
            'inactive_users': _scalar('SELECT count(*) FROM "user" WHERE active = FALSE'),
            'roles': _scalar('SELECT count(*) FROM role'),
            'operating_rooms': _scalar(
                'SELECT count(*) FROM operating_room WHERE institution_id = :inst', **inst),
            'active_institutions': _scalar('SELECT count(*) FROM institution WHERE active = TRUE'),
        },
        'active_model': {
            'id': str(active_model[0]),
            'version_tag': active_model[1],
            'class_count': _scalar('SELECT count(*) FROM model_class WHERE model_id = :m', m=active_model[0]),
        } if active_model else None,
        'operations': {
            'total_sessions': _scalar(f'SELECT count(*) FROM ({TENANT_SESSIONS_SQL}) t', **inst),
            'total_discrepancies': _scalar(
                f'SELECT count(*) FROM discrepancy WHERE session_id IN ({TENANT_SESSIONS_SQL})', **inst),
            'open_discrepancies': _scalar(
                f'SELECT count(*) FROM discrepancy WHERE resolved = FALSE AND session_id IN ({TENANT_SESSIONS_SQL})', **inst),
            'human_corrections': _scalar(
                'SELECT count(*) FROM human_correction hc JOIN count_event ce ON ce.id = hc.count_event_id '
                f'WHERE ce.session_id IN ({TENANT_SESSIONS_SQL})', **inst),
            'ai_human_agreement': agreement,
            'avg_resolution_minutes': round(float(avg_resolution), 1) if avg_resolution is not None else None,
        },
    }
