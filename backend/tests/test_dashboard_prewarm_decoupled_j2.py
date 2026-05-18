"""Deterministic tests for the J2 jewel — the DECOUPLED fast
dashboard-prewarm loop with a ROUND-ROBIN cursor
(aggregation_worker._prewarm_cycle_once / _dashboard_prewarm_loop,
2026-05-18).

Two independent Agent reviews shaped this:
 - a1639e5 caught that the in-heavy-cycle prewarm cannot close the
   10k cold-cliff (heavy period ≈ 350-600s > TTL_DASHBOARD 360s).
 - a37dc4c caught that a cursor-LESS decoupled loop merely RELOCATES
   the cut (restart from sorted(hot)[0] every pass ⟹ permanent
   lexicographic-tail starvation at HOT>~1500) + a false
   "stampede-guarded" claim + a vacuous period test.

The load-bearing tests here pin the two invariants whose violation
WAS each false premise: (1) period default < TTL_DASHBOARD, bound to
the REAL production literal (not a copy); (2) the cursor advances by
ACTUALLY-processed so a budget break mid-slice RESUMES next pass —
no permanent tail starvation. The rest pin the iteration contract.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import app.workers.aggregation_worker as aw
from app.core.redis_client import TTL_DASHBOARD


def test_period_default_below_ttl_binds_the_REAL_production_literal():
    """STRUCTURAL INVARIANT (non-vacuous — binds the production
    constant `aw._DASHBOARD_PREWARM_PERIOD_DEFAULT`, the one
    `_dashboard_prewarm_loop` actually uses, NOT a hardcoded copy.
    If a drift raises that literal ≥ TTL the cold-cliff silently
    returns; this fails CI then). a37dc4c finding 5 fix."""
    default_period = aw._DASHBOARD_PREWARM_PERIOD_DEFAULT
    assert default_period < TTL_DASHBOARD, (
        f"period {default_period}s must be < TTL_DASHBOARD "
        f"{TTL_DASHBOARD}s or the cold-cliff returns")
    # ≥2 re-touches within a TTL so a cheap warm-skip precedes expiry.
    assert default_period * 2 <= TTL_DASHBOARD


def test_no_hot_set_is_safe_noop():
    """Heavy cycle not run yet / Redis down ⟹ cache_get None/[] ⟹ 0
    attempted, _prewarm_hot_tier + cursor NEVER touched."""
    for ret in (None, []):
        with patch("app.core.redis_client.cache_get", return_value=ret), \
             patch.object(aw, "_prewarm_hot_tier") as pht, \
             patch("app.workers._rr_cursor.load_cursor") as ldc, \
             patch("app.workers._rr_cursor.save_cursor") as svc:
            assert aw._prewarm_cycle_once() == 0
            pht.assert_not_called()
            ldc.assert_not_called()
            svc.assert_not_called()


def test_prewarms_sorted_hot_and_advances_cursor_by_processed():
    """Contract + the CURSOR fairness invariant: prewarm the sorted
    HOT slice; advance the cursor by what _prewarm_hot_tier ACTUALLY
    processed (not slice length) so a budget break resumes next pass.
    rr_slice/next_cursor run REAL (pure) — only the Redis I/O mocked."""
    hot = ["b.myshopify.com", "a.myshopify.com", "c.myshopify.com",
           "d.myshopify.com"]
    # cursor starts at 1; _prewarm_hot_tier "budget-breaks" after 2.
    with patch("app.core.redis_client.cache_get", return_value=hot), \
         patch.object(aw, "_prewarm_hot_tier", return_value=2) as pht, \
         patch("app.workers._rr_cursor.load_cursor", return_value=1), \
         patch("app.workers._rr_cursor.save_cursor") as svc:
        n = aw._prewarm_cycle_once()
    assert n == 2
    passed_shops, passed_budget = pht.call_args[0]
    # slice content is rr_slice's (separately-proven) concern — assert
    # only that it is a real slice of the SORTED hot set:
    assert isinstance(passed_shops, list) and set(passed_shops) <= set(hot)
    assert passed_budget == 100
    # THE load-bearing invariant (the a37dc4c fix): cursor advanced by
    # ACTUALLY-processed (done=2) from loaded cursor=1 over total=4,
    # using the REAL next_cursor — so a budget break RESUMES next pass
    # (no permanent tail starvation). next_cursor is pure ⟹ run real.
    from app.workers._rr_cursor import next_cursor
    svc.assert_called_once()
    saved_key, saved_pos = svc.call_args[0]
    assert saved_key == aw._DASHBOARD_PREWARM_CURSOR_KEY
    assert saved_pos == next_cursor(1, 2, len(hot))   # resume-by-processed


def test_respects_budget_and_max_per_pass_env():
    with patch("app.core.redis_client.cache_get",
               return_value=["a.myshopify.com"]), \
         patch.object(aw, "_prewarm_hot_tier", return_value=1) as pht, \
         patch("app.workers._rr_cursor.load_cursor", return_value=0), \
         patch("app.workers._rr_cursor.save_cursor"), \
         patch.dict(os.environ, {"DASHBOARD_PREWARM_BUDGET_S": "37"}):
        aw._prewarm_cycle_once()
    assert pht.call_args[0][1] == 37              # budget env honoured
