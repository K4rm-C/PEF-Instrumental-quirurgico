"""
Type: health
Target: Gateway and service liveness probes (unauthenticated where published).
Host: PEF_GATEWAY_URL. Does not exercise login (safe under rate limits).
"""
from __future__ import annotations

from locust import HttpUser, between, task


class HealthUser(HttpUser):
    wait_time = between(0.5, 1.5)

    @task(6)
    def gateway_healthz(self) -> None:
        self.client.get("/healthz", name="GET /healthz")

    @task(2)
    def auth_openapi(self) -> None:
        # Public contract document through Gateway (no Bearer required).
        self.client.get("/api/v1/auth/openapi.yaml", name="GET /api/v1/auth/openapi.yaml")

    @task(1)
    def session_openapi(self) -> None:
        # SessionService publishes OpenAPI at /openapi.yaml (Gateway: /api/v1/sessions/openapi.yaml).
        # /docs HTML is not exposed on the Gateway path (404); do not hit it here.
        self.client.get("/api/v1/sessions/openapi.yaml", name="GET /api/v1/sessions/openapi.yaml")
