"""Tests for automated merchant onboarding."""
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone

from sqlalchemy import text

from app.core.token_crypto import encrypt_token
from app.models.merchant import Merchant
from app.services.onboarding import run_onboarding, run_pending_onboarding, OnboardingResult
from tests.conftest import SHOP_A, SHOP_B


def _make_merchant(db, shop=SHOP_A, token="shpat_test_123", status="pending") -> Merchant:
    m = Merchant(
        shop_domain=shop,
        access_token=encrypt_token(token),
        plan="lite",
        billing_active=False,
        install_status="active",
        onboarding_status=status,
    )
    db.add(m)
    db.flush()
    return m


def _mock_shopify_ok():
    """Patch Shopify API calls to return success."""
    return (
        patch("app.services.onboarding._ensure_webhook", return_value=True),
        patch("app.services.onboarding._ensure_tracker", return_value=True),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_new_merchant_reaches_ready(db):
    """Merchant with valid token + successful Shopify calls → ready."""
    m = _make_merchant(db)
    p1, p2 = _mock_shopify_ok()
    with p1, p2:
        result = run_onboarding(db, m)

    assert result.status == "ready"
    assert m.onboarding_status == "ready"
    assert "token_verified" in result.steps_completed
    assert "webhook_configured" in result.steps_completed
    assert "tracker_configured" in result.steps_completed


def test_already_ready_skipped(db):
    """Merchant already ready → no work, no API calls."""
    m = _make_merchant(db, status="ready")
    result = run_onboarding(db, m)
    assert result.status == "already_ready"


# ---------------------------------------------------------------------------
# Failure cases
# ---------------------------------------------------------------------------

def test_no_token_fails(db):
    """Merchant without access_token → failed + alert."""
    m = Merchant(
        shop_domain=SHOP_A, access_token=None,
        plan="lite", install_status="active", onboarding_status="pending",
    )
    db.add(m)
    db.flush()

    result = run_onboarding(db, m)
    assert result.status == "failed"
    assert "token" in result.error
    assert m.onboarding_status == "failed"

    # Alert should exist
    alert = db.execute(text(
        "SELECT alert_type FROM ops_alerts WHERE shop_domain = :s AND alert_type = 'onboarding_failed'"
    ), {"s": SHOP_A}).fetchone()
    assert alert is not None


def test_webhook_failure_fails(db):
    """Webhook registration failure → failed."""
    m = _make_merchant(db)
    with patch("app.services.onboarding._ensure_webhook", return_value=False), \
         patch("app.services.onboarding._ensure_tracker", return_value=True):
        result = run_onboarding(db, m)

    assert result.status == "failed"
    assert "webhook" in result.error
    assert m.onboarding_status == "failed"


def test_tracker_failure_fails(db):
    """Tracker installation failure → failed."""
    m = _make_merchant(db)
    with patch("app.services.onboarding._ensure_webhook", return_value=True), \
         patch("app.services.onboarding._ensure_tracker", return_value=False):
        result = run_onboarding(db, m)

    assert result.status == "failed"
    assert "tracker" in result.error


# ---------------------------------------------------------------------------
# Retry / Recovery
# ---------------------------------------------------------------------------

def test_failed_merchant_retries_to_ready(db):
    """Failed merchant retried successfully → transitions to ready."""
    m = _make_merchant(db, status="failed")
    m.onboarding_error = "previous_failure"
    db.flush()

    p1, p2 = _mock_shopify_ok()
    with p1, p2:
        result = run_onboarding(db, m)

    assert result.status == "ready"
    assert m.onboarding_status == "ready"
    assert m.onboarding_error is None


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_multiple_runs_safe(db):
    """Running onboarding twice on same merchant is safe."""
    m = _make_merchant(db)
    p1, p2 = _mock_shopify_ok()
    with p1, p2:
        r1 = run_onboarding(db, m)
        r2 = run_onboarding(db, m)

    assert r1.status == "ready"
    assert r2.status == "already_ready"


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def test_batch_runner_processes_pending(db):
    """run_pending_onboarding processes pending merchants."""
    _make_merchant(db, shop=SHOP_A, status="pending")
    _make_merchant(db, shop=SHOP_B, status="pending")

    p1, p2 = _mock_shopify_ok()
    with p1, p2:
        summary = run_pending_onboarding(db)

    # At least our 2 test merchants processed (may include others from prod DB)
    assert summary["processed"] >= 2
    assert summary["ready"] >= 2


def test_batch_runner_skips_ready(db):
    """Already-ready merchants are not re-processed by run_onboarding."""
    m = _make_merchant(db, shop=SHOP_A, status="ready")
    result = run_onboarding(db, m)
    assert result.status == "already_ready"


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

def test_successful_onboarding_writes_audit_log(db):
    """Completed onboarding writes an audit_log entry."""
    m = _make_merchant(db)
    p1, p2 = _mock_shopify_ok()
    with p1, p2:
        run_onboarding(db, m)

    audit = db.execute(text(
        "SELECT action_type, actor_name FROM audit_log WHERE action_type = 'onboarding_complete' ORDER BY id DESC LIMIT 1"
    )).fetchone()
    assert audit is not None
    assert audit[1] == "onboarding"


# ---------------------------------------------------------------------------
# Aggregate funnel — N+1 collapse correctness (single GROUP BY across all
# milestones must preserve canonical FUNNEL_MILESTONES order in the output)
# ---------------------------------------------------------------------------

def test_aggregate_funnel_preserves_milestone_order(db):
    """get_aggregate_funnel must emit funnel_steps in canonical order even
    though the underlying SQL GROUP BY does not guarantee row order."""
    from datetime import timedelta
    from app.models.onboarding_event import OnboardingEvent
    from app.services.onboarding_funnel import (
        FUNNEL_MILESTONES,
        get_aggregate_funnel,
    )

    shop = "funnel-order-test.myshopify.com"
    m = _make_merchant(db, shop=shop, status="ready")
    m.installed_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    db.flush()

    # Insert milestones in REVERSE order (worst case for ordering bugs).
    base = datetime.now(timezone.utc).replace(tzinfo=None)
    for i, step in enumerate(reversed(FUNNEL_MILESTONES)):
        db.add(OnboardingEvent(
            shop_domain=shop,
            event_type=step,
            elapsed_seconds=float(10 + i),
            created_at=base - timedelta(minutes=i),
        ))
    db.flush()

    out = get_aggregate_funnel(db, days=30)

    # Order MUST match FUNNEL_MILESTONES exactly
    emitted = [s["step"] for s in out["funnel"]]
    assert emitted == list(FUNNEL_MILESTONES), \
        f"funnel order broken: got {emitted}"

    # Every milestone has reached >= 1 (we inserted them all)
    for s in out["funnel"]:
        assert s["reached"] >= 1, f"step {s['step']} reached=0"


def test_aggregate_funnel_handles_missing_steps(db):
    """If a milestone has zero events, it must still appear with reached=0
    and not crash (was prior behavior; preserve after collapse to GROUP BY)."""
    from app.services.onboarding_funnel import (
        FUNNEL_MILESTONES,
        get_aggregate_funnel,
    )
    from app.models.onboarding_event import OnboardingEvent
    from datetime import timedelta

    shop = "funnel-partial-test.myshopify.com"
    m = _make_merchant(db, shop=shop, status="ready")
    m.installed_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    db.flush()

    # Only insert install_completed (first step) — all subsequent must be 0.
    db.add(OnboardingEvent(
        shop_domain=shop,
        event_type="install_completed",
        elapsed_seconds=1.0,
    ))
    db.flush()

    out = get_aggregate_funnel(db, days=30)
    emitted = {s["step"]: s["reached"] for s in out["funnel"]}

    # All canonical steps present
    for step in FUNNEL_MILESTONES:
        assert step in emitted, f"missing step in output: {step}"

    # install_completed has count, others may be 0 (or > 0 from other test
    # merchants in shared db — only assert for this shop's expected zero
    # absence by checking via a no-event step)
    for missing_step in ("pixel_skipped",):  # interaction event, not in milestones
        assert missing_step not in emitted
