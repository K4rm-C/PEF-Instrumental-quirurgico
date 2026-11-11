"""/api/v1/monitoring — scope by role, current state, history, real aggregates, formats, security."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from lxml import etree

from config import COMPONENTS

BASE = "/api/v1/monitoring"
XSD = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parents[1] / "schemas" / "monitoring.xsd")))
XML = {"Accept": "application/xml"}


@pytest.fixture
def measured(history):
    """catalog: 3 up (10, 20, 30 ms), 1 degraded (40 ms), 1 down (timeout, 1000 ms) — all in the last hour."""
    for latency, minutes in ((10, 50), (20, 40), (30, 30)):
        history("catalog", "up", latency, minutes, http=200, deps={"database": "ok", "redis": "ok"})
    history("catalog", "degraded", 40, 20, http=503, deps={"database": "ok", "redis": "error"}, error="dependency_failed")
    history("catalog", "down", 1000, 0.1, error="timeout")
    history("postgres", "up", 5, 0.1)
    history("notifications-worker", "up", 7, 0.1, details={"pending_events": 0, "oldest_pending_seconds": None})
    history("auth", "up", 9, 120)                         # last check two hours ago: stale now
    return history


def test_admin_sees_the_current_state_with_technical_detail(client, measured, as_user):
    body = client.get(f"{BASE}/services", headers=as_user("admin")).get_json()
    items = {i["name"]: i for i in body["items"]}
    assert list(items) == list(COMPONENTS)
    catalog = items["catalog"]
    assert catalog["status"] == "down" and catalog["freshness"] == "current"
    assert catalog["check"]["error_type"] == "timeout" and catalog["check"]["latency_ms"] == 1000.0
    assert items["postgres"]["status"] == "up" and items["postgres"]["check"]["endpoint"]
    assert items["auth"]["status"] == "unknown" and items["auth"]["freshness"] == "stale"
    assert items["auth"]["measured_status"] == "up"             # what was last measured, kept apart
    assert items["mongo"]["status"] == "unknown" and items["mongo"]["freshness"] == "never_checked"
    assert body["counts"]["up"] == 2 and body["counts"]["down"] == 1 and body["collector"]["interval_seconds"] == 30.0


def test_supervisor_gets_an_availability_summary_only(client, measured, as_user):
    body = client.get(f"{BASE}/services", headers=as_user("spd")).get_json()
    assert set(body) == {"items", "counts"}
    assert all(set(i) == {"name", "label", "status", "checked_at", "freshness"} for i in body["items"])
    summary = client.get(f"{BASE}/summary", headers=as_user("spd")).get_json()
    assert all(set(c) == {"name", "label", "checks", "availability_pct", "up_pct"} for c in summary["components"])
    for path in ("/history", "/services/catalog"):
        assert client.get(f"{BASE}{path}", headers=as_user("spd")).status_code == 403


def test_operator_and_inactive_users_are_rejected(client, measured, as_user):
    for path in ("/services", "/summary", "/history", "/services/catalog"):
        response = client.get(f"{BASE}{path}", headers=as_user("op"))
        assert response.status_code == 403 and response.get_json()["error"] == "forbidden"
    assert client.get(f"{BASE}/services", headers=as_user("off_admin")).status_code == 401
    assert "check" in client.get(f"{BASE}/services", headers=as_user("both")).get_json()["items"][0]   # admin wins


def test_aggregates_are_computed_from_the_stored_checks(client, measured, as_user):
    body = client.get(f"{BASE}/summary?component=catalog", headers=as_user("admin")).get_json()
    catalog = body["components"][0]
    assert (catalog["checks"], catalog["up"], catalog["degraded"], catalog["down"]) == (5, 3, 1, 1)
    assert catalog["availability_pct"] == 80.0 and catalog["up_pct"] == 60.0
    # Latency over the answered checks only (the 1000 ms timeout is an outage, not a sample).
    assert catalog["avg_latency_ms"] == 25.0 and catalog["min_latency_ms"] == 10.0 and catalog["max_latency_ms"] == 40.0
    assert catalog["p95_latency_ms"] == 38.5
    everything = {c["name"]: c for c in client.get(f"{BASE}/summary", headers=as_user("admin")).get_json()["components"]}
    assert set(everything) == set(COMPONENTS) and everything["garage"]["checks"] == 0
    assert everything["garage"]["availability_pct"] is None              # no data is not 0 % nor 100 %


def test_series_by_bucket(client, measured, as_user):
    body = client.get(f"{BASE}/summary?component=catalog&bucket=1m&from="
                      f"{(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace('+00:00', 'Z')}",
                      headers=as_user("admin")).get_json()
    points = body["series"][0]["points"]
    assert body["bucket"] == "1m" and sum(p["checks"] for p in points) == 5 and len(points) == 5
    assert {p["availability_pct"] for p in points} == {100.0, 0.0}
    sup = client.get(f"{BASE}/summary?bucket=15m", headers=as_user("spd")).get_json()
    assert all("avg_latency_ms" not in p for s in sup["series"] for p in s["points"])


def test_service_detail(client, measured, as_user):
    body = client.get(f"{BASE}/services/catalog?recent=3", headers=as_user("admin")).get_json()
    assert body["name"] == "catalog" and len(body["recent"]) == 3 and body["recent"][0]["status"] == "down"
    assert body["last_24h"]["checks"] == 5
    assert client.get(f"{BASE}/services/nope", headers=as_user("admin")).status_code == 404
    assert client.get(f"{BASE}/services/garage", headers=as_user("admin")).get_json()["last_24h"]["checks"] == 0


def test_history_filters_and_pagination(client, measured, as_user):
    headers = as_user("admin")
    page = client.get(f"{BASE}/history?component=catalog&per_page=2", headers=headers).get_json()
    assert page["total"] == 5 and page["total_pages"] == 3 and len(page["items"]) == 2
    assert page["items"][0]["checked_at"] >= page["items"][1]["checked_at"]
    second = client.get(f"{BASE}/history?component=catalog&per_page=2&page=2", headers=headers).get_json()
    assert not {i["id"] for i in page["items"]} & {i["id"] for i in second["items"]}
    down = client.get(f"{BASE}/history?status=down", headers=headers).get_json()
    assert [i["component"] for i in down["items"]] == ["catalog"]
    since = (datetime.now(timezone.utc) - timedelta(minutes=25)).isoformat().replace("+00:00", "Z")
    recent = client.get(f"{BASE}/history?component=catalog&from={since}", headers=headers).get_json()
    assert recent["total"] == 2
    oldest = client.get(f"{BASE}/history?component=catalog&sort=checked_at&per_page=1", headers=headers).get_json()
    assert oldest["items"][0]["latency_ms"] == 10.0


@pytest.mark.parametrize("path, field", [
    ("/history?component=nope", "component"), ("/history?status=fine", "status"), ("/history?per_page=500", "per_page"),
    ("/history?page=0", "page"), ("/history?from=yesterday", "from"), ("/history?sort=status", "sort"),
    ("/history?from=2026-10-22&to=2026-10-21", "to"), ("/summary?bucket=2m", "bucket"),
    ("/summary?from=2026-01-01&to=2026-03-01", "from"), ("/summary?from=2026-10-01&to=2026-10-20&bucket=1m", "bucket"),
    ("/summary?component=x", "component"), ("/services/catalog?recent=0", "recent"),
])
def test_invalid_parameters_are_400(client, measured, as_user, path, field):
    response = client.get(f"{BASE}{path}", headers=as_user("admin"))
    assert response.status_code == 400 and response.get_json()["details"]["field"] == field


def test_unknown_parameters_are_400(client, measured, as_user):
    for path in ("/services?verbose=1", "/history?limit=5", "/summary?institution=x"):
        body = client.get(f"{BASE}{path}", headers=as_user("admin")).get_json()
        assert body["error"] == "invalid_request" and body["details"]["unknown_parameters"]


def test_read_only_api(client, measured, as_user):
    headers = as_user("admin")
    assert client.post(f"{BASE}/services", json={}, headers=headers).status_code == 405
    assert client.delete(f"{BASE}/history", headers=headers).status_code == 405


def test_tokens_invalid_expired_revoked_unregistered_or_headers_are_401(client, world, tokens):
    user = world.users["admin"]
    for headers in ({}, {"Authorization": "Bearer abc"},
                    {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id, lifetime=timedelta(seconds=-5))}"},
                    {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id, revoked=True)}"},
                    {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id, store=False)}"},
                    {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id, secret='x' * 40)}"},
                    {"X-User-Id": str(user.id), "X-Role": "it_admin"}):
        response = client.get(f"{BASE}/services", headers=headers)
        assert response.status_code == 401 and response.get_json()["error"] == "unauthorized"


def test_xml_responses_conform_to_the_xsd(client, measured, as_user):
    for user in ("admin", "spd"):
        headers = as_user(user, **XML)
        for path in ("/services", "/summary?bucket=15m", "/summary?component=catalog"):
            document = etree.fromstring(client.get(f"{BASE}{path}", headers=headers).data)
            assert XSD.validate(document), (user, path, XSD.error_log)
    headers = as_user("admin", **XML)
    for path in ("/services/catalog", "/services/mongo", "/history?per_page=3", "/history?status=fine", "/services/nope"):
        document = etree.fromstring(client.get(f"{BASE}{path}", headers=headers).data)
        assert XSD.validate(document), (path, XSD.error_log)
    assert XSD.validate(etree.fromstring(client.get("/health", headers=XML).data))
    page = etree.fromstring(client.get(f"{BASE}/history?component=catalog", headers=headers).data)
    assert page.tag == "check_page" and len(page.findall("items/check")) == 5


def test_contract_documents_health_and_correlation(client, measured):
    assert b"/api/v1/monitoring/summary" in client.get(f"{BASE}/openapi.yaml").data
    assert client.get(f"{BASE}/schemas/monitoring.xsd").status_code == 200
    assert b"swagger-ui" in client.get(f"{BASE}/docs").data
    health = client.get("/health", headers={"X-Correlation-ID": "corr-mon-1"})
    assert health.status_code == 200 and health.headers["X-Correlation-ID"] == "corr-mon-1"
    assert health.headers["X-Last-Run-Age-Seconds"].isdigit()
    assert client.get("/health/live").status_code == 200 and client.get("/health/ready").status_code == 200
    assert client.get(f"{BASE}/services/{uuid.uuid4()}").status_code == 401
