"""MS-11 (R08 / R11) in the portal: the station camera configuration reaches VisionWorker through the signed bind,
choosing a camera re-binds the worker, and the tracking summary of the accepted run is persisted with the final count
WITHOUT ever becoming the count (the accepted total stays the last valid frame, MS-03.1)."""
import uuid

from sqlalchemy import text

from services import vision_bridge

from .test_multitenant import login_as, pg, world  # noqa: F401  (pytest fixtures)
from .test_vision_count_rule import tray  # noqa: F401  (pytest fixture)
from .test_single_video_flow import _lots, _post, _register_initial, flow, frame, worker  # noqa: F401


def _camera(tray, *, device_type='usb', roi=None, name='Overhead'):
    c, room, station, camera = tray.c, uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    c.execute(text("INSERT INTO operating_room (id, code, name, institution_id) VALUES (:r, :c, 'Room', :i)"),
              {'r': room, 'c': f'OR-{room.hex[:6]}', 'i': tray.t.institution_id})
    c.execute(text("INSERT INTO capture_station (id, name, room_id) VALUES (:s, 'MS-11 station', :r)"), {'s': station, 'r': room})
    c.execute(text("""INSERT INTO capture_camera (id, institution_id, station_id, name, device_type, resolution_width,
                      resolution_height, fps, roi) VALUES (:c, :i, :s, :n, :d, 1280, 720, 30, CAST(:roi AS jsonb))"""),
              {'c': camera, 'i': tray.t.institution_id, 's': station, 'n': name, 'd': device_type,
               'roi': None if roi is None else __import__('json').dumps(roi)})
    c.execute(text('UPDATE work_session SET station_id = :s, camera_id = :c WHERE id = :w'),
              {'s': station, 'c': camera, 'w': tray.session.id})
    return camera


def test_bind_carries_the_camera_configuration(app, tray):
    roi = {'x': 100, 'y': 50, 'w': 800, 'h': 600, 'frame_w': 1280, 'frame_h': 720}
    camera = _camera(tray, roi=roi)
    with app.app_context():
        payload = vision_bridge.camera_payload_for_session(tray.session.id)
    assert payload == {'id': str(camera), 'name': 'Overhead', 'device_type': 'usb', 'resolution_width': 1280,
                       'resolution_height': 720, 'fps': 30, 'roi': roi, 'simulated': False,
                       'calibration': None}   # MS-12: no technician calibration recorded


def test_a_simulated_source_is_marked_as_such(app, tray):
    _camera(tray, device_type='simulated', name='Recorded tray video')
    with app.app_context():
        assert vision_bridge.camera_payload_for_session(tray.session.id)['simulated'] is True


def test_no_camera_chosen_means_no_camera_in_the_bind(app, tray):
    with app.app_context():
        assert vision_bridge.camera_payload_for_session(tray.session.id) is None


def test_the_accepted_final_count_stores_tracking_but_counts_the_last_frame(flow):
    assert _register_initial(flow).status_code == 200
    tracking = {'version': 'ms11-hungarian-cv-1', 'unique_confirmed_tracks': 99, 'active_confirmed_tracks': 15,
                'stats': {'lost': 2, 'recovered': 2, 'class_changes': 1}, 'final_frame_total': 15,
                'active_tracks_match_final_frame': True, 'events': [{'event': 'recovered', 'track_id': 4}],
                'note': 'Tracking statistics are not counts; the final count is the last valid frame (MS-03.1).'}
    last = frame(flow.tray, 8, 7, position='last', index=297, run_id='run-1')
    last['tracking'] = tracking
    flow.state = worker(flow.tray, status='ready', run_id='run-1', first=flow.state['initial_frame'], last=last)
    assert _post(flow, '/capture/final').status_code == 200
    final = [lot for lot in _lots(flow.tray) if lot['payload'].get('trigger') == 'final']
    assert len(final) == 1
    assert final[0]['detected_quantity'] == 15                       # the last valid frame, never 99 tracks
    assert final[0]['payload']['tracking'] == tracking


def test_a_rejected_final_count_is_still_rejected_whatever_the_tracking_says(flow):
    """Tracking can never turn a different final count into an accepted one (MS-03.1 rule unchanged)."""
    assert _register_initial(flow).status_code == 200
    last = frame(flow.tray, 8, 6, position='last', index=297, run_id='run-1')
    last['tracking'] = {'unique_confirmed_tracks': 15, 'active_confirmed_tracks': 15, 'stats': {},
                        'active_tracks_match_final_frame': False}
    flow.state = worker(flow.tray, status='ready', run_id='run-1', first=flow.state['initial_frame'], last=last)
    assert _post(flow, '/capture/final').status_code == 409
    assert [lot for lot in _lots(flow.tray) if lot['payload'].get('trigger') == 'final'] == []


def test_choosing_a_camera_rebinds_the_worker(flow, monkeypatch):
    camera = _camera(flow.tray)
    flow.tray.c.execute(text('UPDATE work_session SET camera_id = NULL WHERE id = :w'), {'w': flow.tray.session.id})
    calls = []
    monkeypatch.setattr(vision_bridge, 'bind_session', lambda sid: calls.append(str(sid)) or {'ok': True})
    response = flow.client.post(flow.base + '/capture/camera', json={'camera_id': str(camera)})
    assert response.status_code == 200 and response.get_json()['worker_camera'] is True
    assert calls == [str(flow.tray.session.id)]


def test_a_reconnection_ticket_rebinds_the_session_first(flow, monkeypatch):
    """MS-11 (R09): after a VisionWorker restart the worker forgot the session; the reconnection re-binds it."""
    calls = []
    monkeypatch.setattr(vision_bridge, 'bind_session', lambda sid: calls.append(str(sid)) or {'ok': True})
    plain = flow.client.get(flow.base + '/capture/ws-ticket')
    assert plain.status_code == 200 and plain.get_json()['ticket'] and calls == []      # renewals never re-bind
    rebound = flow.client.get(flow.base + '/capture/ws-ticket?bind=1')
    assert rebound.status_code == 200 and rebound.get_json()['ticket'] and calls == [str(flow.tray.session.id)]


def test_a_reconnection_ticket_while_the_worker_is_down_asks_to_retry(flow, monkeypatch):
    def down(sid):
        raise vision_bridge.VisionBridgeError('Vision worker unreachable', 503)
    monkeypatch.setattr(vision_bridge, 'bind_session', down)
    response = flow.client.get(flow.base + '/capture/ws-ticket?bind=1')
    assert response.status_code == 503 and 'ticket' not in response.get_json()
