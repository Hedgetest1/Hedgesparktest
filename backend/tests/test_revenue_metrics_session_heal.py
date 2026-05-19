"""Contract test — the §0 revenue-path instance of the poisoned-session
class (born 2026-05-19, Sentry deep-DA).

Ground truth: sentry_incidents #239 = `revenue_metrics: error computing
AOV for shop=_loadtest_00131 ... Can't reconnect until invalid
transaction is rolled back. Please rollback() fully`. `get_shop_aov`
caught the DB error and returned FALLBACK_AOV but did NOT roll back —
so every dashboard caller reusing the SAME shared `db` afterwards
(dashboard.py:905/1362 + ~18 more queries) died with
PendingRollbackError. Graceful-degradation that wasn't graceful.

Fix: a `rollback_quiet(db)` (canonical app.core.database helper) in
every revenue_metrics fallback handler. This pins it.
"""
from __future__ import annotations

import app.services.revenue_metrics as rm
from app.core.database import rollback_quiet


class _PoisonedDB:
    """A session whose first statement fails as if already aborted."""

    def __init__(self):
        self.rollback_calls = 0

    def execute(self, *a, **k):
        raise RuntimeError(
            "(psycopg2.errors.InFailedSqlTransaction) current transaction "
            "is aborted, commands ignored until end of transaction block"
        )

    # get_shop_aov / currency / timezone may touch these on the error
    # path; keep them harmless so the except (not an AttributeError) runs.
    def query(self, *a, **k):
        raise RuntimeError("InFailedSqlTransaction: aborted")

    def rollback(self):
        self.rollback_calls += 1


class TestCanonicalRollbackQuiet:
    def test_calls_rollback_and_swallows_failure(self):
        class _S:
            n = 0

            def rollback(self):
                _S.n += 1
                raise RuntimeError("connection already closed")

        rollback_quiet(_S())  # must not raise even if rollback() raises
        assert _S.n == 1


class TestRevenueMetricsHealsSession:
    """Every fallback handler must heal the shared session so the
    fallback is ACTUALLY graceful for the caller (the #239 contract)."""

    def test_get_shop_aov_returns_fallback_and_heals(self, monkeypatch):
        calls = []
        monkeypatch.setattr(rm, "rollback_quiet", lambda s: calls.append(s))
        db = _PoisonedDB()
        out = rm.get_shop_aov(db, "heal-contract-test.myshopify.com")
        assert out == rm.FALLBACK_AOV
        assert len(calls) == 1, (
            "get_shop_aov MUST rollback_quiet(db) on error — else every "
            "caller reusing the shared session dies (sentry #239)"
        )

    def test_get_shop_currency_heals_on_error(self, monkeypatch):
        calls = []
        monkeypatch.setattr(rm, "rollback_quiet", lambda s: calls.append(s))
        db = _PoisonedDB()
        out = rm.get_shop_currency(db, "heal-contract-test.myshopify.com")
        assert out is None
        # Both the primary-lookup and the order-history fallback handler
        # heal — at least one fired on this all-failing session.
        assert len(calls) >= 1

    def test_get_shop_timezone_heals_on_error(self, monkeypatch):
        calls = []
        monkeypatch.setattr(rm, "rollback_quiet", lambda s: calls.append(s))
        db = _PoisonedDB()
        out = rm.get_shop_timezone(db, "heal-contract-test.myshopify.com")
        assert out == "UTC"
        assert len(calls) >= 1
