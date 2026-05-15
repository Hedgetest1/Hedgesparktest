"""Contract: every REQUEST DB dependency bounds how long it can hold
a pooled connection (SET LOCAL statement_timeout +
idle_in_transaction_session_timeout), and the lazy proxy applies it
ONLY on first use (cache-hit stays 0-conn, 0-SET).

Born 2026-05-15b. Truth probed: PG statement_timeout=0 everywhere —
an unbounded query starves the shared PgBouncer pool for every other
endpoint (the 284 uncached-handler contention class, = the c≈64
cliff mechanism generalised). Per-request SET LOCAL is the
class-wide structural bound; it MUST stay wired on all 3 request
deps and MUST NOT leak across pooled clients (transaction-scoped).
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import app.core.database as D


def test_apply_issues_both_set_local_statements():
    sess = MagicMock(name="session")
    D._apply_request_timeouts(sess)
    sqls = [str(c.args[0]) for c in sess.execute.call_args_list]
    assert any("statement_timeout" in s for s in sqls), sqls
    assert any("idle_in_transaction_session_timeout" in s for s in sqls), sqls
    # SET LOCAL (transaction-scoped) — NOT bare SET (would leak under
    # PgBouncer transaction pooling).
    assert all(s.strip().upper().startswith("SET LOCAL") for s in sqls), sqls


def test_apply_is_best_effort_never_raises():
    sess = MagicMock(name="session")
    sess.execute.side_effect = RuntimeError("redis/pg blip")
    D._apply_request_timeouts(sess)  # must NOT raise (unbounded is the
    # pre-existing state, not a new regression)


def test_get_db_and_get_read_db_apply_timeouts():
    for depname in ("get_db", "get_read_db"):
        with patch.object(D, "_apply_request_timeouts") as ap:
            gen = getattr(D, depname)()
            db = next(gen)
            ap.assert_called_once_with(db)
            try:
                next(gen)
            except StopIteration:
                pass


def test_lazy_dep_applies_timeout_only_on_first_use():
    # never accessed → ReadSession never built → timeouts never set
    with patch.object(D, "ReadSession") as RS, \
         patch.object(D, "_apply_request_timeouts") as ap:
        gen = D.get_lazy_read_db()
        next(gen)
        try:
            next(gen)
        except StopIteration:
            pass
        RS.assert_not_called()
        ap.assert_not_called()

    # first attribute access → built once → timeout applied once
    fake = MagicMock(name="ReadSession()")
    with patch.object(D, "ReadSession", return_value=fake), \
         patch.object(D, "_apply_request_timeouts") as ap:
        gen = D.get_lazy_read_db()
        holder = next(gen)
        holder.query("X")          # triggers _ensure
        holder.execute("Y")        # second use must NOT re-apply
        ap.assert_called_once_with(fake)
        try:
            next(gen)
        except StopIteration:
            pass


def test_value_is_bounded_below_pool_timeout():
    # pool_timeout=30s is the documented cliff ceiling; the request
    # statement timeout must stay strictly below it.
    assert 0 < D.REQUEST_STMT_TIMEOUT_MS < 30_000
    assert 0 < D.REQUEST_IDLE_TX_TIMEOUT_MS < 30_000
