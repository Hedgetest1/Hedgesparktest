"""Locks 2026-04-18 ramification fix in system_health_synthesizer.

Before the fix, top_issues only included dimensions that were critical
OR degraded+worsening. A dimension that was degraded+stable (e.g.,
liveness flagged "Pipeline stalled: 5 proposals but 0 applied (7d)")
drove overall_status to "degraded" but produced an empty top_issues
list — so the CTO Telegram transition message read
"🟡 *SYSTEM: DEGRADED*" with NO explanation.

After the fix: every founder-actionable non-healthy dim is surfaced in
top_issues (with a "(worsening)" suffix if applicable), so the
transition message tells the founder WHY.

These tests lock both directions of the contract.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.system_health_synthesizer import HealthDimension, synthesize_health


def _mk(name: str, *, status="healthy", trend="stable", detail="ok", value=0):
    def _fn(db, now):
        return HealthDimension(
            name=name, status=status, value=value, trend=trend, detail=detail,
        )
    return _fn


def _run_with_dims(
    *,
    liveness=("healthy", "stable", "ok"),
    workers=("healthy", "stable", "ok"),
    pipeline=("healthy", "stable", "ok"),
    merchants=("healthy", "stable", "ok"),
    freshness=("healthy", "stable", "ok"),
    fix_rate=("healthy", "stable", "ok"),
    alerts=("healthy", "stable", "ok"),
):
    patches = [
        patch("app.services.system_health_synthesizer._assess_worker_health",
              side_effect=_mk("workers", status=workers[0], trend=workers[1], detail=workers[2])),
        patch("app.services.system_health_synthesizer._assess_pipeline_health",
              side_effect=_mk("pipeline", status=pipeline[0], trend=pipeline[1], detail=pipeline[2])),
        patch("app.services.system_health_synthesizer._assess_pipeline_liveness",
              side_effect=_mk("liveness", status=liveness[0], trend=liveness[1], detail=liveness[2])),
        patch("app.services.system_health_synthesizer._assess_merchant_health",
              side_effect=_mk("merchants", status=merchants[0], trend=merchants[1], detail=merchants[2])),
        patch("app.services.system_health_synthesizer._assess_data_freshness",
              side_effect=_mk("freshness", status=freshness[0], trend=freshness[1], detail=freshness[2])),
        patch("app.services.system_health_synthesizer._assess_fix_effectiveness",
              side_effect=_mk("fix_rate", status=fix_rate[0], trend=fix_rate[1], detail=fix_rate[2])),
        patch("app.services.system_health_synthesizer._assess_alert_pressure",
              side_effect=_mk("alerts", status=alerts[0], trend=alerts[1], detail=alerts[2])),
        patch("app.core.redis_client.cache_get", return_value=None),
    ]
    for p in patches:
        p.start()
    try:
        return synthesize_health(MagicMock())
    finally:
        for p in patches:
            p.stop()


def test_top_issues_includes_stable_degraded_actionable_dim():
    """The load-bearing 2026-04-18 fix: stable-degraded actionable dim
    (liveness: stalled) must appear in top_issues so the CTO transition
    message explains the DEGRADED headline.

    Note: synthesize_health requires ≥2 actionable_degraded OR 1 degraded
    + 1 worsening to reach overall='degraded'. This test uses liveness
    degraded+stable plus pipeline healthy+worsening to match the live
    prod state that triggered the audit finding.
    """
    state = _run_with_dims(
        liveness=("degraded", "stable", "Pipeline stalled: 5 proposals but 0 applied (7d)"),
        pipeline=("healthy", "worsening", "8 queued, 0 applied (7d)"),
    )
    assert state.overall_status == "degraded", \
        f"expected degraded, got {state.overall_status}"
    assert any("liveness" in i and "stalled" in i.lower() for i in state.top_issues), \
        f"top_issues missing liveness stalled explanation: {state.top_issues}"


def test_top_issues_marks_worsening_trend_with_suffix():
    """Worsening degraded dim still surfaces and gets the (worsening) suffix
    so the founder can distinguish sustained vs accelerating problems."""
    state = _run_with_dims(
        liveness=("degraded", "worsening", "something bad"),
    )
    assert any("liveness" in i and "worsening" in i for i in state.top_issues)


def test_top_issues_empty_when_all_healthy():
    """Healthy baseline → empty top_issues + overall healthy."""
    state = _run_with_dims()
    assert state.overall_status == "healthy"
    assert state.top_issues == []


def test_top_issues_skips_stable_degraded_ops_only_dim():
    """ops-only dims (alerts, fix_rate) degraded+stable should NOT spam
    top_issues — they're operational signals, not founder-actionable.
    Only their CRITICAL state surfaces (different code path)."""
    state = _run_with_dims(
        alerts=("degraded", "stable", "12 active types"),
    )
    # alerts being degraded with no actionable criticals → overall stays healthy
    # or degraded but alerts shouldn't leak into top_issues unless critical
    for issue in state.top_issues:
        assert "alerts" not in issue or "(ops)" in issue, \
            f"ops-only 'alerts' leaked into top_issues: {issue}"


def test_top_issues_surfaces_critical_ops_only_dim():
    """A critical ops-only dim (e.g., alerts=critical) should still land
    in top_issues — it's rare but meaningful (20+ distinct alert types)."""
    state = _run_with_dims(
        alerts=("critical", "stable", "25 active issues"),
    )
    assert any("alerts" in i and "(ops)" in i for i in state.top_issues), \
        f"critical ops dim missing from top_issues: {state.top_issues}"


