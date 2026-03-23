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
# Connection pool configuration
#
# pool_size=10        — baseline connections kept alive (server normally uses
#                       ~3-5 for dashboard + worker requests)
# max_overflow=20     — burst capacity; total max = pool_size + max_overflow = 30
# pool_timeout=30     — seconds to wait for a free connection before raising
# pool_pre_ping=True  — issues a lightweight SELECT 1 before handing out each
#                       connection; stale/broken connections are dropped and
#                       replaced rather than causing cryptic mid-request failures
# pool_recycle=1800   — recycle connections every 30 minutes; prevents
#                       server-side idle-connection kills from reaching FastAPI
#
# Without PgBouncer these settings protect against:
#   - require_pro_plan() opening a new session per request (bug fixed in deps.py)
#   - aggregation_worker + multiple PM2 instances competing for connections
#   - Postgres default connection limit (typically 100) being breached
#
# With PgBouncer in transaction mode the pool_size here can be reduced to 2-3
# since PgBouncer owns the real connection pool.  See scripts/pgbouncer.ini.
# ---------------------------------------------------------------------------
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_pre_ping=True,
    pool_recycle=1800,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


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
