"""RF-AD-07 operating rooms, capture stations and (MS-05) cameras, RF-AD-10 institution (own institution only, D-2).

Capture station ROI (region of the tray inside the camera frame) is stored as the JSON the vision
pipeline already reads: {"shape": "rect", "x", "y", "w", "h", "frame_w", "frame_h"} in pixels.
Validation: all six integers, frame >= 1, w/h >= 1, x/y >= 0, x + w <= frame_w, y + h <= frame_h;
a partial ROI is rejected; extra keys already stored are kept. Deactivating never deletes history.
"""
import math
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import audit
from .database import db
from .errors import bad_request, conflict, not_found
from .models import CaptureCamera, CaptureCameraCalibration, CaptureStation, Institution, OperatingRoom
from .common import integer, parse_uuid, status_flag, text

ROI_KEYS = ('x', 'y', 'w', 'h', 'frame_w', 'frame_h')
ROI_MAX = 20000


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
        # Every account of this institution (including the caller) would be locked out; with D-2 the
        # administrator's institution is the only one they manage, so this also covers "the only
        # active institution" of RF-AD-10.
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
    roi = row.roi if isinstance(row.roi, dict) else {}
    return {
        'id': str(row.id),
        'name': row.name,
        'active': row.active,
        'roi_configured': all(key in roi for key in ROI_KEYS),
        'roi': {key: roi.get(key) for key in ROI_KEYS} if roi else None,
        'room': _room_dict(room) if room else None,
    }


def _roi(payload, current=None):
    """Validated ROI dict, None to clear it, or `current` when the payload does not mention it."""
    raw = payload.get('roi')
    if raw is None:
        return current
    if raw == {} or payload.get('clear_roi') in (True, '1', 'true', 'on'):
        return None
    if not isinstance(raw, dict):
        raise bad_request('invalid_roi', 'The ROI must define x, y, w, h, frame_w and frame_h.', 'roi')
    values = {key: str(raw.get(key) if raw.get(key) is not None else '').strip() for key in ROI_KEYS}
    if not any(values.values()):
        return current
    if not all(values.values()):
        raise bad_request('roi_incomplete', 'Fill in every ROI value (x, y, w, h, frame width and frame height).', 'roi')
    try:
        numbers = {key: int(value) for key, value in values.items()}
    except ValueError:
        raise bad_request('invalid_roi', 'ROI values must be whole numbers of pixels.', 'roi')
    if any(numbers[key] < 1 or numbers[key] > ROI_MAX for key in ('w', 'h', 'frame_w', 'frame_h')) \
            or numbers['x'] < 0 or numbers['y'] < 0:
        raise bad_request('invalid_roi', 'ROI sizes must be positive and the origin cannot be negative.', 'roi')
    if numbers['x'] + numbers['w'] > numbers['frame_w'] or numbers['y'] + numbers['h'] > numbers['frame_h']:
        raise bad_request('roi_outside_frame', 'The ROI must fit inside the camera frame.', 'roi')
    merged = dict(current or {})
    merged.setdefault('shape', 'rect')
    merged.update(numbers)
    return merged


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
    station = CaptureStation(name=name, room_id=room.id, active=status_flag(payload), roi=_roi(payload))
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
    roi = _roi(payload, station.roi)
    was_active = station.active
    changed = (station.name, station.room_id, station.roi) != (name, room.id, roi)
    station.name, station.room_id, station.active, station.roi = name, room.id, active, roi
    audit.record_update('CAPTURE_STATION', 'capture_station', station.id, was_active=was_active,
                        is_active=active, changed=changed)
    db.commit()
    return _station_dict(station, room)


# --- Cameras (MS-05) ----------------------------------------------------------------------------
# A camera belongs to one capture station of the administrator's institution. Supported capture
# settings (resolution, FPS) and an optional camera-specific ROI; deactivating keeps its history (sessions
# that used it keep the reference). The database also refuses a camera of another institution's station.

# MS-11: 'simulated' = a test source (recorded video / virtual device). It is never evidence of working hardware.
CAMERA_DEVICE_TYPES = ('usb', 'integrated', 'ip', 'other', 'simulated')


def _camera_dict(row, station=None, room=None):
    roi = row.roi if isinstance(row.roi, dict) else {}
    return {
        'calibration': current_calibration(row),
        'id': str(row.id),
        'name': row.name,
        'device_type': row.device_type,
        'resolution_width': row.resolution_width,
        'resolution_height': row.resolution_height,
        'fps': row.fps,
        'active': row.active,
        'roi_configured': all(key in roi for key in ROI_KEYS),
        'roi': {key: roi.get(key) for key in ROI_KEYS} if roi else None,
        'station': {'id': str(station.id), 'name': station.name, 'active': station.active} if station else None,
        'room': _room_dict(room) if room else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }


