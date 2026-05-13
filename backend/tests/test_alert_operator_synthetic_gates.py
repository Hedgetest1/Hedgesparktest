"""Lock 2026-05-13 alert gates for synthetic test shops + operator-shop
funnel-class alert silencing.

Two gates closed in the same commit (sibling sweep on noise alerts):
  1. `shop.myshopify.com` added to synthetic_test_shop patterns —
     auth_hardening session_anomaly stops leaking from OAuth tests.
  2. `is_operator_silenced_alert(shop, alert_type)` predicate +
     write_alert wiring — funnel-state alerts (slow_activation,
     onboarding_slow_progress, etc.) stop firing for hedgespark-dev.

Both gates are fail-open: a bug in the predicate must not silence
real alerts.
"""
from __future__ import annotations

from sqlalchemy import text as _sql_text

from app.core.operator_blocklist import (
    is_operator_silenced_alert,
    operator_silenced_alert_types,
    _OPERATOR_DEV_SHOPS,
)
from app.core.test_shop_blocklist import is_synthetic_test_shop
from app.services.alerting import write_alert


# ---------- synthetic-test-shop gate ----------


def test_shop_dot_myshopify_is_synthetic():
    """The default OAuth dev placeholder must be recognised as test."""
    assert is_synthetic_test_shop("shop.myshopify.com") is True
    # Case-insensitive
    assert is_synthetic_test_shop("Shop.myshopify.com") is True


def test_real_merchant_subdomain_is_not_synthetic():
    """Real merchant subdomains must NOT match — conservative anchor."""
    assert is_synthetic_test_shop("hedgespark-dev.myshopify.com") is False
    assert is_synthetic_test_shop("my-real-store.myshopify.com") is False
    assert is_synthetic_test_shop("shop-1234.myshopify.com") is False


def test_session_anomaly_dropped_for_synthetic_shop(db):
    """write_alert called with shop_domain='shop.myshopify.com' MUST
    NOT persist to ops_alerts."""
    before = db.execute(_sql_text(
        "SELECT COUNT(*) FROM ops_alerts WHERE alert_type='session_anomaly' "
        "AND shop_domain='shop.myshopify.com' AND resolved=false"
    )).scalar() or 0

    write_alert(
        db,
        severity="warning",
        source="auth_hardening",
        alert_type="session_anomaly",
        summary="test synthetic anomaly",
        shop_domain="shop.myshopify.com",
        detail={"reasons": ["test"]},
    )
    db.flush()
    after = db.execute(_sql_text(
        "SELECT COUNT(*) FROM ops_alerts WHERE alert_type='session_anomaly' "
        "AND shop_domain='shop.myshopify.com' AND resolved=false"
    )).scalar() or 0

    assert after == before, "synthetic-shop alert must not persist"


# ---------- operator-shop silenced-alert gate ----------


def test_operator_silenced_predicate_basic():
    """slow_activation on hedgespark-dev → True."""
    assert is_operator_silenced_alert(
        "hedgespark-dev.myshopify.com", "slow_activation"
    ) is True


def test_operator_silenced_real_bug_not_silenced():
    """LLM/code-error alert types on operator shop → False (still fires)."""
    assert is_operator_silenced_alert(
        "hedgespark-dev.myshopify.com", "llm_safety_input"
    ) is False
    assert is_operator_silenced_alert(
        "hedgespark-dev.myshopify.com", "bugfix_apply_failed"
    ) is False


def test_operator_silenced_real_merchant_not_silenced():
    """slow_activation on REAL merchant → False (still fires)."""
    assert is_operator_silenced_alert(
        "real-customer.myshopify.com", "slow_activation"
    ) is False


def test_operator_silenced_set_is_non_empty():
    """The curated list must contain the documented funnel-class types."""
    types = operator_silenced_alert_types()
    assert "slow_activation" in types
    assert "onboarding_slow_progress" in types
    assert "pixel_abandonment" in types
    # Real-bug alert types MUST stay out
    assert "llm_safety_input" not in types
    assert "session_anomaly" not in types  # delegated to synthetic gate


def test_slow_activation_dropped_for_operator_dev_shop(db):
    """write_alert called with the funnel-state combo MUST drop."""
    op_shop = next(iter(_OPERATOR_DEV_SHOPS))  # hedgespark-dev
    before = db.execute(_sql_text(
        "SELECT COUNT(*) FROM ops_alerts WHERE alert_type='slow_activation' "
        "AND shop_domain=:s AND resolved=false"
    ), {"s": op_shop}).scalar() or 0

    write_alert(
        db,
        severity="warning",
        source="onboarding_health",
        alert_type="slow_activation",
        summary="should not persist",
        shop_domain=op_shop,
        detail={"event_count": 100, "signal_count": 0},
    )
    db.flush()
    after = db.execute(_sql_text(
        "SELECT COUNT(*) FROM ops_alerts WHERE alert_type='slow_activation' "
        "AND shop_domain=:s AND resolved=false"
    ), {"s": op_shop}).scalar() or 0

    assert after == before, "operator-shop funnel alert must not persist"


def test_real_bug_alert_for_operator_shop_persists(db):
    """A real-bug alert type on operator shop MUST persist (gate is narrow)."""
    op_shop = next(iter(_OPERATOR_DEV_SHOPS))
    before = db.execute(_sql_text(
        "SELECT COUNT(*) FROM ops_alerts WHERE alert_type='bugfix_apply_failed' "
        "AND shop_domain=:s AND resolved=false"
    ), {"s": op_shop}).scalar() or 0

    write_alert(
        db,
        severity="critical",
        source="bugfix_pipeline",
        alert_type="bugfix_apply_failed",
        summary="real bug — must persist",
        shop_domain=op_shop,
        detail={"err": "real"},
    )
    db.flush()
    after = db.execute(_sql_text(
        "SELECT COUNT(*) FROM ops_alerts WHERE alert_type='bugfix_apply_failed' "
        "AND shop_domain=:s AND resolved=false"
    ), {"s": op_shop}).scalar() or 0

    assert after > before, "real-bug alert for operator shop MUST persist"
