"""RF-AD-07 rooms / stations / ROI and RF-AD-10 institution / application parameters (ADMIN-01), real PostgreSQL."""
import json
import uuid

import pytest

from services.admin_local import facade, settings
from services.admin_local.messages import AdminDomainError

from .admin_fixtures import as_user, login_as, no_remote_http, pg, tenants  # noqa: F401

ROI = {'x': 240, 'y': 120, 'w': 1180, 'h': 760, 'frame_w': 1920, 'frame_h': 1080}


def station_body(tenants, key='A', **overrides):
    data = {'name': f'Station {tenants.tag}', 'room_id': str(tenants.room[key]), 'status': 'active'}
    data.update(overrides)
    return data


# --- RF-AD-07 rooms ---------------------------------------------------------------------------

def test_room_code_is_unique_per_institution(as_user, tenants, no_remote_http):
    code = tenants.q('SELECT code FROM operating_room WHERE id = :r', r=tenants.room['B']).scalar_one()
    with as_user('admin', 'A', 'POST'):
        same_code_other_institution = facade.post('/operating-rooms', {'code': code, 'name': 'Allowed in A'})
        with pytest.raises(AdminDomainError) as duplicate:
            facade.post('/operating-rooms', {'code': code, 'name': 'Twice in A'})
    assert same_code_other_institution['code'] == code
    assert (duplicate.value.status, duplicate.value.code) == (409, 'room_code_exists')


def test_rooms_of_another_institution_are_invisible(as_user, tenants):
    with as_user('admin', 'A', 'PUT'):
        listed = facade.get('/operating-rooms')
        with pytest.raises(AdminDomainError) as exc:
            facade.put(f"/operating-rooms/{tenants.room['B']}", {'code': 'X', 'name': 'Hijack'})
    assert str(tenants.room['B']) not in {room['id'] for room in listed['items']}
    assert exc.value.status == 404


def test_deactivating_a_room_keeps_history(as_user, tenants):
    status = tenants.q("SELECT id FROM cat_operation_status WHERE code = 'scheduled'").scalar_one()
    operation = uuid.uuid4()
    tenants.q('INSERT INTO operation (id, status_id, institution_id, room_id) VALUES (:o, :s, :i, :r)',
              o=operation, s=status, i=tenants.inst['A'], r=tenants.room['A'])
    code = tenants.q('SELECT code FROM operating_room WHERE id = :r', r=tenants.room['A']).scalar_one()
    with as_user('admin', 'A', 'PUT'):
        room = facade.put(f"/operating-rooms/{tenants.room['A']}", {'code': code, 'name': 'Room', 'status': 'inactive'})
    assert room['active'] is False
    assert tenants.q('SELECT room_id FROM operation WHERE id = :o', o=operation).scalar_one() == tenants.room['A']
    assert [r.resource_id for r in tenants.audit('DEACTIVATE_OPERATING_ROOM')] == [tenants.room['A']]


# --- RF-AD-07 stations and ROI ----------------------------------------------------------------

def test_station_requires_a_room_of_the_institution(as_user, tenants):
    with as_user('admin', 'A', 'POST'):
        with pytest.raises(AdminDomainError) as missing:
            facade.post('/capture-stations', station_body(tenants, room_id=''))
        with pytest.raises(AdminDomainError) as foreign:
            facade.post('/capture-stations', station_body(tenants, room_id=str(tenants.room['B'])))
    assert (missing.value.status, missing.value.code) == (400, 'room_required')
    assert (foreign.value.status, foreign.value.code) == (400, 'invalid_room')


def test_roi_is_validated_and_stored_in_the_vision_format(as_user, tenants, no_remote_http):
    with as_user('admin', 'A', 'POST'):
        station = facade.post('/capture-stations', station_body(tenants, roi=ROI))
    stored = tenants.q('SELECT roi FROM capture_station WHERE id = :s', s=station['id']).scalar_one()
    assert stored == {'shape': 'rect', **ROI}  # same JSON as the seeded station read by the vision pipeline
    assert station['roi_configured'] is True and station['roi'] == ROI


