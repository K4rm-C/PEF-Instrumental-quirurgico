from sqlalchemy import text as sql

from database import db


def _scalar(query, **params):
    return db.execute(sql(query), params).scalar() or 0


def overview(actor):
    inst = {'inst': actor.institution_id}
    active_model = db.execute(sql(
        "SELECT id, version_tag FROM yolo_model WHERE active = TRUE LIMIT 1"
    )).first()

    vision_counts = db.execute(sql("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE hc.id IS NULL) AS agreed
        FROM count_event ce
        JOIN cat_event_type et ON et.id = ce.event_type_id
        LEFT JOIN human_correction hc ON hc.count_event_id = ce.id
        WHERE et.code = 'manual_count' AND ce.family_id IS NOT NULL
          AND (ce.payload ->> 'ai') = 'true'
    """)).mappings().first()
    agreement = None
    if vision_counts and vision_counts['total']:
        agreement = round(100.0 * vision_counts['agreed'] / vision_counts['total'], 1)

    avg_resolution = db.execute(sql("""
        SELECT avg(extract(epoch FROM (d.resolved_at - COALESCE(ce.occurred_at, ws.started_at))) / 60.0)
        FROM discrepancy d
        LEFT JOIN count_event ce ON ce.id = d.origin_event_id
        JOIN work_session ws ON ws.id = d.session_id
        WHERE d.resolved = TRUE AND d.resolved_at IS NOT NULL
          AND COALESCE(ce.occurred_at, ws.started_at) IS NOT NULL
    """)).scalar()

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
            'total_sessions': _scalar('SELECT count(*) FROM work_session'),
            'total_discrepancies': _scalar('SELECT count(*) FROM discrepancy'),
            'open_discrepancies': _scalar('SELECT count(*) FROM discrepancy WHERE resolved = FALSE'),
            'human_corrections': _scalar('SELECT count(*) FROM human_correction'),
            'ai_human_agreement': agreement,
            'avg_resolution_minutes': round(float(avg_resolution), 1) if avg_resolution is not None else None,
        },
    }
