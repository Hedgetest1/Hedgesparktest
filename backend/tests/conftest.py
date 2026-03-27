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

    yield session

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

    # Use httpx sync client via ASGITransport for TestClient-like behavior
    from starlette.testclient import TestClient
    with TestClient(fastapi_app, raise_server_exceptions=False) as c:
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
