"""Lock the 2026-05-13 sentry_regression pivot from `consumed` precursor
to producer-agnostic silent-period detection.

Bug class: detect_sentry_regressions required `ai_triage_status='consumed'`
as the precursor — that state had no producer post-Brain-Vero supersession
(§21.6, 2026-05-07). Detector became structurally dead.

Pivot: regression = fingerprint had a prior incident more than 7 days
ago, no activity in the silent window (between 7d ago and 30min ago),
then NEW incidents in last 30min. Producer-agnostic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text as _sql_text

from app.models.sentry_incident import SentryIncident
from app.services import observability_spikes


def _seed_incident(db, fingerprint: str, age, status: str = "ready"):
    """Insert a sentry incident at given age (timedelta back from now)."""
    when = datetime.now(timezone.utc).replace(tzinfo=None) - age
    inc = SentryIncident(
        fingerprint=fingerprint,
        ai_triage_status=status,
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
            "WHERE alert_type='sentry_regression' AND resolved=false "
            "AND source LIKE :s"
        ),
        {"s": f"sentry_regression:{fingerprint[:20]}%"},
    ).scalar() or 0


def test_fires_on_silent_then_reappear(db, monkeypatch):
    """Fingerprint with prior > 7d, silent 7d→30min, new in last 30min
    → regression fires."""
    monkeypatch.setattr(observability_spikes, "_cooldown_ok", lambda *_: True)
    fp = "pivot01" * 4 + "ab"  # 30-char distinct fingerprint
    # Wipe any prior test rows
    db.execute(_sql_text("DELETE FROM sentry_incidents WHERE fingerprint=:fp"), {"fp": fp})

    _seed_incident(db, fp, timedelta(days=10))  # prior > 7d
    _seed_incident(db, fp, timedelta(minutes=5))  # new in last 30min
    fired = observability_spikes.detect_sentry_regressions(db)
    db.flush()
    assert fired >= 1
    assert _unresolved(db, fp) >= 1


def test_does_not_fire_when_steady_state_active(db, monkeypatch):
    """Fingerprint that fired CONTINUOUSLY (no silent period) is NOT
    a regression — it's just an ongoing issue."""
    monkeypatch.setattr(observability_spikes, "_cooldown_ok", lambda *_: True)
    fp = "pivot02" * 4 + "cd"
    db.execute(_sql_text("DELETE FROM sentry_incidents WHERE fingerprint=:fp"), {"fp": fp})

    _seed_incident(db, fp, timedelta(days=10))   # prior > 7d
    _seed_incident(db, fp, timedelta(days=3))    # mid-period activity
    _seed_incident(db, fp, timedelta(hours=1))   # recent (but pre-30min — still in silent window for new)
    _seed_incident(db, fp, timedelta(minutes=5)) # new in last 30min
    fired = observability_spikes.detect_sentry_regressions(db)
    db.flush()
    # Mid-period activity at -3d disqualifies (no 7d silence)
    assert _unresolved(db, fp) == 0


def test_does_not_fire_on_consumed_legacy_state(db, monkeypatch):
    """Pre-pivot the detector required `consumed`. Post-pivot, the
    state field is irrelevant — only timing matters. A fingerprint
    in `consumed` state (legacy 49 rows) without silent period must
    NOT fire."""
    monkeypatch.setattr(observability_spikes, "_cooldown_ok", lambda *_: True)
    fp = "pivot03" * 4 + "ef"
    db.execute(_sql_text("DELETE FROM sentry_incidents WHERE fingerprint=:fp"), {"fp": fp})

    _seed_incident(db, fp, timedelta(days=10), status="consumed")  # legacy state, > 7d
    _seed_incident(db, fp, timedelta(days=3), status="ready")  # mid-period — kills silence
    _seed_incident(db, fp, timedelta(minutes=5))
    fired = observability_spikes.detect_sentry_regressions(db)
    db.flush()
    # Mid-period activity kills the silent-period condition
    assert _unresolved(db, fp) == 0


def test_no_prior_incident_does_not_fire(db, monkeypatch):
    """First-ever fingerprint occurrence is NOT a regression."""
    monkeypatch.setattr(observability_spikes, "_cooldown_ok", lambda *_: True)
    fp = "pivot04" * 4 + "gh"
    db.execute(_sql_text("DELETE FROM sentry_incidents WHERE fingerprint=:fp"), {"fp": fp})

    _seed_incident(db, fp, timedelta(minutes=5))  # only one, no history
    fired = observability_spikes.detect_sentry_regressions(db)
    db.flush()
    assert _unresolved(db, fp) == 0
