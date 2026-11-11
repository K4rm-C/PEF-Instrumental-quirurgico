"""MS-07B FHIR deliveries: authorized destinations only, idempotent requests, transaction Bundle with PUT,
temporary failures retried, permanent failures, audit. The FHIR server here is a local HTTP double; a real
HAPI FHIR R4 server is used by scripts/e2e_ms07_sync_fhir.py."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from main import create_app
from services.deliveries import DeliveryService
from tests.conftest import make_settings


@pytest.fixture
def fhir_server(upstream):
    def start(answers):
        answers = iter(answers)
        return upstream(lambda m, p, n: next(answers))
    return start


def endpoint(client, headers, url, **extra):
    return client.post("/api/v1/integration/endpoints", json={"name": f"HIS {uuid.uuid4().hex[:6]}", "base_url": url, **extra},
                       headers=headers)


def request(client, headers, endpoint_id, encounter, key="deliv-0000001"):
    return client.post("/api/v1/integration/deliveries", json={"endpoint_id": endpoint_id, "encounter_id": str(encounter)},
                       headers={**headers, "Idempotency-Key": key})


def test_only_authorized_hosts_and_https(client, as_user):
    admin = as_user("admin_a")
    assert endpoint(client, admin, "https://his.example.org/fhir").get_json()["details"]["reason"] == "host_not_allowed"
    assert endpoint(client, admin, "ftp://127.0.0.1/fhir").status_code == 422
    assert endpoint(client, admin, "http://user:pw@127.0.0.1/fhir").status_code == 422
    assert endpoint(client, admin, "http://127.0.0.1:1/fhir", resource_types=["Observation"]).status_code == 422
    assert endpoint(client, as_user("spd_a"), "http://127.0.0.1:1/fhir").status_code == 403


def test_https_required_outside_the_test_stack(database, redis_client, as_user):
    app = create_app(make_settings(allow_plain_http=False), database=database, redis_client=redis_client)
    response = endpoint(app.test_client(), as_user("admin_a"), "http://127.0.0.1:9/fhir")
    assert response.status_code == 422 and response.get_json()["details"]["reason"] == "https_required"


def test_delivery_is_idempotent_and_sent_as_a_transaction_bundle(client, as_user, world, database, settings, fhir_server, db_conn):
    server = fhir_server([(200, {"resourceType": "Bundle", "type": "transaction-response"}, 0)])
    admin = as_user("admin_a")
    ep = endpoint(client, admin, server.url + "/fhir").get_json()
    first = request(client, admin, ep["id"], world.operations["A"])
    again = request(client, admin, ep["id"], world.operations["A"])
    assert first.status_code == 202 and again.status_code == 200 and first.get_json()["id"] == again.get_json()["id"]
    other = request(client, admin, ep["id"], world.operations["A"], key="deliv-0000001x")
    assert other.status_code == 202
    mismatch = client.post("/api/v1/integration/deliveries", json={"endpoint_id": ep["id"], "encounter_id": str(uuid.uuid4())},
                           headers={**admin, "Idempotency-Key": "deliv-0000001"})
    assert mismatch.status_code == 422
    db_conn.execute(text("DELETE FROM integration_delivery WHERE id = :id"), {"id": other.get_json()["id"]})
    stats = DeliveryService(database, settings).deliver_due()
    assert stats == {"sent": 1, "retry": 0, "failed": 0}
    body = server.calls[0]["body"]
    assert body["type"] == "transaction" and server.calls[0]["headers"]["Content-Type"] == "application/fhir+json"
    requests_ = [(e["request"]["method"], e["request"]["url"].split("/")[0]) for e in body["entry"]]
    assert requests_ == [("PUT", "Organization"), ("PUT", "Location"), ("PUT", "Practitioner"), ("PUT", "Encounter")]
    assert all(e["request"]["url"] == f"{e['resource']['resourceType']}/{e['resource']['id']}" for e in body["entry"])
    assert "Patient" not in str(body)                                  # not in the endpoint's resource types
    detail = client.get(f"/api/v1/integration/deliveries/{first.get_json()['id']}", headers=admin).get_json()
    assert detail["status"] == "sent" and [a["outcome"] for a in detail["attempts"]] == ["sent"]
    audit = db_conn.execute(text("SELECT action FROM access_audit WHERE resource_id = :id ORDER BY occurred_at"),
                            {"id": first.get_json()["id"]}).scalars().all()
    assert audit == ["REQUEST_FHIR_DELIVERY", "FHIR_DELIVERY_SENT"]


def test_temporary_failures_are_retried_then_succeed(client, as_user, world, database, settings, fhir_server, db_conn):
    server = fhir_server([(503, {}, 0), (500, {}, 0), (200, {}, 0)])
    admin = as_user("admin_a")
    ep = endpoint(client, admin, server.url).get_json()
    delivery = request(client, admin, ep["id"], world.operations["A"]).get_json()
    service = DeliveryService(database, settings)
    for _ in range(3):
        db_conn.execute(text("UPDATE integration_delivery SET next_attempt_at = now() WHERE id = :id"), {"id": delivery["id"]})
        service.deliver_due()
    detail = client.get(f"/api/v1/integration/deliveries/{delivery['id']}", headers=admin).get_json()
    assert detail["status"] == "sent" and [a["outcome"] for a in detail["attempts"]] == ["retry", "retry", "sent"]
    assert len(server.calls) == 3 and server.calls[0]["body"] == server.calls[2]["body"]   # same PUTs: no duplicates


def test_permanent_failures_and_attempt_limit(client, as_user, world, database, settings, fhir_server, db_conn, closed_port=None):
    refused = fhir_server([(422, {"resourceType": "OperationOutcome"}, 0)])
    admin = as_user("admin_a")
    ep = endpoint(client, admin, refused.url).get_json()
    delivery = request(client, admin, ep["id"], world.operations["A"]).get_json()
    DeliveryService(database, settings).deliver_due()
    assert client.get(f"/api/v1/integration/deliveries/{delivery['id']}", headers=admin).get_json()["status"] == "failed"
    down = fhir_server([(503, {}, 0)] * 5)
    ep2 = endpoint(client, admin, down.url).get_json()
    second = request(client, admin, ep2["id"], world.operations["A"], key="deliv-0000002").get_json()
    service = DeliveryService(database, settings)
    for _ in range(settings.delivery_max_attempts):
        db_conn.execute(text("UPDATE integration_delivery SET next_attempt_at = now() WHERE id = :id"), {"id": second["id"]})
        service.deliver_due()
    detail = client.get(f"/api/v1/integration/deliveries/{second['id']}", headers=admin).get_json()
    assert detail["status"] == "failed" and detail["attempts_count"] == settings.delivery_max_attempts


def test_unknown_encounter_other_institution_and_inactive_endpoint(client, as_user, world, fhir_server):
    server = fhir_server([])
    admin = as_user("admin_a")
    ep = endpoint(client, admin, server.url).get_json()
    assert request(client, admin, ep["id"], world.operations["B"]).status_code == 422          # not of this institution
    assert request(client, admin, ep["id"], uuid.uuid4(), key="deliv-0000003").status_code == 422
    assert request(client, as_user("admin_b"), ep["id"], world.operations["B"], key="deliv-0000004").status_code == 422
    client.patch(f"/api/v1/integration/endpoints/{ep['id']}", json={"active": False}, headers=admin)
    assert request(client, admin, ep["id"], world.operations["A"], key="deliv-0000005").status_code == 409
    assert not server.calls


def test_patient_only_when_the_endpoint_receives_it(client, as_user, world, database, settings, fhir_server):
    server = fhir_server([(200, {}, 0)])
    admin = as_user("admin_a")
    ep = endpoint(client, admin, server.url,
                  resource_types=["Encounter", "Location", "Practitioner", "Organization", "Patient"]).get_json()
    request(client, admin, ep["id"], world.operations["A"])
    DeliveryService(database, settings).deliver_due()
    body = server.calls[0]["body"]
    patient = next(e["resource"] for e in body["entry"] if e["resource"]["resourceType"] == "Patient")
    encounter = next(e["resource"] for e in body["entry"] if e["resource"]["resourceType"] == "Encounter")
    assert patient["id"] == str(world.patients["A"]) and encounter["subject"]["reference"] == f"Patient/{patient['id']}"
