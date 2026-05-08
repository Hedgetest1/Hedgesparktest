"""Tests for streamlined daily digest — scannable in 3 seconds.

Locks the shape: headline + revenue + merchants + pipeline + attention
(only if truly needed) + footer.

Hermeticity note
----------------
build_daily_digest reads from several module-level singletons beyond
the `db` argument: Redis for `hs:system_health` cache, Redis for RARS
history keys, synthesize_health() for degraded-system detection, and
llm_budget.get_usage_summary for cost rollups. A MagicMock()'d db is
not enough to make the test deterministic — any of those globals may
return "degraded" or "critical" based on real prod state, which flips
the string assertions on the digest output.

Each test below uses `_hermetic_digest_mocks()` to patch every
external dependency, guaranteeing a deterministic "healthy + zero
state" baseline.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from app.services import telegram_agent as ta


@contextmanager
def _hermetic_digest_mocks():
    """Patch every non-db dependency of build_daily_digest so the
    digest output is a function of ONLY the mocked db argument.

    Returns a MagicMock for the `db` argument pre-configured to
    return zero/empty for every execute/fetchone/fetchall/scalar."""
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (0, 0)
    db.execute.return_value.fetchall.return_value = []
    db.execute.return_value.scalar.return_value = 0

    healthy_health = {
        "overall_status": "ok",
        "dimensions": [],
        "grade": "A",
        "score": 100,
    }
    healthy_synth = MagicMock()
    healthy_synth.to_dict.return_value = healthy_health

    # Reset button cache before each test so the assertion on
    # callback_data is a function of THIS test's output, not leftover
    # state from whichever other test happened to run first.
    ta._digest_buttons_cache.clear()

    with patch("app.core.redis_client.cache_get", return_value=healthy_health), \
         patch("app.services.system_health_synthesizer.synthesize_health",
               return_value=healthy_synth), \
         patch("app.core.redis_client._client", return_value=None), \
         patch("app.core.llm_budget.get_usage_summary", return_value={
             "month_spend_eur": 0.0,
             "month_cap_eur": 10.0,
             "module_spend_7d": {},
         }):
        yield db


def test_digest_returns_string_with_header():
    """Smoke: build runs against a fully-mocked DB without crashing."""
    with _hermetic_digest_mocks() as db:
        msg = ta.build_daily_digest(db)
    assert "*Daily Digest*" in msg
    assert "all systems running" in msg.lower() or "OK" in msg


def test_digest_no_approve_buttons_for_tier0_or_tier1():
    """Zero TIER_0/1 buttons in the cache."""
    with _hermetic_digest_mocks() as db:
        ta.build_daily_digest(db)

    # Buttons cache must be empty when no TIER_2 is pending
    flat = [b for row in ta._digest_buttons_cache for b in row]
    callback_data = [b.get("callback_data", "") for b in flat]
    assert not any("/bugfix_approve" in c for c in callback_data)
    assert not any("/bugfix_apply" in c for c in callback_data)
    assert not any("/approve" in c for c in callback_data)


def test_digest_attention_section_only_when_needed():
    """No attention section when everything is healthy."""
    with _hermetic_digest_mocks() as db:
        msg = ta.build_daily_digest(db)
    assert "Needs you" not in msg


# ---------------------------------------------------------------------------
# B3/B4/B6 — extended attention coverage + silence policy
# Added 2026-04-18 from reality_founder_messaging.md audit.
# ---------------------------------------------------------------------------

def test_digest_surfaces_critical_unresolved_ops_alerts():
    """B3a — unresolved critical ops_alerts must surface in attention."""
    with _hermetic_digest_mocks() as db:
        def _execute(sql, *args, **kwargs):
            r = MagicMock()
            sql_s = str(sql).lower()
            if "severity='critical'" in sql_s and "group by alert_type" in sql_s:
                r.fetchall.return_value = [
                    ("circuit_breaker_tripped", 1),
                    ("semantic_drift", 1),
                ]
            else:
                r.fetchall.return_value = []
            r.fetchone.return_value = (0, 0)
            r.scalar.return_value = 0
            return r
        db.execute.side_effect = _execute
        msg = ta.build_daily_digest(db)
    assert "Needs you" in msg
    assert "circuit" in msg.lower() and "breaker" in msg.lower()
    assert "semantic" in msg.lower() and "drift" in msg.lower()


def test_digest_spike_ignores_resolved_probes():
    """B3b — high-volume self-resolving probes (heartbeat_synthetic_test)
    must NOT surface as a spike. Only unresolved warning/critical count."""
    with _hermetic_digest_mocks() as db:
        def _execute(sql, *args, **kwargs):
            r = MagicMock()
            sql_s = str(sql).lower()
            # The spike query includes the filter we're locking:
            # `severity IN ('warning', 'critical') AND resolved = false`.
            # Our mock DB has no rows matching that — the audit pattern
            # proves the query filter is intact.
            r.fetchall.return_value = []
            r.fetchone.return_value = (0, 0)
            r.scalar.return_value = 0
            return r
        db.execute.side_effect = _execute
        msg = ta.build_daily_digest(db)
        # Introspect the SQL text actually passed to db.execute — the
        # spike query MUST carry the resolved=false filter.
        executed_sqls = [str(c.args[0]).lower() for c in db.execute.call_args_list]
        spike_queries = [s for s in executed_sqls
                         if "group by alert_type" in s and "having count(*)" in s]
        assert spike_queries, "expected a spike-detection query"
        for q in spike_queries:
            assert "resolved = false" in q or "resolved=false" in q, \
                f"spike query missing resolved=false filter: {q}"
            assert "severity in ('warning', 'critical')" in q, \
                f"spike query missing severity filter: {q}"
    assert "spike" not in msg.lower()


def test_digest_warning_downgraded_when_attention_empty():
    """B4 — WARNING status with no attention-line sources downgrades to OK.
    Prevents misleading 'WARNING' headline without a 'Needs you:' section."""
    degraded_health = {
        "overall_status": "degraded",
        "dimensions": [
            {"name": "alerts", "status": "critical", "value": 0,
             "trend": "stable", "detail": "ops-only", "changed": False},
        ],
        "grade": "B",
        "score": 75,
    }
    degraded_synth = MagicMock()
    degraded_synth.to_dict.return_value = degraded_health

    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (0, 0)
    db.execute.return_value.fetchall.return_value = []
    db.execute.return_value.scalar.return_value = 0
    ta._digest_buttons_cache.clear()

    with patch("app.core.redis_client.cache_get", return_value=degraded_health), \
         patch("app.services.system_health_synthesizer.synthesize_health",
               return_value=degraded_synth), \
         patch("app.core.redis_client._client", return_value=None), \
         patch("app.core.llm_budget.get_usage_summary", return_value={
             "month_spend_eur": 0.0, "month_cap_eur": 10.0, "module_spend_7d": {},
         }):
        msg = ta.build_daily_digest(db)

    # System is degraded but nothing needs founder → headline downgrades
    # and no attention section appears.
    assert "Needs you" not in msg
    assert "WARNING" not in msg
    assert "all systems running" in msg.lower() or "✅" in msg


def test_is_digest_quiet_true_for_clean_state():
    """B6 helper — all healthy + zero attention sources → quiet (True).

    Note: is_digest_quiet checks strict `'healthy'` (matches the real
    synthesizer schema "healthy/degraded/critical"). _hermetic_digest_mocks
    uses the legacy "ok" sentinel, so this test builds its own fixture.
    """
    healthy_health = {"overall_status": "healthy", "dimensions": []}
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (0, 0)
    db.execute.return_value.fetchall.return_value = []
    db.execute.return_value.scalar.return_value = 0

    with patch("app.core.redis_client.cache_get", return_value=healthy_health), \
         patch("app.services.system_health_synthesizer.synthesize_health",
               return_value=MagicMock(to_dict=lambda: healthy_health)):
        assert ta.is_digest_quiet(db) is True


def test_is_digest_quiet_false_on_critical_ops_alert():
    """B6 helper — a single critical unresolved alert defeats silence."""
    healthy_health = {"overall_status": "healthy", "dimensions": []}
    db = MagicMock()

    call_counter = {"crit": 0}
    def _execute(sql, *args, **kwargs):
        r = MagicMock()
        sql_s = str(sql).lower()
        if "severity='critical'" in sql_s and "resolved=false" in sql_s:
            call_counter["crit"] += 1
            r.scalar.return_value = 1  # one critical alert present
        else:
            r.scalar.return_value = 0
        r.fetchone.return_value = (0, 0)
        r.fetchall.return_value = []
        return r
    db.execute.side_effect = _execute

    with patch("app.core.redis_client.cache_get", return_value=healthy_health), \
         patch("app.services.system_health_synthesizer.synthesize_health",
               return_value=MagicMock(to_dict=lambda: healthy_health)):
        assert ta.is_digest_quiet(db) is False
    assert call_counter["crit"] >= 1, "critical-alerts query must run"
