"""Deterministic structural-invariant test for the J1 jewel-J2 fix
(aggregation_worker._prewarm_hot_tier, 2026-05-18).

The merchant-facing dashboard cold-cliff at 10k existed because the
dashboard prewarm was the 7th of ~10 heavy per-shop sub-ops in the
HOT-every-cycle loop: a 240s-budget break BEFORE reaching it left HOT
dashboards cold. The fix runs prewarm as a dedicated, budget-PROTECTED
FIRST pass over the HOT tier. These tests pin the invariants that
make that fix correct WITHOUT a (env-blocked) 10k load run:

  1. unbounded budget → EVERY hot shop attempted, in order, count == N.
  2. budget cut-off is honoured deterministically (injected clock):
     stops at exactly K, never starves later because of an early one.
  3. prewarm is best-effort: one shop raising does NOT abort the pass.
  4. a SessionLocal is opened AND closed per attempted shop (no leak).
  5. empty HOT → 0 attempts, no DB session created.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import app.workers.aggregation_worker as aw


def _clock(seq):
    it = iter(seq)
    last = [0.0]

    def _now():
        try:
            last[0] = next(it)
        except StopIteration:
            pass  # hold the last value (loop ended)
        return last[0]
    return _now


def test_unbounded_budget_attempts_every_hot_shop_in_order():
    hot = [f"s{i}.myshopify.com" for i in range(5)]
    calls = []
    sess = MagicMock(name="SessionLocal()")
    with patch("app.api.dashboard.prewarm_lite_dashboard",
               side_effect=lambda db, s: calls.append(s)), \
         patch("app.core.database.SessionLocal", return_value=sess):
        # clock never advances past budget
        n = aw._prewarm_hot_tier(hot, 90, _now=_clock([0.0]))
    assert n == 5
    assert calls == hot                       # every shop, in order
    assert sess.close.call_count == 5         # opened+closed per shop


def test_budget_cutoff_is_deterministic_and_protects_the_first_slice():
    hot = [f"s{i}.myshopify.com" for i in range(10)]
    calls = []
    # start=0; iters 1..3 see t=0 (≤90); iter 4 sees t=999 (>90) → break.
    seq = [0.0, 0.0, 0.0, 0.0, 999.0]
    with patch("app.api.dashboard.prewarm_lite_dashboard",
               side_effect=lambda db, s: calls.append(s)), \
         patch("app.core.database.SessionLocal", return_value=MagicMock()):
        n = aw._prewarm_hot_tier(hot, 90, _now=_clock(seq))
    assert n == 3                              # exactly the pre-cutoff slice
    assert calls == hot[:3]                    # the FIRST 3 (protected)


def test_prewarm_exception_does_not_abort_the_pass():
    hot = ["a.myshopify.com", "b.myshopify.com", "c.myshopify.com"]
    seen = []

    def _pw(db, s):
        seen.append(s)
        if s == "b.myshopify.com":
            raise RuntimeError("prewarm boom")

    with patch("app.api.dashboard.prewarm_lite_dashboard", side_effect=_pw), \
         patch("app.core.database.SessionLocal", return_value=MagicMock()):
        n = aw._prewarm_hot_tier(hot, 90, _now=_clock([0.0]))
    assert n == 3                              # b raised, a+c still done
    assert seen == hot                         # the pass continued past b


def test_empty_hot_does_no_work_no_session():
    with patch("app.core.database.SessionLocal") as SL, \
         patch("app.api.dashboard.prewarm_lite_dashboard") as pw:
        n = aw._prewarm_hot_tier([], 90, _now=_clock([0.0]))
    assert n == 0
    SL.assert_not_called()
    pw.assert_not_called()
