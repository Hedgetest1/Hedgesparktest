"""
Test fixtures for WishSpark backend integration tests.

Uses a dedicated 'wishspark_test' PostgreSQL database (schema cloned from prod)
to get real SQL dialect behavior (SAVEPOINT, ARRAY types, etc.) without
touching production data.

All test data is created inside transactions that are rolled back after each
test, so the test database stays clean between runs.
"""
from __future__ import annotations

import os
import sys
import time

# Ensure backend is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure backend/scripts is importable — audit scripts now import the
# `_audit_telemetry_shim` shim at module level, and tests load audits
# via `importlib.util.spec_from_file_location`, which does NOT add the
# script's directory to sys.path. Add it once here so every test that
# loads an audit can resolve the shim.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

# Set env vars BEFORE any app imports (modules read env at import time)
os.environ["APP_ENV"] = "test"
os.environ.pop("NOTIFICATIONS_ALLOW_REAL", None)

# Isolate tests from prod Redis state — use Redis DB 15 so the live
# backend running on this host (DB 0 via .env) doesn't pollute test
# state and vice versa. Must be set BEFORE any app import reads
# REDIS_URL at module load.
_prod_redis = os.environ.get("REDIS_URL") or "redis://localhost:6379/0"
if _prod_redis.endswith("/0") or _prod_redis.endswith(":6379"):
    _test_redis = _prod_redis.rstrip("/").rsplit("/", 1)[0] if _prod_redis.endswith("/0") else _prod_redis
    _test_redis = _test_redis.rstrip("/") + "/15"
    os.environ["REDIS_URL"] = _test_redis
