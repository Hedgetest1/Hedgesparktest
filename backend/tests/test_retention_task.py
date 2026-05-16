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


# ---------------------------------------------------------------------------
# 10k structural: every retention DELETE must be batched + commit-per-batch.
# Cannot make 100M rows in a test, so we prove the STRUCTURAL MECHANISM
# (bounded iterations, commit after every batch, correct total, circuit
# breaker) deterministically with a fake connection (§19.1 pt 5), plus a
# static source-shape guard mirroring scripts/audit_retention_batched.py.
# ---------------------------------------------------------------------------
import inspect as _inspect


class _FakeResult:
    def __init__(self, rc):
        self.rowcount = rc


class _FakeConn:
    """Records execute/commit interleaving and replays scripted rowcounts."""

    def __init__(self, rowcounts):
        self._rc = list(rowcounts)
        self.calls = []  # ordered log of ("execute", params) / ("commit",)
        self.executes = 0
        self.commits = 0

    def execute(self, _stmt, params=None):
        self.executes += 1
        self.calls.append(("execute", params))
        rc = self._rc.pop(0) if self._rc else 0
        return _FakeResult(rc)

    def commit(self):
        self.commits += 1
        self.calls.append(("commit",))


def test_run_batched_commits_after_every_batch_and_is_bounded():
    """A short batch ends the loop; each batch is committed BEFORE the
    next executes (bounds txn + lock duration to one batch)."""
    bs = retention_task._RETENTION_BATCH_SIZE
    conn = _FakeConn([bs, bs, 3])
    total = retention_task._run_batched(
        conn, "stmt", {"x": 1}, label="t"
    )
    assert total == bs + bs + 3
    assert conn.executes == 3
    assert conn.commits == 3
    # strict interleave: execute, commit, execute, commit, execute, commit
    kinds = [c[0] for c in conn.calls]
    assert kinds == ["execute", "commit"] * 3
    # the LIMIT param is injected (self-limiting sub-select contract)
    assert conn.calls[0][1]["_lim"] == bs and conn.calls[0][1]["x"] == 1


def test_run_batched_circuit_breaker_stops_unbounded_loop(monkeypatch, caplog):
    """A pathological cutoff (every batch full forever) must STOP at the
    iteration cap and resume next cycle — never loop unbounded (§2 r8)."""
    monkeypatch.setattr(retention_task, "_RETENTION_MAX_BATCHES", 4)
    bs = retention_task._RETENTION_BATCH_SIZE
    conn = _FakeConn([bs] * 100)
    with caplog.at_level("WARNING"):
        total = retention_task._run_batched(conn, "stmt", {}, label="events")
    assert conn.executes == 4
    assert conn.commits == 4
    assert total == bs * 4
    assert "circuit breaker" in caplog.text


def test_all_retention_deletes_are_batched_id_scoped():
    """Regression guard: no retention DELETE may be a single unbatched
    table-wide statement. Each must use the id-scoped self-limiting
    sub-select. Mirrors scripts/audit_retention_batched.py."""
    fns = [
        retention_task.cleanup_expired_signals,
        retention_task.run_event_retention,
        retention_task.run_nudge_event_retention,
        retention_task.run_worker_log_retention,
        retention_task.run_sentry_incident_retention,
    ]
    for fn in fns:
        src = _inspect.getsource(fn)
        assert "WHERE id IN (" in src, f"{fn.__name__} not id-scoped batched"
        assert "ORDER BY id LIMIT :_lim" in src, f"{fn.__name__} missing LIMIT"
        assert "_run_batched(" in src, f"{fn.__name__} bypasses batched helper"


def test_event_bus_cleanup_old_events_is_batched():
    """The 5th sibling (analytics_events) must be batched too."""
    from app.services import event_bus

    src = _inspect.getsource(event_bus.cleanup_old_events)
    assert "WHERE id IN (" in src
    assert "ORDER BY id LIMIT :lim" in src
    assert "for _ in range(" in src  # bounded loop / circuit breaker
