"""Lock the heal-but-stay-open class fix for invariant_regression alerts.

Bug class context
-----------------
Before 2026-05-05, invariant_monitor.run_invariant_check wrote an
ops_alert when an audit failed but did NOT close the prior alert when
the audit subsequently passed. Result: 38 stale unresolved alerts
piled up across 2026-05-02..05-05 even after every audit was green
again. The capillary-scope probe went RED on ops_alerts_volume
(56 unresolved/24h, threshold RED >= 50).

Fix: helper `_auto_resolve_prior_invariant(db, source)` is invoked
in every `if check_passes:` early-return branch across the 9 inline
`_check_*` functions + the subprocess audit "ok" path.

Hermeticity (FINDING 5 hardening 2026-05-06): all seeded alerts use
`source` prefix `invariant:test_` so an `audit_no_test_leakage` check
can prove no test rows survived to prod ops_alerts. The `db` fixture
from conftest wraps each test in a SAVEPOINT that rolls back at
teardown; the marker-prefix is a SECOND line of defense in case the
fixture is ever refactored without the SAVEPOINT.

This test pins the contract.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text as _sql_text

from app.services import invariant_monitor
from app.services.alerting import write_alert


def _count_unresolved(db, source: str) -> int:
    return db.execute(
        _sql_text(
            "SELECT COUNT(*) FROM ops_alerts "
            "WHERE alert_type='invariant_regression' "
            "  AND source=:s AND resolved=false"
        ),
        {"s": source},
    ).scalar() or 0


def test_auto_resolve_clears_only_matching_source(db):
    db_session = db
    src_a = "invariant:test_auto_resolve_a"
    src_b = "invariant:test_auto_resolve_b"
    # Seed two unresolved alerts on src_a + one on src_b
    for _ in range(2):
        write_alert(
            db_session,
            severity="critical",
            source=src_a,
            alert_type="invariant_regression",
            summary="seed alert A",
            detail={"seed": True},
        )
    write_alert(
        db_session,
        severity="critical",
        source=src_b,
        alert_type="invariant_regression",
        summary="seed alert B",
        detail={"seed": True},
    )
    assert _count_unresolved(db_session, src_a) >= 1
    assert _count_unresolved(db_session, src_b) >= 1

    # Resolve only A
    n = invariant_monitor._auto_resolve_prior_invariant(db_session, src_a)
    assert n >= 1
    assert _count_unresolved(db_session, src_a) == 0
    # B must remain untouched
    assert _count_unresolved(db_session, src_b) >= 1


def test_auto_resolve_idempotent_when_no_prior_alert(db):
    db_session = db
    src = "invariant:test_no_prior_alert_xyz"
    n = invariant_monitor._auto_resolve_prior_invariant(db_session, src)
    assert n == 0


def test_auto_resolve_only_touches_invariant_regression_type(db):
    db_session = db
    src = "invariant:test_other_type_isolation"
    # Seed a different alert_type with same source
    write_alert(
        db_session,
        severity="warning",
        source=src,
        alert_type="frontend_error",  # NOT invariant_regression
        summary="non-invariant alert",
        detail={"seed": True},
    )
    other_count_before = db_session.execute(
        _sql_text(
            "SELECT COUNT(*) FROM ops_alerts "
            "WHERE source=:s AND alert_type='frontend_error' AND resolved=false"
        ),
        {"s": src},
    ).scalar()
    assert other_count_before >= 1

    invariant_monitor._auto_resolve_prior_invariant(db_session, src)

    other_count_after = db_session.execute(
        _sql_text(
            "SELECT COUNT(*) FROM ops_alerts "
            "WHERE source=:s AND alert_type='frontend_error' AND resolved=false"
        ),
        {"s": src},
    ).scalar()
    # Helper must NOT touch other alert_types — only invariant_regression
    # + invariant_audit_timeout (see test_auto_resolve_heals_audit_timeout below)
    assert other_count_after == other_count_before


def test_auto_resolve_heals_audit_timeout(db):
    """Lock the 2026-05-06 heal-detection fix: audit-timeout alerts must
    auto-resolve when the audit subsequently exits 0 within budget.

    Bug class context
    -----------------
    invariant_monitor.run_invariant_check writes a CRITICAL ops_alert with
    `alert_type="invariant_audit_timeout"` whenever a registered audit
    exceeds `_TIMEOUT_SECONDS`. Before this fix, `_auto_resolve_prior_
    invariant` only cleared `invariant_regression` alerts on the OK-branch,
    so timeout alerts piled up indefinitely under transient preflight load
    even when the audit ran fine on subsequent ticks. Founder caught the
    accumulation 2026-05-06 (alerts #123347 + #128244 stuck CRITICAL with
    audits running GREEN manually).

    Fix: helper resolves BOTH classes for the same source.
    """
    db_session = db
    src = "invariant:test_audit_timeout_heal"
    # Seed an audit-timeout alert
    write_alert(
        db_session,
        severity="critical",
        source=src,
        alert_type="invariant_audit_timeout",
        summary="seed timeout alert",
        detail={"seed": True, "timeout": 30},
    )
    timeout_before = db_session.execute(
        _sql_text(
            "SELECT COUNT(*) FROM ops_alerts "
            "WHERE source=:s AND alert_type='invariant_audit_timeout' "
            "  AND resolved=false"
        ),
        {"s": src},
    ).scalar()
    assert timeout_before >= 1

    # Helper invocation should resolve the timeout alert too
    n = invariant_monitor._auto_resolve_prior_invariant(db_session, src)
    assert n >= 1

    timeout_after = db_session.execute(
        _sql_text(
            "SELECT COUNT(*) FROM ops_alerts "
            "WHERE source=:s AND alert_type='invariant_audit_timeout' "
            "  AND resolved=false"
        ),
        {"s": src},
    ).scalar()
    assert timeout_after == 0, (
        "invariant_audit_timeout alert was NOT auto-resolved on audit "
        "OK-branch — heal-detection regression."
    )


def test_subprocess_audit_timeout_budget_at_least_60s():
    """Lock the 2026-05-07 timeout bump (30s → 60s).

    Bug class context
    -----------------
    Alert #128658 (invariant_audit_timeout for audit_dead_endpoints.py)
    fired under 4-way parallel contention. Empirical wall-clock with
    ThreadPoolExecutor(max_workers=4) running 4 concurrent
    audit_dead_endpoints subprocesses = ~28.7s steady-state (subprocess
    startup + Python imports × CPU+DB pool pressure), right at the 30s
    edge. Random variance pushed individual runs over 30s → timeout
    alert fired even though the script itself runs <2s sequentially.

    The ceiling MUST stay ≥ 60s. Lowering it back to 30s without an
    architectural change (per-audit override map, subprocess pool with
    cached Python imports, or rewriting hot audits as in-process Python
    calls) reintroduces the timeout-edge regression.
    """
    assert invariant_monitor._TIMEOUT_SECONDS >= 60, (
        f"_TIMEOUT_SECONDS={invariant_monitor._TIMEOUT_SECONDS} reintroduces "
        "the 30s-edge regression that fired alert #128658. If you need a "
        "shorter budget, ship a per-audit override map first."
    )


def test_audit_exception_sinks_runs_with_critical_only():
    """Lock the 2026-05-07 fix for #129082.

    Bug class context
    -----------------
    `audit_exception_sinks.py` has an inverse-contract severity model —
    default mode blocks on INFO findings (bare_pass / catches_base),
    `--critical-only` restricts blocking to CRITICAL kinds
    (write_no_rollback / lying_return). preflight.sh has invoked it
    with `--critical-only` since the 2026-04-24 SINK sweep, accepting
    the 95 INFO baseline. invariant_monitor was running it WITHOUT
    `--critical-only`, so the 95-finding baseline fired
    `invariant_regression` every cycle and accumulated CRITICAL
    ops_alerts (#123346 yesterday, #129082 today).

    The per-audit `_AUDIT_ARGS_OVERRIDE` map MUST keep the
    `--critical-only` flag for this audit. Removing it reintroduces
    the cycle-pollution.
    """
    override = invariant_monitor._AUDIT_ARGS_OVERRIDE.get(
        "audit_exception_sinks.py"
    )
    assert override is not None, (
        "audit_exception_sinks.py missing from _AUDIT_ARGS_OVERRIDE — "
        "default-mode invocation refloods CRITICAL ops_alerts with "
        "the 95-finding INFO baseline."
    )
    assert "--critical-only" in override, (
        f"audit_exception_sinks.py override = {override!r} but must "
        "include --critical-only to match preflight.sh's contract."
    )
