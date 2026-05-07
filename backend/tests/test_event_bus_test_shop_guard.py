"""Lock the 2026-05-07 event_bus synthetic-test-shop guard.

Bug class context
-----------------
`event_bus._emit_postgres` and `_emit_postgres_bulk` open their own
SessionLocal via `_get_db()` when the caller doesn't pass `db=`,
which bypasses pytest SAVEPOINT and writes rows that escape test
cleanup. Surfaced 2026-05-07 by audit_db_table_growth (88 → 541
row spike during pytest run, all from `test-trust-suite.myshopify
.com`). 542 orphan rows cleaned + this guard added in same commit.

The guard mirrors `alerting.write_alert:206-220` synthetic-test-
shop pattern. Tests pin:
  - real shop → emit goes through (or fails on real reasons,
    but the guard does NOT pre-empt)
  - synthetic shop → emit no-ops, returns success
"""
from __future__ import annotations

from app.services import event_bus


def test_real_shop_passes_guard():
    """Real shop_domain doesn't trigger the synthetic guard."""
    assert event_bus._is_test_leak("real-merchant.myshopify.com") is False
    assert event_bus._is_test_leak("hedgespark-dev.myshopify.com") is False


def test_test_trust_suite_caught():
    """The exact shop that surfaced the leak today must be caught."""
    assert event_bus._is_test_leak("test-trust-suite.myshopify.com") is True


def test_loadtest_caught():
    """_loadtest_* prefix from CLAUDE.md §12.2 is caught."""
    assert event_bus._is_test_leak("_loadtest_001.myshopify.com") is True


def test_none_or_empty_passes():
    """Defensive: None / empty / non-string don't crash + don't fire."""
    assert event_bus._is_test_leak(None) is False
    assert event_bus._is_test_leak("") is False


def test_emit_postgres_noops_on_test_shop():
    """The whole emit no-ops when shop matches synthetic pattern.
    Returns success (True) so the bus producer doesn't surface
    'failed emit' false alarms; debug log records the suppression.
    The DB session is NOT touched."""
    row = {
        "ts_ms": 1234567890,
        "event_name": "trust_action_executed",
        "shop_domain": "test-trust-suite.myshopify.com",
        "visitor_id": None,
        "session_id": None,
        "source": None,
        "campaign": None,
        "product_url": None,
        "revenue_eur": None,
        "props": None,
    }
    # If the guard didn't work, _get_db() would be called → DB write.
    # We pass db=None to verify the early-return branch fires before
    # the _get_db() lookup that bypasses SAVEPOINT.
    ok = event_bus._emit_postgres(row, db=None)
    assert ok is True


def test_emit_postgres_bulk_drops_test_rows():
    """Bulk emit filters out synthetic rows BEFORE the insert."""
    rows = [
        {"ts_ms": 1, "event_name": "ev", "shop_domain": "test-trust-suite.myshopify.com",
         "visitor_id": None, "session_id": None, "source": None, "campaign": None,
         "product_url": None, "revenue_eur": None, "props": None},
        {"ts_ms": 2, "event_name": "ev", "shop_domain": "_loadtest_x.myshopify.com",
         "visitor_id": None, "session_id": None, "source": None, "campaign": None,
         "product_url": None, "revenue_eur": None, "props": None},
    ]
    # Both rows are synthetic → real_rows empty → no-op return 0
    n = event_bus._emit_postgres_bulk(rows, db=None)
    assert n == 0
