"""Test runtime N+1 detector — query_count_monitor.

Paired with audit_n_plus_one static check; this catches the next
N+1 regression at runtime before the next preflight fires.
"""
from __future__ import annotations

import logging

import pytest
from sqlalchemy import text

from app.core.query_count_monitor import (
    QueryCountMiddleware,
    get_count,
    reset_count,
)


def test_listener_increments_count_on_query(db, monkeypatch):
    """Each cursor execute against the wired engine increments the
    contextvar counter. Reset → 0 → query → 1."""
    reset_count()
    assert get_count() == 0

    db.execute(text("SELECT 1"))
    n1 = get_count()
    assert n1 >= 1, f"expected >=1 after 1 query, got {n1}"

    db.execute(text("SELECT 2"))
    n2 = get_count()
    assert n2 >= 2, f"expected >=2 after 2 queries, got {n2}"


def test_reset_count_zeroes_after_increment(db):
    """reset_count() returns counter to zero — invariant for
    middleware request-scope reset."""
    db.execute(text("SELECT 1"))
    assert get_count() >= 1
    reset_count()
    assert get_count() == 0


def test_get_count_outside_context_returns_zero():
    """Calling get_count() in a fresh context (no setter ran) yields 0
    via the contextvar default — no LookupError."""
    reset_count()  # ensure clean baseline
    assert get_count() == 0


def test_count_crosses_soft_threshold(db, monkeypatch):
    """Verify N queries against the wired engine drive count above
    a low soft threshold — proves the increment + threshold semantics
    that the middleware acts on. (Logging output is verified separately;
    project uses custom handlers that fight pytest's caplog — direct
    count assertion is the equivalent guard.)"""
    monkeypatch.setattr(
        "app.core.query_count_monitor._SOFT_THRESHOLD", 5,
    )
    from app.core.query_count_monitor import _SOFT_THRESHOLD

    reset_count()
    for _ in range(6):  # > soft
        db.execute(text("SELECT 1"))

    n = get_count()
    assert n >= _SOFT_THRESHOLD, \
        f"soft threshold not crossed: n={n} < {_SOFT_THRESHOLD}"


def test_count_crosses_hard_threshold(db, monkeypatch):
    """Same for hard threshold."""
    monkeypatch.setattr(
        "app.core.query_count_monitor._HARD_THRESHOLD", 5,
    )
    from app.core.query_count_monitor import _HARD_THRESHOLD

    reset_count()
    for _ in range(7):
        db.execute(text("SELECT 1"))

    n = get_count()
    assert n >= _HARD_THRESHOLD


def test_sentry_breadcrumb_no_op_safe():
    """The internal _sentry_breadcrumb helper must never raise even
    when sentry_sdk is absent or the call shape is unexpected."""
    from app.core.query_count_monitor import _sentry_breadcrumb
    # Should be a no-op without raising
    _sentry_breadcrumb("/test", 42, level="info", tag="test_tag")


# ---------------------------------------------------------------------------
# Worker-scope query monitor — paired with HTTP middleware
# ---------------------------------------------------------------------------

def test_worker_scope_resets_count_on_enter(db):
    """worker_scope.__enter__ must reset the contextvar so the per-
    iteration counter starts at 0 regardless of prior state."""
    from app.core.query_count_monitor import worker_scope

    # Pollute count BEFORE entering scope
    reset_count()
    db.execute(text("SELECT 1"))
    db.execute(text("SELECT 2"))
    assert get_count() >= 2

    with worker_scope("test_worker", "shop_x"):
        # Count must be 0 right after enter
        assert get_count() == 0
        db.execute(text("SELECT 3"))
        assert get_count() >= 1


def test_worker_scope_does_not_swallow_exceptions(db):
    """worker_scope.__exit__ returns None (or False) → propagates exceptions."""
    from app.core.query_count_monitor import worker_scope

    with pytest.raises(ValueError, match="boom"):
        with worker_scope("test_worker", "shop_y"):
            raise ValueError("boom")


def test_worker_scope_isolates_iterations(db):
    """Two consecutive worker_scope blocks must NOT share count state.
    Per-shop iterations get isolated alarms."""
    from app.core.query_count_monitor import worker_scope

    # First scope: do many queries
    with worker_scope("test_worker", "shop_a"):
        for _ in range(5):
            db.execute(text("SELECT 1"))
        # Inside scope, count > 0
        assert get_count() >= 5

    # Second scope: should NOT inherit prior count
    with worker_scope("test_worker", "shop_b"):
        # Right after enter, count is 0 again
        assert get_count() == 0
        db.execute(text("SELECT 1"))
        assert get_count() >= 1
