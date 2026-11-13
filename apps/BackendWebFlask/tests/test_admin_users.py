"""RF-AD-05 Users in the monolith (ADMIN-01.2), real PostgreSQL. AuthService CRUD is never called;
session revocation goes through admin_local.auth_sessions (simulated here, real in the smoke)."""
import uuid

import pytest
from werkzeug.security import check_password_hash

from services.admin_local import auth_sessions, facade
from services.admin_local.messages import AdminDomainError

from .admin_fixtures import as_user, login_as, no_remote_http, pg, tenants  # noqa: F401

PASSWORD = 'Strong-Pass-123'


@pytest.fixture
def revocations(monkeypatch):
    calls = []

    def revoke(user_id):
        calls.append(str(user_id))
        return 1
    monkeypatch.setattr(auth_sessions, 'revoke_user_sessions', revoke)
    return calls


def body(tenants, **overrides):
    data = {'name': 'Ana Operadora', 'email': f'ana.{tenants.tag}@test.invalid', 'role_codes': ['station_operator'],
            'status': 'active', 'password': PASSWORD, 'confirm_password': PASSWORD}
    data.update(overrides)
    return data


def user_row(tenants, user_id):
    return tenants.q('SELECT name, email, active, institution_id, password_hash FROM "user" WHERE id = :id', id=user_id).one()


def role_codes(tenants, user_id):
    return sorted(tenants.q('SELECT r.code FROM user_role ur JOIN role r ON r.id = ur.role_id WHERE ur.user_id = :u',
                            u=user_id).scalars())


def test_create_user_locally_with_auth_compatible_hash(as_user, tenants, no_remote_http):
    with as_user('admin', 'A', 'POST'):
        created = facade.post('/users', body(tenants, email=f'ANA.{tenants.tag}@Test.Invalid', status='inactive'))
    row = user_row(tenants, created['id'])
    assert row.email == f'ana.{tenants.tag}@test.invalid' and row.active is False
    assert row.institution_id == tenants.inst['A']
    assert check_password_hash(row.password_hash, PASSWORD)  # what AuthService /login verifies
    assert 'password' not in str(created).lower() or 'password_updated_at' in created
    assert role_codes(tenants, created['id']) == ['station_operator']
    assert [r.outcome for r in tenants.audit('CREATE_USER')] == ['success']


