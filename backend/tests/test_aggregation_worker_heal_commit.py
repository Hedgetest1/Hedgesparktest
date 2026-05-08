"""Lock the 2026-05-08 heal-only commit class.

Bug class context
-----------------
`aggregation_worker._run_cycle_inner` invokes
`run_all_spike_detectors`, which in turn runs heal-detection inside
detectors like `detect_slo_breaches` (auto_resolve_alerts on healthy
SLOs) and `detect_sentry_regressions` (auto_resolve when fingerprint
no longer firing). The helpers use SAVEPOINT for isolation so a failed
heal UPDATE doesn't poison the outer transaction — but the OUTER
commit is the caller's responsibility.

Pre-fix: `if total_spikes > 0: db.commit()` skipped the commit when
detectors fired 0 alerts but heal-resolved >0. Pending heal UPDATEs
were dropped by any subsequent step's `db.rollback()` (lighthouse,
rum, prediction_log, ...). Empirical: 3 slo_breach alerts (#132373,
#132374, #132375) accumulated for 16h after p95 returned to
insufficient_data because the worker never committed the heal.

Fix: aggregation_worker.py:411 → unconditional `db.commit()` after
run_all_spike_detectors regardless of fired count.

This test reads the source file and asserts the commit is
unconditional (AST-level, not behavioral). Stronger than a behavior
test because it locks the structural invariant: future refactors
that re-introduce the conditional commit fail this test immediately.
"""
from __future__ import annotations

import ast
from pathlib import Path

WORKER_PATH = Path(__file__).parent.parent / "app" / "workers" / "aggregation_worker.py"


def _find_observability_spikes_block() -> ast.Try:
    """Walk the AST to find the try-block that calls run_all_spike_detectors."""
    tree = ast.parse(WORKER_PATH.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for stmt in node.body:
            # Look for: from app.services.observability_spikes import run_all_spike_detectors
            if isinstance(stmt, ast.ImportFrom) and stmt.module and "observability_spikes" in stmt.module:
                return node
    raise AssertionError("observability_spikes try-block not found in aggregation_worker.py")


def test_observability_spikes_commits_unconditionally():
    """The try-block that runs spike detectors MUST commit unconditionally.

    Pre-fix bug: `if total_spikes > 0: db.commit()` dropped heal-only
    UPDATEs. Fix: commit must be a top-level statement in the try-body,
    NOT wrapped in an If gating on spike count.
    """
    block = _find_observability_spikes_block()
    found_unconditional_commit = False
    for stmt in block.body:
        # Direct: db.commit()
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Attribute)
            and stmt.value.func.attr == "commit"
            and isinstance(stmt.value.func.value, ast.Name)
            and stmt.value.func.value.id == "db"
        ):
            found_unconditional_commit = True
            break
    assert found_unconditional_commit, (
        "aggregation_worker.py: observability_spikes try-block must commit "
        "unconditionally (not gated on `if total_spikes > 0`). Heal-detection "
        "inside detect_slo_breaches/detect_sentry_regressions uses SAVEPOINT; "
        "the outer commit is required to persist heal UPDATEs even when "
        "fired=0. See docstring + 2026-05-08 fix."
    )


def test_no_conditional_commit_around_run_all_spike_detectors():
    """No If-gated db.commit() may live in the same try-block as
    run_all_spike_detectors. Forbids the regression class entirely."""
    block = _find_observability_spikes_block()
    for stmt in block.body:
        if isinstance(stmt, ast.If):
            for sub in ast.walk(stmt):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "commit"
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "db"
                ):
                    raise AssertionError(
                        "aggregation_worker.py: db.commit() found inside an "
                        "If-block in the observability_spikes try. This "
                        "regression re-introduces the 2026-05-08 heal-only "
                        "commit class — heal UPDATEs would be dropped by "
                        "later steps' db.rollback() when fired=0. Move "
                        "db.commit() out of the conditional."
                    )
