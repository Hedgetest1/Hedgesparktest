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
    assert other_count_after == other_count_before