@pytest.mark.parametrize('overrides, status, code', [
    ({'email': 'not-an-email'}, 400, 'invalid_email'),
    ({'name': ' '}, 400, 'name_required'),
    ({'role_codes': []}, 400, 'roles_required'),
    ({'role_codes': ['ghost_role']}, 400, 'invalid_roles'),
    ({'password': 'short', 'confirm_password': 'short'}, 400, 'password_too_short'),
    ({'confirm_password': 'Other-Pass-123'}, 400, 'password_mismatch'),
])
def test_create_validation(as_user, tenants, overrides, status, code):
    with as_user('admin', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post('/users', body(tenants, **overrides))
    assert (exc.value.status, exc.value.code) == (status, code)


def test_duplicate_email_is_409_even_across_institutions(as_user, tenants):
    taken = tenants.q('SELECT email FROM "user" WHERE id = :id', id=tenants.admin['B']).scalar_one()
    with as_user('admin', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post('/users', body(tenants, email=taken.upper()))
    assert (exc.value.status, exc.value.code) == (409, 'email_exists')


def test_password_policy_comes_from_settings(as_user, tenants):
    tenants.q("INSERT INTO app_setting (institution_id, key, value) VALUES (:i, 'password_min_length', '16')",
              i=tenants.inst['A'])
    with as_user('admin', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post('/users', body(tenants))  # 15 characters
    assert exc.value.code == 'password_too_short' and '16' in exc.value.message


def test_scope_other_institution_is_invisible(as_user, tenants):
    foreign = tenants.users['operator']['B']
    with as_user('admin', 'A'):
        listed = facade.get('/users', status='all', per_page=50)
        for call in (lambda: facade.get(f'/users/{foreign}'),
                     lambda: facade.put(f'/users/{foreign}', body(tenants, email='x@test.invalid')),
                     lambda: facade.post(f'/users/{foreign}/deactivate'),
                     lambda: facade.post(f'/users/{foreign}/password', {'new_password': PASSWORD})):
            with pytest.raises(AdminDomainError) as exc:
                call()
            assert exc.value.status == 404
        with pytest.raises(AdminDomainError) as moved:
            facade.post('/users', body(tenants, institution_id=str(tenants.inst['B'])))
    assert {item['institution']['id'] for item in listed['items']} == {str(tenants.inst['A'])}
    assert str(foreign) not in {item['id'] for item in listed['items']}
    assert (moved.value.status, moved.value.code) == (400, 'foreign_institution')
    assert user_row(tenants, foreign).active is True


def test_roles_of_another_institution_cannot_be_assigned(as_user, tenants):
    # Codes are resolved inside the admin's institution only.
    with as_user('admin', 'A', 'POST'):
        created = facade.post('/users', body(tenants, role_codes=['or_charge']))
    assert tenants.q('SELECT r.institution_id FROM user_role ur JOIN role r ON r.id = ur.role_id WHERE ur.user_id = :u',
                     u=created['id']).scalar_one() == tenants.inst['A']


def test_deactivate_revokes_sessions_and_reactivate(as_user, tenants, revocations):
    target = tenants.users['operator']['A']
    with as_user('admin', 'A', 'POST'):
        result = facade.post(f'/users/{target}/deactivate')
        assert result['session_revocation'] == 'revoked'
        facade.post(f'/users/{target}/activate')
    assert revocations == [str(target)]
    assert user_row(tenants, target).active is True
    assert [r.resource_id for r in tenants.audit('DEACTIVATE_USER')] == [target]
    assert [r.resource_id for r in tenants.audit('ACTIVATE_USER')] == [target]


def test_password_reset_hashes_and_revokes(as_user, tenants, revocations):
    target = tenants.users['supervisor']['A']
    with as_user('admin', 'A', 'POST'):
        result = facade.post(f'/users/{target}/password', {'new_password': 'Another-Pass-9', 'confirm_new_password': 'Another-Pass-9'})
    assert check_password_hash(user_row(tenants, target).password_hash, 'Another-Pass-9')
    assert result['session_revocation'] == 'revoked' and revocations == [str(target)]
    assert [r.resource_id for r in tenants.audit('RESET_USER_PASSWORD')] == [target]


def test_revocation_failure_keeps_the_change_and_is_reported(client, login_as, tenants, monkeypatch):
    def down(user_id):
        raise auth_sessions.RevocationFailed('HTTP 503')
    monkeypatch.setattr(auth_sessions, 'revoke_user_sessions', down)
    target = tenants.users['operator']['A']
    login_as('admin', 'A')
    response = client.post(f'/admin/users/{target}/deactivate')
    page = client.get(response.headers['Location']).get_data(as_text=True)
    assert user_row(tenants, target).active is False  # still closed by AuthService's PostgreSQL check
    assert 'could not be closed' in page


def test_self_protection(as_user, tenants):
    admin = tenants.admin['A']
    with as_user('admin', 'A', 'POST'):
        with pytest.raises(AdminDomainError) as self_off:
            facade.post(f'/users/{admin}/deactivate')
        with pytest.raises(AdminDomainError) as self_demote:
            facade.put(f'/users/{admin}', body(tenants, email=f'admin.A.{tenants.tag}@test.invalid',
                                               role_codes=['station_operator']))
    assert (self_off.value.status, self_off.value.code) == (409, 'cannot_deactivate_self')
    assert (self_demote.value.status, self_demote.value.code) == (409, 'cannot_remove_own_admin')
    assert user_row(tenants, admin).active is True and role_codes(tenants, admin) == ['it_admin']


def _make_admin(tenants, user_id):
    tenants.q("INSERT INTO user_role (user_id, role_id) SELECT :u, id FROM role WHERE institution_id = :i AND code = 'it_admin'",
              u=user_id, i=tenants.inst['A'])


def test_another_admin_can_be_removed_while_one_remains(as_user, tenants, revocations):
    second = tenants.users['supervisor']['A']
    _make_admin(tenants, second)
    with as_user('admin', 'A', 'POST'):
        facade.post(f'/users/{second}/deactivate')
        facade.post(f'/users/{second}/activate')
        facade.put(f'/users/{second}', body(tenants, email=f'supervisor.A.{tenants.tag}@test.invalid',
                                            role_codes=['spd_supervisor']))
    assert role_codes(tenants, second) == ['spd_supervisor']


def test_the_last_active_admin_cannot_be_removed(as_user, tenants, revocations):
    """In PostgreSQL only `second` is an effective it_admin of A (the caller's token is stale): the guard
    still refuses to leave the institution without an active administrator."""
    second = tenants.users['supervisor']['A']
    _make_admin(tenants, second)
    tenants.q("DELETE FROM user_role WHERE user_id = :u", u=tenants.admin['A'])
    tenants.q("INSERT INTO user_role (user_id, role_id) SELECT :u, id FROM role WHERE institution_id = :i "
              "AND code = 'station_operator'", u=tenants.admin['A'], i=tenants.inst['A'])
    with as_user('admin', 'A', 'POST'):
        with pytest.raises(AdminDomainError) as deactivate:
            facade.post(f'/users/{second}/deactivate')
        with pytest.raises(AdminDomainError) as demote:
            facade.put(f'/users/{second}', body(tenants, email=f'supervisor.A.{tenants.tag}@test.invalid',
                                                role_codes=['spd_supervisor']))
    assert (deactivate.value.status, deactivate.value.code) == (409, 'last_admin')
    assert demote.value.code == 'last_admin'
    assert user_row(tenants, second).active is True and 'it_admin' in role_codes(tenants, second)
    assert revocations == []


def test_admin_of_another_institution_is_not_reachable(as_user, tenants):
    with as_user('admin', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post(f"/users/{tenants.admin['B']}/deactivate")
    assert exc.value.status == 404


def test_floor_supervisor_rooms(as_user, tenants):
    with as_user('admin', 'A', 'POST'):
        created = facade.post('/users', body(tenants, role_codes=['or_charge'], room_ids=[str(tenants.room['A'])]))
        with pytest.raises(AdminDomainError) as foreign_room:
            facade.post('/users', body(tenants, email=f'x.{tenants.tag}@test.invalid', role_codes=['or_charge'],
                                       room_ids=[str(tenants.room['B'])]))
        with pytest.raises(AdminDomainError) as not_floor:
            facade.post('/users', body(tenants, email=f'y.{tenants.tag}@test.invalid', room_ids=[str(tenants.room['A'])]))
        # Removing or_charge clears the rooms.
        facade.put(f"/users/{created['id']}", body(tenants, role_codes=['station_operator']))
    assert created['operating_rooms'][0]['id'] == str(tenants.room['A'])
    assert (foreign_room.value.status, foreign_room.value.code) == (400, 'invalid_room')
    assert not_floor.value.code == 'rooms_need_floor_supervisor'
    assert tenants.q('SELECT count(*) FROM user_operating_room WHERE user_id = :u', u=created['id']).scalar_one() == 0
    assert len(tenants.audit('CHANGE_USER_ROOMS')) == 2


def test_screens_create_and_edit(client, login_as, tenants, no_remote_http, revocations):
    login_as('admin', 'A')
    form = client.get('/admin/users/new').get_data(as_text=True)
    assert f'Inst A {tenants.tag}' in form and 'name="institution"' not in form
    assert 'value="or_charge"' in form and f'value="{tenants.room["A"]}"' in form and str(tenants.room['B']) not in form
    response = client.post('/admin/users/new', data={
        'name': 'Floor A', 'email': f'floor.new.{tenants.tag}@test.invalid', 'roles[]': ['or_charge'],
        'rooms[]': [str(tenants.room['A'])], 'status': 'active', 'password': PASSWORD, 'confirm_password': PASSWORD})
    assert response.status_code == 302
    new_id = tenants.q('SELECT id FROM "user" WHERE email = :e', e=f'floor.new.{tenants.tag}@test.invalid').scalar_one()
    listing = client.get('/admin/users?status=all').get_data(as_text=True)
    assert 'Floor A' in listing and f'admin B {tenants.tag}' not in listing
    edit = client.post(f'/admin/users/{new_id}/edit', data={'name': 'Floor A2', 'email': f'floor.new.{tenants.tag}@test.invalid',
                                                            'roles[]': ['or_charge'], 'status': 'inactive'})
    assert edit.status_code == 302 and user_row(tenants, new_id).active is False and revocations == [str(new_id)]
    assert client.get(f"/admin/users/{tenants.users['operator']['B']}/edit").status_code == 404


def test_only_it_admin(client, login_as, as_user, tenants):
    login_as('supervisor', 'B')
    assert client.get('/admin/users').status_code == 403
    with as_user('operator', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post('/users', body(tenants))
    assert exc.value.status == 403
