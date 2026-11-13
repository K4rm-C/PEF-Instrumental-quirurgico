"""MS-01 — P3 (write permissions by role, checked inside the service) and P4 (inactive families).

Real PostgreSQL/Redis, rolled-back transaction (see conftest). World: family "c" is INACTIVE and the kit
kits_a["v2"] already contains it (a historical reference that must keep working).

Permission matrix (RF RACI: families → Administration; pieces and kits → SPD, "not Admin"):

    operation              it_admin   spd_supervisor   station_operator   or_charge   it_admin of B only
    POST/PUT families        yes          403               403              403            403
    POST/PUT instruments     403          yes               403              403            403
    POST/PUT kits            403          yes               403              403            403
    GET (any)                yes          yes               yes              yes             —
"""
from __future__ import annotations

import dataclasses
import uuid

import pytest
from sqlalchemy import text

BASE = "/api/v1/catalog"


@pytest.fixture
def or_charge_a(db_conn, world):
    """A floor supervisor (fourth product role) of institution A: it must not get write permissions."""
    role_id, user_id = uuid.uuid4(), uuid.uuid4()
    db_conn.execute(text("INSERT INTO role (id, code, description, institution_id) VALUES (:id, 'or_charge', 'test', :i)"),
                    {"id": role_id, "i": world.institution_a})
    db_conn.execute(text('INSERT INTO "user" (id, name, email, password_hash, active, institution_id) '
                         "VALUES (:id, 'OR charge', :e, 'x', TRUE, :i)"),
                    {"id": user_id, "e": f"orc.{world.tag}@catalog-test.invalid", "i": world.institution_a})
    db_conn.execute(text("INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)"), {"u": user_id, "r": role_id})
    world.users["or_charge_a"] = user_id
    return "or_charge_a"


def _operations(world):
    tag = world.tag
    family = {"name": "Renamed", "category_code": world.category_code}
    instrument = {"family_id": str(world.families["a"]), "cycle_status_code": world.cycle_status_code}
    kit = {"name": f"Matrix kit {tag}", "items": [{"family_id": str(world.families["a"]), "quantity": 1}]}
    return {
        "POST families": ("post", "families", "families",
                          {"code": f"zz_mx_{tag}", "name": "Matrix family", "category_code": world.category_code}),
        "PUT families": ("put", f"families/{world.families['b']}", "families", family),
        "POST instruments": ("post", "instruments", "stock", {**instrument, "internal_code": f"MX-{tag}"}),
        "PUT instruments": ("put", f"instruments/{world.instruments_a[0]}", "stock", instrument),
        "POST kits": ("post", "kits", "stock", kit),
        "PUT kits": ("put", f"kits/{world.kits_a['v1']}", "stock",
                     {"name": f"Test Kit {tag}", "items": [{"family_id": str(world.families["a"]), "quantity": 5}]}),
    }


ALLOWED = {"families": {"admin_a", "multi_a"}, "stock": {"supervisor_a"}}
ROLES = ["admin_a", "multi_a", "supervisor_a", "operator_a", "or_charge_a", "foreign_role_a"]
OPERATIONS = ["POST families", "PUT families", "POST instruments", "PUT instruments", "POST kits", "PUT kits"]


def _denied(db_conn, user_id) -> int:
    return db_conn.execute(text("SELECT count(*) FROM access_audit WHERE actor_user_id = :u AND outcome = 'denied' "
                                "AND action LIKE 'DENIED_%'"), {"u": user_id}).scalar_one()


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("operation", OPERATIONS)
def test_write_permission_matrix(client, world, auth_header, db_conn, or_charge_a, role, operation):
    method, path, group, body = _operations(world)[operation]
    before = _denied(db_conn, world.users[role])
    response = getattr(client, method)(f"{BASE}/{path}", json=body, headers=auth_header(role))
    if role in ALLOWED[group]:
        assert response.status_code in (200, 201), (role, operation, response.get_json())
        assert _denied(db_conn, world.users[role]) == before
    else:
        assert response.status_code == 403, (role, operation, response.status_code)
        assert response.get_json()["error"] == "forbidden"
        assert _denied(db_conn, world.users[role]) == before + 1        # audited in access_audit (DENIED_*)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_writes_need_a_valid_token(client, world, operation):
    method, path, _, body = _operations(world)[operation]
    assert getattr(client, method)(f"{BASE}/{path}", json=body).status_code == 401
    bad = {"Authorization": "Bearer not-a-jwt"}
    assert getattr(client, method)(f"{BASE}/{path}", json=body, headers=bad).status_code == 401


