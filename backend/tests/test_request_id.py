"""Tests for request ID middleware and structured logging."""
from tests.conftest import SHOP_A


def test_response_has_request_id_header(client, merchant_a, auth_a):
    """Every response must include X-Request-ID header."""
    resp = client.get("/merchant/me", cookies=auth_a)
    assert resp.status_code == 200
    rid = resp.headers.get("X-Request-ID")
    assert rid is not None
    assert len(rid) == 12  # hex(6 bytes) = 12 chars


def test_request_id_propagated_from_client(client, merchant_a, auth_a):
    """If client sends X-Request-ID, server echoes it back."""
    resp = client.get(
        "/merchant/me",
        cookies=auth_a,
        headers={"X-Request-ID": "test-trace-123"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID") == "test-trace-123"


def test_unauthenticated_request_still_gets_id(client):
    """Even 401 responses get a request ID for correlation."""
    resp = client.get("/merchant/me")
    assert resp.status_code == 401
    assert resp.headers.get("X-Request-ID") is not None


def test_health_endpoint_gets_request_id(client):
    """Health endpoint (no auth) gets request ID."""
    resp = client.get("/system/health")
    assert resp.status_code in (200, 503)
    assert resp.headers.get("X-Request-ID") is not None
