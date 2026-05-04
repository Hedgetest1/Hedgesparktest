"""
query_count_monitor.py — runtime N+1 recognition (paired with the
static audit_n_plus_one preflight check).

Born 2026-05-04 (post-N+1-sweep wave 1-9) per
`feedback_post_fix_pipeline_recognition.md` doctrine: every fix
teaches the pipeline. We closed 9 N+1 candidates statically; this
module catches the next regression at *runtime* before the audit
fires on the next preflight.

Architecture
============
 - Contextvar tracks per-request query count (works for FastAPI async).
 - SQLAlchemy `after_cursor_execute` event listener increments on every
   cursor execute against the wired engine.
 - FastAPI middleware resets count at request start, checks at request
   end:
     * count >= QUERY_COUNT_SOFT_THRESHOLD (default 30) → log.warning
     * count >= QUERY_COUNT_HARD_THRESHOLD (default 100) → log.error
       + Sentry breadcrumb so on-call/founder digest can surface it.
 - Response carries `X-Query-Count` header — useful for ad-hoc local
   tracing without parsing logs.

Worker scope
============
The middleware only covers HTTP requests. For background workers,
N+1 detection is deferred to the static audit (run in preflight) and
to operator inspection of /system/health. Worker-scope query counting
would require per-cycle reset hooks in each worker loop — separate
sub-sprint.

Why thresholds 30 / 100
=======================
 - 30 (soft): a typical Pro dashboard load issues ~5-15 queries
   (auth + tier + ~5-10 widget aggregations). 30 is "something is off".
 - 100 (hard): unambiguous N+1 territory; nothing in our codebase
   *should* legitimately issue 100 queries per request.
 - Both thresholds env-overridable for ops tuning.
"""
from __future__ import annotations

import contextvars
import logging
import os

from sqlalchemy import event
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger("query_count")

# Env-tunable thresholds — both default to project bounds.
_SOFT_THRESHOLD = int(os.getenv("QUERY_COUNT_SOFT_THRESHOLD", "30"))
_HARD_THRESHOLD = int(os.getenv("QUERY_COUNT_HARD_THRESHOLD", "100"))

# Per-request count.
#
# IMPORTANT: we hold a MUTABLE dict in the contextvar, not an int. When
# FastAPI dispatches a sync route handler it runs the body via
# anyio.to_thread.run_sync() which COPIES the current context. Integer
# `.set()` calls inside the worker thread don't propagate back to the
# event-loop's view (the copy is, well, a copy). Holding a dict and
# mutating it works because both copies hold the SAME object reference;
# `state["count"] += 1` in the worker thread mutates the dict the
# middleware then reads. ContextVar still gives task-local isolation
# (each request's middleware creates a fresh dict via reset_count()).
_query_state: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "query_state", default={"count": 0},
)


def reset_count() -> None:
    """Replace the contextvar's dict with a fresh one. Must run at
    request entry so a NEW shared object is in scope for both the
    middleware (event loop) and the worker thread that runs the
    sync route + DB execute."""
    _query_state.set({"count": 0})


def get_count() -> int:
    try:
        return _query_state.get()["count"]
    except LookupError:
        return 0


def install_listener(engine) -> None:
    """Wire the after_cursor_execute listener. Idempotent — calling
    twice on the same engine just registers two listeners (cheap).
    Call once at module import time from database.py."""
    @event.listens_for(engine, "after_cursor_execute")
    def _on_query(conn, cursor, statement, parameters, context, executemany):
        # Defensive: contextvar may not be set in non-request scopes
        # (workers, scripts) — silently skip. In request scopes, mutating
        # the dict in-place propagates back to the event-loop's reader.
        try:
            state = _query_state.get()
            state["count"] = state.get("count", 0) + 1
        except LookupError:
            pass  # SILENT-EXCEPT-OK: contextvar lookup outside a request scope is expected for worker / script queries; runtime counting only meaningful for HTTP request scope.


class QueryCountMiddleware(BaseHTTPMiddleware):
    """Reset count at request start, log/alert at request end if
    count crossed soft / hard threshold. Adds X-Query-Count header
    to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        reset_count()
        try:
            response = await call_next(request)
            n = get_count()
            route = request.url.path

            if n >= _HARD_THRESHOLD:
                log.error(
                    "query_count_hard: route=%s n=%d (threshold=%d) — "
                    "likely N+1 regression; investigate.",
                    route, n, _HARD_THRESHOLD,
                )
                _sentry_breadcrumb(route, n, level="warning",
                                   tag="query_count_hard")
            elif n >= _SOFT_THRESHOLD:
                log.warning(
                    "query_count_soft: route=%s n=%d (threshold=%d)",
                    route, n, _SOFT_THRESHOLD,
                )
                _sentry_breadcrumb(route, n, level="info",
                                   tag="query_count_soft")

            response.headers["X-Query-Count"] = str(n)
            return response
        finally:
            reset_count()


def _sentry_breadcrumb(route: str, n: int, *, level: str, tag: str) -> None:
    """Best-effort Sentry breadcrumb. Sentry may be absent or scope
    inactive — never raises."""
    try:
        import sentry_sdk
        sentry_sdk.add_breadcrumb(
            category="performance",
            level=level,
            message=f"{tag} route={route} n={n}",
            data={"query_count": n, "route": route, "tag": tag},
        )
    except Exception:
        pass  # SILENT-EXCEPT-OK: sentry breadcrumb best-effort observability; never raise from a middleware finally branch.
