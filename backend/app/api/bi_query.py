"""bi_query.py — Pro #3 BI Query Builder API.

Endpoints (all Pro-gated):

  GET  /pro/bi/schema           — closed table/column metadata for the builder UI
  POST /pro/bi/query            — execute a structured QueryRequest
  GET  /pro/bi/saved-queries    — list this shop's saved queries
  POST /pro/bi/saved-queries    — save (upsert by name)
  DELETE /pro/bi/saved-queries/{id}

Safety layers applied in order:
  1. Pro session resolution via require_pro_session (auth shop_domain
     comes from the cookie, NEVER from the request body)
  2. Per-merchant rate limit (30 queries / 60s) via Redis counter
  3. Pydantic schema validation
  4. bi_query_builder.compile_query — full whitelist validation
  5. statement_timeout 10s + LIMIT 5000 (set in compile + execute)
  6. Audit log row per query (every execution, including failures)
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api._types import OkResponse
from app.core.database import get_db
from app.core.deps import require_pro_session


log = logging.getLogger(__name__)
router = APIRouter(tags=["bi_query"])


_RATE_LIMIT_PER_60S = 30
_RATE_LIMIT_KEY_PREFIX = "hs:bi_query:rate"


# ---------------------------------------------------------------------------
# Pydantic shapes — top-level structure only; deeper validation happens in
# bi_query_builder.compile_query against the closed allowlist.
# ---------------------------------------------------------------------------


class SchemaColumn(BaseModel):
    name: str
    label: str
    type: str


class SchemaTable(BaseModel):
    name: str
    label: str
    description: str
    columns: list[SchemaColumn]


class SchemaLimits(BaseModel):
    max_columns: int
    max_filters: int
    max_group_by: int
    max_order_by: int
    max_rows: int
    default_limit: int


class BiSchemaResponse(BaseModel):
    tables: list[SchemaTable]
    operators: list[str]
    aggregations: list[str]
    limits: SchemaLimits


class FilterItem(BaseModel):
    column: str = Field(..., max_length=64)
    op: str = Field(..., max_length=16)
    value: Any | None = None


class SelectItem(BaseModel):
    column: str | None = Field(None, max_length=64)
    agg: str | None = Field(None, max_length=16)
    alias: str | None = Field(None, max_length=64)


class OrderByItem(BaseModel):
    column: str = Field(..., max_length=64)
    direction: str = Field("ASC", max_length=4)


class QueryRequest(BaseModel):
    table: str = Field(..., max_length=64)
    select: list[SelectItem] = Field(..., max_length=10)
    where: list[FilterItem] = Field(default_factory=list, max_length=10)
    group_by: list[str] = Field(default_factory=list, max_length=5)
    having: list[FilterItem] = Field(default_factory=list, max_length=10)
    order_by: list[OrderByItem] = Field(default_factory=list, max_length=3)
    limit: int = Field(100, ge=1, le=5000)


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    duration_ms: int
    truncated: bool


class SavedQueryItem(BaseModel):
    id: int
    name: str
    query_json: dict
    created_at: str
    updated_at: str


class SavedQueriesResponse(BaseModel):
    shop_domain: str
    queries: list[SavedQueryItem] = Field(default_factory=list)


class SaveQueryPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    query: QueryRequest


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------


def _rate_limit_key(shop: str) -> str:
    digest = hashlib.md5(shop.encode()).hexdigest()[:16]
    return f"{_RATE_LIMIT_KEY_PREFIX}:{digest}"


def _check_rate_limit(shop: str) -> tuple[bool, int]:
    """Return (allowed, remaining). When Redis is down, fail open
    (allowed=True) per silent_fallback contract — bi_query is read-
    only, so DoS via Redis-down is a smaller risk than blocking a
    paying merchant during a Redis incident."""
    try:
        from app.core.redis_client import _client
        from app.core.silent_fallback import record_silent_return
        rc = _client()
        if rc is None:
            record_silent_return("bi_query.rate_limit_redis_down")
            return True, _RATE_LIMIT_PER_60S
        key = _rate_limit_key(shop)
        # INCR + EXPIRE if first hit. Atomic via pipeline.
        pipe = rc.pipeline()
        pipe.incr(key)
        pipe.expire(key, 60)
        n, _ = pipe.execute()
        if n > _RATE_LIMIT_PER_60S:
            return False, 0
        return True, _RATE_LIMIT_PER_60S - n
    except Exception as exc:
        log.warning("bi_query: rate-limit check failed for %s: %s", shop, exc)
        return True, _RATE_LIMIT_PER_60S


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/pro/bi/schema", response_model=BiSchemaResponse)
def get_bi_schema(
    shop: str = Depends(require_pro_session),
):
    """Return the closed tables/columns/operators/limits metadata.

    Pure read of the static allowlist; no DB or Redis dependency."""
    from app.services.bi_query_builder import get_schema
    return get_schema()


@router.post("/pro/bi/query", response_model=QueryResponse)
def execute_bi_query(
    payload: QueryRequest,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Execute a structured query against the shop's data.

    Tenant filter is hardcoded — request body cannot override
    shop_domain. Statement timeout 10s, row cap 5000 enforced in
    compile_query + execute_query. Every execution audit-logged."""
    allowed, remaining = _check_rate_limit(shop)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="BI query rate limit (30/min) exceeded; wait 60s",
        )

    from app.services.bi_query_builder import (
        compile_query, execute_query, QueryValidationError,
    )

    started = time.monotonic()
    audit_outcome = "ok"
    audit_detail: dict = {}
    try:
        sql, params = compile_query(payload.model_dump(), shop)
        result = execute_query(db, sql, params)
        audit_detail = {
            "table": payload.table,
            "row_count": result.row_count,
            "duration_ms": result.duration_ms,
            "truncated": result.truncated,
        }
        return QueryResponse(
            columns=result.columns,
            rows=result.rows,
            row_count=result.row_count,
            duration_ms=result.duration_ms,
            truncated=result.truncated,
        )
    except QueryValidationError as exc:
        audit_outcome = "validation_error"
        audit_detail = {"error": str(exc), "table": payload.table}
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        # Likely statement_timeout or DB exec error
        audit_outcome = "execution_error"
        audit_detail = {"error": str(exc)[:200], "table": payload.table}
        log.warning("bi_query exec error for %s: %s", shop, exc)
        raise HTTPException(
            status_code=503,
            detail="BI query execution failed (timeout or DB error)",
        )
    finally:
        # Audit-log every attempt — even failures — so the operator
        # can spot abuse patterns. Audit write must NEVER block the
        # response; on failure log + swallow.
        try:
            from app.services.audit import write_audit_log
            duration_ms = int((time.monotonic() - started) * 1000)
            write_audit_log(
                db,
                actor_type="merchant",
                actor_name=shop,
                action_type="bi_query.execute",
                shop_domain=shop,
                status="completed" if audit_outcome == "ok" else "failed",
                metadata={
                    **audit_detail,
                    "outcome": audit_outcome,
                    "total_duration_ms": duration_ms,
                },
            )
            db.commit()
        except Exception as exc:
            log.warning("bi_query audit log write failed: %s", exc)


