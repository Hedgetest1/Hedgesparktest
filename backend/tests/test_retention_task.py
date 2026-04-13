"""Sanity tests for the retention task after the Ω⁶ worker split."""
from __future__ import annotations

from sqlalchemy import inspect

from app.core.database import engine
from app.workers.tasks import retention_task


def test_worker_log_uses_started_at_not_created_at():
    """
    Regression guard: the worker_log table uses `started_at` for its
    oldest-first sort, not `created_at`. The original retention SQL
    referenced the wrong column and silently failed for months. If
    this test fails, check app/workers/tasks/retention_task.py.
    """
    cols = {c["name"] for c in inspect(engine).get_columns("worker_log")}
    assert "started_at" in cols, "worker_log must have started_at column"
    # And the retention SQL we now use must reference started_at
    import inspect as _i
    source = _i.getsource(retention_task.run_worker_log_retention)
    assert "started_at" in source
    assert "WHERE created_at" not in source


def test_nudge_events_has_created_at():
    """nudge_events retention correctly uses created_at (the column exists)."""
    cols = {c["name"] for c in inspect(engine).get_columns("nudge_events")}
    assert "created_at" in cols


def test_retention_module_exports_expected_surface():
    """Stability contract for the aggregation_worker re-imports."""
    expected = [
        "cleanup_expired_signals",
        "should_run_event_retention",
        "mark_retention_done",
        "get_distinct_shops",
        "run_event_retention",
        "run_nudge_event_retention",
        "run_worker_log_retention",
    ]
    for name in expected:
        assert hasattr(retention_task, name), f"retention_task missing {name}"
