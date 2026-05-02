import logging
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set. "
        "Add it to backend/.env and reload PM2 before starting."
    )

# ---------------------------------------------------------------------------
# Managed Postgres / SSL readiness
#
# Managed database providers (DigitalOcean, Supabase, Neon, RDS) require
# SSL connections.  Detection logic:
#
#   1. Explicit env var: DATABASE_SSL=require  → force sslmode=require
#   2. URL already contains sslmode=           → let psycopg2 handle it
#   3. Neither                                 → no extra connect_args (local PG)
#
# This keeps local development unchanged while enabling a one-env-var
# switch when migrating to managed Postgres.
# ---------------------------------------------------------------------------
def _ailab_dsn() -> str:
    """Derive ailab DB connection string from DATABASE_URL, swapping dbname to 'ailab'."""
    from urllib.parse import urlparse, urlunparse
    db_url = os.environ.get("DATABASE_URL", "postgresql://aiuser:aipassword@localhost:5432/wishspark")
    parsed = urlparse(db_url)
    return urlunparse(parsed._replace(path="/ailab"))


_connect_args: dict = {}
_DATABASE_SSL = os.getenv("DATABASE_SSL", "").lower()
if _DATABASE_SSL == "require":
    _connect_args["sslmode"] = "require"
    log.info("database: SSL mode enforced via DATABASE_SSL=require")
elif "sslmode=" in (DATABASE_URL or ""):
    log.info("database: SSL mode detected in DATABASE_URL")
# else: no SSL — local Postgres assumed


# ---------------------------------------------------------------------------
# Connection pool configuration
#
# Sized for the CURRENT runtime (uvicorn --workers 4, per ecosystem.config.js
# + CLAUDE.md §6). DEFAULTS NOW MATCH the documented doctrine, not the legacy
# single-worker numbers. Math at 4 workers:
#
#   backend  = 4 workers × (5 + 10) = 60 conn ceiling
#   PM2      = 7 singleton workers × ~2 conn = 14
#   admin    = psql / pg_stat headroom ~10
#   ─────────────────────────────────────────
#   total    = ~84 conn, vs Postgres max_connections=200 → 116 conn headroom
#
# Drift discovery (2026-05-02): the previous defaults (20 + 40 = 60 per
# worker × 4 = 240) silently exceeded Postgres max_connections=200 and
# produced 20× QueuePool timeout exhaustions in the live error log. The
# defaults were sized for SINGLE-WORKER uvicorn but the runtime flipped
# to multi-worker without scaling them down. Fix: align the code default
# to CLAUDE.md §6 doctrine (5 + 10) so future deployments inherit the
# correct values without env-override gymnastics. audit_db_pool_doctrine
# locks this in.
#
# Env vars still let an operator override (e.g. PgBouncer mode 2-3, or
# higher when intentionally provisioning Postgres for >200 conn).
#
# pool_timeout=30     — seconds to wait for a free connection before raising
# pool_pre_ping=True  — issues a lightweight SELECT 1 before handing out each
#                       connection; stale/broken connections are dropped and
#                       replaced rather than causing cryptic mid-request failures
# pool_recycle=1800   — recycle connections every 30 minutes; prevents
#                       server-side idle-connection kills from reaching FastAPI
#
# With PgBouncer in transaction mode the pool_size here can be reduced to
# 2-3 since PgBouncer owns the real connection pool.
# ---------------------------------------------------------------------------
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
POOL_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))

engine = create_engine(
    DATABASE_URL,
    pool_size=POOL_SIZE,
    max_overflow=POOL_MAX_OVERFLOW,
    pool_timeout=30,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


# ---------------------------------------------------------------------------
# Read-replica support (ε1)
# ---------------------------------------------------------------------------
# When DATABASE_READ_URL is set, analytics-heavy queries can route to a
# Postgres read replica while writes stay on the primary. This unlocks
# dashboard reads from contending with tracker event inserts on the hot
# path, which is the bottleneck past ~50 merchants.
#
# Default: read replica = primary (no-op). When enabled, callers use
# ReadSession() or Depends(get_read_db) for analytics queries.
#
# Call sites to migrate (opt-in, TIER_0 safe):
#   - app/api/roi_hero.py
#   - app/api/cac_ltv.py
#   - app/api/mta.py
#   - app/services/mta_engine.py
#   - app/api/forecasts.py
#   - app/api/compliance_evidence.py
#   - app/services/customer_churn_scorer.py
#   - app/services/nudge_dna.py
#
# Transactional writes (actions, bugfix apply, trust contracts, webhooks,
# OAuth, billing) MUST continue to use the primary via SessionLocal().
# ---------------------------------------------------------------------------
DATABASE_READ_URL = os.getenv("DATABASE_READ_URL")

if DATABASE_READ_URL:
    log.info("database: read replica configured (DATABASE_READ_URL set)")
    _read_connect_args: dict = dict(_connect_args)
    READ_POOL_SIZE = int(os.getenv("DB_READ_POOL_SIZE", "15"))
    READ_POOL_MAX_OVERFLOW = int(os.getenv("DB_READ_MAX_OVERFLOW", "25"))
    read_engine = create_engine(
        DATABASE_READ_URL,
        pool_size=READ_POOL_SIZE,
        max_overflow=READ_POOL_MAX_OVERFLOW,
        pool_timeout=30,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=_read_connect_args,
    )
    ReadSession = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=read_engine,
    )
else:
    # No replica configured — fall through to primary.
    read_engine = engine
    ReadSession = SessionLocal


def get_read_db():
    """
    FastAPI dependency — yields a read-optimized session.

    Routes to DATABASE_READ_URL when set; otherwise falls back to the
    primary. Safe to use for analytics queries; DO NOT use for writes
    (changes on a replica will error with a read-only cursor or,
    worse, silently replicate back).
    """
    db = ReadSession()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Request-scoped session dependency
#
# Use this in FastAPI routes via Depends(get_db).  The session is guaranteed
# to be closed (and returned to the pool) after the response is sent,
# regardless of whether the handler raised an exception.
#
# All Pro-plan route handlers must use this via deps.require_pro_plan() which
# now accepts db: Session = Depends(get_db) to eliminate the per-request
# SessionLocal() anti-pattern that was present in the initial implementation.
# ---------------------------------------------------------------------------
def get_db():
    """
    FastAPI dependency — yields a request-scoped SQLAlchemy session.

    Usage in route:
        db: Session = Depends(get_db)

    Usage in dependency chain:
        def require_pro_plan(..., db: Session = Depends(get_db)) -> str: ...

    The session is closed (connection returned to pool) after the response
    is finalized, even on exceptions.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
