"""MS-12 (ms-camaras): calibration of a camera MEASURED by a technician (reference length -> mm/pixel).

Real PostgreSQL (rolled back): IT admin only, own institution, rules, immutable history, audit, the screen, and the
current calibration reaching VisionWorker in the signed bind only when it matches the camera resolution.
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from services import vision_bridge
from services.admin_local import facade
from services.admin_local.messages import AdminDomainError

from .admin_fixtures import as_user, login_as as admin_login, no_remote_http, pg, tenants  # noqa: F401
from .test_ms05_stations_portal import camera_body, stations  # noqa: F401
from .test_multitenant import world  # noqa: F401
from .test_ms11_tracking_camera import _camera as session_camera
from .test_vision_count_rule import tray  # noqa: F401


def calibration(**overrides):
    data = {'reference_length_mm': '100', 'measured_pixels': '400', 'frame_width': '1920', 'frame_height': '1080',
            'notes': 'Steel ruler, tray plane'}
    data.update(overrides)
    return data


@pytest.fixture
def camera(as_user, stations):
    with as_user('admin', 'A', 'POST'):
        return facade.post('/cameras', camera_body(stations))


def test_mm_per_pixel_is_derived_only_from_the_measured_values_and_audited(as_user, tenants, camera):
    with as_user('admin', 'A', 'POST'):
        first = facade.post(f"/cameras/{camera['id']}/calibrations", calibration())
        second = facade.post(f"/cameras/{camera['id']}/calibrations", calibration(measured_pixels='512.5'))
        history = facade.get(f"/cameras/{camera['id']}/calibrations")
        listed = facade.get('/cameras')
    assert first['mm_per_pixel'] == 0.25 and first['method'] == 'reference_length' and first['matches_resolution'] is True
    assert second['mm_per_pixel'] == pytest.approx(0.195122)                    # 100 / 512.5, 6 decimals
    assert [c['id'] for c in history['items']] == [second['id'], first['id']]   # newest first, nothing replaced
    assert listed['items'][0]['calibration']['id'] == second['id']
    assert len(tenants.audit('CALIBRATE_CAPTURE_CAMERA')) == 2


@pytest.mark.parametrize('overrides, code', [
    ({'reference_length_mm': '0'}, 'invalid_reference_length_mm'),
    ({'reference_length_mm': '-5'}, 'invalid_reference_length_mm'),
    ({'reference_length_mm': '5000.5'}, 'invalid_reference_length_mm'),
    ({'reference_length_mm': '10.0001'}, 'invalid_reference_length_mm'),
    ({'reference_length_mm': 'abc'}, 'invalid_reference_length_mm'),
    ({'measured_pixels': '0'}, 'invalid_measured_pixels'),
    ({'measured_pixels': '2203'}, 'measured_pixels_outside_frame'),             # > diagonal of 1920x1080 (~2202.9)
    ({'frame_width': '1280', 'frame_height': '720'}, 'calibration_frame_mismatch'),
    ({'frame_width': '100'}, 'invalid_frame_width'),
    ({'method': 'laser'}, 'invalid_calibration_method'),
])
def test_invalid_calibrations_are_rejected(as_user, tenants, camera, overrides, code):
    with as_user('admin', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post(f"/cameras/{camera['id']}/calibrations", calibration(**overrides))
    assert (exc.value.status, exc.value.code) == (400, code)
    assert tenants.q('SELECT count(*) FROM capture_camera_calibration WHERE camera_id = :c', c=camera['id']).scalar_one() == 0


def test_inactive_and_foreign_cameras_cannot_be_calibrated(as_user, stations, camera):
    with as_user('admin', 'A', 'PUT'):
        facade.put(f"/cameras/{camera['id']}", camera_body(stations, status='inactive'))
        with pytest.raises(AdminDomainError) as inactive:
            facade.post(f"/cameras/{camera['id']}/calibrations", calibration())
    with as_user('admin', 'B', 'POST'):
        with pytest.raises(AdminDomainError) as foreign:
            facade.post(f"/cameras/{camera['id']}/calibrations", calibration())
        with pytest.raises(AdminDomainError) as foreign_read:
            facade.get(f"/cameras/{camera['id']}/calibrations")
    assert inactive.value.code == 'camera_inactive'
    assert foreign.value.status == 404 and foreign_read.value.status == 404


def test_history_is_immutable_and_reports_a_later_resolution_change(as_user, pg, stations, camera):
    with as_user('admin', 'A', 'POST'):
        cal = facade.post(f"/cameras/{camera['id']}/calibrations", calibration())
    with pytest.raises(DBAPIError, match='immutable'):
        with pg.begin_nested():
            pg.execute(text('UPDATE capture_camera_calibration SET mm_per_pixel = 1 WHERE id = :i'), {'i': cal['id']})
    with as_user('admin', 'A', 'PUT'):
        facade.put(f"/cameras/{camera['id']}", camera_body(stations, resolution_width='1280', resolution_height='720'))
        current = facade.get(f"/cameras/{camera['id']}")['calibration']
    assert current['id'] == cal['id'] and current['matches_resolution'] is False


def test_only_it_admin_calibrates_and_the_screen_works(client, admin_login, tenants, camera):
    admin_login('operator', 'A')
    assert client.get(f"/admin/configuration/cameras/{camera['id']}/calibration").status_code in (302, 403)
    admin_login('admin', 'A')
    page = client.get(f"/admin/configuration/cameras/{camera['id']}/calibration").get_data(as_text=True)
    assert 'data-calibration-form' in page and 'value="1920"' in page and 'This camera has not been calibrated.' in page
    bad = client.post(f"/admin/configuration/cameras/{camera['id']}/calibration", data=calibration(measured_pixels='0'))
    assert bad.status_code == 400 and 'positive number of pixels' in bad.get_data(as_text=True)
    ok = client.post(f"/admin/configuration/cameras/{camera['id']}/calibration", data=calibration())
    assert ok.status_code == 302
    page = client.get(f"/admin/configuration/cameras/{camera['id']}/calibration").get_data(as_text=True)
    assert 'data-calibration=' in page and '0.250000' in page
    assert '0.250000 mm/px' in client.get('/admin/configuration').get_data(as_text=True)


def test_the_bind_carries_the_calibration_only_when_it_matches_the_resolution(app, tray):
    camera = session_camera(tray)                                                 # 1280x720
    insert = text("""INSERT INTO capture_camera_calibration (institution_id, camera_id, method, reference_length_mm,
                     measured_pixels, mm_per_pixel, frame_width, frame_height, performed_by_user_id, performed_at)
                     VALUES (:i, :c, 'reference_length', 100, 400, 0.25, :w, :h, :u, now() + make_interval(secs => :s))""")
    user_id = tray.c.execute(text('SELECT id FROM "user" WHERE institution_id = :i LIMIT 1'),
                             {'i': tray.t.institution_id}).scalar_one()
    with app.app_context():
        assert vision_bridge.camera_payload_for_session(tray.session.id)['calibration'] is None
        tray.c.execute(insert, {'i': tray.t.institution_id, 'c': camera, 'w': 1280, 'h': 720, 'u': user_id, 's': 0})
        cal = vision_bridge.camera_payload_for_session(tray.session.id)['calibration']
        assert (cal['mm_per_pixel'], cal['frame_width'], cal['frame_height']) == (0.25, 1280, 720)
        tray.c.execute(insert, {'i': tray.t.institution_id, 'c': camera, 'w': 1920, 'h': 1080, 'u': user_id, 's': 1})
        assert vision_bridge.camera_payload_for_session(tray.session.id)['calibration'] is None   # latest does not match
