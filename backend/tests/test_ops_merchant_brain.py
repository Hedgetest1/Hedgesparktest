"""Tests for /ops/merchant-brain/* operator endpoints.

Born 2026-05-08 alongside Brain Vero v0.2 ship — gives operator/founder
visibility into brain state before + after un-park ceremony. Pins:
  - both endpoints require operator auth
  - summary aggregates the dispatched/blocked/deferred counts honestly
  - decisions endpoint accepts shop + action_kind filters
  - is_brain_enabled() reflects env state
"""
from __future__ import annotations

import os

from sqlalchemy import text as _sql_text

# Operator key — same convention as test_ops_api.py
_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "")
if not _OP_KEY:
    _OP_KEY = "test-operator-key-for-ci"
    os.environ["DASHBOARD_API_KEY"] = _OP_KEY


def _op_headers():
    return {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}


def _seed_decision(
    db,
    shop: str,
    action_kind: str,
    *,
    limb_dispatched: str | None = None,
    limb_response: dict | None = None,
) -> int:
    import json as _json
    resp_json = _json.dumps(limb_response or {})
    row = db.execute(
        _sql_text(
            "INSERT INTO brain_decisions "
            "(shop_domain, sense_snapshot, synthesis, action_kind, "
            " action_payload, rationale, limb_dispatched, limb_response, "
            " expected_outcome_metric, outcome_window_hours, baseline_value, "
            " decision_at) "
            "VALUES (:s, '{}', 'test', :ak, '{}', 'test', :limb, "
            " (:resp)::jsonb, 'm', 24, 0.0, NOW()) "
            "RETURNING id"
        ),
        {
            "s": shop,
            "ak": action_kind,
            "limb": limb_dispatched,
            "resp": resp_json,
        },
    ).fetchone()
    db.flush()
    return row[0]


# -------------------------------------------------------------------------
# Auth gate
# -------------------------------------------------------------------------

def test_summary_requires_auth(client):
    resp = client.get("/ops/merchant-brain/summary")
    assert resp.status_code == 401


def test_decisions_requires_auth(client):
    resp = client.get("/ops/merchant-brain/decisions")
    assert resp.status_code == 401


# -------------------------------------------------------------------------
# Summary endpoint
# -------------------------------------------------------------------------

def test_summary_reflects_brain_disabled(client, db, monkeypatch):
    monkeypatch.delenv("MERCHANT_BRAIN_ENABLED", raising=False)
    resp = client.get("/ops/merchant-brain/summary", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert "window_24h" in data
    assert "window_7d" in data


def test_summary_reflects_brain_enabled(client, monkeypatch):
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    resp = client.get("/ops/merchant-brain/summary", headers=_op_headers())
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


def test_summary_aggregates_24h(client, db):
    """Seed 3 decisions on a fresh test shop and verify the 24h aggregation
    reports dispatched / deferred / blocked correctly per action_kind."""
    shop = "ops-brain-summary-test.myshopify.com"
    # Wipe any prior rows for hermeticity
    db.execute(
        _sql_text("DELETE FROM brain_decisions WHERE shop_domain = :s"),
        {"s": shop},
    )
    _seed_decision(
        db, shop, "re_engagement_check",
        limb_dispatched="email_orchestrator",
        limb_response={"intent_id": "abc", "email_type": "reengagement_drift"},
    )
    _seed_decision(
        db, shop, "recovery_digest",
        limb_dispatched=None,
        limb_response={"deferred_to": "v0.3_copy_or_limb_pending",
                       "action_kind": "recovery_digest"},
    )
    _seed_decision(
        db, shop, "re_engagement_check",
        limb_dispatched=None,
        limb_response={"blocked_by_review": "no_contact_email_or_paused"},
    )
    db.commit()

    resp = client.get("/ops/merchant-brain/summary", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    by_kind = {row["action_kind"]: row for row in data["window_24h"]}
    # All 3 of our seeds count, plus any other 24h rows (test isolation
    # via db fixture rolls back at test end). At minimum, our seeds appear:
    assert "re_engagement_check" in by_kind
    assert "recovery_digest" in by_kind
    re_row = by_kind["re_engagement_check"]
    assert re_row["dispatched"] >= 1
    assert re_row["blocked"] >= 1
    rec_row = by_kind["recovery_digest"]
    assert rec_row["deferred"] >= 1


# -------------------------------------------------------------------------
# Decisions endpoint
# -------------------------------------------------------------------------

def test_decisions_filter_by_shop(client, db):
    shop = "ops-brain-decisions-test.myshopify.com"
    db.execute(
        _sql_text("DELETE FROM brain_decisions WHERE shop_domain = :s"),
        {"s": shop},
    )
    _seed_decision(
        db, shop, "re_engagement_check",
        limb_dispatched="email_orchestrator",
        limb_response={"intent_id": "X1", "email_type": "reengagement_drift"},
    )
    db.commit()
    resp = client.get(
        f"/ops/merchant-brain/decisions?shop={shop}",
        headers=_op_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    row = data[0]
    assert row["shop_domain"] == shop
    assert row["action_kind"] == "re_engagement_check"
    assert row["limb_dispatched"] == "email_orchestrator"
    assert row["limb_response"]["email_type"] == "reengagement_drift"


def test_decisions_filter_by_action_kind(client, db):
    shop_a = "ops-brain-akind-a.myshopify.com"
    shop_b = "ops-brain-akind-b.myshopify.com"
    db.execute(
        _sql_text(
            "DELETE FROM brain_decisions WHERE shop_domain IN (:a, :b)"
        ),
        {"a": shop_a, "b": shop_b},
    )
    _seed_decision(db, shop_a, "re_engagement_check")
    _seed_decision(db, shop_b, "recovery_digest")
    db.commit()
    resp = client.get(
        "/ops/merchant-brain/decisions?action_kind=recovery_digest&limit=10",
        headers=_op_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    # Every returned row must have the filtered action_kind
    for row in data:
        assert row["action_kind"] == "recovery_digest"


def test_decisions_limit_clamp(client, db):
    """`limit` query param clamps within [1, 500] per Query(...)."""
    resp = client.get(
        "/ops/merchant-brain/decisions?limit=10000",
        headers=_op_headers(),
    )
    # FastAPI Query(le=500) returns 422 for out-of-range
    assert resp.status_code == 422