os.environ.setdefault("MERCHANT_SESSION_SECRET", "test-session-secret-32chars-long!")
os.environ.setdefault("SHOPIFY_API_SECRET", "test-shopify-secret")
os.environ.setdefault("MERCHANT_TOKEN_ENCRYPTION_KEY", os.urandom(32).hex())
os.environ.setdefault("SHOPIFY_WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("ALLOW_INSECURE_DEV", "false")

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import pytest
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, Session

from app.core.database import Base, get_db, get_read_db
from app.main import app as fastapi_app
from app.models.merchant import Merchant
from app.models.event import Event
from app.models.opportunity_signal import OpportunitySignal
from app.models.product_metrics import ProductMetrics
from app.core.merchant_session import create_session_token, SESSION_COOKIE_NAME


# ---------------------------------------------------------------------------
# Database engine — uses same DATABASE_URL but with isolated test data
# ---------------------------------------------------------------------------

_DATABASE_URL = os.environ.get("DATABASE_URL_TEST") or os.environ.get("DATABASE_URL")
if not _DATABASE_URL:
    raise RuntimeError("DATABASE_URL or DATABASE_URL_TEST must be set for tests")

# Auto-derive test DB URL: replace the database name with wishspark_test
# unless DATABASE_URL_TEST was explicitly set
if not os.environ.get("DATABASE_URL_TEST"):
    # Replace /wishspark at end of URL with /wishspark_test
    import re as _re
    _DATABASE_URL = _re.sub(r"/wishspark(\?|$)", r"/wishspark_test\1", _DATABASE_URL)

_test_engine = create_engine(_DATABASE_URL, pool_pre_ping=True)
_TestSession = sessionmaker(bind=_test_engine, autocommit=False, autoflush=False)

# Wire the runtime N+1 detector listener to the test engine so
# tests/test_query_count_monitor.py can verify the wiring works
# end-to-end. Production engine is wired in app/core/database.py at
# import time; tests use _test_engine so wire it here too.
try:
    from app.core.query_count_monitor import install_listener as _install_qcm
    _install_qcm(_test_engine)
except Exception:
    pass  # SILENT-EXCEPT-OK: test-time listener wiring is best-effort; tests that depend on it will surface the failure via assertion.


# ---------------------------------------------------------------------------
# Session-start safety net — keep wishspark_test at alembic head
# ---------------------------------------------------------------------------
# Background: prior to 2026-04-23, a programmatic `alembic upgrade head`
# against wishspark_test silently ran against PROD because env.py
# unconditionally overrode `sqlalchemy.url` from `DATABASE_URL`. That bug
# was fixed in migrations/env.py (it now respects Config override first),
# but the class of silent divergence deserves a runtime belt + suspenders:
# we auto-upgrade the test DB here before any test collects. A dev who
# pulls a branch with a new migration cannot run a stale test DB.
#
# Intentional failure mode: if the upgrade itself errors, we FAIL the
# whole test session with a clear message rather than letting individual
# tests fail with UndefinedColumn/ProgrammingError noise 30 seconds in.
try:
    from alembic.config import Config as _AlembicConfig
    from alembic import command as _alembic_command

    _alembic_cfg = _AlembicConfig(
        os.path.join(os.path.dirname(__file__), "..", "alembic.ini")
    )
    _alembic_cfg.set_main_option("sqlalchemy.url", _DATABASE_URL)
    _alembic_command.upgrade(_alembic_cfg, "head")
except Exception as _alembic_exc:
    # Surface clearly — stale test DB is a frequent-enough paper-cut that
    # silent continuation would just produce a blizzard of column errors.
    raise RuntimeError(
        f"conftest: failed to upgrade wishspark_test to alembic head: "
        f"{type(_alembic_exc).__name__}: {_alembic_exc}. "
        "Investigate migrations/env.py or run "
        "`DATABASE_URL={_test_url} ./venv/bin/alembic upgrade head` manually."
    ) from _alembic_exc


@pytest.fixture(autouse=True)
def _reset_redis_state():
    """Flush the test Redis DB between tests.

    Many HedgeSpark modules cache or gate state via Redis (model_config,
    llm_budget 429 backoff, orchestrator cooldowns, promotion_pipeline
    cooldowns, telegram rate limits, shopify rate limits, realtime-stream
    snapshots, fleet metrics aggregation). Under tests' SAVEPOINT DB
    isolation these caches get out of sync with rolled-back DB state
    and pollute subsequent tests.

    Tests run against Redis DB 15 (set above in the file header); live
    backend runs on DB 0. This fixture FLUSHDB's DB 15 before each test
    and also clears in-process caches that wouldn't be reset by Redis
    flush alone (module-level dicts that mirror Redis).
    """
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.flushdb()
    except Exception:
        pass
    # Reset the in-process dict caches whose Redis-backed twin was just flushed
    try:
        from app.services.model_config import _cache, _cache_ts
        _cache.clear()
        _cache_ts.clear()
    except Exception:
        pass
    try:
        from app.services.orchestrator import _cooldown_cache
        _cooldown_cache.clear()
    except Exception:
        pass
    try:
        from app.services.promotion_pipeline import _auto_push_cooldown
        _auto_push_cooldown.clear()
    except Exception:
        pass
    try:
        from app.core.llm_budget import _provider_429
        _provider_429.clear()
    except Exception:
        pass
    yield
    # After-test sweep: same Redis flush so next setUp starts clean even if
    # a test added keys but raised before teardown.
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            rc.flushdb()
    except Exception:
        pass


@pytest.fixture()
def db():
    """
    Provide a DB session against the dedicated test database.

    Each test gets a fresh session. After the test, ALL rows created during
    the test are rolled back via an outer transaction that wraps the session.

    For tests using the TestClient (which triggers application-level commits),
    we intercept session.commit() to flush+expire instead of truly committing,
    keeping all data within the outer rollback-able transaction.
    """
    connection = _test_engine.connect()
    transaction = connection.begin()
    session = _TestSession(bind=connection)

    # Intercept commits inside application code — restart the SAVEPOINT
    # instead of committing the outer transaction
    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, transaction_inner):
        nonlocal nested
        if transaction_inner.nested and not transaction_inner.parent.nested:
            nested = connection.begin_nested()

    # Make evolution_engine weakness scoring use the test session
    # so test data is visible to _sort_by_weakness
    import app.services.evolution_engine as _ee
    _prev_override = _ee._weakness_db_override
    _ee._weakness_db_override = session

    yield session

    _ee._weakness_db_override = _prev_override
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db):
    """FastAPI TestClient with the DB session overridden to use the test transaction."""
    from httpx import ASGITransport, AsyncClient

    def _override_get_db():
        yield db

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    # Read replica routes use the same hermetic transactional session in tests
    # so SAVEPOINT isolation holds across both primary and read paths.
    fastapi_app.dependency_overrides[get_read_db] = _override_get_db

    # Use httpx sync client via ASGITransport for TestClient-like behavior.
    # Wrapped to move per-request cookies= onto the client instance,
    # avoiding Starlette DeprecationWarning (httpx per-request cookies).
    from starlette.testclient import TestClient

    class _CookieForwardClient(TestClient):
        """Intercept cookies= kwarg and set on client instance instead."""
        def request(self, *args, **kwargs):
            cookies = kwargs.pop("cookies", None)
            if cookies:
                self.cookies.clear()
                self.cookies.update(cookies)
            return super().request(*args, **kwargs)

    with _CookieForwardClient(fastapi_app, raise_server_exceptions=False) as c:
        yield c

    fastapi_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Merchant fixtures
