"""Lock the 2026-05-07 slo_breach heal-detection.

Bug class context
-----------------
`detect_slo_breaches` wrote `slo_breach` (critical) alerts when an
SLO's health went red, but did NOT resolve them when the SLO returned
to healthy on a subsequent cycle. Result: load-induced spikes (9
fresh alerts from pytest+restart cycles 2026-05-07) accumulated
indefinitely + polluted the founder digest "Needs you" line, masking
real merchant outages.

Fix: healthy / insufficient_data branch invokes `auto_resolve_alerts`
for both slo_breach and slo_burn_warning on that source. Plus
cross-class heal on the alert-write path so warning→critical
escalation closes the warning row.
"""
from __future__ import annotations

from sqlalchemy import text as _sql_text

from app.services import observability_spikes
from app.services.alerting import write_alert


def _seed(db, name: str, alert_type: str = "slo_breach") -> int:
    a = write_alert(
        db,
        severity="critical",
        source=f"slo:{name}",
        alert_type=alert_type,
        summary=f"seed {alert_type} {name}",
        detail={"seed": True},
    )
    return a.id if a else None


def _unresolved(db, name: str, alert_type: str) -> int:
    return db.execute(
        _sql_text(
            "SELECT COUNT(*) FROM ops_alerts "
            "WHERE source=:s AND alert_type=:at AND resolved=false"
        ),
        {"s": f"slo:{name}", "at": alert_type},
    ).scalar() or 0


def test_healthy_slo_resolves_prior_breach(db, monkeypatch):
    name = "test_slo_heal_a"
    _seed(db, name, "slo_breach")
    assert _unresolved(db, name, "slo_breach") >= 1

    # SLO report returns ONLY the test SLO as healthy → heal branch fires
    fake_report = [{"name": name, "health": "healthy", "route": "/x", "method": "GET"}]
    monkeypatch.setattr(
        "app.core.slo.slo_report", lambda: fake_report
    )
    observability_spikes.detect_slo_breaches(db)
    db.flush()
    assert _unresolved(db, name, "slo_breach") == 0


def test_healthy_slo_resolves_prior_burn_warning(db, monkeypatch):
    name = "test_slo_heal_b"
    _seed(db, name, "slo_burn_warning")
    assert _unresolved(db, name, "slo_burn_warning") >= 1

    fake_report = [{"name": name, "health": "insufficient_data", "route": "/y", "method": "GET"}]
    monkeypatch.setattr(
        "app.core.slo.slo_report", lambda: fake_report
    )
    observability_spikes.detect_slo_breaches(db)
    db.flush()
    assert _unresolved(db, name, "slo_burn_warning") == 0


def test_breaching_slo_writes_alert_and_does_NOT_heal(db, monkeypatch):
    """Reverse contract: when the SLO is still red, the heal branch
    is bypassed; the alert is written (or dedup'd by cooldown) and
    the prior alert stays unresolved."""
    name = "test_slo_heal_c"
    _seed(db, name, "slo_breach")
    fake_report = [{
        "name": name, "health": "breach", "route": "/z", "method": "GET",
        "availability_pct": 80, "availability_target_pct": 99,
        "p95_ms": 500, "p95_target_ms": 200, "burn_rate": 5,
    }]
    monkeypatch.setattr(
        "app.core.slo.slo_report", lambda: fake_report
    )
    # Force-clear cooldown so the alert write isn't suppressed
    monkeypatch.setattr(
        observability_spikes, "_cooldown_ok", lambda *a, **kw: True
    )
    observability_spikes.detect_slo_breaches(db)
    db.flush()
    # Prior alert still unresolved (since SLO didn't return to healthy)
    assert _unresolved(db, name, "slo_breach") >= 1
