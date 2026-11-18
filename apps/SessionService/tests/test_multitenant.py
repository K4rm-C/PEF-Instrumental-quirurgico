"""Role/tenant scope across every read endpoint (no existence leakage)."""

from __future__ import annotations

import pytest

BASE = "/api/v1/sessions"
SUBRESOURCES = ("", "/expected-inventory", "/counts", "/discrepancies")


def _paths(session_id):
    return [f"{BASE}/{session_id}{suffix}" for suffix in SUBRESOURCES]


def test_operator_a_own_session_visible_everywhere(client, world, as_user):
    for path in _paths(world.sessions["a3"].id):
        assert client.get(path, headers=as_user("op_a")).status_code == 200, path


@pytest.mark.parametrize("session_key", ["a2", "b1", "no_operation"])
def test_operator_a_cannot_read_unassigned_foreign_or_operationless(client, world, as_user, session_key):
    for path in _paths(world.sessions[session_key].id):
        response = client.get(path, headers=as_user("op_a"))
        assert response.status_code == 404, path
        assert response.get_json()["error"] == "not_found"


def test_supervisor_a_reads_every_session_of_tenant(client, world, as_user):
    for key in ("a1", "a2", "a3", "a_unscheduled"):
        for path in _paths(world.sessions[key].id):
            assert client.get(path, headers=as_user("spd_a")).status_code == 200, path


@pytest.mark.parametrize("session_key", ["b1", "no_operation"])
def test_supervisor_a_cannot_read_foreign_or_operationless(client, world, as_user, session_key):
    for path in _paths(world.sessions[session_key].id):
        assert client.get(path, headers=as_user("spd_a")).status_code == 404, path


def test_supervisor_b_cannot_read_tenant_a(client, world, as_user):
    for path in _paths(world.sessions["a3"].id):
        assert client.get(path, headers=as_user("spd_b")).status_code == 404, path


def test_it_admin_is_forbidden_everywhere(client, world, as_user):
    assert client.get(BASE, headers=as_user("admin_a")).status_code == 403
    for path in _paths(world.sessions["a3"].id):
        assert client.get(path, headers=as_user("admin_a")).status_code == 403, path


def test_multi_role_reads_any_session_of_tenant(client, world, as_user):
    # multi_a has station_operator + spd_supervisor: supervisor scope.
    for path in _paths(world.sessions["a2"].id):
        assert client.get(path, headers=as_user("multi_a")).status_code == 200, path
    for path in _paths(world.sessions["b1"].id):
        assert client.get(path, headers=as_user("multi_a")).status_code == 404, path


def test_foreign_body_in_404_does_not_leak(client, world, as_user):
    response = client.get(f"{BASE}/{world.sessions['b1'].id}/counts", headers=as_user("spd_a"))
    raw = response.get_data(as_text=True)
    assert str(world.institutions["B"]) not in raw and "B shortage" not in raw