# ---------------------------------------------------------------------------

SHOP_A = "test-shop-a.myshopify.com"
SHOP_B = "test-shop-b.myshopify.com"


@pytest.fixture()
def merchant_a(db: Session) -> Merchant:
    """Create a test merchant (Shop A) and return the ORM object.

    Plan: 'scale' (top tier) — gives access to every endpoint
    (require_merchant_session, require_pro_session which accepts
    pro+scale, AND require_scale_session). The Pro→Scale migration
    2026-04-29 moved 12 moat endpoints to require_scale_session;
    fixing merchant_a to scale-tier keeps all existing 200-asserting
    tests green without per-test fixture rewrites."""
    m = Merchant(
        shop_domain=SHOP_A,
        plan="scale",
        billing_active=True,
        install_status="active",
        session_version=0,
        contact_email="owner@test-shop-a.com",
    )
    db.add(m)
    db.flush()
    return m


@pytest.fixture()
def merchant_b(db: Session) -> Merchant:
    """Create a second merchant (Shop B) for tenant isolation tests."""
    m = Merchant(
        shop_domain=SHOP_B,
        plan="lite",
        billing_active=False,
        install_status="active",
        session_version=0,
    )
    db.add(m)
    db.flush()
    return m


# EUR merchant fixture for native-currency correctness tests. A separate
# fixture (not just patching merchant_a) so existing tests that assume a
# USD/unset-currency shop keep working.
SHOP_EUR = "test-shop-eur.myshopify.com"


@pytest.fixture()
def merchant_eur(db: Session) -> Merchant:
    """Pro merchant explicitly set to EUR.

    Used by currency-correctness smoke tests to prove that endpoints
    don't silently emit `"USD"` for every shop — they read the shop's
    real primary_currency through get_shop_currency().
    """
    m = Merchant(
        shop_domain=SHOP_EUR,
        plan="scale",
        billing_active=True,
        install_status="active",
        session_version=0,
        contact_email="owner@test-shop-eur.com",
        primary_currency="EUR",
    )
    db.add(m)
    db.flush()
    return m


@pytest.fixture()
def auth_eur(merchant_eur) -> dict:
    """Auth cookies for the EUR merchant."""
    return auth_cookies(SHOP_EUR)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def auth_cookies(shop_domain: str, session_version: int = 0) -> dict:
    """Return a cookie dict with a valid session token for the given shop."""
    token = create_session_token(shop_domain, session_version)
    return {SESSION_COOKIE_NAME: token}


@pytest.fixture()
def auth_a(merchant_a) -> dict:
    """Auth cookies for merchant A (Pro)."""
    return auth_cookies(SHOP_A)


