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
# pool_pre_ping        — DROPPED 2026-05-15b (TIER_2-rigor, founder-approved
#                       lever b). An isolated bench showed pre_ping costs
#                       ~0.1ms (0.9 vs 0.8ms c=1; 12.7 vs 9.8ms c=16) —
#                       negligible — but it adds a per-checkout PgBouncer
#                       round-trip. Liveness is now purely time-based via
#                       a TIGHTENED pool_recycle (below), which is the
#                       correct mechanism behind PgBouncer transaction mode.
# pool_recycle=240     — recycle connections every 4 min. MUST stay below
#                       PgBouncer server_idle_timeout=600s: without
#                       pre_ping, a pooled conn whose PgBouncer-side server
#                       conn was idle-killed at 600s would otherwise be
#                       handed out dead (mid-request 500) if recycle were
#                       still 1800. 240 << 600 closes that window.
#
# With PgBouncer in transaction mode the pool_size here can be reduced to
# 2-3 since PgBouncer owns the real connection pool.
# ---------------------------------------------------------------------------
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "50"))
POOL_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "100"))

# ---------------------------------------------------------------------------
# Per-REQUEST statement / idle-in-txn timeout — bounds the worst-case
# time a single request can hold a pooled connection.
#
# Truth (probed 2026-05-15b): PG `statement_timeout` and
# `idle_in_transaction_session_timeout` are BOTH 0 (unlimited) — no
# bound anywhere (PG / PgBouncer / connect_args). That is the systemic
# root of the 284 uncached-handler "shared-ceiling contention" class:
# ONE pathological query (bad plan / missing index at 10k / lock wait)
# holds a pooled conn indefinitely and starves the shared PgBouncer
# pool for EVERY other endpoint (the c≈64 cliff mechanism, generalised).
#
# This is applied PER REQUEST ONLY — inside get_db / get_read_db /
# get_lazy_read_db — via `SET LOCAL` (transaction-scoped, so it resets
# at txn end → safe under PgBouncer transaction pooling, no leak to
# the next pooled client). It is deliberately NOT on the shared engine
# `connect_args`: the aggregation/agent/SIP/CIG workers bind their own
# SessionLocal to the SAME engine and legitimately run multi-minute
# jobs — a global engine timeout would kill them. Workers never call
# these FastAPI deps, so per-dep scoping is provably request-only
# (verified: aggregation_worker.py builds its own sessionmaker).
#
# Value: 20s. Justification is an INVARIANT, not a fabricated
# benchmark (pg_stat_statements is unavailable; honest about that): a
# request query is not a worker job; `pool_timeout=30` is the
# documented max-wait ceiling; 20s < 30s bounds worst-case pool-hold
# below the cliff; every request path measured this session was ≤ low
# single-digit seconds. A request query > 20s is a pathology that
# SHOULD be killed (clean 500 on ONE request) rather than allowed to
# starve the whole backend. Env-tunable for ops; tradeoff documented,
# not hidden.
# ---------------------------------------------------------------------------
REQUEST_STMT_TIMEOUT_MS = int(os.getenv("DB_REQUEST_STATEMENT_TIMEOUT_MS", "20000"))
REQUEST_IDLE_TX_TIMEOUT_MS = int(os.getenv("DB_REQUEST_IDLE_TX_TIMEOUT_MS", "20000"))


def _apply_request_timeouts(session) -> None:
    """Bound how long a REQUEST may hold a pooled connection. SET LOCAL
    is transaction-scoped → PgBouncer-transaction-safe (resets on txn
    end, no cross-client leak). Best-effort: a failure here must never
    break the request (the unbounded behaviour is the pre-existing
    state, not a regression)."""
    try:
        from sqlalchemy import text as _text
        session.execute(_text(
            f"SET LOCAL statement_timeout = {int(REQUEST_STMT_TIMEOUT_MS)}"
        ))
        session.execute(_text(
            "SET LOCAL idle_in_transaction_session_timeout = "
            f"{int(REQUEST_IDLE_TX_TIMEOUT_MS)}"
        ))
    except Exception as exc:  # pragma: no cover - defensive
        try:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("database.request_timeout_set_failed")
        except Exception:
            pass  # SILENT-EXCEPT-OK: observability best-effort
        log.warning("database: SET LOCAL request timeouts failed: %s", exc)

