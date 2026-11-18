"""GET /api/v1/sessions/{id}/evidence (Phase B): session authorized first, metadata only, no URLs, no audit."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET

import pytest

from evidence_fixtures import audits, edata  # noqa: F401  (fixture)

FIELDS = ["id", "session_id", "kind", "content_type", "size_bytes", "sha256", "storage_provider",
          "retention_until", "created_at", "blocked", "purged", "signed_url", "signed_url_expires_at"]
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def _url(session_id):
    return f"/api/v1/sessions/{session_id}/evidence"


def test_list_order_fields_and_no_urls(client, world, edata, as_user, db_conn, storage):
    response = client.get(_url(edata.sessions["a1"].id), headers=as_user("op_a"))
    assert response.status_code == 200
    items = response.get_json()
    a = edata.assets
    # created_at ASC, then id ASC for the two assets created at the same instant.
    assert [i["id"] for i in items] == [str(a[k]) for k in ("a1_jpeg", "a1_gif", "a1_blocked", "a1_purged",
                                                             "a1_tie_lo", "a1_tie_hi")]
    for item in items:
        assert list(item) == FIELDS
        assert item["signed_url"] is None and item["signed_url_expires_at"] is None
        assert item["session_id"] == str(edata.sessions["a1"].id)
    first = items[0]
    assert first == {
        "id": str(a["a1_jpeg"]), "session_id": str(edata.sessions["a1"].id), "kind": "jpeg",
        "content_type": "image/jpeg", "size_bytes": 1234, "sha256": "a" * 64, "storage_provider": "other",
        "retention_until": "2027-01-12T08:00:00Z", "created_at": "2026-10-14T08:00:00Z",
        "blocked": False, "purged": False, "signed_url": None, "signed_url_expires_at": None,
    }
    # Blocked and purged assets stay in the history, flagged.
    flags = {i["id"]: (i["blocked"], i["purged"]) for i in items}
    assert flags[str(a["a1_blocked"])] == (True, False) and flags[str(a["a1_purged"])] == (False, True)
    text = response.get_data(as_text=True)
    for internal in ("bucket", "object_key", "gcs_uri", "s3://", "evidence/", "X-Amz"):
        assert internal not in text
    assert audits(db_conn) == []                     # listing is not a download
    assert storage.calls == []                       # never signs nor touches storage


def test_empty_list(client, world, edata, as_user):
    response = client.get(_url(edata.sessions["empty"].id), headers=as_user("op_a"))
    assert response.status_code == 200 and response.get_json() == []


def test_xml_list(client, world, edata, as_user):
    response = client.get(_url(edata.sessions["a1"].id), headers=as_user("spd_a", Accept="application/xml"))
    root = ET.fromstring(response.data)
    assert response.status_code == 200 and root.tag == "evidence_list"
    items = list(root)
    assert [i.tag for i in items] == ["evidence"] * 6
    assert [c.tag for c in items[0]] == FIELDS
    assert items[0].find("signed_url").get(XSI_NIL) == "true" and items[0].find("signed_url").text is None
    assert items[0].find("signed_url_expires_at").get(XSI_NIL) == "true"
    assert items[0].findtext("blocked") == "false" and items[2].findtext("blocked") == "true"
    assert ET.fromstring(client.get(_url(edata.sessions["empty"].id),
                                    headers=as_user("op_a", Accept="application/xml")).data).tag == "evidence_list"


@pytest.mark.parametrize("user_key, session_key, status", [
    ("op_a", "a1", 200),        # own session
    ("op_a", "a2", 404),        # same tenant, assigned to another operator
    ("op_a", "b1", 404),        # other tenant
    ("op_a2", "a2", 200),
    ("spd_a", "a1", 200),       # supervisor: any session of the tenant
    ("spd_a", "a2", 200),
    ("spd_a", "b1", 404),       # supervisor of another tenant
    ("spd_b", "b1", 200),
    ("multi_a", "a2", 200),     # station_operator + spd_supervisor → supervisor scope
    ("admin_a", "a1", 403),
    ("viewer_a", "a1", 403),    # non-clinical role
    ("foreign_role_a", "a1", 403),
    ("op_a", "noop", 404),      # session without operation: no tenant, no fallback by user_id
    ("spd_a", "noop", 404),
])
def test_access_rules(client, world, edata, as_user, db_conn, user_key, session_key, status):
    response = client.get(_url(edata.sessions[session_key].id), headers=as_user(user_key))
    assert response.status_code == status
    if status != 200:
        assert response.get_json()["error"] == ("forbidden" if status == 403 else "not_found")
    assert audits(db_conn) == []


def test_403_is_decided_before_any_lookup(client, world, edata, as_user):
    """it_admin gets the same 403 for an existing, a foreign and an unknown session (existence not revealed)."""
    for session_id in (edata.sessions["a1"].id, edata.sessions["b1"].id, uuid.uuid4(), "not-a-uuid"):
        assert client.get(_url(session_id), headers=as_user("admin_a")).status_code == 403


@pytest.mark.parametrize("session_id", ["not-a-uuid", str(uuid.uuid4())])
def test_unknown_or_malformed_session_is_404(client, world, edata, as_user, session_id):
    assert client.get(_url(session_id), headers=as_user("spd_a")).status_code == 404


def test_not_found_is_indistinguishable(client, world, edata, as_user):
    bodies = [client.get(_url(s), headers=as_user("op_a")).get_json() for s in
              (edata.sessions["a2"].id, edata.sessions["b1"].id, uuid.uuid4(), edata.sessions["noop"].id)]
    assert {(b["error"], b["message"]) for b in bodies} == {("not_found", "Session not found")}
