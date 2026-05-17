"""Contract tests — J3-part-1 storefront-ingest admission (2026-05-17).

Locks the invariant that bounds the 10k WRITE-path catastrophic mode:
concurrent expensive ingests are capped ≪ the PgBouncer pool so an
aggregate event storm can NEVER saturate the shared pool and cascade
into the dashboard/workers. Mirrors the proven
test_dashboard_cold_build_admission contract.
"""
from __future__ import annotations

import threading

from app.core import ingest_admission as ia


def test_redis_down_admission_is_BOUNDED_not_open(monkeypatch):
    """Redis down MUST NOT degrade OPEN (an unbounded write path is the
    pool-cascade). ≤ local budget get a token, the rest shed (None) —
    NEVER admit-all."""
    monkeypatch.setattr("app.core.redis_client._client", lambda: None)
    monkeypatch.setattr(ia, "_ingest_local_sem",
                        threading.BoundedSemaphore(3))

    toks = [ia.ingest_admit() for _ in range(10)]
    admitted = [t for t in toks if t is not None]
    assert all(t == "local" for t in admitted)
    assert len(admitted) == 3, (
        f"Redis-down ingest must be BOUNDED to the local budget (3), "
        f"got {len(admitted)} — unbounded-open regression, the write "
        f"pool-cascade is back")
    assert toks.count(None) == 7, "over-budget must shed (None), not open"


def test_redis_down_release_refrees_the_slot(monkeypatch):
    monkeypatch.setattr("app.core.redis_client._client", lambda: None)
    monkeypatch.setattr(ia, "_ingest_local_sem",
                        threading.BoundedSemaphore(1))
    assert ia.ingest_admit() == "local"     # slot taken
    assert ia.ingest_admit() is None         # full → shed
    ia.ingest_release("local")               # free it
    assert ia.ingest_admit() == "local"      # re-acquirable
    # over-release + None tolerated without raising
    ia.ingest_release("local")
    ia.ingest_release("local")               # BoundedSemaphore guard
    ia.ingest_release(None)


def test_redis_up_uses_precise_zset_global_cap(monkeypatch):
    """Redis up → the ZSET is the global cap (NOT the per-worker sem,
    which would wrongly reject a busy worker the global cap has room
    for). Budget 2 → 3rd concurrent admit sheds."""
    store: dict = {}

    class _Z:
        def zremrangebyscore(self, k, lo, hi):
            for m, s in list(store.items()):
                if lo <= s <= hi:
                    del store[m]

        def zcard(self, k):
            return len(store)

        def zadd(self, k, mapping):
            store.update(mapping)

        def zrem(self, k, *members):
            for m in members:
                store.pop(m, None)

        def expire(self, k, ttl):
            pass

    monkeypatch.setattr("app.core.redis_client._client", lambda: _Z())
    monkeypatch.setattr(ia, "_INGEST_BUDGET", 2)
    t1 = ia.ingest_admit()
    t2 = ia.ingest_admit()
    assert t1 and t2 and t1 != "local" and t2 != "local"
    assert ia.ingest_admit() is None          # global cap full → shed
    ia.ingest_release(t1)                      # frees a ZSET slot
    assert ia.ingest_admit() is not None       # now admits again


def test_track_handlers_release_in_finally():
    """Structural lock: both ingest handlers wrap the expensive body in
    try/finally with ingest_release (a `local` semaphore slot does NOT
    auto-heal — an unreleased one is permanently lost)."""
    import ast
    src = open("app/api/track.py").read()
    tree = ast.parse(src)
    for name in ("track_event", "track_event_batch"):
        fn = [n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == name][0]
        tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
        assert any(
            t.finalbody and "ingest_release" in ast.dump(t)
            for t in tries
        ), f"{name} must release the ingest slot in a finally"


def test_budget_is_well_below_pgbouncer_pool():
    """The cap MUST leave pool headroom for dashboard reads + 8 workers
    — consuming the whole pool is the cascade this prevents. PgBouncer
    default_pool_size=80."""
    assert ia._INGEST_BUDGET < 80, (
        "ingest budget must stay well below PgBouncer pool 80 (it is "
        "SHARED with reads + workers)")
    assert ia._INGEST_LOCAL_BUDGET >= 2
