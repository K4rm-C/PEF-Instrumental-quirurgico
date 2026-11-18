"""Only the configured JWT_ALGORITHM is accepted (no HS* family, no alg=none)."""

from __future__ import annotations

import jwt
import pytest

from conftest import TEST_JWT_ALGORITHM

FAMILIES = "/api/v1/catalog/families"

# HS384/HS512 tokens are forged on purpose with the HS256 test secret.
pytestmark = pytest.mark.filterwarnings("ignore:The HMAC key is")


def test_configured_algorithm_is_hs256(app):
    assert app.config["CATALOG_SETTINGS"].jwt_algorithm == TEST_JWT_ALGORITHM == "HS256"


def test_hs256_token_is_accepted(client, world, auth_header):
    assert client.get(FAMILIES, headers=auth_header("operator_a", algorithm="HS256")).status_code == 200


@pytest.mark.parametrize("algorithm", ["HS384", "HS512", "none"])
def test_other_algorithms_are_rejected(client, world, auth_header, algorithm):
    """Same secret, valid claims and live Redis session: only the algorithm differs."""
    headers = auth_header("admin_a", algorithm=algorithm)
    token = headers["Authorization"].split(" ", 1)[1]
    assert jwt.get_unverified_header(token)["alg"] == algorithm
    response = client.get(FAMILIES, headers=headers)
    assert response.status_code == 401
    assert response.get_json()["error"] == "unauthorized"