@pytest.mark.parametrize("role", ["admin_a", "supervisor_a", "operator_a", "or_charge_a"])
def test_reads_are_unchanged_for_every_role(client, world, auth_header, or_charge_a, role):
    for path in ("families", "instruments", "kits", f"kits/{world.kits_a['v2']}", "categories"):
        assert client.get(f"{BASE}/{path}", headers=auth_header(role)).status_code == 200, (role, path)


def test_revoked_token_cannot_write(client, world, auth_header):
    method, path, _, body = _operations(world)["POST kits"]
    headers = auth_header("supervisor_a", revoked=True)
    assert getattr(client, method)(f"{BASE}/{path}", json=body, headers=headers).status_code == 401


# --- Steward institution for the shared family catalog -----------------------------------------


@pytest.fixture
def steward(app):
    def set_to(institution_id):
        app.config["CATALOG_SETTINGS"] = dataclasses.replace(
            app.config["CATALOG_SETTINGS"], catalog_steward_institution_id=str(institution_id or ""))
    yield set_to
    set_to("")


def test_steward_restricts_family_writes_to_its_institution(client, world, auth_header, db_conn, steward):
    steward(world.institution_b)
    before = _denied(db_conn, world.users["admin_a"])
    for operation in ("POST families", "PUT families"):
        method, path, _, body = _operations(world)[operation]
        response = getattr(client, method)(f"{BASE}/{path}", json=body, headers=auth_header("admin_a"))
        assert response.status_code == 403 and response.get_json()["error"] == "shared_catalog_read_only"
    assert _denied(db_conn, world.users["admin_a"]) == before + 2
    assert client.get(f"{BASE}/families", headers=auth_header("admin_a")).status_code == 200   # still reads
    steward(world.institution_a)
    method, path, _, body = _operations(world)["PUT families"]
    assert getattr(client, method)(f"{BASE}/{path}", json=body, headers=auth_header("admin_a")).status_code == 200


def test_steward_does_not_change_stock_permissions(client, world, auth_header, steward):
    steward(world.institution_b)
    method, path, _, body = _operations(world)["POST kits"]
    assert getattr(client, method)(f"{BASE}/{path}", json=body, headers=auth_header("supervisor_a")).status_code == 201


# --- P4: inactive families ----------------------------------------------------------------------


def _instrument_body(world, family_key, code=None):
    body = {"family_id": str(world.families[family_key]), "cycle_status_code": world.cycle_status_code}
    return {**body, "internal_code": code} if code else body


def test_new_instrument_with_an_inactive_family_is_refused(client, world, auth_header, db_conn):
    response = client.post(f"{BASE}/instruments", json=_instrument_body(world, "c", f"INA-{world.tag}"),
                           headers=auth_header("supervisor_a"))
    assert response.status_code == 422 and response.get_json()["details"] == {"field": "family_id",
                                                                              "reason": "inactive_family"}
    assert db_conn.execute(text("SELECT count(*) FROM instrument WHERE internal_code = :c"),
                           {"c": f"INA-{world.tag}"}).scalar_one() == 0
    xml = (f"<instrument><internal_code>INX-{world.tag}</internal_code><family_id>{world.families['c']}</family_id>"
           f"<cycle_status_code>{world.cycle_status_code}</cycle_status_code></instrument>").encode()
    response = client.post(f"{BASE}/instruments", data=xml, headers={**auth_header("supervisor_a"),
                                                                      "Content-Type": "application/xml"})
    assert response.status_code == 422 and response.get_json()["details"]["reason"] == "inactive_family"


def test_unknown_family_keeps_its_previous_answer(client, world, auth_header):
    body = {**_instrument_body(world, "a", f"UNK-{world.tag}"), "family_id": str(uuid.uuid4())}
    response = client.post(f"{BASE}/instruments", json=body, headers=auth_header("supervisor_a"))
    assert response.status_code == 422 and response.get_json()["details"]["reason"] == "invalid_family_or_cycle"


