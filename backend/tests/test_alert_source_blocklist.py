"""Tests for app/core/alert_source_blocklist.py — the L1+L2 source-side
defense against synthetic/retired test fixtures leaking ops_alerts.
Born 2026-05-11 Senior+++ close.
"""
from __future__ import annotations


def test_synthetic_phase_test_source_matches():
    from app.core.alert_source_blocklist import is_synthetic_alert_source
    # The exact source that persisted 16d (id=109688)
    assert is_synthetic_alert_source("phase_c_synthetic_test") is True
    # Pattern generalizes to other Phase letters
    assert is_synthetic_alert_source("phase_a_synthetic_test") is True
    assert is_synthetic_alert_source("phase_z_synthetic_test") is True


def test_synthetic_test_suffix_matches():
    from app.core.alert_source_blocklist import is_synthetic_alert_source
    assert is_synthetic_alert_source("e2e_synthetic_test") is True
    assert is_synthetic_alert_source("smoke_synthetic_test") is True
    assert is_synthetic_alert_source("integration_synthetic_test") is True


def test_synthetic_prefix_matches():
    from app.core.alert_source_blocklist import is_synthetic_alert_source
    assert is_synthetic_alert_source("synthetic-loadtest-runner") is True
    assert is_synthetic_alert_source("synthetic_test_runner") is True


def test_loadtest_prefix_matches():
    from app.core.alert_source_blocklist import is_synthetic_alert_source
    assert is_synthetic_alert_source("_loadtest_burst_runner") is True
    assert is_synthetic_alert_source("_loadtest_") is True


def test_real_production_sources_do_not_match():
    """Critical: real production sources must NEVER match. A false
    positive here = silently dropped real alerts."""
    from app.core.alert_source_blocklist import is_synthetic_alert_source
    # All real sources from a current ops_alerts source distribution
    for source in (
        "onboarding_health",
        "onboarding_funnel",
        "auth_hardening",
        "merchant_brain",
        "staged_rollout",
        "fe:DailyBrief:1555c453",
        "fe:lite:584b7522",
        "p95_drift:/pro/night-shift/latest",
        "invariant:endpoint_test_coverage",
        "phase_c_synthetic_test_real_module",  # not anchored, real module
        # Brand-named sources (HedgeSpark, hs_*) must not collide
        "hs_brain",
        "hs_test_phase",  # contains "test" but no anchor pattern
    ):
        assert is_synthetic_alert_source(source) is False, \
            f"FALSE POSITIVE on real source: {source!r}"


def test_none_and_empty_safe():
    from app.core.alert_source_blocklist import is_synthetic_alert_source
    assert is_synthetic_alert_source(None) is False
    assert is_synthetic_alert_source("") is False
    assert is_synthetic_alert_source("   ") is False


def test_case_insensitive():
    from app.core.alert_source_blocklist import is_synthetic_alert_source
    assert is_synthetic_alert_source("PHASE_C_SYNTHETIC_TEST") is True
    assert is_synthetic_alert_source("Synthetic-Loadtest-Runner") is True


def test_alerting_write_alert_drops_synthetic_source(monkeypatch):
    """End-to-end: write_alert with a synthetic source returns a stub
    OpsAlert WITHOUT persisting to the DB. Mirrors the shop-side guard
    contract."""
    from app.services import alerting

    # Use a sentinel to detect any DB persist attempt
    persisted = []

    class _SpyDb:
        def add(self, obj):
            persisted.append(obj)
        def flush(self):
            pass
        def execute(self, *a, **kw):
            class _R:
                rowcount = 0
                def fetchone(self):
                    return None
                def scalar(self):
                    return 0
            return _R()
        def commit(self):
            pass
        def rollback(self):
            pass

    db = _SpyDb()
    result = alerting.write_alert(
        db,
        severity="warning",
        source="phase_c_synthetic_test",
        alert_type="lite_nav_section_missing",
        shop_domain=None,
        summary="SYNTHETIC: simulated test",
    )

    # Stub returned, NOT persisted
    assert result is not None
    assert result.id is None  # never assigned by add+flush
    assert persisted == [], "synthetic-source alert MUST NOT be persisted"


def test_auto_resolve_sql_targets_synthetic_sources_only(db):
    """L2 sweep SQL must only resolve alerts matching synthetic source
    patterns AND age >7d. Real-source alerts and recent synthetic alerts
    are untouched."""
    from sqlalchemy import text
    from app.core.alert_source_blocklist import AUTO_RESOLVE_SQL

    # Insert mix: 1 old synthetic, 1 recent synthetic, 1 old real
    db.execute(text("""
        INSERT INTO ops_alerts
            (created_at, severity, source, alert_type, summary, resolved)
        VALUES
            (now() - interval '10 days', 'medium',
             'phase_b_synthetic_test', 'test_alert',
             'old synthetic should be resolved', false),
            (now() - interval '1 day', 'medium',
             'phase_b_synthetic_test', 'test_alert',
             'recent synthetic should NOT be resolved', false),
            (now() - interval '10 days', 'warning',
             'onboarding_health', 'real_alert',
             'old REAL should NOT be resolved', false)
    """))
    db.flush()

    res = db.execute(text(AUTO_RESOLVE_SQL))
    db.flush()

    # Only the old synthetic was resolved
    rows = db.execute(text("""
        SELECT summary, resolved
          FROM ops_alerts
         WHERE summary LIKE '%should %'
         ORDER BY summary
    """)).fetchall()
    summary_to_resolved = {r.summary: r.resolved for r in rows}

    assert summary_to_resolved["old synthetic should be resolved"] is True
    assert summary_to_resolved["recent synthetic should NOT be resolved"] is False
    assert summary_to_resolved["old REAL should NOT be resolved"] is False
