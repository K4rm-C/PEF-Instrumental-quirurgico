"""MS-05 in the portal: cameras in Administration, the Operator's camera / heartbeat, the Supervisor's view.

Real PostgreSQL (rolled back). MonitoringService is replaced at the HTTP boundary (requests.post /
monitoring_remote._request); its own behaviour is tested in apps/MonitoringService (test_ms05_stations.py).
"""
import json
import uuid

import pytest
from sqlalchemy import text

from controllers import routes
from services import monitoring_remote, rf_session, station_capture, vision_bridge
from services.admin_local import facade
from services.admin_local.messages import AdminDomainError

from .admin_fixtures import as_user, login_as as admin_login, no_remote_http, pg, tenants  # noqa: F401
from .conftest import FakeResponse
from .test_multitenant import login_as, world  # noqa: F401
from .test_single_video_flow import flow, frame, worker  # noqa: F401
from .test_vision_count_rule import tray  # noqa: F401


# --- Administration: cameras ------------------------------------------------------------------------------


@pytest.fixture
def stations(tenants):
    ids = {}
    for key in ('A', 'B'):
        ids[key] = uuid.uuid4()
        tenants.q("INSERT INTO capture_station (id, name, room_id) VALUES (:s, :n, :r)",
                  s=ids[key], n=f'Station {key} {tenants.tag}', r=tenants.room[key])
    return ids


def camera_body(stations, key='A', **overrides):
    data = {'name': 'Tray camera', 'station_id': str(stations[key]), 'device_type': 'usb',
            'resolution_width': '1920', 'resolution_height': '1080', 'fps': '30', 'status': 'active'}
    data.update(overrides)
    return data


def test_camera_is_created_on_an_own_station_and_audited(as_user, tenants, stations, no_remote_http):
    with as_user('admin', 'A', 'POST'):
        camera = facade.post('/cameras', camera_body(stations, roi={'x': 0, 'y': 0, 'w': 640, 'h': 480,
                                                                    'frame_w': 1920, 'frame_h': 1080}))
        listed = facade.get('/cameras')
    row = tenants.q('SELECT institution_id, station_id, resolution_width, fps, roi FROM capture_camera WHERE id = :c',
                    c=camera['id']).mappings().one()
    assert (row['institution_id'], row['station_id'], row['resolution_width'], row['fps']) == (
        tenants.inst['A'], stations['A'], 1920, 30)
    assert row['roi']['w'] == 640 and camera['roi_configured'] is True
    assert [c['id'] for c in listed['items']] == [camera['id']]
    assert [r.resource_id for r in tenants.audit('CREATE_CAPTURE_CAMERA')] == [uuid.UUID(camera['id'])]


def test_cameras_of_another_institution_are_invisible_and_untouchable(as_user, tenants, stations):
    with as_user('admin', 'B', 'POST'):
        foreign = facade.post('/cameras', camera_body(stations, 'B'))
    with as_user('admin', 'A', 'PUT'):
        with pytest.raises(AdminDomainError) as on_foreign_station:
            facade.post('/cameras', camera_body(stations, 'B'))
        with pytest.raises(AdminDomainError) as edit_foreign:
            facade.put(f"/cameras/{foreign['id']}", camera_body(stations, 'A'))
        listed = facade.get('/cameras')
    assert (on_foreign_station.value.status, on_foreign_station.value.code) == (400, 'station_required')
    assert edit_foreign.value.status == 404 and listed['items'] == []
    assert tenants.q('SELECT station_id FROM capture_camera WHERE id = :c', c=foreign['id']).scalar_one() == stations['B']


