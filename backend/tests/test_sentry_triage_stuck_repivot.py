"""Lock the 2026-05-13 sentry_triage_stuck semantics pivot.

Bug class: pre-pivot, detect_sentry_triage_stuck watched
ai_triage_status='ready' assuming the (now-superseded) bugfix_pipeline
would advance ready→consumed within 6h. After Brain Vero supersession
(CLAUDE.md §21.6), `ready` became the terminal observability state
and the detector accumulated false-positives (30 incidents stuck for
~39 days, observed 2026-05-13).

The pivot watches `pending` instead — the live producer transition
where run_triage_generation (agent_worker, every 15min) advances
pending→ready deterministically. Same SLA (20 incidents, 6h).

Heal-detection: when backlog drains below threshold, prior unresolved
alerts auto-resolve.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text as _sql_text

from app.models.sentry_incident import SentryIncident
from app.services import observability_spikes
from app.services.alerting import write_alert


def _seed_incident(db, status: str, age_hours: float, fingerprint: str):
    """Insert a row in sentry_incidents at given age via ORM."""
    when = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=age_hours)
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


def _unresolved_count(db) -> int:
    return db.execute(
        _sql_text(
            "SELECT COUNT(*) FROM ops_alerts "
            "WHERE alert_type='sentry_triage_stuck' AND resolved=false"
        )
    ).scalar() or 0


def test_pending_backlog_above_threshold_fires(db, monkeypatch):
    """20+ pending incidents older than 6h → alert fires."""
    monkeypatch.setattr(observability_spikes, "_cooldown_ok", lambda *_: True)
    # Wipe existing pending-old incidents from this test's view
    db.execute(_sql_text(
        "DELETE FROM sentry_incidents WHERE fingerprint LIKE 'test_pivot_pending_%'"
    ))
    for i in range(21):
        _seed_incident(db, "pending", age_hours=7.0, fingerprint=f"test_pivot_pending_{i:02d}")

    fired = observability_spikes.detect_sentry_triage_stuck(db)
    db.flush()
    assert fired == 1, "21 pending > 6h must fire alert"
    assert _unresolved_count(db) >= 1


def test_ready_backlog_does_not_fire(db, monkeypatch):
    """`ready` is terminal observability state post-Brain-Vero — even
    huge accumulation must NOT fire (regression test for the false-
    positive that motivated this pivot)."""
    monkeypatch.setattr(observability_spikes, "_cooldown_ok", lambda *_: True)
    db.execute(_sql_text(
        "DELETE FROM sentry_incidents WHERE fingerprint LIKE 'test_pivot_ready_%'"
    ))
    # Wipe pending too so old-pending in DB doesn't fire this test
    db.execute(_sql_text(
        "DELETE FROM sentry_incidents WHERE ai_triage_status='pending' "
        "AND created_at < (now() - interval '6 hours')"
    ))
    for i in range(50):
        _seed_incident(db, "ready", age_hours=24.0, fingerprint=f"test_pivot_ready_{i:02d}")

    fired = observability_spikes.detect_sentry_triage_stuck(db)
    db.flush()
    assert fired == 0, "ready state must NOT trigger alert post-pivot"


def test_heal_clears_unresolved_when_drained(db, monkeypatch):
    """If backlog drops below threshold, unresolved alerts auto-resolve."""
    monkeypatch.setattr(observability_spikes, "_cooldown_ok", lambda *_: True)
    # Wipe pending-old to ensure drained state
    db.execute(_sql_text(
        "DELETE FROM sentry_incidents WHERE ai_triage_status='pending' "
        "AND created_at < (now() - interval '6 hours')"
    ))
    # Seed an unresolved alert manually
    write_alert(
        db,
        severity="warning",
        source="sentry_triage_stuck",
        alert_type="sentry_triage_stuck",
        summary="legacy stuck alert (pre-pivot)",
        detail={"stuck_count": 30, "watched_state": "ready"},
    )
    db.flush()
    assert _unresolved_count(db) >= 1

    # Run detector — backlog drained → heal fires
    observability_spikes.detect_sentry_triage_stuck(db)
    db.flush()
    assert _unresolved_count(db) == 0, "heal must auto-resolve when drained"


def test_recent_pending_does_not_fire(db, monkeypatch):
    """Recent pending (< 6h old) is normal pipeline lag, must not fire."""
    monkeypatch.setattr(observability_spikes, "_cooldown_ok", lambda *_: True)
    db.execute(_sql_text(
        "DELETE FROM sentry_incidents WHERE ai_triage_status='pending' "
        "AND created_at < (now() - interval '6 hours')"
    ))
    for i in range(100):
        _seed_incident(db, "pending", age_hours=1.0, fingerprint=f"test_pivot_recent_{i:02d}")

    fired = observability_spikes.detect_sentry_triage_stuck(db)
    db.flush()
    assert fired == 0, "recent pending (< 6h) must not trigger alert"