@pytest.mark.parametrize('roi, code', [
    ({**ROI, 'h': ''}, 'roi_incomplete'),
    ({**ROI, 'x': 'ten'}, 'invalid_roi'),
    ({**ROI, 'x': -1}, 'invalid_roi'),
    ({**ROI, 'w': 0}, 'invalid_roi'),
    ({**ROI, 'x': 800}, 'roi_outside_frame'),           # 800 + 1180 > 1920
    ({**ROI, 'frame_h': 700}, 'roi_outside_frame'),     # 120 + 760 > 700
])
def test_invalid_roi_is_rejected(as_user, tenants, roi, code):
    with as_user('admin', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post('/capture-stations', station_body(tenants, roi=roi))
    assert (exc.value.status, exc.value.code) == (400, code)
    assert tenants.q('SELECT count(*) FROM capture_station WHERE name = :n', n=f'Station {tenants.tag}').scalar_one() == 0


def test_roi_update_keeps_extra_keys_keeps_when_empty_and_clears(as_user, tenants):
    station = uuid.uuid4()
    tenants.q("INSERT INTO capture_station (id, name, roi, room_id) VALUES (:s, 'Cam', CAST(:roi AS jsonb), :r)",
              s=station, roi=json.dumps({'shape': 'rect', 'calibration': 'v2', **ROI}), r=tenants.room['A'])
    with as_user('admin', 'A', 'PUT'):
        facade.put(f'/capture-stations/{station}', station_body(tenants, name='Cam', roi={**ROI, 'x': 100}))
        moved = tenants.q('SELECT roi FROM capture_station WHERE id = :s', s=station).scalar_one()
        facade.put(f'/capture-stations/{station}', station_body(tenants, name='Cam 2', roi=dict.fromkeys(ROI, '')))
        kept = tenants.q('SELECT roi FROM capture_station WHERE id = :s', s=station).scalar_one()
        facade.put(f'/capture-stations/{station}', station_body(tenants, name='Cam 2', roi=ROI, clear_roi='1'))
    assert moved == {'shape': 'rect', 'calibration': 'v2', **ROI, 'x': 100}
    assert kept == moved
    assert tenants.q('SELECT roi FROM capture_station WHERE id = :s', s=station).scalar_one() is None
    assert len(tenants.audit('UPDATE_CAPTURE_STATION')) == 3


def test_station_of_another_institution_is_invisible(as_user, tenants):
    station = uuid.uuid4()
    tenants.q("INSERT INTO capture_station (id, name, room_id) VALUES (:s, 'B cam', :r)", s=station, r=tenants.room['B'])
    with as_user('admin', 'A', 'PUT'), pytest.raises(AdminDomainError) as exc:
        facade.put(f'/capture-stations/{station}', station_body(tenants, roi=ROI))
    assert exc.value.status == 404
    assert tenants.q('SELECT roi FROM capture_station WHERE id = :s', s=station).scalar_one() is None


def test_station_screen_edits_roi(client, login_as, tenants, no_remote_http):
    login_as('admin', 'A')
    assert 'roi_frame_w' in client.get('/admin/configuration/capture-stations/new').get_data(as_text=True)
    response = client.post('/admin/configuration/capture-stations/new', data={
        'name': f'Web cam {tenants.tag}', 'operating_room': str(tenants.room['A']), 'status': 'active',
        **{f'roi_{key}': str(value) for key, value in ROI.items()}})
    assert response.status_code == 302
    assert tenants.q('SELECT roi FROM capture_station WHERE name = :n', n=f'Web cam {tenants.tag}').scalar_one()['w'] == 1180
    bad = client.post('/admin/configuration/capture-stations/new', data={
        'name': f'Bad cam {tenants.tag}', 'operating_room': str(tenants.room['A']), 'roi_x': '5000',
        **{f'roi_{key}': str(value) for key, value in ROI.items() if key != 'x'}})
    assert bad.status_code == 400 and 'must fit inside the camera frame' in bad.get_data(as_text=True)


# --- RF-AD-10 institution ---------------------------------------------------------------------

def test_institution_rename_is_audited_and_cannot_be_deactivated(as_user, tenants):
    with as_user('admin', 'A', 'PUT'):
        renamed = facade.put('/institution', {'name': f'Renamed {tenants.tag}', 'status': 'active'})
        with pytest.raises(AdminDomainError) as exc:
            facade.put('/institution', {'name': f'Renamed {tenants.tag}', 'status': 'inactive'})
    assert renamed['name'] == f'Renamed {tenants.tag}'
    assert tenants.q('SELECT name FROM institution WHERE id = :i', i=tenants.inst['B']).scalar_one() == f'Inst B {tenants.tag}'
    assert exc.value.code == 'cannot_deactivate_own_institution'
    assert tenants.q('SELECT active FROM institution WHERE id = :i', i=tenants.inst['A']).scalar_one() is True
    assert [r.resource_id for r in tenants.audit('UPDATE_INSTITUTION')] == [tenants.inst['A']]


# --- RF-AD-10 application parameters ------------------------------------------------------------

def test_parameters_default_update_persist_and_audit(as_user, tenants, no_remote_http):
    with as_user('admin', 'A', 'PUT'):
        defaults = {item['key']: item['value'] for item in facade.get('/settings')['items']}
        facade.put('/settings', {'kpi_or_hold_warning': '2', 'kpi_or_hold_danger': '4', 'privacy_notice_max_mb': 20,
                                 'family_example_allowed_mime': ['image/png', 'image/webp'],
                                 'privacy_notice_single_active': True})
        again = facade.put('/settings', {'kpi_or_hold_warning': 2})  # unchanged: not audited
    assert defaults['privacy_notice_max_mb'] == 10 and defaults['family_example_max_mb'] == 5
    assert defaults['password_min_length'] == 8 and defaults['privacy_notice_allowed_mime'] == ['application/pdf', 'text/html']
    rows = dict(tenants.q('SELECT key, value FROM app_setting WHERE institution_id = :i', i=tenants.inst['A']).all())
    assert rows == {'kpi_or_hold_warning': 2, 'kpi_or_hold_danger': 4, 'privacy_notice_max_mb': 20,
                    'family_example_allowed_mime': ['image/png', 'image/webp'], 'privacy_notice_single_active': True}
    assert {i['key']: i['value'] for i in again['items']}['kpi_or_hold_warning'] == 2
    assert len(tenants.audit('UPDATE_SETTING')) == 5
    # D-2: institution B still sees the defaults.
    assert settings.value(tenants.inst['B'], 'privacy_notice_max_mb') == 10


@pytest.mark.parametrize('payload, field', [
    ({'privacy_notice_max_mb': 0}, 'privacy_notice_max_mb'),
    ({'password_min_length': 4}, 'password_min_length'),
    ({'model_weights_max_mb': 'big'}, 'model_weights_max_mb'),
    ({'privacy_notice_allowed_mime': ['application/x-msdownload']}, 'privacy_notice_allowed_mime'),
    ({'family_example_allowed_mime': []}, 'family_example_allowed_mime'),
    ({'kpi_or_hold_warning': 9, 'kpi_or_hold_danger': 3}, 'kpi_or_hold_warning'),
    ({'unknown_key': 1}, 'unknown_key'),
    ({'missing_families_overlay_text': '   '}, 'missing_families_overlay_text'),
])
def test_invalid_parameters_are_rejected_without_partial_saves(as_user, tenants, payload, field):
    with as_user('admin', 'A', 'PUT'), pytest.raises(AdminDomainError) as exc:
        facade.put('/settings', {'kpi_awaiting_review_warning': 7, **payload})
    assert (exc.value.status, exc.value.code, exc.value.field) == (400, 'invalid_setting', field)
    assert tenants.q('SELECT count(*) FROM app_setting WHERE institution_id = :i', i=tenants.inst['A']).scalar_one() == 0


def test_parameters_screen(client, login_as, tenants):
    login_as('admin', 'A')
    page = client.get('/admin/configuration').get_data(as_text=True)
    assert 'Application Parameters' in page and 'name="privacy_notice_max_mb"' in page and '.pt' in page
    form = {item: '' for item in ()}
    form.update({'kpi_or_hold_warning': '1', 'kpi_or_hold_danger': '3', 'kpi_correction_required_warning': '1',
                 'kpi_correction_required_danger': '5', 'kpi_awaiting_review_warning': '5',
                 'kpi_awaiting_review_danger': '10', 'password_min_length': '12', 'family_example_max_mb': '5',
                 'family_example_allowed_mime[]': ['image/jpeg'], 'privacy_notice_max_mb': '10',
                 'privacy_notice_allowed_mime[]': ['application/pdf', 'text/html'], 'privacy_notice_single_active': 'false',
                 'model_weights_max_mb': '500', 'missing_families_overlay_text': 'Missing families warning'})
    assert client.post('/admin/configuration/settings', data=form).status_code == 302
    assert settings.value(tenants.inst['A'], 'password_min_length') == 12
    assert settings.value(tenants.inst['A'], 'family_example_allowed_mime') == ['image/jpeg']


def test_only_it_admin(client, login_as, as_user, tenants):
    login_as('supervisor', 'A')
    assert client.get('/admin/configuration').status_code == 403
    assert client.post('/admin/configuration/settings', data={'password_min_length': '9'}).status_code == 403
    with as_user('operator', 'A', 'PUT'), pytest.raises(AdminDomainError) as exc:
        facade.put('/settings', {'password_min_length': 9})
    assert exc.value.status == 403