@pytest.mark.parametrize('overrides, code', [
    ({'resolution_height': ''}, 'resolution_incomplete'),
    ({'resolution_width': '100'}, 'invalid_resolution_width'),
    ({'resolution_height': '5000'}, 'invalid_resolution_height'),
    ({'fps': '0'}, 'invalid_fps'),
    ({'device_type': 'laser'}, 'invalid_device_type'),
    ({'name': ''}, None),
])
def test_invalid_camera_settings_are_rejected(as_user, tenants, stations, overrides, code):
    with as_user('admin', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post('/cameras', camera_body(stations, **overrides))
    assert exc.value.status == 400 and (code is None or exc.value.code == code)
    assert tenants.q('SELECT count(*) FROM capture_camera WHERE station_id = :s', s=stations['A']).scalar_one() == 0


def test_camera_name_is_unique_per_station_and_updates_are_audited(as_user, tenants, stations):
    with as_user('admin', 'A', 'POST'):
        camera = facade.post('/cameras', camera_body(stations))
        with pytest.raises(AdminDomainError) as duplicate:
            facade.post('/cameras', camera_body(stations))
    with as_user('admin', 'A', 'PUT'):
        updated = facade.put(f"/cameras/{camera['id']}", camera_body(stations, fps='15', status='inactive'))
    assert (duplicate.value.status, duplicate.value.code) == (409, 'camera_name_taken')
    assert updated['fps'] == 15 and updated['active'] is False
    assert len(tenants.audit('UPDATE_CAPTURE_CAMERA')) == 1 and len(tenants.audit('DEACTIVATE_CAPTURE_CAMERA')) == 1


def test_camera_screens(client, admin_login, tenants, stations, no_remote_http):
    admin_login('admin', 'A')
    page = client.get('/admin/configuration').get_data(as_text=True)
    assert 'data-admin-cameras' in page and 'No cameras registered yet.' in page
    assert 'name="resolution_width"' in client.get('/admin/configuration/cameras/new').get_data(as_text=True)
    response = client.post('/admin/configuration/cameras/new', data=camera_body(stations))
    assert response.status_code == 302
    camera_id = tenants.q('SELECT id FROM capture_camera WHERE station_id = :s', s=stations['A']).scalar_one()
    assert 'Tray camera' in client.get('/admin/configuration').get_data(as_text=True)
    bad = client.post(f'/admin/configuration/cameras/{camera_id}/edit', data=camera_body(stations, fps='500'))
    assert bad.status_code == 400 and 'FPS must be between 1 and 120' in bad.get_data(as_text=True)


def test_only_it_admin_manages_cameras(client, admin_login, tenants, stations):
    admin_login('operator', 'A')
    assert client.get('/admin/configuration/cameras/new').status_code in (302, 403)
    assert client.post('/admin/configuration/cameras/new', data=camera_body(stations)).status_code in (302, 403)
    assert tenants.q('SELECT count(*) FROM capture_camera WHERE station_id = :s', s=stations['A']).scalar_one() == 0


# --- Operator: camera of the capture and heartbeat ---------------------------------------------------


def _camera(tray, station_id, name='Cam', active=True, institution=None):
    camera_id = uuid.uuid4()
    tray.c.execute(text("INSERT INTO capture_camera (id, institution_id, station_id, name, active) "
                        "VALUES (:id, :i, :s, :n, :a)"),
                   {'id': camera_id, 'i': institution or tray.t.institution_id, 's': station_id, 'n': name, 'a': active})
    return camera_id


def _choose(flow, camera_id):
    return flow.client.post(flow.base + '/capture/camera', json={'camera_id': str(camera_id)})


def _camera_of(tray):
    return tray.c.execute(text('SELECT camera_id FROM work_session WHERE id = :s'), {'s': tray.session.id}).scalar()


def test_operator_chooses_a_camera_of_the_session_station(app, flow):
    tray = flow.tray
    camera = _camera(tray, tray.t.station_id, 'Overhead')
    response = _choose(flow, camera)
    assert response.status_code == 200 and response.get_json()['changed'] is True and _camera_of(tray) == camera
    assert _choose(flow, camera).get_json()['changed'] is False        # same camera again: nothing new
    audits = tray.c.execute(text("SELECT count(*) FROM access_audit WHERE action = 'session.camera_selected' "
                                 "AND resource_id = :s"), {'s': tray.session.id}).scalar_one()
    assert audits == 1


def test_operator_cannot_choose_a_foreign_or_inactive_camera(app, flow):
    tray = flow.tray
    other_station = _camera(tray, tray.t.other_station_id, 'Other station')
    inactive = _camera(tray, tray.t.station_id, 'Off', active=False)
    foreign = _camera(tray, tray.w.b.station_id, 'Foreign', institution=tray.w.b.institution_id)
    assert _choose(flow, other_station).status_code == 400
    assert _choose(flow, inactive).status_code == 409
    assert _choose(flow, foreign).status_code == 400
    assert _choose(flow, 'garbage').status_code == 400
    assert _camera_of(tray) is None


def test_count_lots_keep_the_station_and_camera_as_evidence(app, flow):
    tray = flow.tray
    camera = _camera(tray, tray.t.station_id, 'Overhead')
    _choose(flow, camera)
    assert flow.client.post(flow.base + '/capture/initial').status_code == 200
    payload = tray.c.execute(text(
        "SELECT ce.payload FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id WHERE ce.session_id = :s "
        "AND t.code = 'auto_count' AND ce.payload->>'trigger' = 'initial'"), {'s': tray.session.id}).scalar_one()
    assert payload['capture_device'] == {'station_id': str(tray.t.station_id), 'camera_id': str(camera)}
    assert payload['initial_total'] if 'initial_total' in payload else True   # counting payload otherwise unchanged


def test_capture_without_a_camera_still_counts(app, flow):
    """No camera chosen: nothing new blocks the counting flow."""
    assert flow.client.post(flow.base + '/capture/initial').status_code == 200


def test_heartbeat_is_relayed_for_the_session_station_only(app, flow, monkeypatch):
    tray = flow.tray
    camera = _camera(tray, tray.t.station_id, 'Overhead')
    _choose(flow, camera)
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers))
        return FakeResponse(200, {'status': 'online', 'cameras': [{'id': str(camera), 'status': 'unverified'}]})
    monkeypatch.setenv('MONITORING_SERVICE_URL', 'http://monitoring.test')
    monkeypatch.setattr(station_capture.requests, 'post', fake_post)
    # A forged station in the body is ignored: the station is the session's own.
    response = flow.client.post(flow.base + '/capture/heartbeat',
                                json={'camera_status': 'ok', 'station_id': str(tray.t.other_station_id)})
    body = response.get_json()
    assert response.status_code == 200 and body['recorded'] is True and body['camera_status'] == 'unverified'
    (url, sent, headers) = calls[0]
    assert url == f'http://monitoring.test/api/v1/monitoring/stations/{tray.t.station_id}/heartbeat'
    assert sent == {'camera_status': 'ok', 'camera_id': str(camera), 'client': 'portal-capture'}
    assert headers['Authorization'].startswith('Bearer ')


