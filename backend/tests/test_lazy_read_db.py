"""Contract: get_lazy_read_db checks out a pooled connection ONLY on
first use — zero connections when a cache-first handler returns a
Redis hit without touching db.

Born 2026-05-15b. This is the class-level fix for the c≈64
pool-timeout cliff: FastAPI resolves Depends() before the handler
body, so the pre-existing Depends(get_read_db) pinned a PgBouncer
connection for the whole request even on cache hits. The 6 RED
siblings (audit_cachefirst_conn_pin.py) now use get_lazy_read_db.

These tests pin the invariant that would catch a regression:
  1. holder never accessed → ReadSession NEVER constructed (0 conns).
  2. first attribute access → constructed exactly once, delegated.
  3. teardown → real session closed iff it was opened.
  4. exception mid-use → still closed (no leak).
  5. `with db:` dunder delegated (defensive — not used by the 6 but
     guards future call sites since __getattr__ skips dunders).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import app.core.database as dbmod


def _drive(gen):
    """Run a FastAPI-style yield dependency to completion."""
    holder = next(gen)
    try:
        yield holder
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_never_accessed_opens_zero_connections():
    with patch.object(dbmod, "ReadSession") as RS:
        gen = dbmod.get_lazy_read_db()
        holder = next(gen)
        # handler returns a cache hit WITHOUT touching db:
        try:
            next(gen)  # teardown (finally → close_if_opened)
        except StopIteration:
            pass
        RS.assert_not_called()  # the whole point: 0 pooled conns


def test_first_attribute_access_opens_once_and_delegates():
    fake = MagicMock(name="ReadSession()")
    fake.query.return_value = "RESULT"
    with patch.object(dbmod, "ReadSession", return_value=fake) as RS:
        gen = dbmod.get_lazy_read_db()
        holder = next(gen)
        assert holder.query("X") == "RESULT"      # triggers _ensure
        assert holder.execute("Y") is fake.execute.return_value
        try:
            next(gen)
        except StopIteration:
            pass
        RS.assert_called_once()                    # opened exactly once
        fake.close.assert_called_once()            # closed at teardown


def test_exception_during_use_still_closes():
    fake = MagicMock(name="ReadSession()")
    with patch.object(dbmod, "ReadSession", return_value=fake):
        gen = dbmod.get_lazy_read_db()
        holder = next(gen)
        holder.query("X")           # opened
        try:
            gen.throw(RuntimeError("boom"))
        except RuntimeError:
            pass
        fake.close.assert_called_once()  # finally closed despite raise


def test_with_statement_is_delegated():
    fake = MagicMock(name="ReadSession()")
    fake.__enter__.return_value = "ENTERED"
    with patch.object(dbmod, "ReadSession", return_value=fake):
        holder = dbmod._LazyReadSession()
        with holder as h:
            assert h == "ENTERED"
        fake.__enter__.assert_called_once()
        fake.__exit__.assert_called_once()


def test_close_if_opened_is_noop_when_never_opened():
    with patch.object(dbmod, "ReadSession") as RS:
        holder = dbmod._LazyReadSession()
        holder.close_if_opened()      # must not construct/raise
        RS.assert_not_called()
