from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import audit
from database import db
from errors import bad_request, conflict, not_found
from models import CaptureStation, Institution, OperatingRoom
from services.common import parse_uuid, status_flag, text


# --- Institution ----------------------------------------------------------------------------

def _institution_dict(row):
    return {
        'id': str(row.id),
        'name': row.name,
        'code': str(row.id)[:8].upper(),
        'active': row.active,
    }


def get_institution(actor):
    row = db.get(Institution, actor.institution_id)
    if row is None:
        raise not_found('institution_not_found', 'Institution not found.')
    return _institution_dict(row)


def update_institution(actor, payload):
    row = db.get(Institution, actor.institution_id)
    if row is None:
        raise not_found('institution_not_found', 'Institution not found.')
    name = text(payload, 'name', required=True, max_length=200, label='Institution name')
    active = status_flag(payload)
    if not active:
        # Every account of this institution (including the caller) would be locked out.
        raise bad_request('cannot_deactivate_own_institution',
                          'You cannot deactivate the institution your account belongs to.', 'status')
    if name != row.name:
        row.name = name
        audit.record('UPDATE_INSTITUTION', 'institution', row.id)
        db.commit()
    return _institution_dict(row)


# --- Operating rooms ------------------------------------------------------------------------

def _room_dict(row):
    return {'id': str(row.id), 'code': row.code, 'name': row.name, 'active': row.active}


def list_rooms(actor):
    rows = db.execute(
        select(OperatingRoom).where(OperatingRoom.institution_id == actor.institution_id)
        .order_by(OperatingRoom.code)
    ).scalars()
    return {'items': [_room_dict(row) for row in rows]}


def _owned_room(actor, room_id, field=None):
    room = db.get(OperatingRoom, parse_uuid(room_id, 'invalid_room', 'Choose a valid operating room.', field))
    if room is None or room.institution_id != actor.institution_id:
        if field:
            raise bad_request('invalid_room', 'Choose a valid operating room.', field)
        raise not_found('room_not_found', 'Operating room not found.')
    return room


def get_room(actor, room_id):
    return _room_dict(_owned_room(actor, room_id))


def create_room(actor, payload):
    room = OperatingRoom(
        code=text(payload, 'code', required=True, max_length=32, label='Room code'),
        name=text(payload, 'name', required=True, max_length=120, label='Room name'),
        active=status_flag(payload),
        institution_id=actor.institution_id,
    )
    db.add(room)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict('room_code_exists', 'An operating room with this code already exists.', 'code')
    audit.record('CREATE_OPERATING_ROOM', 'operating_room', room.id)
    db.commit()
    return _room_dict(room)


def update_room(actor, room_id, payload):
    room = _owned_room(actor, room_id)
    code = text(payload, 'code', required=True, max_length=32, label='Room code')
    name = text(payload, 'name', required=True, max_length=120, label='Room name')
    active = status_flag(payload)
    was_active = room.active
    changed = (room.code, room.name) != (code, name)
    room.code, room.name, room.active = code, name, active
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict('room_code_exists', 'An operating room with this code already exists.', 'code')
    audit.record_update('OPERATING_ROOM', 'operating_room', room.id, was_active=was_active,
                        is_active=active, changed=changed)
    db.commit()
    return _room_dict(room)


# --- Capture stations -----------------------------------------------------------------------

def _station_dict(row, room):
    return {
        'id': str(row.id),
        'name': row.name,
        'active': row.active,
        'roi_configured': bool(row.roi),
        'room': _room_dict(room) if room else None,
    }


def list_stations(actor):
    rooms = {row.id: row for row in db.execute(
        select(OperatingRoom).where(OperatingRoom.institution_id == actor.institution_id)
    ).scalars()}
    rows = db.execute(
        select(CaptureStation).where(CaptureStation.room_id.in_(list(rooms) or [None]))
        .order_by(CaptureStation.name)
    ).scalars()
    return {'items': [_station_dict(row, rooms.get(row.room_id)) for row in rows]}


def _owned_station(actor, station_id):
    station = db.get(CaptureStation, parse_uuid(station_id))
    room = db.get(OperatingRoom, station.room_id) if station else None
    if station is None or room is None or room.institution_id != actor.institution_id:
        raise not_found('station_not_found', 'Capture station not found.')
    return station, room


def get_station(actor, station_id):
    station, room = _owned_station(actor, station_id)
    return _station_dict(station, room)


def create_station(actor, payload):
    name = text(payload, 'name', required=True, max_length=120, label='Station name')
    room = _owned_room(actor, text(payload, 'room_id', required=True, label='Operating room',
                                   code='room_required'), field='room_id')
    station = CaptureStation(name=name, room_id=room.id, active=status_flag(payload), roi=None)
    db.add(station)
    db.flush()
    audit.record('CREATE_CAPTURE_STATION', 'capture_station', station.id)
    db.commit()
    return _station_dict(station, room)


def update_station(actor, station_id, payload):
    station, _ = _owned_station(actor, station_id)
    name = text(payload, 'name', required=True, max_length=120, label='Station name')
    room = _owned_room(actor, text(payload, 'room_id', required=True, label='Operating room',
                                   code='room_required'), field='room_id')
    active = status_flag(payload)
    was_active = station.active
    changed = (station.name, station.room_id) != (name, room.id)
    station.name, station.room_id, station.active = name, room.id, active
    audit.record_update('CAPTURE_STATION', 'capture_station', station.id, was_active=was_active,
                        is_active=active, changed=changed)
    db.commit()
    return _station_dict(station, room)
