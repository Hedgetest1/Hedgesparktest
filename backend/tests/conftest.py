"""
Test fixtures for WishSpark backend integration tests.

Uses the real production PostgreSQL database with a dedicated test schema
('test_wishspark') to get real SQL dialect behavior (SAVEPOINT, ARRAY types,
etc.) that SQLite cannot provide.

All test data is created inside transactions that are rolled back after each
test, so the production schema is never polluted.
"""
from __future__ import annotations

import os
import sys
import time

# Ensure backend is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set env vars BEFORE any app imports (modules read env at import time)
os.environ["APP_ENV"] = "test"
os.environ.pop("NOTIFICATIONS_ALLOW_REAL", None)
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

from app.core.database import Base, get_db
from app.main import app as fastapi_app
from app.models.merchant import Merchant
from app.models.event import Event
from app.models.opportunity_signal import OpportunitySignal
from app.models.product_metrics import ProductMetrics
from app.core.merchant_session import create_session_token, SESSION_COOKIE_NAME


# ---------------------------------------------------------------------------
# Database engine — uses same DATABASE_URL but with isolated test data
# ---------------------------------------------------------------------------

_DATABASE_URL = os.environ.get("DATABASE_URL")
if not _DATABASE_URL:
    raise RuntimeError("DATABASE_URL must be set for tests")

_test_engine = create_engine(_DATABASE_URL, pool_pre_ping=True)
_TestSession = sessionmaker(bind=_test_engine, autocommit=False, autoflush=False)


@pytest.fixture()
def db():
    """
    Provide a transactional DB session that rolls back after each test.

    Uses SAVEPOINT so that application code calling commit() inside the
    test doesn't actually persist data — the outer transaction is always
    rolled back.
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
    """Create a test merchant (Shop A) and return the ORM object."""
    m = Merchant(
        shop_domain=SHOP_A,
        plan="pro",
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
        plan="starter",
        billing_active=False,
        install_status="active",
        session_version=0,
    )
    db.add(m)
    db.flush()
    return m


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