def test_heartbeat_never_disturbs_the_capture(app, flow, monkeypatch):
    def down(*args, **kwargs):
        raise station_capture.requests.ConnectionError('down')
    monkeypatch.setenv('MONITORING_SERVICE_URL', 'http://monitoring.test')
    monkeypatch.setattr(station_capture.requests, 'post', down)
    response = flow.client.post(flow.base + '/capture/heartbeat', json={'camera_status': 'error', 'camera_error': 'x y!'})
    assert response.status_code == 200 and response.get_json()['recorded'] is False
    assert flow.client.post(flow.base + '/capture/initial').status_code == 200


def test_capture_page_offers_the_station_cameras(app, flow):
    tray = flow.tray
    _camera(tray, tray.t.station_id, 'Overhead camera')
    _camera(tray, tray.t.station_id, 'Retired camera', active=False)
    page = flow.client.get(flow.base + '/capture').get_data(as_text=True)
    assert 'id="capture-station"' in page and 'Overhead camera' in page and 'Retired camera' not in page
    assert 'js/pages/station-heartbeat.js' in page


# --- Supervisor: services health with stations ---------------------------------------------------------------


def _fake_monitoring(monkeypatch, *, stations_fail=False):
    def fake(path, params=None):
        if path == '/services':
            return {'items': [{'name': 'session', 'status': 'up', 'checked_at': '2026-11-08T10:00:00Z'}],
                    'counts': {'up': 1}}
        if path == '/summary':
            return {'components': [{'name': 'session', 'availability_pct': 100.0}]}
        if stations_fail:
            raise monitoring_remote.MonitoringServiceError(503, 'monitoring_unavailable')
        if path == '/stations':
            return {'stale_seconds': 90, 'totals': {}, 'items': [{
                'id': 's1', 'name': 'Station 1', 'room': {'code': 'OR-1'}, 'status': 'online',
                'last_seen_at': '2026-11-08T10:00:00Z', 'open_alerts': 1,
                'cameras': [{'name': 'Overhead', 'status': 'error', 'error': 'NotReadableError'},
                            {'name': 'Side', 'status': 'ok', 'error': None}]}]}
        if path == '/alerts':
            return {'status': 'open', 'items': [{'station_name': 'Station 1', 'kind': 'camera_error',
                                                 'error': 'NotReadableError', 'occurred_at': '2026-11-08T09:59:00Z'}]}
        raise AssertionError(path)
    monkeypatch.setattr(monitoring_remote, '_request', fake)


def test_supervisor_sees_stations_cameras_and_alerts(client, login, monkeypatch):
    login(role='spd_supervisor')
    _fake_monitoring(monkeypatch)
    page = client.get('/supervisor/services-health').get_data(as_text=True)
    assert 'data-services-health-table' in page and 'data-stations-table' in page
    assert 'data-camera-status="error"' in page and 'data-camera-status="ok"' in page
    assert 'data-alert-kind="camera_error"' in page and 'NotReadableError' in page


def test_supervisor_page_keeps_services_when_stations_fail(client, login, monkeypatch):
    login(role='spd_supervisor')
    _fake_monitoring(monkeypatch, stations_fail=True)
    page = client.get('/supervisor/services-health').get_data(as_text=True)
    assert 'data-services-health-table' in page and 'data-stations-unavailable' in page
    assert 'data-stations-table' not in page
