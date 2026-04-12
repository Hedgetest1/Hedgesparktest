"""Tests for gdpr_sla — Shopify 48h + GDPR 30d SLA enforcement."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.gdpr_request import GdprRequest
from app.models.ops_alert import OpsAlert
from app.services.gdpr_sla import (
    _DEADLINES_HOURS,
    enforce_sla,
    get_pending_violations,
)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_request(db, *, request_type: str, hours_ago: int, status: str = "pending"):
    r = GdprRequest(
        request_type=request_type,
        shop_domain=f"sla-{uuid.uuid4().hex[:8]}.myshopify.com",
        customer_id=None,
        customer_email=None,
        status=status,
        created_at=_utcnow() - timedelta(hours=hours_ago),
    )
    db.add(r)
    db.flush()
    return r


def test_shop_redact_within_48h_is_clean(db):
    r = _make_request(db, request_type="shop_redact", hours_ago=24)
    violations = get_pending_violations(db)
    assert not any(v["request_id"] == r.id for v in violations)


def test_shop_redact_over_48h_is_violation(db):
    r = _make_request(db, request_type="shop_redact", hours_ago=72)
    violations = get_pending_violations(db)
    hit = next((v for v in violations if v["request_id"] == r.id), None)
    assert hit is not None
    assert hit["request_type"] == "shop_redact"
    assert hit["overdue_minutes"] > 0


def test_customers_data_request_uses_30d_deadline(db):
    # 20 days ago — still inside window
    r = _make_request(db, request_type="customers_data_request", hours_ago=20 * 24)
    violations = get_pending_violations(db)
    assert not any(v["request_id"] == r.id for v in violations)


def test_customers_data_request_over_30d_is_violation(db):
    r = _make_request(db, request_type="customers_data_request", hours_ago=35 * 24)
    violations = get_pending_violations(db)
    assert any(v["request_id"] == r.id for v in violations)


def test_completed_requests_never_breach(db):
    r = _make_request(
        db, request_type="shop_redact", hours_ago=200, status="completed",
    )
    violations = get_pending_violations(db)
    assert not any(v["request_id"] == r.id for v in violations)


def test_enforce_sla_emits_alert_once_per_breach(db):
    r = _make_request(db, request_type="shop_redact", hours_ago=72)
    first = enforce_sla(db)
    assert first["new_alerts"] >= 1

    # Second run must NOT create a duplicate alert
    second = enforce_sla(db)
    assert second["new_alerts"] == 0
    # The original alert still exists and is unresolved
    count = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type == "gdpr_sla_breach",
            OpsAlert.source == f"gdpr_request:{r.id}",
            OpsAlert.resolved == False,  # noqa: E712
        )
        .count()
    )
    assert count == 1


def test_deadlines_constant_has_all_types():
    assert "shop_redact" in _DEADLINES_HOURS
    assert "customers_redact" in _DEADLINES_HOURS
    assert "customers_data_request" in _DEADLINES_HOURS
    assert _DEADLINES_HOURS["shop_redact"] == 48