engine = create_engine(
    DATABASE_URL,
    pool_size=POOL_SIZE,
    max_overflow=POOL_MAX_OVERFLOW,
    pool_timeout=30,
    pool_recycle=240,  # < PgBouncer server_idle_timeout=600 (pre_ping dropped)
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def rollback_quiet(session) -> None:
    """Best-effort un-poison of a SQLAlchemy session after a CAUGHT DB
    error, so the next operation that reuses the SAME session isn't
    rejected with `InFailedSqlTransaction: current transaction is
    aborted` / `PendingRollbackError: Can't reconnect until invalid
    transaction is rolled back`.

    Canonical single-SoT helper for the write_no_rollback class. Born
    2026-05-19 from the Sentry deep-DA: a caught DB error logged
    WITHOUT rollback leaves a shared session poisoned; every
    subsequent query (in the same worker cycle / request) then fails
    spuriously and the real work (or the alert that would flag it) is
    silently lost. Ground truth: invariant_monitor (6 incidents
    2026-05-11) + `revenue_metrics.get_shop_aov` (#239 — §0 revenue
    path). Consumed by invariant_monitor (`_rollback_quiet` re-export),
    revenue_metrics, and the class-wide sweep (sprint memo
    project_db_session_rollback_class_sweep_2026_05_19).

    Never raises: if rollback itself fails the connection is already
    dead and the request-scoped / worker-cycle teardown will discard
    the session anyway — re-raising here would mask the original error
    the caller's handler exists to record."""
    try:
        session.rollback()
    except Exception:
        pass  # SILENT-EXCEPT-OK: rollback-after-poison is best-effort recovery; a failed rollback = dead connection the next SessionLocal cycle / request scope replaces cleanly. Re-raising would mask the original error the caller's handler exists to record.


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
# Routing coverage: 55 files / ~85+ pure-read GET endpoints route via
# get_read_db (verified by audit_read_replica_routing_drift.py — exit 0
# when all pure-read GETs are routed). Coverage was 6.5% before
# 2026-05-04, 41% after the same-day mega-sweep.
#
# Drift preventer: scripts/audit_read_replica_routing_drift.py walks
# every app/api/*.py and flags pure-read GET endpoints still using
# Depends(get_db). Conservative — treats any function calling into
# app.services.* as opaque (may write through service). Opt-out per
# route via `# read-replica: stay-primary — <reason>` annotation
# above @router.get(...).
#
# Whole-file allowlist (admin/ops/auth/billing/webhook surfaces — primary
# is correct even when pure-read): see ALLOWLIST_FILES in the audit.
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
        pool_recycle=240,  # < PgBouncer server_idle_timeout=600 (pre_ping dropped)
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
    _apply_request_timeouts(db)
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Lazy read-session dependency — defers the pooled-connection checkout
# to FIRST USE.
#
# Born 2026-05-15b. FastAPI resolves Depends() BEFORE the handler body,
# so `Depends(get_read_db)` checks a connection out of the pool for the
# WHOLE request even on a cache-first handler whose warm path returns
# with zero DB work. Behind PgBouncer's GLOBAL ceiling (shared by all
# uvicorn workers) that pinned-but-unused conn was the c≈64
# pool-timeout cliff (proven on /dashboard/overview, fixed 8291d0d via
# an inline lazy session). A class-wide audit
# (audit_cachefirst_conn_pin.py) found 6 IDENTICAL siblings; this
# dependency is the reusable class-level fix: a handler that returns a
# cache hit never touches the proxy → no connection is ever checked
# out. Correct-by-construction for any future cache-first handler.
#
# _LazyReadSession proxies attribute access (db.query/execute/add/
# commit/...) to a real ReadSession opened on first access. Dunder
# context-manager use (`with db:`) is delegated explicitly since
# __getattr__ does not intercept dunders — the 6 swept handlers use
# only regular attribute access (audited), the dunder delegation is
# defensive insurance for future call sites.
# ---------------------------------------------------------------------------
class _LazyReadSession:
    __slots__ = ("_real",)

    def __init__(self) -> None:
        self._real = None

    def _ensure(self):
        if self._real is None:
            self._real = ReadSession()
            # Applied on FIRST USE only — a cache-hit handler never
            # calls _ensure, so it stays 0-conn AND skips this SET.
            _apply_request_timeouts(self._real)
        return self._real

    def __getattr__(self, name):
        # __getattr__ only fires for names NOT found normally, so
        # _real / _ensure / close_if_opened never route here.
        return getattr(self._ensure(), name)

    def __enter__(self):
        return self._ensure().__enter__()

    def __exit__(self, exc_type, exc, tb):
        if self._real is not None:
            return self._real.__exit__(exc_type, exc, tb)
        return False

    def close_if_opened(self) -> None:
        if self._real is not None:
            self._real.close()
            self._real = None


def get_lazy_read_db():
    """FastAPI dependency — yields a lazy read session that checks out
    a pooled connection ONLY on first use. A cache-first handler that
    returns a Redis hit without touching `db` holds ZERO DB
    connections for the whole request (the c≈64 cliff fix, class
    form). Same read-only contract as get_read_db."""
    holder = _LazyReadSession()
    try:
        yield holder
    finally:
        holder.close_if_opened()


# ---------------------------------------------------------------------------
# Lazy WRITE-session dependency — the write-path sibling of
# _LazyReadSession (jewel J3 follow-on, 2026-05-17; honest-residual #6).
#
# `/track` is the highest-VOLUME path on the system. Post J3-part-2 the
# dominant traffic (non-purchase analytics) is Redis-only on the hot
# path: known-shop cache hit (track.py:_is_known_shop short-circuits
# before any db.query) → enqueue to the ingest buffer → heatmap →
# return, ZERO DB work. But FastAPI resolves Depends() BEFORE the
# handler body, so the pre-existing `Depends(get_db)` pinned a primary
# PgBouncer connection (and ran 2 SET LOCAL) for the WHOLE request even
# on that buffered path — the same c≈64 conn-pin class the lazy-read
# fix closed for cache-first GETs, here on the busiest write path,
# behind the shared 150-conn ceiling.
#
# Bound to SessionLocal (primary, WRITABLE) — the purchase path
# (revenue/attribution, never buffered, §0) still add/commit/rollback
# normally; the connection opens on its first real DB use
# (_upsert_visitor's query). The buffered path never calls _ensure →
# stays 0-conn AND skips the SET LOCAL, identically to _LazyReadSession.
#
# rollback()/commit() are GUARDED no-ops when no connection was ever
# taken: track_event's write_no_rollback defense does
# `except Exception: db.rollback()`. If the failure was on the
# Redis-only path before any DB use there is NO transaction to roll
# back — routing rollback() through __getattr__ would _ensure() a
# checkout purely to roll back nothing, silently defeating the 0-conn
# property on the error path (and is semantically wrong). The guard
# keeps the error path 0-conn too.
# ---------------------------------------------------------------------------
class _LazyDbSession:
    __slots__ = ("_real",)

    def __init__(self) -> None:
        self._real = None

    def _ensure(self):
        if self._real is None:
            self._real = SessionLocal()
            # FIRST USE only — a buffered (Redis-only) request never
            # calls _ensure, so it stays 0-conn AND skips this SET.
            _apply_request_timeouts(self._real)
        return self._real

    def __getattr__(self, name):
        # __getattr__ only fires for names NOT found normally, so
        # _real / _ensure / rollback / commit / close_if_opened never
        # route here (they are real attrs/methods).
        return getattr(self._ensure(), name)

    def __enter__(self):
        return self._ensure().__enter__()

    def __exit__(self, exc_type, exc, tb):
        if self._real is not None:
            return self._real.__exit__(exc_type, exc, tb)
        return False

    def rollback(self) -> None:
        # No conn ever taken ⟹ no txn ⟹ nothing to roll back. Must NOT
        # _ensure(): the error path before first DB use stays 0-conn.
        if self._real is not None:
            self._real.rollback()

    def commit(self) -> None:
        # Same guard symmetry: a 0-conn request has nothing to commit.
        if self._real is not None:
            self._real.commit()

    def close_if_opened(self) -> None:
        if self._real is not None:
            self._real.close()
            self._real = None


def get_lazy_db():
    """FastAPI dependency — yields a lazy WRITE session that checks out
    a primary pooled connection ONLY on first use. A request whose hot
    path is Redis-only (the buffered non-purchase /track path, the
    highest-volume path on the system) holds ZERO DB connections for
    the whole request. Write-capable: the purchase path commits
    normally once it touches `db`. Write-side sibling of
    get_lazy_read_db (same lazy-checkout contract)."""
    holder = _LazyDbSession()
    try:
        yield holder
    finally:
        holder.close_if_opened()


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
    _apply_request_timeouts(db)
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Runtime N+1 detector (paired with audit_n_plus_one static check).
# Wires the after_cursor_execute event listener once at import time so
# QueryCountMiddleware can read the per-request count.
# Detail: app/core/query_count_monitor.py.
# ---------------------------------------------------------------------------
try:
    from app.core.query_count_monitor import install_listener as _install_query_listener
    _install_query_listener(engine)
    if read_engine is not engine:
        _install_query_listener(read_engine)
except Exception as _exc:
    log.warning("query_count_monitor: install failed (non-fatal): %s", _exc)
