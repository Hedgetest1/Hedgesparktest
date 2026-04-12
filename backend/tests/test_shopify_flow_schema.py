"""Tests for H4 — Shopify Flow Connector schema endpoint (F8 v2)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.signal_webhooks import SIGNAL_EVENTS

client = TestClient(app)


def test_flow_schema_endpoint_returns_200():
    r = client.get("/shopify-flow/schema")
    assert r.status_code == 200
    j = r.json()
    assert j["app"] == "HedgeSpark"
    assert j["version"] == "1.0"
    assert isinstance(j["triggers"], list)
    assert len(j["triggers"]) > 0


def test_flow_schema_triggers_subset_of_signal_events():
    """Every advertised trigger must correspond to a real outbound signal."""
    j = client.get("/shopify-flow/schema").json()
    trigger_names = {t["name"] for t in j["triggers"]}
    # Subset check — we can advertise fewer than we emit, but never more
    assert trigger_names.issubset(SIGNAL_EVENTS)
    # And we should advertise every "real" signal (skip test_ping which
    # isn't meaningful inside a Flow)
    expected = set(SIGNAL_EVENTS) - {"test_ping"}
    assert trigger_names == expected


def test_flow_schema_triggers_have_required_fields():
    j = client.get("/shopify-flow/schema").json()
    for t in j["triggers"]:
        assert "name" in t and t["name"]
        assert "title" in t and t["title"]
        assert "description" in t and t["description"]
        schema = t["schema"]
        assert schema["type"] == "object"
        required = schema["required"]
        for f in ("event_id", "event_type", "shop_domain", "occurred_at"):
            assert f in required


def test_flow_schema_is_unauthenticated():
    """No auth header, still works."""
    r = client.get("/shopify-flow/schema")
    assert r.status_code == 200
