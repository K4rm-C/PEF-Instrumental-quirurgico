"""MS-07B FHIR R4 projection: mappings, scopes, isolation, structural validation, audit."""

from __future__ import annotations

from sqlalchemy import text

from services.fhir import validate

ALL = ("fhir.location.read", "fhir.practitioner.read", "fhir.encounter.read", "fhir.export.bundle")


def test_metadata_is_public_and_describes_the_projection(client):
    response = client.get("/fhir/r4/metadata")
    body = response.get_json()
    assert response.status_code == 200 and response.mimetype == "application/fhir+json"
    assert body["resourceType"] == "CapabilityStatement" and body["fhirVersion"] == "4.0.1"
    assert {r["type"] for r in body["rest"][0]["resource"]} == {"Organization", "Location", "Practitioner", "Patient", "Encounter"}


def test_encounter_mapping(client, machine, as_client, world):
    machine("his", scopes=ALL)
    response = client.get(f"/fhir/r4/Encounter/{world.operations['A']}", headers=as_client("his"))
    enc = response.get_json()
    assert response.status_code == 200 and validate(enc) == []
    assert enc["status"] == "in-progress" and enc["class"]["code"] == "UNK"          # nothing invented
    assert enc["period"] == {"start": "2026-11-03T09:10:00Z"}
    assert enc["location"] == [{"location": {"reference": f"Location/{world.rooms['A']}"}}]
    assert enc["participant"][0]["individual"]["reference"] == f"Practitioner/{world.physicians['A']}"
    assert enc["serviceProvider"]["reference"] == f"Organization/{world.inst['A']}"
    assert "subject" not in enc                                                    # no patient scope: no patient
    assert {"system": "urn:pef:operation", "value": str(world.operations["A"])} in enc["identifier"]


def test_patient_only_with_its_scope(client, machine, as_client, world):
    machine("nopat", scopes=ALL)
    assert client.get(f"/fhir/r4/Patient/{world.patients['A']}", headers=as_client("nopat")).status_code == 403
    machine("pat", scopes=ALL + ("fhir.patient.read",))
    patient = client.get(f"/fhir/r4/Patient/{world.patients['A']}", headers=as_client("pat")).get_json()
    assert patient["gender"] == "female" and patient["birthDate"] == "1980-02-03" and validate(patient) == []
    assert [i["system"] for i in patient["identifier"]] == ["urn:pef:patient"]       # no identifier invented
    enc = client.get(f"/fhir/r4/Encounter/{world.operations['A']}", headers=as_client("pat")).get_json()
    assert enc["subject"] == {"reference": f"Patient/{world.patients['A']}"}


def test_location_practitioner_organization_and_identifiers(client, machine, as_client, world, db_conn):
    machine("his", scopes=ALL)
    db_conn.execute(text("INSERT INTO resource_identifier (resource_type, resource_id, system, value) VALUES "
                         "('operating_room', :id, 'https://hospital.test/sid/room', 'Q-1')"), {"id": world.rooms["A"]})
    loc = client.get(f"/fhir/r4/Location/{world.rooms['A']}", headers=as_client("his")).get_json()
    assert loc["status"] == "active" and loc["name"] == "OR 1" and validate(loc) == []
    assert {"use": "official", "system": "https://hospital.test/sid/room", "value": "Q-1"} in loc["identifier"]
    practitioner = client.get(f"/fhir/r4/Practitioner/{world.physicians['A']}", headers=as_client("his")).get_json()
    assert practitioner["name"] == [{"text": "Dr. Test A"}] and validate(practitioner) == []
    org = client.get(f"/fhir/r4/Organization/{world.inst['A']}", headers=as_client("his")).get_json()
    assert org["name"].startswith("Integration A")


def test_other_institution_does_not_exist(client, machine, as_client, world):
    machine("his", scopes=ALL)
    for path in (f"Encounter/{world.operations['B']}", f"Location/{world.rooms['B']}",
                 f"Practitioner/{world.physicians['B']}", f"Organization/{world.inst['B']}", "Encounter/not-a-uuid"):
        response = client.get(f"/fhir/r4/{path}", headers=as_client("his"))
        assert response.status_code == 404 and response.get_json()["resourceType"] == "OperationOutcome", path


def test_scopes_unknown_types_and_search(client, machine, as_client, world):
    machine("loc", scopes=("fhir.location.read",))
    assert client.get(f"/fhir/r4/Encounter/{world.operations['A']}", headers=as_client("loc")).status_code == 403
    assert client.get("/fhir/r4/Observation/x", headers=as_client("loc")).status_code == 404
    search = client.get("/fhir/r4/Location?_count=10", headers=as_client("loc")).get_json()
    assert search["type"] == "searchset" and search["total"] == 1 and validate(search) == []
    assert client.get("/fhir/r4/Location?name=x", headers=as_client("loc")).status_code == 400
    machine("enc", scopes=("fhir.encounter.read",))
    planned = client.get("/fhir/r4/Encounter?status=planned", headers=as_client("enc")).get_json()
    assert planned["total"] == 0
    active = client.get("/fhir/r4/Encounter?status=in-progress", headers=as_client("enc")).get_json()
    assert [e["resource"]["id"] for e in active["entry"]] == [str(world.operations["A"])]


def test_bundle_and_audit(client, machine, as_client, world, db_conn):
    row = machine("his", scopes=ALL)
    bundle = client.get(f"/fhir/r4/Encounter/{world.operations['A']}/$pef-bundle", headers=as_client("his")).get_json()
    types = sorted(e["resource"]["resourceType"] for e in bundle["entry"])
    assert bundle["type"] == "collection" and types == ["Encounter", "Location", "Organization", "Practitioner"]
    assert validate(bundle) == []
    audit = db_conn.execute(text("SELECT actor_type, action FROM access_audit WHERE actor_client_id = :c"), {"c": row}).all()
    assert audit == [("client", "FHIR_EXPORT_BUNDLE")]
    machine("nobundle", scopes=("fhir.encounter.read",))
    assert client.get(f"/fhir/r4/Encounter/{world.operations['A']}/$pef-bundle",
                      headers=as_client("nobundle")).status_code == 403


def test_validator_catches_invalid_resources():
    assert validate({"resourceType": "Encounter", "id": "x y", "status": "done", "class": {}}) == [
        "id invalid", "Encounter.status invalid", "Encounter.class required"]
    assert validate({"resourceType": "Patient", "id": "a", "gender": "f", "birthDate": "03/02/1980"}) == [
        "Patient.gender invalid", "Patient.birthDate invalid"]
    assert validate({"resourceType": "Location", "id": "a", "managingOrganization": {"reference": "http://x/1"}}) == [
        "reference 'http://x/1' invalid"]
    assert validate({"resourceType": "Observation", "id": "a"}) == ["unsupported resourceType 'Observation'"]