def test_top_issues_severity_sort_critical_before_degraded():
    """With the Telegram 3-line cap, critical must land ABOVE degraded
    in top_issues so the worst dim is always visible. Before the
    2026-04-18 severity-sort, insertion order was dimension-iteration
    order (workers→pipeline→liveness→...), so a degraded 'workers'
    would bury a critical 'liveness' at index 2."""
    state = _run_with_dims(
        workers=("degraded", "stable", "workers degraded"),
        liveness=("critical", "stable", "liveness broken"),
    )
    # Find indices
    liveness_idx = next(
        (i for i, issue in enumerate(state.top_issues) if "liveness" in issue), -1,
    )
    workers_idx = next(
        (i for i, issue in enumerate(state.top_issues) if "workers" in issue), -1,
    )
    assert liveness_idx != -1 and workers_idx != -1
    assert liveness_idx < workers_idx, \
        f"critical 'liveness' must sort BEFORE degraded 'workers': {state.top_issues}"


def test_top_issues_severity_sort_worsening_before_stable():
    """Among degraded dims, worsening must come before stable so
    accelerating problems get the top Telegram slot."""
    state = _run_with_dims(
        workers=("degraded", "stable", "workers stable-degraded"),
        liveness=("degraded", "worsening", "liveness worsening"),
    )
    liveness_idx = next(
        (i for i, issue in enumerate(state.top_issues) if "liveness" in issue), -1,
    )
    workers_idx = next(
        (i for i, issue in enumerate(state.top_issues) if "workers" in issue), -1,
    )
    assert liveness_idx != -1 and workers_idx != -1
    assert liveness_idx < workers_idx, \
        f"worsening 'liveness' must sort BEFORE stable 'workers': {state.top_issues}"


def test_invariant_degraded_overall_always_has_at_least_one_top_issue():
    """AXIS-4 structural preventer for bug class C2: whenever
    overall_status is not 'healthy', top_issues MUST be non-empty.
    Otherwise the CTO Telegram transition message says 'DEGRADED' with
    no explanation — the exact 2026-04-18 regression."""
    # Case 1: critical actionable
    s1 = _run_with_dims(workers=("critical", "stable", "x"),
                        pipeline=("critical", "stable", "y"))
    assert s1.overall_status != "healthy"
    assert len(s1.top_issues) >= 1

    # Case 2: two degraded actionable
    s2 = _run_with_dims(workers=("degraded", "stable", "a"),
                        pipeline=("degraded", "stable", "b"))
    assert s2.overall_status != "healthy"
    assert len(s2.top_issues) >= 1

    # Case 3: degraded+worsening mix
    s3 = _run_with_dims(workers=("degraded", "worsening", "w"))
    # 1 degraded alone doesn't escalate past healthy — but if it did,
    # the invariant still must hold. Test the escalation path:
    s3b = _run_with_dims(workers=("degraded", "worsening", "w"),
                         pipeline=("degraded", "stable", "p"))
    assert s3b.overall_status != "healthy"
    assert len(s3b.top_issues) >= 1

    # Case 4: pure ops critical (alerts=critical)
    s4 = _run_with_dims(alerts=("critical", "stable", "spike"))
    assert s4.overall_status != "healthy"
    assert len(s4.top_issues) >= 1, \
        "ops-only critical must still produce a top_issues line (with (ops) tag)"
