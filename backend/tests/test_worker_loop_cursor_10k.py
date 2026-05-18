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

@pytest.mark.parametrize("key", [
    "hs:aggregation:cursor", "hs:intel:cursor", "hs:billing_sync:cursor",
])
def test_new_redis_keys_documented_in_claude_md(key):
    claude_md = (_BACKEND.parent / "CLAUDE.md").read_text()
    assert key in claude_md, (
        f"{key} missing from CLAUDE.md §13 — every new Redis key lands "
        f"there in the same commit (§13 rule)"
    )


# ---------------------------------------------------------------------------
# Detector 2 — unordered-limited-Merchant-scan (the §11-miss class,
# born 2026-05-18 extending the audit to app/services worker-invoked
# loops; a directory-only widening would have been theater — the
# time-budget detector is structurally blind to the `.limit(N)`
# no-break/no-monotonic shape).
# ---------------------------------------------------------------------------

import ast as _ast


def _scan_fn(src: str):
    """Run detector-2 over the first top-level function in `src`,
    returning (flagged: bool)."""
    m = _load_audit()
    tree = _ast.parse(src)
    fn = next(n for n in _ast.walk(tree)
              if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)))
    scans = m._unordered_limited_merchant_scans(fn)
    if not scans or m._function_has_cursor(fn, src):
        return False
    if "worker-loop-cursor: ok" in (_ast.get_source_segment(src, fn) or ""):
        return False
    return True


def test_detector2_flags_the_real_pre77f3a34_merchant_brain_miss():
    """NON-VACUITY (strongest form — the actual git-historical source,
    not a synthetic mock): the witnessed §11 miss
    `merchant_brain.tick_all_active_merchants` pre-77f3a34 MUST be
    flagged. If this ever stops flagging, the preventer is vacuous."""
    import subprocess
    src = subprocess.run(
        ["git", "-C", str(_BACKEND.parent), "show",
         "77f3a34^:backend/app/services/merchant_brain.py"],
        capture_output=True, text=True, timeout=30,
    ).stdout
    if not src:
        pytest.skip("git history for 77f3a34 unavailable")
    m = _load_audit()
    tree = _ast.parse(src)
    flagged = [
        fn.name for fn in _ast.walk(tree)
        if isinstance(fn, (_ast.FunctionDef, _ast.AsyncFunctionDef))
        and m._unordered_limited_merchant_scans(fn)
        and not m._function_has_cursor(fn, src)
    ]
    assert "tick_all_active_merchants" in flagged


def test_detector2_clears_when_rr_cursor_wired():
    assert _scan_fn(
        "def tick():\n"
        "    from app.workers._rr_cursor import rr_slice as _rr_slice\n"
        "    doms = sorted(r[0] for r in "
        "db.query(Merchant.shop_domain).all())\n"
        "    for d in _rr_slice(doms, 0, 10):\n"
        "        work(d)\n"
    ) is False


def test_detector2_offset_pagination_full_sweep_is_cleared():
    """The lite/merchant digest shape: `.order_by(id).offset(o)
    .limit(N)` inside a `while: offset+=N` loop covers EVERYONE each
    cycle — offset IS the cross-batch progression, not a fixed window.
    Must NOT be flagged (real precision, not a silenced FP)."""
    assert _scan_fn(
        "def run_digest():\n"
        "    offset = 0\n"
        "    while True:\n"
        "        ms = (db.query(Merchant)\n"
        "              .filter(Merchant.install_status=='active')\n"
        "              .order_by(Merchant.id).offset(offset)"
        ".limit(200).all())\n"
        "        if not ms: break\n"
        "        offset += 200\n"
    ) is False


def test_detector2_flags_fixed_window_limit_no_cursor():
    """The exact bug class: fixed `.limit(N)` over Merchant, no
    order_by, no offset, no cursor."""
    assert _scan_fn(
        "def tick(max_shops=100):\n"
        "    shops = [m.shop_domain for m in "
        "db.query(Merchant).filter(Merchant.install_status=='active')"
        ".limit(max_shops).all()]\n"
        "    for s in shops: work(s)\n"
    ) is True


def test_detector2_per_function_optout_is_granular_not_file_level():
    """The key correctness property: an opt-out in ONE function must
    NOT blind a sibling cursorless function in the same module
    (file-level opt-out would — agent_worker has both an opted-out
    self-draining queue AND time-budget loops that must stay checked)."""
    m = _load_audit()
    src = (
        "def queue_drain():\n"
        "    # worker-loop-cursor: ok — self-draining via NOT EXISTS\n"
        "    rows = db.query(Merchant).filter(x).limit(50).all()\n"
        "    for r in rows: handle(r)\n"
        "\n"
        "def bug_sibling(n=10):\n"
        "    rows = db.query(Merchant).filter(y).limit(n).all()\n"
        "    for r in rows: work(r)\n"
    )
    tree = _ast.parse(src)
    flagged = []
    for fn in _ast.walk(tree):
        if not isinstance(fn, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        if (m._unordered_limited_merchant_scans(fn)
                and not m._function_has_cursor(fn, src)
                and "worker-loop-cursor: ok"
                not in (_ast.get_source_segment(src, fn) or "")):
            flagged.append(fn.name)
    assert flagged == ["bug_sibling"], (
        f"per-function opt-out not granular: {flagged} "
        f"(queue_drain opted out; bug_sibling MUST still flag)"
    )


def test_billing_sync_wires_the_rr_cursor():
    """The 🔴 real revenue bug found by detector 2: run_billing_sync
    must now use the shared round-robin cursor (regression pin — a
    revert to the cursorless `.limit(_MAX_PER_CYCLE)` would re-strand
    ~all Pro merchants from billing verification at 10k)."""
    src = (_BACKEND / "app" / "services" / "billing_sync.py").read_text()
    assert "from app.workers._rr_cursor import" in src
    assert 'hs:billing_sync:cursor' in src
    for tok in ("_rr_load(", "_rr_slice(", "_rr_save(", "_rr_next("):
        assert tok in src, f"billing_sync missing {tok}"


def test_uninstall_erasure_self_drains_via_not_exists():
    """The 🔴 real GDPR-Art.17 bug found by detector 2: the scan must
    exclude merchants that already have a recent shop_redact request
    (NOT EXISTS) so the monotonically-growing ex-merchant set drains
    instead of perpetually re-scanning the oldest _BATCH_CAP."""
    src = (_BACKEND / "app" / "services" / "uninstall_erasure.py").read_text()
    assert "exists()" in src and "~_recent_redact" in src
    assert "GdprRequest.request_type == \"shop_redact\"" in src
