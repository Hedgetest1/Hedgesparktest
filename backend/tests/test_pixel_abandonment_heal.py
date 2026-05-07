"""Lock the 2026-05-07 pixel_abandonment heal-detection.

Bug class: write_onboarding_alerts wrote pixel_abandonment alerts
when a merchant had been onboarded >72h ago without a single
pixel-source order, but the existing heal_per_shop_alerts call at
onboarding_health.py:573-576 already resolves any prior alert whose
shop is no longer in the long_abandon (>72h, no pixel orders) set.
This test locks the contract so the heal call cannot regress
silently.

Recipe note: pixel_abandonment uses the population-scan pattern
(`heal_per_shop_alerts`), not the periodic-check
`auto_resolve_alerts` pattern. The audit recognises it via the
`# heal-detection:` comment marker inside the write_alert kwargs
because heal_per_shop_alerts uses positional args that the AST
scanner doesn't follow.
"""
from __future__ import annotations

from sqlalchemy import text as _sql_text

from app.services import onboarding_health
from app.services.alerting import write_alert


_SOURCE = "onboarding_health"
_TYPE = "pixel_abandonment"


def _seed_alert(db, shop: str) -> int | None:
    a = write_alert(
        db,
        severity="info",
        source=_SOURCE,
        alert_type=_TYPE,
        shop_domain=shop,
        summary=f"seed pixel_abandonment {shop}",
        detail={"shop_domain": shop, "hours_since_install": 96},
    )
    return a.id if a else None


def _unresolved(db, shop: str) -> int:
    return db.execute(
        _sql_text(
            "SELECT COUNT(*) FROM ops_alerts "
            "WHERE source=:s AND alert_type=:t AND resolved=false "
            "  AND shop_domain=:shop"
        ),
        {"s": _SOURCE, "t": _TYPE, "shop": shop},
    ).scalar() or 0


def test_shop_no_longer_abandoning_heals(db, monkeypatch):
    """Shop that was previously in long_abandon population but
    is no longer (pixel started firing OR uninstalled) → its
    unresolved alert auto-resolves."""
    healed_shop = "test-pixel-heal-1.myshopify.com"
    _seed_alert(db, healed_shop)
    db.flush()
    assert _unresolved(db, healed_shop) == 1

    # Stub detect_pixel_abandonment: no shops are abandoning anymore.
    monkeypatch.setattr(
        onboarding_health, "detect_pixel_abandonment", lambda _db: []
    )
    # Stub the other detectors so they don't touch the test seed.
    monkeypatch.setattr(
        onboarding_health, "detect_stuck_merchants", lambda _db: []
    )
    monkeypatch.setattr(
        onboarding_health, "detect_slow_activation", lambda _db: []
    )
    monkeypatch.setattr(
        onboarding_health, "detect_drifting_new_installs", lambda _db: []
    )

    onboarding_health.write_onboarding_alerts(db)
    db.flush()
    assert _unresolved(db, healed_shop) == 0


def test_shop_still_abandoning_does_not_heal(db, monkeypatch):
    """Shop still in the long_abandon (>72h) population → its
    existing unresolved alert STAYS unresolved."""
    active_shop = "test-pixel-still-1.myshopify.com"
    _seed_alert(db, active_shop)
    db.flush()
    assert _unresolved(db, active_shop) == 1

    monkeypatch.setattr(
        onboarding_health,
        "detect_pixel_abandonment",
        lambda _db: [
            {
                "shop_domain": active_shop,
                "installed_at": "2026-05-01T00:00:00",
                "hours_since_install": 100,
                "pixel_orders": 0,
            }
        ],
    )
    monkeypatch.setattr(
        onboarding_health, "detect_stuck_merchants", lambda _db: []
    )
    monkeypatch.setattr(
        onboarding_health, "detect_slow_activation", lambda _db: []
    )
    monkeypatch.setattr(
        onboarding_health, "detect_drifting_new_installs", lambda _db: []
    )

    onboarding_health.write_onboarding_alerts(db)
    db.flush()
    # Still in active set → not healed.
    assert _unresolved(db, active_shop) >= 1


def test_shop_below_72h_threshold_heals(db, monkeypatch):
    """Shop in detect_pixel_abandonment but <=72h → excluded from
    long_abandon, so its prior alert auto-resolves (the heal call
    operates on the long_abandon set, not the raw detect output)."""
    sub_threshold_shop = "test-pixel-sub-1.myshopify.com"
    _seed_alert(db, sub_threshold_shop)
    db.flush()
    assert _unresolved(db, sub_threshold_shop) == 1

    monkeypatch.setattr(
        onboarding_health,
        "detect_pixel_abandonment",
        lambda _db: [
            {
                "shop_domain": sub_threshold_shop,
                "installed_at": "2026-05-05T00:00:00",
                "hours_since_install": 50,  # below 72h cutoff
                "pixel_orders": 0,
            }
        ],
    )
    monkeypatch.setattr(
        onboarding_health, "detect_stuck_merchants", lambda _db: []
    )
    monkeypatch.setattr(
        onboarding_health, "detect_slow_activation", lambda _db: []
    )
    monkeypatch.setattr(
        onboarding_health, "detect_drifting_new_installs", lambda _db: []
    )

    onboarding_health.write_onboarding_alerts(db)
    db.flush()
    assert _unresolved(db, sub_threshold_shop) == 0