def _camera_settings(payload):
    device_type = (payload.get('device_type') or 'usb').strip()
    if device_type not in CAMERA_DEVICE_TYPES:
        raise bad_request('invalid_device_type', 'Choose a valid device type.', 'device_type')
    width = str(payload.get('resolution_width') or '').strip()
    height = str(payload.get('resolution_height') or '').strip()
    if bool(width) != bool(height):
        raise bad_request('resolution_incomplete', 'Fill in both the width and the height of the resolution, or neither.',
                          'resolution_width')
    resolution = (None, None)
    if width:
        resolution = (integer(width, name='resolution_width', minimum=160, maximum=7680,
                              message='The resolution width must be between 160 and 7680 pixels.'),
                      integer(height, name='resolution_height', minimum=120, maximum=4320,
                              message='The resolution height must be between 120 and 4320 pixels.'))
    fps_raw = str(payload.get('fps') or '').strip()
    fps = integer(fps_raw, name='fps', minimum=1, maximum=120, message='FPS must be between 1 and 120.') \
        if fps_raw else None
    return device_type, resolution, fps


def list_cameras(actor):
    stations = {}
    for station in db.execute(select(CaptureStation)).scalars():
        stations[station.id] = station
    rooms = {row.id: row for row in db.execute(
        select(OperatingRoom).where(OperatingRoom.institution_id == actor.institution_id)).scalars()}
    rows = db.execute(select(CaptureCamera).where(CaptureCamera.institution_id == actor.institution_id)
                      .order_by(CaptureCamera.name)).scalars()
    items = []
    for row in rows:
        station = stations.get(row.station_id)
        items.append(_camera_dict(row, station, rooms.get(station.room_id) if station else None))
    return {'items': items}


def _owned_camera(actor, camera_id):
    camera = db.get(CaptureCamera, parse_uuid(camera_id))
    if camera is None or camera.institution_id != actor.institution_id:
        raise not_found('camera_not_found', 'Camera not found.')
    station, room = _owned_station(actor, camera.station_id)
    return camera, station, room


def get_camera(actor, camera_id):
    return _camera_dict(*_owned_camera(actor, camera_id))


def _camera_station(actor, payload):
    raw = text(payload, 'station_id', required=True, label='Capture station', code='station_required')
    try:
        return _owned_station(actor, raw)
    except Exception:
        raise bad_request('station_required', 'Choose a capture station of your institution.', 'station_id')


def create_camera(actor, payload):
    name = text(payload, 'name', required=True, max_length=120, label='Camera name')
    station, room = _camera_station(actor, payload)
    device_type, (width, height), fps = _camera_settings(payload)
    camera = CaptureCamera(institution_id=actor.institution_id, station_id=station.id, name=name,
                           device_type=device_type, resolution_width=width, resolution_height=height, fps=fps,
                           roi=_roi(payload), active=status_flag(payload))
    db.add(camera)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if 'uk_capture_camera_station_name' in str(exc.orig):
            raise conflict('camera_name_taken', 'This station already has a camera with that name.', 'name')
        raise
    audit.record('CREATE_CAPTURE_CAMERA', 'capture_camera', camera.id)
    db.commit()
    return _camera_dict(camera, station, room)


def update_camera(actor, camera_id, payload):
    camera, _, _ = _owned_camera(actor, camera_id)
    name = text(payload, 'name', required=True, max_length=120, label='Camera name')
    station, room = _camera_station(actor, payload)
    device_type, (width, height), fps = _camera_settings(payload)
    active = status_flag(payload)
    roi = _roi(payload, camera.roi)
    was_active = camera.active
    new_values = (name, station.id, device_type, width, height, fps, roi)
    changed = (camera.name, camera.station_id, camera.device_type, camera.resolution_width,
               camera.resolution_height, camera.fps, camera.roi) != new_values
    (camera.name, camera.station_id, camera.device_type, camera.resolution_width, camera.resolution_height,
     camera.fps, camera.roi) = new_values
    camera.active = active
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if 'uk_capture_camera_station_name' in str(exc.orig):
            raise conflict('camera_name_taken', 'This station already has a camera with that name.', 'name')
        raise
    audit.record_update('CAPTURE_CAMERA', 'capture_camera', camera.id, was_active=was_active, is_active=active,
                        changed=changed)
    db.commit()
    return _camera_dict(camera, station, room)


