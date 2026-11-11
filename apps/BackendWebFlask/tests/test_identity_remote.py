"""ADMIN-01.2: user and role administration no longer goes to AuthService's identity CRUD.

services/admin_local/identity_remote.py is kept only for reference (deprecated, not dispatched).
User administration runs in-process (tests/test_admin_users.py, real PostgreSQL); AuthService keeps
authenticating every role and only receives session-revocation calls (admin_local.auth_sessions).
"""
import inspect

from services.admin_local import facade


def test_facade_never_dispatches_to_the_identity_adapter():
    source = inspect.getsource(facade)
    assert 'identity_remote' not in source.split('"""', 2)[2]


def test_admin_screens_use_the_local_domain_for_users_and_roles(client, login, fake_admin):
    login('it_admin')
    assert client.get('/admin/users').status_code == 200
    assert client.get('/admin/roles').status_code == 200
    assert ('GET', '/users') in {(method, path) for method, path, _, _ in fake_admin.calls}
    assert ('GET', '/roles') in {(method, path) for method, path, _, _ in fake_admin.calls}
