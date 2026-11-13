"""MS-12 Supervisor screen of kit equivalences (CatalogService answers simulated; the service is tested on real
PostgreSQL in apps/CatalogService/tests/test_ms12_equivalences.py and end to end in scripts/e2e_ms11_compose.py)."""

import uuid

import pytest
import requests

from services import equivalences_remote

KIT = str(uuid.uuid4())
FAM_A, FAM_B, FAM_C = (str(uuid.uuid4()) for _ in range(3))
EQ = str(uuid.uuid4())
KIT_DETAIL = {"id": KIT, "name": "Basic Laparotomy", "version": 4, "active": True,
              "items": [{"family_id": FAM_A, "family_code": "KELLY", "family_name": "Kelly forceps", "quantity": 2}]}
EQUIVALENCE = {"id": EQ, "kit_id": KIT, "family_id": FAM_A, "family_code": "KELLY", "family_name": "Kelly forceps",
               "equivalent_family_id": FAM_B, "equivalent_code": "CRILE", "equivalent_name": "Crile forceps",
               "notes": "Same pattern", "active": True, "authorized_by_user_id": str(uuid.uuid4()),
               "authorized_by_name": "Super Visor", "authorized_at": "2026-11-10T10:00:00Z",
               "revoked_by_user_id": None, "revoked_by_name": None, "revoked_at": None}


class Fake:
    def __init__(self, answers):
        self.calls, self.answers = [], answers

    def __call__(self, method, url, json=None, params=None, timeout=None, headers=None):
        self.calls.append((method, url, json, params, headers))
        status, body = self.answers.get((method, url.split("/api/v1", 1)[1]), (404, {"error": "not_found"}))
        response = requests.Response()
        response.status_code = status
        response._content = __import__("json").dumps(body).encode()
        return response


@pytest.fixture
def services(app, monkeypatch):
    def install(answers=None):
        base = {("GET", "/catalog/kits"): (200, [{"id": KIT, "name": "Basic Laparotomy", "version": 4, "active": True},
                                                 {"id": str(uuid.uuid4()), "name": "Old", "version": 1, "active": False}]),
                ("GET", f"/catalog/kits/{KIT}"): (200, KIT_DETAIL),
                ("GET", f"/catalog/kits/{KIT}/equivalences"): (200, [EQUIVALENCE]),
                ("GET", "/catalog/families"): (200, [
                    {"id": FAM_A, "code": "KELLY", "name": "Kelly forceps", "active": True},
                    {"id": FAM_B, "code": "CRILE", "name": "Crile forceps", "active": True},
                    {"id": FAM_C, "code": "OLD", "name": "Retired family", "active": False}])}
        base.update(answers or {})
        fake = Fake(base)
        monkeypatch.setattr(equivalences_remote, "CATALOG_SERVICE_URL", "http://catalog.test")
        monkeypatch.setattr(requests, "request", fake)
        return fake
    return install


def test_only_the_spd_supervisor_opens_the_screen(client, login, services):
    services()
    login("operator")
    assert client.get("/supervisor/kit-equivalences").status_code in (302, 403)


def test_lists_active_kits_and_the_authorizations_of_the_chosen_kit(client, login, services):
    fake = services()
    login("spd_supervisor")
    page = client.get("/supervisor/kit-equivalences").get_data(as_text=True)
    assert "Basic Laparotomy · v4" in page and "Old · v1" not in page and "data-equivalence-form" not in page
    detail = client.get(f"/supervisor/kit-equivalences?kit_id={KIT}").get_data(as_text=True)
    assert f'data-equivalence="{EQ}"' in detail and "Super Visor" in detail and "Crile forceps" in detail
    # Equivalent options: active families that are not items of the kit.
    options = detail.split('name="equivalent_family_id"', 1)[1].split("</select>", 1)[0]
    assert FAM_B in options and FAM_A not in options and FAM_C not in options
    assert all(call[4]["Authorization"] == "Bearer test-access-token" for call in fake.calls)
    assert ("GET", f"http://catalog.test/api/v1/catalog/kits/{KIT}/equivalences") in {c[:2] for c in fake.calls}
    assert next(c[3] for c in fake.calls if c[1].endswith("/equivalences")) == {"status": "all"}


def test_authorize_forwards_the_form_and_shows_the_rule_that_failed(client, login, services):
    fake = services({("POST", f"/catalog/kits/{KIT}/equivalences"): (
        422, {"error": "equivalent_in_kit", "message": "The equivalent family is already part of the kit",
              "details": {"field": "equivalent_family_id"}})})
    login("spd_supervisor")
    response = client.post("/supervisor/kit-equivalences", data={"kit_id": KIT, "action": "authorize",
                                                                   "family_id": FAM_A, "equivalent_family_id": FAM_B,
                                                                   "notes": "  "})
    assert response.status_code == 422
    assert "The equivalent family is already part of the kit." in response.get_data(as_text=True)
    sent = next(c for c in fake.calls if c[0] == "POST")[2]
    assert sent == {"family_id": FAM_A, "equivalent_family_id": FAM_B, "notes": None}


def test_authorize_and_revoke_redirect_back_to_the_kit(client, login, services):
    fake = services({("POST", f"/catalog/kits/{KIT}/equivalences"): (201, EQUIVALENCE),
                     ("DELETE", f"/catalog/kits/{KIT}/equivalences/{EQ}"): (200, {**EQUIVALENCE, "active": False})})
    login("spd_supervisor")
    created = client.post("/supervisor/kit-equivalences", data={"kit_id": KIT, "action": "authorize",
                                                                  "family_id": FAM_A, "equivalent_family_id": FAM_B})
    assert created.status_code == 302 and created.headers["Location"].endswith(f"kit_id={KIT}")
    revoked = client.post("/supervisor/kit-equivalences", data={"kit_id": KIT, "action": "revoke", "equivalence_id": EQ})
    assert revoked.status_code == 302
    assert ("DELETE", f"http://catalog.test/api/v1/catalog/kits/{KIT}/equivalences/{EQ}") in {c[:2] for c in fake.calls}


def test_catalog_down_is_reported_without_writing(client, login, services, monkeypatch):
    services()

    def down(*args, **kwargs):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(requests, "request", down)
    login("spd_supervisor")
    response = client.get(f"/supervisor/kit-equivalences?kit_id={KIT}")
    assert response.status_code == 503 and "temporarily unavailable" in response.get_data(as_text=True)