# --- Camera calibration (MS-12, ms-camaras) -------------------------------------------------------------------
# Method 'reference_length': a technician places an object of KNOWN length on the tray plane, measures its length in
# pixels on a frame of the camera and records both values. PEF only derives mm/pixel = length / pixels; it never
# estimates a physical parameter, distortion or height. The calibration belongs to the frame size it was measured on:
# if the camera resolution changes afterwards it is reported as not matching (and not sent to the worker).
CALIBRATION_METHODS = ('reference_length',)
REFERENCE_MAX_MM = Decimal('5000')


def _decimal(payload, name, *, maximum, message):
    raw = str(payload.get(name) or '').strip().replace(',', '.')
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise bad_request(f'invalid_{name}', message, name)
    if not value.is_finite() or value <= 0 or value > maximum or value != value.quantize(Decimal('0.001')):
        raise bad_request(f'invalid_{name}', message, name)
    return value


def _calibration_dict(row, camera=None):
    matches = None
    if camera is not None:
        matches = (camera.resolution_width, camera.resolution_height) == (row.frame_width, row.frame_height)
    return {
        'id': str(row.id), 'method': row.method,
        'reference_length_mm': float(row.reference_length_mm), 'measured_pixels': float(row.measured_pixels),
        'mm_per_pixel': float(row.mm_per_pixel), 'frame_width': row.frame_width, 'frame_height': row.frame_height,
        'notes': row.notes, 'performed_by_user_id': str(row.performed_by_user_id),
        'performed_at': row.performed_at.isoformat() if row.performed_at else None,
        'matches_resolution': matches,
    }


def current_calibration(camera):
    """Latest calibration of the camera (or None), flagged with whether it matches the current resolution."""
    row = db.execute(select(CaptureCameraCalibration).where(CaptureCameraCalibration.camera_id == camera.id)
                     .order_by(CaptureCameraCalibration.performed_at.desc(), CaptureCameraCalibration.id.desc())
                     .limit(1)).scalars().first()
    return _calibration_dict(row, camera) if row is not None else None


def list_calibrations(actor, camera_id):
    camera, _, _ = _owned_camera(actor, camera_id)
    rows = db.execute(select(CaptureCameraCalibration).where(CaptureCameraCalibration.camera_id == camera.id)
                      .order_by(CaptureCameraCalibration.performed_at.desc(), CaptureCameraCalibration.id.desc())).scalars()
    return {'items': [_calibration_dict(row, camera) for row in rows]}


def record_calibration(actor, camera_id, payload):
    camera, _, _ = _owned_camera(actor, camera_id)
    if not camera.active:
        raise bad_request('camera_inactive', 'Activate the camera before calibrating it.', 'camera_id')
    method = (payload.get('method') or 'reference_length').strip()
    if method not in CALIBRATION_METHODS:
        raise bad_request('invalid_calibration_method', 'Choose a valid calibration method.', 'method')
    width = integer(payload.get('frame_width'), name='frame_width', minimum=160, maximum=7680,
                    message='The frame width must be between 160 and 7680 pixels.')
    height = integer(payload.get('frame_height'), name='frame_height', minimum=120, maximum=4320,
                     message='The frame height must be between 120 and 4320 pixels.')
    if camera.resolution_width and (camera.resolution_width, camera.resolution_height) != (width, height):
        raise bad_request('calibration_frame_mismatch',
                          'The calibration frame must have the resolution configured for the camera.', 'frame_width')
    length = _decimal(payload, 'reference_length_mm', maximum=REFERENCE_MAX_MM,
                      message='The reference length must be a positive value in millimetres (up to 5000, 3 decimals).')
    pixels = _decimal(payload, 'measured_pixels', maximum=Decimal('20000'),
                      message='The measured length must be a positive number of pixels (3 decimals at most).')
    if pixels > Decimal(str(math.hypot(width, height))):
        raise bad_request('measured_pixels_outside_frame',
                          'The measured length cannot be longer than the diagonal of the frame.', 'measured_pixels')
    notes = text(payload, 'notes', max_length=300, label='Notes') or None
    row = CaptureCameraCalibration(
        institution_id=actor.institution_id, camera_id=camera.id, method=method, reference_length_mm=length,
        measured_pixels=pixels, mm_per_pixel=(length / pixels).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP),
        frame_width=width, frame_height=height, notes=notes, performed_by_user_id=actor.id,
        performed_at=datetime.now(timezone.utc))
    db.add(row)
    db.flush()
    audit.record('CALIBRATE_CAPTURE_CAMERA', 'capture_camera', camera.id)
    db.commit()
    return _calibration_dict(row, camera)
