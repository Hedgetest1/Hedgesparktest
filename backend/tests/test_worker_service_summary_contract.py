"""Contract test: every key consumed by a worker's WorkerLog construction
must exist in the underlying service summary the worker calls.

Born 2026-05-04 evening after a §20 brutal-honesty round caught:
nudge_optimization_worker._record_cycle was reading
`result.get("shops_processed", 0)` etc, but
`nudge_optimizer.run_optimization_cycle` returned `{"evaluated", ...}`
— silent 0s in WorkerLog.shops_processed for the entire history of
that worker.

Per `feedback_bugs_dont_inherit.md` 3-layer doctrine, every fix
needs a runtime preventer. This test pins the contract for the ONE
site where the bug class exists (the only worker reading from a
service summary dict; all others use local counters).

Future regression: removing one of the alias keys from the service
summary will fail this test at preflight time.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from app.services import nudge_optimizer
from app.workers import nudge_optimization_worker as nuw


# Keys that nudge_optimization_worker._record_cycle reads from the
# service summary dict. Sourced from the worker module by inspection
# rather than hardcoded — keeps the test honest if the worker changes.
def _extract_consumed_keys() -> set[str]:
    """Pull `result.get("xxx", ...)` literal keys out of _record_cycle."""
    src = inspect.getsource(nuw._record_cycle)
    import re
    return set(re.findall(r'result\.get\("(\w+)"', src))


def test_nudge_optimizer_summary_provides_all_keys_worker_reads(monkeypatch):
    """Every key worker._record_cycle reads must exist in service summary."""
    consumed = _extract_consumed_keys()

    # Sanity floor: if worker stops reading any keys via .get(), the test
    # is no longer meaningful. Pin the historical floor so the contract
    # surface stays visible.
    assert len(consumed) >= 4, (
        f"worker._record_cycle reads only {len(consumed)} keys via .get() "
        f"— if reduced legitimately, lower this floor explicitly."
    )

    # Build a synthetic empty cycle (no DB rows; service short-circuits)
    # and assert every consumed key appears in the returned summary.
    class FakeQuery:
        def filter(self, *a, **kw): return self
        def all(self): return []
    class FakeDB:
        def query(self, *a, **kw): return FakeQuery()
        def execute(self, *a, **kw):
            class _R:
                def fetchall(self): return []
            return _R()
        def commit(self): pass
        def rollback(self): pass

    summary = asyncio.run(nudge_optimizer.run_optimization_cycle(FakeDB()))

    missing = consumed - set(summary.keys())
    assert not missing, (
        f"nudge_optimizer.run_optimization_cycle missing keys "
        f"consumed by nudge_optimization_worker._record_cycle: {missing}. "
        f"Either add the key to the summary OR update the worker to read "
        f"the actual key names (CLAUDE.md §12.1 / "
        f"feedback_bugs_dont_inherit.md 3-layer)."
    )


def test_known_alias_keys_present():
    """Belt-and-suspenders: directly assert the 4 alias keys exist
    so a regression on the service alone (without changing the worker)
    still fails."""
    class FakeQuery:
        def filter(self, *a, **kw): return self
        def all(self): return []
    class FakeDB:
        def query(self, *a, **kw): return FakeQuery()
        def execute(self, *a, **kw):
            class _R:
                def fetchall(self): return []
            return _R()
        def commit(self): pass
        def rollback(self): pass

    summary = asyncio.run(nudge_optimizer.run_optimization_cycle(FakeDB()))

    for key in (
        "shops_processed",
        "nudges_evaluated",
        "winners_promoted",
        "challengers_generated",
    ):
        assert key in summary, f"alias key {key} missing — worker_log will silently log 0"
