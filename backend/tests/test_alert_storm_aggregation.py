"""
Tests for the alert storm aggregation behaviour added to alerting.write_alert.

Regression: pre-2026-04-11 the dedup window was 5 minutes. Workers running
every 15 minutes therefore wrote a fresh ops_alert row every cycle for
chronic issues (circuit_breaker_tripped, slow_activation, …), reaching
95 duplicate rows per 24h in production.

New behaviour:
  * 5-minute acute dedup: identical alerts within 5 min are dropped silently
  * 24-hour chronic aggregation: repeat alerts between 5 min and 24 hours
    collapse into the existing unresolved row and increment
    detail.occurrence_count
  * After 24 h the alert is treated as a new occurrence (operator hasn't
    acknowledged it in a day — we want a fresh row so the freshness is
    visible)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.models.ops_alert import OpsAlert
from app.services.alerting import write_alert


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_acute_duplicate_within_5min_returns_existing_no_mutation(db):
    """Two identical alerts within 5 min → one row, no counter."""
    a = write_alert(
        db, severity="warning", source="test_acute",
        alert_type="pipeline_stall_test", summary="First",
        detail={"cycle": 1},
    )
    b = write_alert(
        db, severity="warning", source="test_acute",
        alert_type="pipeline_stall_test", summary="Second",
        detail={"cycle": 2},
    )
    assert a.id == b.id, "acute dedup must return the same row"
    # Detail should NOT have been mutated into a counter shape
    parsed = json.loads(a.detail) if a.detail else {}
    assert "occurrence_count" not in parsed, (
        "acute dedup must not trigger counter aggregation"
    )


def test_chronic_aggregation_collapses_repeats_into_counter(db):
    """An alert from >5 min but <24 h ago should collapse the repeat."""
    # Create a chronic-window alert by backdating.
    old = OpsAlert(
        severity="warning",
        source="test_chronic",
        alert_type="pipeline_stall_chronic",
        summary="Initial chronic",
        detail='{"cycle": 1}',
        created_at=_now() - timedelta(minutes=30),  # past acute, inside chronic
        resolved=False,
    )
    db.add(old)
    db.flush()

    # Now write a "repeat" — same source/type — should collapse into `old`.
    result = write_alert(
        db, severity="warning", source="test_chronic",
        alert_type="pipeline_stall_chronic", summary="Repeat",
        detail={"cycle": 2, "extra": "new context"},
    )
    assert result.id == old.id, "chronic repeat must collapse into existing row"
    parsed = json.loads(result.detail)
    assert parsed["occurrence_count"] == 2, f"expected 2, got {parsed}"
    assert "last_seen_at" in parsed
    assert "last_summary" in parsed
    assert parsed["last_summary"] == "Repeat"


def test_chronic_aggregation_increments_counter_on_each_repeat(db):
    """Three repeats → counter reaches 3, last_seen_at is fresh."""
    old = OpsAlert(
        severity="warning",
        source="test_counter",
        alert_type="pipeline_stall_counter",
        summary="Start",
        detail=None,
        created_at=_now() - timedelta(hours=2),
        resolved=False,
    )
    db.add(old)
    db.flush()

    for i in range(3):
        write_alert(
            db, severity="warning", source="test_counter",
            alert_type="pipeline_stall_counter",
            summary=f"Repeat {i}",
            detail={"idx": i},
        )

    db.refresh(old)
    parsed = json.loads(old.detail)
    assert parsed["occurrence_count"] == 4  # 1 initial + 3 repeats
    assert "last_seen_at" in parsed
    assert "initial_summary" in parsed
    assert parsed["initial_summary"] == "Start"


def test_chronic_aggregation_ignores_resolved_alerts(db):
    """If the existing alert has been resolved, a repeat creates a new row."""
    old = OpsAlert(
        severity="warning",
        source="test_resolved",
        alert_type="pipeline_stall_resolved",
        summary="Resolved",
        detail=None,
        created_at=_now() - timedelta(hours=2),
        resolved=True,
        resolved_at=_now() - timedelta(hours=1),
    )
    db.add(old)
    db.flush()

    result = write_alert(
        db, severity="warning", source="test_resolved",
        alert_type="pipeline_stall_resolved", summary="New occurrence",
        detail={"fresh": True},
    )
    assert result.id != old.id, "a new row must be created after a resolution"


def test_chronic_aggregation_ignores_alerts_older_than_24h(db):
    """After 24h, create a fresh row so freshness is visible."""
    old = OpsAlert(
        severity="warning",
        source="test_stale",
        alert_type="pipeline_stall_stale",
        summary="Stale 25h old",
        detail=None,
        created_at=_now() - timedelta(hours=25),
        resolved=False,
    )
    db.add(old)
    db.flush()

    result = write_alert(
        db, severity="warning", source="test_stale",
        alert_type="pipeline_stall_stale", summary="New day",
        detail=None,
    )
    assert result.id != old.id, "alerts older than 24h must not aggregate"


def test_aggregation_preserves_initial_detail_on_first_collapse(db):
    """The first collapse must move the prior detail string into
    initial_detail so we don't lose context."""
    old = OpsAlert(
        severity="warning",
        source="test_preserve",
        alert_type="pipeline_stall_preserve",
        summary="Initial",
        detail="some_legacy_context_string",
        created_at=_now() - timedelta(hours=1),
        resolved=False,
    )
    db.add(old)
    db.flush()

    write_alert(
        db, severity="warning", source="test_preserve",
        alert_type="pipeline_stall_preserve", summary="Repeat",
        detail=None,
    )
    db.refresh(old)
    parsed = json.loads(old.detail)
    assert parsed["initial_detail"] == "some_legacy_context_string"
    assert parsed["occurrence_count"] == 2


def test_aggregation_scoped_by_shop_domain(db):
    """Two alerts with same (type,source) but different shop_domain must
    not collapse into each other."""
    a = write_alert(
        db, severity="warning", source="test_shop_scope",
        alert_type="pipeline_stall_scope", summary="Shop A",
        shop_domain="shop-a.myshopify.com", detail=None,
    )
    b = write_alert(
        db, severity="warning", source="test_shop_scope",
        alert_type="pipeline_stall_scope", summary="Shop B",
        shop_domain="shop-b.myshopify.com", detail=None,
    )
    assert a.id != b.id, "per-shop isolation must be preserved"
