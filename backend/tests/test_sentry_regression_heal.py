"""Lock the 2026-05-07 sentry_regression heal-detection.

Bug class: detect_sentry_regressions wrote alerts when a
fingerprint that had a `consumed` bugfix candidate fired again
within 30 min, but never resolved them when the fingerprint stopped
firing. 4 alerts piled up from 2026-05-04→05-07 with their
underlying incidents long-quiet — polluting the founder digest
"Needs you" line. Heal-detection: at end of detect cycle, any
unresolved sentry_regression alert whose fingerprint has zero
recent incidents auto-resolves.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text as _sql_text

from app.services import observability_spikes
from app.services.alerting import write_alert


def _seed_alert(db, fingerprint: str) -> int:
    a = write_alert(
        db,
        severity="critical",
        source=f"sentry_regression:{fingerprint[:32]}",
        alert_type="sentry_regression",
        summary=f"seed regression {fingerprint[:20]}",
        detail={"fingerprint": fingerprint},
    )
    return a.id if a else None


def _seed_incident(db, fingerprint: str, age_minutes: int):
    """Insert a row in sentry_incidents at age_minutes ago via ORM
    so SQLAlchemy applies all default values for NOT NULL columns."""
    from app.models.sentry_incident import SentryIncident
    when = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=age_minutes)
    inc = SentryIncident(
        fingerprint=fingerprint,
        ai_triage_status="consumed",
        project="p",
        environment="production",
        source_type="email",
    )
    inc.created_at = when
    db.add(inc)
    db.flush()


def _unresolved(db, fingerprint: str) -> int:
    return db.execute(
        _sql_text(
            "SELECT COUNT(*) FROM ops_alerts "
            "WHERE source=:s AND alert_type='sentry_regression' AND resolved=false"
        ),
        {"s": f"sentry_regression:{fingerprint[:32]}"},
    ).scalar() or 0


def test_quiet_fingerprint_heals(db, monkeypatch):
    """Fingerprint with no recent (last 30min) incidents → unresolved
    alert auto-resolves."""
    fp = "abcdef" * 5 + "ab"  # 32 chars (matches DB column cap)
    _seed_alert(db, fp)
    assert _unresolved(db, fp) >= 1

    # No sentry_incidents seeded → fingerprint is quiet → heal fires.
    observability_spikes.detect_sentry_regressions(db)
    db.flush()
    assert _unresolved(db, fp) == 0


def test_active_fingerprint_does_not_heal(db, monkeypatch):
    """Fingerprint WITH recent incidents stays unresolved (the
    detect-write path may also fire a fresh alert, but the existing
    one doesn't auto-resolve)."""
    fp = "feedfa" * 5 + "fe"  # 32 chars
    _seed_alert(db, fp)
    _seed_incident(db, fp, age_minutes=5)  # recent

    # Force-clear cooldown so any new alert write isn't suppressed
    monkeypatch.setattr(
        observability_spikes, "_cooldown_ok", lambda *a, **kw: True
    )
    observability_spikes.detect_sentry_regressions(db)
    db.flush()
    # Underlying issue still firing → don't auto-resolve.
    assert _unresolved(db, fp) >= 1