@pytest.fixture()
def auth_b(merchant_b) -> dict:
    """Auth cookies for merchant B (Lite)."""
    return auth_cookies(SHOP_B)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Shared git-subprocess mock for bugfix_apply / patch_tiering tests
# ---------------------------------------------------------------------------
#
# The 2026-04-11 security hardening added three new dirty-tree checks
# to bugfix_pipeline.apply_bugfix_candidate:
#   1. git diff --quiet         (working tree clean?)
#   2. git diff --cached --quiet (index clean?)
#   3. git ls-files --others --exclude-standard  (no untracked?)
#
# The tests that mock subprocess.run need to return a "clean tree"
# response for each of these. This helper builds a mock function
# that returns appropriate stdout/returncode per command and can
# be extended with extra overrides.

from unittest.mock import MagicMock as _MM


def make_git_safe_subprocess_mock(
    *,
    pytest_returncode: int = 0,
    apply_check_returncode: int = 0,
    apply_returncode: int = 0,
    commit_sha: str = "test_sha_deadbeef",
    tree_dirty: bool = False,
    index_dirty: bool = False,
    untracked: str = "",
    extra: dict | None = None,
):
    """
    Return a callable suitable for `patch("subprocess.run", side_effect=...)`.

    Defaults: everything is clean, pytest passes, apply succeeds.
    Flip flags to simulate specific failure modes.
    """
    def _mock(cmd, **kwargs):
        m = _MM(stdout="", stderr="", returncode=0)
        # git diff --quiet  → clean working tree unless tree_dirty
        if len(cmd) >= 2 and cmd[:2] == ["git", "diff"] and "--quiet" in cmd and "--cached" not in cmd:
            m.returncode = 1 if tree_dirty else 0
            return m
        # git diff --cached --quiet  → clean index
        if len(cmd) >= 3 and cmd[:3] == ["git", "diff", "--cached"] and "--quiet" in cmd:
            m.returncode = 1 if index_dirty else 0
            return m
        # git ls-files --others --exclude-standard → untracked listing
        if len(cmd) >= 3 and cmd[:3] == ["git", "ls-files", "--others"]:
            m.stdout = untracked
            return m
        # git diff --cached --name-only → files we staged (for commit sanity)
        if len(cmd) >= 4 and cmd[:4] == ["git", "diff", "--cached", "--name-only"]:
            # Return whatever the test's patch_files implies — empty by default
            m.stdout = ""
            return m
        # git apply --check
        if len(cmd) >= 3 and cmd[:2] == ["git", "apply"] and "--check" in cmd:
            m.returncode = apply_check_returncode
            m.stderr = "check failed" if apply_check_returncode else ""
            return m
        # git apply (real)
        if len(cmd) >= 2 and cmd[:2] == ["git", "apply"] and "--check" not in cmd:
            m.returncode = apply_returncode
            return m
        # git add — always succeed
        if len(cmd) >= 2 and cmd[:2] == ["git", "add"]:
            m.returncode = 0
            return m
        # git commit — always succeed
        if len(cmd) >= 2 and cmd[:2] == ["git", "commit"]:
            m.returncode = 0
            return m
        # git rev-parse HEAD → return our fake sha
        if "rev-parse" in cmd:
            m.stdout = commit_sha
            return m
        # git reset (defensive)
        if len(cmd) >= 2 and cmd[:2] == ["git", "reset"]:
            m.returncode = 0
            return m
        # pytest — simulated
        if "pytest" in " ".join(str(c) for c in cmd) or (cmd and "python" in str(cmd[0])):
            m.returncode = pytest_returncode
            return m
        # Fallback — generic ok
        m.stdout = "ok"
        return m

    if extra:
        # Allow tests to override specific commands
        original = _mock
        def _with_overrides(cmd, **kwargs):
            key = " ".join(str(c) for c in cmd)
            for pat, result in extra.items():
                if pat in key:
                    mm = _MM(stdout=result.get("stdout", ""), stderr=result.get("stderr", ""), returncode=result.get("returncode", 0))
                    return mm
            return original(cmd, **kwargs)
        return _with_overrides

    return _mock
