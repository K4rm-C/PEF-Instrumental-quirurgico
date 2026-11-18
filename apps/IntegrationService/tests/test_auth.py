"""Who may call: users vs machine clients, scopes from PostgreSQL, revocation, deactivation, isolation."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text


def test_machine_client_with_scope_reads_sync_resources(client, machine, as_client):
    machine("his", scopes=("sync.catalog.read",))
    body = client.get("/api/v1/sync/resources", headers=as_client("his")).get_json()
    assert {r["name"] for r in body["resources"]} == {"instrument_family", "kit", "instrument", "surgical_tray"}


def test_scope_removed_in_the_database_stops_working_at_once(client, machine, as_client, db_conn, world):
    machine("his", scopes=("sync.catalog.read",))
    headers = as_client("his")
    db_conn.execute(text("UPDATE integration_client SET scopes = '[]'::jsonb WHERE id = :id"), {"id": world.clients["his"]})
    assert client.get("/api/v1/sync/resources", headers=headers).status_code == 403


def test_claim_cannot_widen_the_scopes(client, machine, as_client):
    machine("his", scopes=("sync.catalog.read",))
    forged = as_client("his", scopes=["sync.catalog.read", "sync.sessions.read"])
    assert client.get("/api/v1/sync/changes?types=work_session", headers=forged).status_code == 403


def test_deactivated_revoked_or_expired_clients_are_refused(client, machine, as_client, db_conn, world, tokens):
    machine("off", scopes=("sync.catalog.read",), active=False)
    assert client.get("/api/v1/sync/resources", headers=as_client("off")).status_code == 401
    machine("rev", scopes=("sync.catalog.read",))
    revoked = tokens.client(world.clients["rev"], world.inst["A"], ["sync.catalog.read"], revoked=True)
    assert client.get("/api/v1/sync/resources", headers={"Authorization": f"Bearer {revoked}"}).status_code == 401
    expired = tokens.client(world.clients["rev"], world.inst["A"], ["sync.catalog.read"], lifetime=timedelta(seconds=-5))
    assert client.get("/api/v1/sync/resources", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_client_token_bound_to_another_institution_is_refused(client, machine, tokens, world):
    machine("his", scopes=("sync.catalog.read",))
    moved = tokens.client(world.clients["his"], world.inst["B"], ["sync.catalog.read"])
    assert client.get("/api/v1/sync/resources", headers={"Authorization": f"Bearer {moved}"}).status_code == 401


def test_users_need_a_role_and_a_live_session(client, as_user, tokens, world):
    assert client.get("/api/v1/sync/resources", headers=as_user("op_a")).status_code == 200
    user = world.users["op_a"]
    revoked = tokens.user(user.id, user.institution_id, revoked=True)
    assert client.get("/api/v1/sync/resources", headers={"Authorization": f"Bearer {revoked}"}).status_code == 401
    assert client.get("/api/v1/integration/endpoints", headers=as_user("spd_a")).status_code == 403
    assert client.get("/api/v1/integration/endpoints", headers=as_user("admin_a")).status_code == 200


def test_a_user_token_cannot_read_fhir_and_a_client_cannot_administer(client, as_user, machine, as_client):
    assert client.get("/fhir/r4/Location?_count=1", headers=as_user("admin_a")).status_code == 403
    machine("his", scopes=("fhir.location.read",))
    assert client.get("/api/v1/integration/endpoints", headers=as_client("his")).status_code == 403
