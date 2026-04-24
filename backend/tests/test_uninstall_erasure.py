"""Tests for uninstall_erasure watchdog — GDPR Art. 17 belt-and-braces."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models.gdpr_request import GdprRequest
from app.models.merchant import Merchant
from app.models.ops_alert import OpsAlert
from app.services.uninstall_erasure import (
    _GRACE_PERIOD_HOURS,
    _has_recent_redact_request,
    run_uninstall_erasure_watchdog,
)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_uninstalled(db, hours_since: int) -> Merchant:
    shop = f"watchdog-{uuid.uuid4().hex[:8]}.myshopify.com"
    m = Merchant(
        shop_domain=shop,
        access_token=None,
        plan="lite",
        install_status="uninstalled",
        uninstalled_at=_utcnow() - timedelta(hours=hours_since),
    )
    db.add(m)
    db.flush()
    return m


def test_inside_grace_window_is_untouched(db):  # hermetic-ok: uuid-via-callee
    """Merchants uninstalled < 48h ago must NOT be redacted yet.

    Hermeticity: `_make_uninstalled()` generates a uuid-scoped
    shop_domain (`watchdog-{uuid4().hex[:8]}.myshopify.com`) and the
    assertion filters by that unique shop — prod rows cannot collide."""
    m = _make_uninstalled(db, hours_since=24)
    report = run_uninstall_erasure_watchdog(db)
    assert report["self_healed"] == 0
    # No GdprRequest created
    count = db.query(GdprRequest).filter(
        GdprRequest.shop_domain == m.shop_domain,
        GdprRequest.request_type == "shop_redact",
    ).count()
    assert count == 0


def test_past_grace_window_self_heals(db):
    """Merchants uninstalled > 48h ago with no redact request get one."""
    m = _make_uninstalled(db, hours_since=_GRACE_PERIOD_HOURS + 5)
    report = run_uninstall_erasure_watchdog(db)
    assert report["self_healed"] >= 1
    created = db.query(GdprRequest).filter(
        GdprRequest.shop_domain == m.shop_domain,
        GdprRequest.request_type == "shop_redact",
    ).all()
    assert len(created) == 1
    assert created[0].status == "pending"
    assert "uninstall_erasure_watchdog" in (created[0].payload or "")


def test_self_heal_emits_warning_alert(db):
    m = _make_uninstalled(db, hours_since=_GRACE_PERIOD_HOURS + 10)
    run_uninstall_erasure_watchdog(db)
    alerts = db.query(OpsAlert).filter(
        OpsAlert.alert_type == "uninstall_erasure_self_healed",
        OpsAlert.shop_domain == m.shop_domain,
    ).all()
    assert len(alerts) == 1
    assert alerts[0].severity == "warning"


def test_dedup_existing_redact_request(db):
    """If a shop_redact request already exists, don't duplicate."""
    m = _make_uninstalled(db, hours_since=72)
    # Pre-existing redact request (e.g. from Shopify's own webhook)
    db.add(GdprRequest(
        request_type="shop_redact",
        shop_domain=m.shop_domain,
        status="completed",
        created_at=_utcnow() - timedelta(hours=1),
    ))
    db.flush()

    report = run_uninstall_erasure_watchdog(db)
    assert report["self_healed"] == 0
    assert report["already_redacted"] >= 1


def test_active_shops_ignored(db):
    """install_status='active' must never be touched."""
    shop = f"active-{uuid.uuid4().hex[:8]}.myshopify.com"
    m = Merchant(
        shop_domain=shop,
        access_token="enc:fake",
        plan="lite",
        install_status="active",
        uninstalled_at=None,
    )
    db.add(m)
    db.flush()

    report = run_uninstall_erasure_watchdog(db)
    count = db.query(GdprRequest).filter(
        GdprRequest.shop_domain == shop,
    ).count()
    assert count == 0


def test_has_recent_redact_request_helper(db):
    shop = f"helper-{uuid.uuid4().hex[:8]}.myshopify.com"
    assert _has_recent_redact_request(db, shop) is False

    db.add(GdprRequest(
        request_type="shop_redact",
        shop_domain=shop,
        status="pending",
        created_at=_utcnow(),
    ))
    db.flush()
    assert _has_recent_redact_request(db, shop) is True