def test_moving_an_instrument_to_an_inactive_family_is_refused(client, world, auth_header):
    url = f"{BASE}/instruments/{world.instruments_a[0]}"
    response = client.put(url, json=_instrument_body(world, "c"), headers=auth_header("supervisor_a"))
    assert response.status_code == 422 and response.get_json()["details"]["reason"] == "inactive_family"
    assert client.put(url, json=_instrument_body(world, "b"), headers=auth_header("supervisor_a")).status_code == 200


def test_historical_instrument_of_a_deactivated_family_keeps_working(client, world, auth_header, db_conn):
    instrument_id = world.instruments_a[0]
    current = db_conn.execute(text("SELECT family_id FROM instrument WHERE id = :i"), {"i": instrument_id}).scalar_one()
    db_conn.execute(text("UPDATE instrument_family SET active = FALSE WHERE id = :f"), {"f": current})
    body = {"family_id": str(current), "cycle_status_code": world.cycle_status_code, "active": True}
    response = client.put(f"{BASE}/instruments/{instrument_id}", json=body, headers=auth_header("supervisor_a"))
    assert response.status_code == 200
    detail = client.get(f"{BASE}/instruments/{instrument_id}", headers=auth_header("supervisor_a"))
    assert detail.status_code == 200 and detail.get_json()["family_id"] == str(current)


def test_new_kit_with_an_inactive_family_is_refused(client, world, auth_header, db_conn):
    body = {"name": f"Inactive kit {world.tag}", "items": [{"family_id": str(world.families["a"]), "quantity": 1},
                                                             {"family_id": str(world.families["c"]), "quantity": 1}]}
    response = client.post(f"{BASE}/kits", json=body, headers=auth_header("supervisor_a"))
    assert response.status_code == 422 and response.get_json()["details"] == {"field": "items",
                                                                              "reason": "inactive_family"}
    assert db_conn.execute(text("SELECT count(*) FROM kit WHERE name = :n"), {"n": body["name"]}).scalar_one() == 0


def test_adding_an_inactive_family_to_an_existing_kit_is_refused(client, world, auth_header):
    kit_id = world.kits_a["v1"]          # contains only family "a"
    body = {"name": f"Test Kit {world.tag}", "items": [{"family_id": str(world.families["a"]), "quantity": 1},
                                                         {"family_id": str(world.families["c"]), "quantity": 2}]}
    response = client.put(f"{BASE}/kits/{kit_id}", json=body, headers=auth_header("supervisor_a"))
    assert response.status_code == 422 and response.get_json()["details"]["reason"] == "inactive_family"


def test_historical_kit_keeps_its_deactivated_family(client, world, auth_header, db_conn):
    """kits_a["v2"] already contains the inactive family "c": changing a quantity creates the new version
    with the same composition; nothing is deleted or rewritten automatically."""
    kit_id = world.kits_a["v2"]
    detail = client.get(f"{BASE}/kits/{kit_id}?expand=detail", headers=auth_header("supervisor_a")).get_json()
    assert any(i["family_id"] == str(world.families["c"]) and i["family_active"] is False for i in detail["items"])
    items = [{"family_id": i["family_id"], "quantity": i["quantity"] + (1 if i["family_id"] == str(world.families["a"])
                                                                        else 0)} for i in detail["items"]]
    response = client.put(f"{BASE}/kits/{kit_id}", json={"name": detail["name"], "items": items},
                          headers=auth_header("supervisor_a"))
    assert response.status_code == 201
    assert str(world.families["c"]) in [i["family_id"] for i in response.get_json()["items"]]
    # Removing the inactive family from a kit is always allowed.
    new_id = response.get_json()["id"]
    trimmed = [i for i in items if i["family_id"] != str(world.families["c"])]
    assert client.put(f"{BASE}/kits/{new_id}", json={"name": detail["name"], "items": trimmed},
                      headers=auth_header("supervisor_a")).status_code == 201
    old = db_conn.execute(text("SELECT count(*) FROM kit_item WHERE kit_id = :k AND family_id = :f"),
                          {"k": kit_id, "f": world.families["c"]}).scalar_one()
    assert old == 1                       # the historical version still holds its row
