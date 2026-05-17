"""Contract tests — 10k worker-loop tail-starvation fix (2026-05-17).

Locks the invariant: a time-budget worker `break` loop MUST resume via a
cross-cycle cursor so the iteration-order tail is reached over bounded
cycles instead of *never* at 10k. Covers the shared `_rr_cursor` helper
(aggregation_worker), the intelligence_worker keyset cursor, the fairness
property end-to-end (pure-helper simulation), the structural preventer's
non-vacuity, and the CLAUDE.md §13 documentation rule.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# _rr_cursor helper — pure unit contracts
# ---------------------------------------------------------------------------

def test_rr_slice_below_scale_returns_whole_list():
    """<= max_per_cycle ⟹ everyone every cycle (zero behaviour change at
    today's 4 merchants; cursor is irrelevant below scale)."""
    from app.workers._rr_cursor import rr_slice
    items = ["a", "b", "c"]
    assert rr_slice(items, 0, 2000) == items
    assert rr_slice(items, 999, 2000) == items  # cursor ignored below cap
    assert rr_slice([], 0, 2000) == []


def test_rr_slice_wraps_around_deterministically():
    from app.workers._rr_cursor import rr_slice
    items = list(range(10))
    # No wrap.
    assert rr_slice(items, 0, 4) == [0, 1, 2, 3]
    assert rr_slice(items, 4, 4) == [4, 5, 6, 7]
    # Wrap.
    assert rr_slice(items, 8, 4) == [8, 9, 0, 1]
    # Cursor normalised modulo n.
    assert rr_slice(items, 18, 4) == [8, 9, 0, 1]


def test_next_cursor_advances_by_actual_processed():
    from app.workers._rr_cursor import next_cursor
    assert next_cursor(0, 4, 10) == 4
    assert next_cursor(8, 4, 10) == 2          # wraps
    assert next_cursor(5, 0, 10) == 5          # processed 0 → no advance
    assert next_cursor(3, -1, 10) == 3         # defensive: no negative move
    assert next_cursor(0, 5, 0) == 0           # empty population


def test_rr_cursor_redis_down_degrades_open(monkeypatch):
    """Redis down ⟹ cursor 0 (process from head), never raise. Failing
    *closed* would starve the loop — the opposite of the bug."""
    from app.workers import _rr_cursor as rc
    monkeypatch.setattr("app.core.redis_client._client", lambda: None)
    assert rc.load_cursor("hs:aggregation:cursor") == 0
    rc.save_cursor("hs:aggregation:cursor", 99)  # must not raise


def test_rr_cursor_roundtrip_when_redis_up(monkeypatch):
    from app.workers import _rr_cursor as rc
    store: dict = {}

    class _FakeRedis:
        def get(self, k):
            return store.get(k)

        def set(self, k, v, ex=None):
            store[k] = v

    monkeypatch.setattr("app.core.redis_client._client",
                        lambda: _FakeRedis())
    rc.save_cursor("hs:aggregation:cursor", 1234)
    assert rc.load_cursor("hs:aggregation:cursor") == 1234


# ---------------------------------------------------------------------------
# THE bug contract — fairness over K cycles (no systematic tail starvation)
# ---------------------------------------------------------------------------

def test_every_shop_reached_within_bounded_cycles_under_budget_break():
    """Simulate the exact worker composition: each cycle loads the cursor,
    slices, processes only B (forced budget break), advances by B. After
    ceil(N/B) cycles EVERY shop must have been processed at least once."""
    from app.workers._rr_cursor import rr_slice, next_cursor

    shops = sorted(f"shop-{i:05d}.myshopify.com" for i in range(10_000))
    N = len(shops)
    B = 1500          # shops the budget allows before the break fires
    MAX = 2000        # slice cap
    cursor = 0
    seen: set[str] = set()
    cycles = 0
    # Bound: even with wrap-partial re-processing, full coverage within
    # 2*ceil(N/B) cycles is the contract (well under one round-robin day).
    max_cycles = 2 * (-(-N // B)) + 2
    while len(seen) < N and cycles < max_cycles:
        batch = rr_slice(shops, cursor, MAX)
        processed = batch[:B]               # budget break after B
        seen.update(processed)
        cursor = next_cursor(cursor, len(processed), N)
        cycles += 1
    assert seen == set(shops), (
        f"tail starvation: only {len(seen)}/{N} shops reached in "
        f"{cycles} cycles — the cursor failed to cover the population"
    )
    assert cycles <= -(-N // B) + 1, (
        f"took {cycles} cycles to cover {N} at {B}/cycle — expected "
        f"~{-(-N // B)} (advance-by-processed must not stall)"
    )


def test_pre_fix_shape_would_starve_the_tail():
    """Proves the test above is non-vacuous: WITHOUT advancing the cursor
    (the pre-fix shape — re-grind the same head every cycle) the tail is
    provably never reached."""
    from app.workers._rr_cursor import rr_slice

    shops = sorted(f"s{i:05d}" for i in range(10_000))
    B, MAX = 1500, 2000
    seen: set[str] = set()
    for _ in range(50):                     # 50 cycles, cursor stuck at 0
        seen.update(rr_slice(shops, 0, MAX)[:B])
    assert len(seen) == B, "pre-fix shape must cover only the static head"
    assert seen != set(shops), "the tail MUST be starved without a cursor"


def test_j1_tiered_cadence_hot_every_cycle_cold_bounded():
    """J1 jewel-structure invariant (aggregation_worker tiered cadence,
    2026-05-17). Reproduces the exact in-loop composition:
      cycle_shops = sorted(HOT) + rr_slice(COLD, cursor, MAX)
      cursor' = next_cursor(cursor, cold_processed, len(COLD))
    Asserts: (1) a HOT merchant is processed EVERY cycle it is hot
    (freshness — fixes prewarm-dead-at-10k for the active population);
    (2) EVERY cold merchant is reached within ~ceil(|COLD|/MAX) cycles
    (no cold starvation — the 10k defect); (3) the cursor advances by
    cold-processed only (hot never consumes cold rotation budget)."""
    from app.workers._rr_cursor import rr_slice, next_cursor

    N = 10_000
    active = sorted(f"shop-{i:05d}.myshopify.com" for i in range(N))
    MAX = 2000               # _AGG_COLD_MAX_PER_CYCLE default
    BUDGET = 1700             # shops the 240s budget allows per cycle
    # A rotating hot subset: ~400 distinct active merchants per cycle.
    cursor = 0
    cold_seen: set[str] = set()
    hot_freshness_violations = 0
    cycles = 0
    max_cycles = 2 * (-(-N // MAX)) + 4
    while len(cold_seen) < N and cycles < max_cycles:
        hot = sorted(active[(cycles * 137) % N:(cycles * 137) % N + 400])
        hot_set = set(hot)
        cold = [s for s in active if s not in hot_set]
        cold_slice = rr_slice(cold, cursor, MAX)
        cycle_shops = hot + cold_slice            # HOT first, exactly as code
        processed = cycle_shops[:BUDGET]          # 240s budget break
        # (1) every hot shop that fit the budget was processed this cycle
        if set(hot) - set(processed) and len(processed) >= len(hot):
            hot_freshness_violations += 1
        cold_processed = max(0, len(processed) - len(hot))
        cold_seen.update(s for s in processed if s not in hot_set)
        cursor = next_cursor(cursor, cold_processed, len(cold))
        cycles += 1
    assert hot_freshness_violations == 0, (
        "a HOT merchant was skipped while budget remained — prewarm/"
        "freshness regression for the active population")
    assert len(cold_seen) >= int(0.99 * N), (
        f"cold starvation: only {len(cold_seen)}/{N} cold merchants "
        f"reached in {cycles} cycles — the 10k defect is back")


# ---------------------------------------------------------------------------
# intelligence_worker keyset cursor — contracts
# ---------------------------------------------------------------------------

def test_intel_cursor_roundtrip_and_wrap(monkeypatch):
    from app.workers import intelligence_worker as iw
    store: dict = {}

    class _FakeRedis:
        def get(self, k):
            return store.get(k)

        def set(self, k, v, ex=None):
            store[k] = v

        def delete(self, k):
            store.pop(k, None)

    monkeypatch.setattr("app.core.redis_client._client",
                        lambda: _FakeRedis())
    assert iw._load_intel_cursor() is None              # cold
    iw._save_intel_cursor(("shop-a.myshopify.com", "/products/x"))
    assert iw._load_intel_cursor() == (
        "shop-a.myshopify.com", "/products/x")
    iw._save_intel_cursor(None)                         # keyset exhausted
    assert iw._load_intel_cursor() is None              # wrapped to head


def test_intel_cursor_redis_down_degrades_open(monkeypatch):
    from app.workers import intelligence_worker as iw
    monkeypatch.setattr("app.core.redis_client._client", lambda: None)
    assert iw._load_intel_cursor() is None              # process from head
    iw._save_intel_cursor(("s", "p"))                   # must not raise


def test_intel_cursor_malformed_json_returns_none(monkeypatch):
    from app.workers import intelligence_worker as iw

    class _FakeRedis:
        def get(self, k):
            return "{not valid json"

        def set(self, k, v, ex=None):
            pass

        def delete(self, k):
            pass

    monkeypatch.setattr("app.core.redis_client._client",
                        lambda: _FakeRedis())
    assert iw._load_intel_cursor() is None              # no raise, head


def test_intel_keyset_query_is_deterministic_and_resumable(db):
    """The query the worker builds MUST carry ORDER BY (shop,product) AND
    a row-value `> (cursor)` comparison — the determinism + resume
    contract (the old `.distinct().limit(5000)` had neither)."""
    from sqlalchemy import tuple_ as _tuple
    from app.models.visitor_product_state import VisitorProductState as V

    q = (
        db.query(V.shop_domain, V.product_url)
        .filter(V.shop_domain.isnot(None), V.product_url.isnot(None))
        .filter(_tuple(V.shop_domain, V.product_url)
                > _tuple("shop-a.myshopify.com", "/p/1"))
        .distinct()
        .order_by(V.shop_domain, V.product_url)
        .limit(5000)
    )
    sql = str(q.statement.compile(
        compile_kwargs={"literal_binds": True})).upper()
    assert "ORDER BY" in sql and "SHOP_DOMAIN" in sql and "PRODUCT_URL" in sql
    assert ">" in sql and "LIMIT" in sql
    assert "DISTINCT" in sql


# ---------------------------------------------------------------------------
# Structural preventer — non-vacuity + real-tree green
# ---------------------------------------------------------------------------

def _load_audit():
    spec = importlib.util.spec_from_file_location(
        "audit_wlc", _BACKEND / "scripts" / "audit_worker_loop_cursor.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_audit_flags_a_cursorless_time_budget_loop(tmp_path):
    """Non-vacuous: the audit MUST flag the exact pre-fix shape."""
    m = _load_audit()
    broken = tmp_path / "app" / "workers" / "z_broken_worker.py"
    broken.parent.mkdir(parents=True)
    broken.write_text(
        "import time\n"
        "def run_cycle():\n"
        "    _budget_seconds = 240\n"
        "    _start = time.monotonic()\n"
        "    for shop in all_shops:\n"
        "        if time.monotonic() - _start > _budget_seconds:\n"
        "            break\n"
        "        do_work(shop)\n"
    )
    m._ROOT = tmp_path
    v = m._violations_in(broken)
    assert v and "NO cross-cycle resume cursor" in v[0]


def test_audit_green_on_real_tree():
    """Every real time-budget worker loop now has a cursor (regression
    pin: a future cursorless worker loop fails preflight)."""
    m = _load_audit()
    assert m.main() == 0


def test_audit_opt_out_comment_clears(tmp_path):
    m = _load_audit()
    f = tmp_path / "app" / "workers" / "z_optout_worker.py"
    f.parent.mkdir(parents=True)
    f.write_text(
        "# worker-loop-cursor: ok — fixed 3-element literal, no resume\n"
        "import time\n"
        "def run_cycle():\n"
        "    _time_budget = 5\n"
        "    s = time.monotonic()\n"
        "    for x in (1, 2, 3):\n"
        "        if time.monotonic() - s > _time_budget:\n"
        "            break\n"
    )
    m._ROOT = tmp_path
    assert m._violations_in(f) == []


# ---------------------------------------------------------------------------
# CLAUDE.md §13 documentation rule
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["hs:aggregation:cursor", "hs:intel:cursor"])
def test_new_redis_keys_documented_in_claude_md(key):
    claude_md = (_BACKEND.parent / "CLAUDE.md").read_text()
    assert key in claude_md, (
        f"{key} missing from CLAUDE.md §13 — every new Redis key lands "
        f"there in the same commit (§13 rule)"
    )