@router.get("/pro/bi/saved-queries", response_model=SavedQueriesResponse)
def list_saved_queries(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    rows = db.execute(text("""
        SELECT id, name, query_json, created_at, updated_at
        FROM bi_saved_queries
        WHERE shop_domain = :shop
        ORDER BY updated_at DESC
        LIMIT 50
    """), {"shop": shop}).fetchall()
    return SavedQueriesResponse(
        shop_domain=shop,
        queries=[
            SavedQueryItem(
                id=int(r.id),
                name=r.name,
                query_json=r.query_json,
                created_at=r.created_at.isoformat(),
                updated_at=r.updated_at.isoformat(),
            )
            for r in rows
        ],
    )


@router.post("/pro/bi/saved-queries", response_model=SavedQueryItem)
def save_query(
    payload: SaveQueryPayload,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Upsert a saved query (by shop_domain + name).

    Validates the embedded query against the same builder allowlist
    BEFORE persisting — prevents storing an invalid query that would
    crash on later execute."""
    from app.services.bi_query_builder import (
        compile_query, QueryValidationError,
    )
    try:
        # Trial-compile to assert the query is valid; discard result.
        compile_query(payload.query.model_dump(), shop)
    except QueryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    query_dict = payload.query.model_dump()
    import json as _json
    query_json_str = _json.dumps(query_dict)

    row = db.execute(text("""
        INSERT INTO bi_saved_queries (shop_domain, name, query_json)
        VALUES (:s, :n, cast(:q as jsonb))
        ON CONFLICT (shop_domain, name) DO UPDATE
        SET query_json = EXCLUDED.query_json, updated_at = now()
        RETURNING id, name, query_json, created_at, updated_at
    """), {"s": shop, "n": payload.name, "q": query_json_str}).fetchone()
    db.commit()

    return SavedQueryItem(
        id=int(row.id),
        name=row.name,
        query_json=row.query_json,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


@router.delete(
    "/pro/bi/saved-queries/{query_id}",
    response_model=OkResponse,
)
def delete_saved_query(
    query_id: int,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    n = db.execute(text("""
        DELETE FROM bi_saved_queries
        WHERE id = :id AND shop_domain = :s
    """), {"id": query_id, "s": shop}).rowcount
    db.commit()
    if not n:
        raise HTTPException(status_code=404, detail="saved query not found")
    return {"deleted": True, "id": query_id}
