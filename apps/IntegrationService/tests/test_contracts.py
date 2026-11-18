"""Real responses validated against openapi.yaml (parsed) and schemas/integration.xsd; migration idempotent."""

from __future__ import annotations

from pathlib import Path

import yaml
from lxml import etree

SERVICE_DIR = Path(__file__).resolve().parents[1]
XSD = etree.XMLSchema(etree.parse(str(SERVICE_DIR / "schemas" / "integration.xsd")))


def xml_ok(response):
    document = etree.fromstring(response.data)
    return XSD.validate(document), XSD.error_log


def test_openapi_is_valid_yaml_with_every_route(app):
    spec = yaml.safe_load((SERVICE_DIR / "openapi.yaml").read_text(encoding="utf-8"))
    documented = set(spec["paths"])
    routes = {str(r).replace("<raw_id>", "{id}").replace("<resource_type>", "{type}") for r in app.url_map.iter_rules()
              if not str(r).startswith("/static")}
    expected = {r.replace("/{type}/{id}", "/{type}/{id}") for r in routes}
    missing = sorted(r for r in expected if r not in documented)
    assert missing == [], missing


def test_xml_responses_conform(client, as_user, machine, as_client):
    machine("his", scopes=("sync.catalog.read",))
    for response in (client.get("/api/v1/sync/resources", headers=as_client("his", Accept="application/xml")),
                     client.get("/api/v1/sync/changes?limit=5", headers=as_user("spd_a", Accept="application/xml")),
                     client.get("/api/v1/integration/endpoints", headers=as_user("admin_a", Accept="application/xml")),
                     client.get("/api/v1/integration/deliveries", headers=as_user("admin_a", Accept="application/xml")),
                     client.get("/api/v1/sync/changes?cursor=bad", headers=as_user("spd_a", Accept="application/xml")),
                     client.get("/health/ready", headers={"Accept": "application/xml"})):
        ok, log = xml_ok(response)
        assert ok, (response.request.path, log)


def test_migration_is_idempotent_and_shipped(pg_engine):
    from database import apply_migrations
    apply_migrations(pg_engine)
    apply_migrations(pg_engine)
    assert (SERVICE_DIR / "migrations" / "014_sincronizacion_integracion.sql").read_bytes() == \
        (SERVICE_DIR.parents[1] / "data" / "migrations" / "014_sincronizacion_integracion.sql").read_bytes()
