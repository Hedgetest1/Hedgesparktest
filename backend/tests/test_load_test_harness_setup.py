"""Contract tests for load_test_harness.setup_merchants chunking.

Born 2026-05-15 — closes "load test harness setup batching at 10k
merchants" pending item from project_post_2026_05_14_audit_pending.md.

The pre-fix code did a single-transaction INSERT of all N rows. At
N=10000 this tripped PgBouncer at transaction-pool mode (the tx held
the server-side connection too long, queuing other clients). The fix
chunks INSERTs into 1000-row batches with separate commits, and adds
ON CONFLICT (shop_domain) DO NOTHING for idempotency.

Test design — DB isolation:
  The harness uses `app.core.database.SessionLocal` directly so it can
  run against the live backend (the harness IS a load test). For unit
  tests we patch SessionLocal to use the conftest's test-DB engine —
  otherwise these tests would write into the PROD `merchants` table.
  Cleanup is double-bracketed (before + after) so a crashed test or
  CI restart doesn't leak rows.

These tests pin:
  - small-N (5) creates exactly 5 rows
  - chunk-boundary-N (2500) → 3 chunks of 1000+1000+500 → 2500 rows
  - ON CONFLICT DO NOTHING — idempotent re-runs
  - refuse-without-force guard
  - chunk-size constant pinning
  - ON CONFLICT clause source-grep contract
"""
from __future__ import annotations

import os
import sys
from typing import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


_SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"
)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import load_test_harness as lth  # noqa: E402

_TEST_PREFIX = "_loadtest_unittest_"


# Build the test-DB URL the same way conftest does: rewrite the database
# name to wishspark_test. This binds the harness's SessionLocal to the
# isolated test DB instead of PROD wishspark.
def _derive_test_db_url() -> str:
    url = os.environ.get("DATABASE_URL_TEST") or os.environ.get("DATABASE_URL", "")
    if "DATABASE_URL_TEST" not in os.environ:
        import re
        url = re.sub(r"/wishspark(\?|$)", r"/wishspark_test\1", url)
    return url


_TEST_DB_URL = _derive_test_db_url()
_test_engine = create_engine(_TEST_DB_URL, pool_pre_ping=True)
_TestSessionLocal = sessionmaker(bind=_test_engine, autocommit=False, autoflush=False)


@pytest.fixture(autouse=True)
def _override_prefix_and_session(monkeypatch) -> Generator[None, None, None]:
    """Force a distinct prefix per test run AND swap SessionLocal so writes
    land in the test DB, not PROD."""
    monkeypatch.setattr(lth, "_SHOP_PREFIX", _TEST_PREFIX)
    monkeypatch.setattr(lth, "SessionLocal", _TestSessionLocal)
    yield


@pytest.fixture(autouse=True)
def _scrub_test_prefix() -> Generator[None, None, None]:
    """Clean before AND after each test — guarantees clean slate
    regardless of test ordering. Targets the test DB explicitly."""

    def _wipe() -> None:
        db = _TestSessionLocal()
        try:
            db.execute(
                text("DELETE FROM merchants WHERE shop_domain LIKE :p"),
                {"p": f"{_TEST_PREFIX}%"},
            )
            db.commit()
        finally:
            db.close()

    _wipe()
    yield
    _wipe()


def _count_loadtest_merchants() -> int:
    db = _TestSessionLocal()
    try:
        return db.execute(
            text("SELECT COUNT(*) FROM merchants WHERE shop_domain LIKE :p"),
            {"p": f"{_TEST_PREFIX}%"},
        ).scalar() or 0
    finally:
        db.close()


def test_setup_merchants_small_n_creates_exact_count():
    """5 merchants → 5 rows."""
    shops = lth.setup_merchants(5)
    assert len(shops) == 5
    assert _count_loadtest_merchants() == 5


def test_setup_merchants_at_chunk_boundary_creates_all_rows():
    """2500 merchants → 3 chunks (1000+1000+500) → 2500 rows.
    Proves the chunk boundary doesn't drop or duplicate rows."""
    shops = lth.setup_merchants(2500)
    assert len(shops) == 2500
    assert _count_loadtest_merchants() == 2500


def test_setup_merchants_idempotent_on_conflict():
    """Force re-run over existing rows → no duplicates. ON CONFLICT
    DO NOTHING is the contract. The pre-fix code would have raised
    UniqueViolation on the second INSERT."""
    lth.setup_merchants(10)
    assert _count_loadtest_merchants() == 10
    lth.setup_merchants(10, force=True)
    assert _count_loadtest_merchants() == 10


def test_setup_merchants_refuses_without_force_when_existing():
    """Existing _loadtest_* rows + force=False → RuntimeError."""
    lth.setup_merchants(3)
    with pytest.raises(RuntimeError, match="refusing to run"):
        lth.setup_merchants(3, force=False)


def test_setup_merchants_chunk_size_constant_documented():
    """The chunk constant is the load-bearing fix — surface it in
    contract so a future "let me bump to 5000" doesn't silently
    reintroduce the PgBouncer trip."""
    assert lth._INSERT_CHUNK_SIZE == 1000


def test_setup_merchants_chunked_inserts_use_on_conflict_clause():
    """The SQL inside setup_merchants must use ON CONFLICT DO NOTHING
    for idempotency. Verified via source inspection — the
    chunk-loop is the load-bearing structure."""
    import inspect

    source = inspect.getsource(lth.setup_merchants)
    assert "ON CONFLICT (shop_domain) DO NOTHING" in source, (
        "setup_merchants lost the ON CONFLICT clause — idempotency regression"
    )
    assert "_INSERT_CHUNK_SIZE" in source, (
        "setup_merchants lost the chunking loop — back to single-tx INSERT"
    )
