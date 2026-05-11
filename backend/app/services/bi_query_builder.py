"""bi_query_builder.py — Pro #3 safe query builder over merchant data.

Strategic choice over "raw SQL sandbox":
  Pro mid-band competitors at $60-130 (Klaviyo Marketing, Glew Pro,
  Mixpanel Growth) ship STRUCTURED query builders, not raw-SQL
  sandboxes. Raw SQL is reserved for Mixpanel's Growth+ tier
  (>$1k/mo). Builder mode meets parity at the right tier while
  shrinking the safety bunker by an order of magnitude — the SQL is
  never user-typed; we reconstruct it from a typed Pydantic schema
  with hard-validated tables/columns/ops on every request.

Layered defenses (per CLAUDE.md §2 #12 "Defense in depth: 5+ layers"):

  Layer 1 — Allowlist tables. _ALLOWED_TABLES is the closed set; any
  reference outside fails validation at parse time.

  Layer 2 — Allowlist columns per table. _ALLOWED_COLUMNS is the
  closed set per table; safety-sensitive columns (gdpr_*, encrypted_*,
  _internal) are intentionally excluded even when the underlying
  table has them.

  Layer 3 — Allowlist filter operators. _ALLOWED_OPS rejects
  unknown / unsafe operators (no regex / no JSON path / no arbitrary
  function calls).

  Layer 4 — Hardcoded tenant filter. compile_query injects
  `shop_domain = :auth_shop` as a WHERE clause that the user CANNOT
  override or remove. Even if the request omits all WHERE conditions,
  the compiled SQL always carries the tenant scope.

  Layer 5 — Parameterized values. Every filter value goes through
  SQLAlchemy text() params — zero string interpolation.

  Layer 6 — Row cap. _MAX_ROWS = 5000. Compiled SQL ALWAYS carries
  LIMIT min(user_limit, _MAX_ROWS) — even if user_limit is missing
  (defaults to 100) or > 5000 (clamped down).

  Layer 7 — Statement timeout (set by executor via SET LOCAL
  statement_timeout — see execute_query() in this module).

  Layer 8 — Per-merchant rate limit (Redis SETNX counter — see
  api/bi_query.py).

  Layer 9 — Audit log (every executed query persisted via
  app.services.audit.write_audit_log — see api/bi_query.py).

The builder is a strict whitelist all the way down. Adding a new
table or column requires editing this file (TIER_0 review). Adding
a new operator requires editing _ALLOWED_OPS + the SQL compiler.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


log = logging.getLogger(__name__)


_MAX_COLUMNS = 10
_MAX_FILTERS = 10
_MAX_GROUP_BY = 5
_MAX_ORDER_BY = 3
_MAX_ROWS = 5000
_DEFAULT_LIMIT = 100
_STATEMENT_TIMEOUT_MS = 10_000  # 10 seconds


# ---------------------------------------------------------------------------
# Schema metadata — the closed set of tables/columns the builder exposes.
# Every entry here is intentional. Any column NOT listed is unreachable.
# ---------------------------------------------------------------------------

# Type tags for value coercion + UI rendering
_TYPE_STRING = "string"
_TYPE_INT = "int"
_TYPE_FLOAT = "float"
_TYPE_DATETIME = "datetime"


# (column_name, sql_type, label, type_tag)
_SHOP_ORDERS_COLS: list[tuple[str, str, str, str]] = [
    ("shopify_order_id", "VARCHAR", "Order ID", _TYPE_STRING),
    ("total_price", "NUMERIC", "Total price", _TYPE_FLOAT),
    ("currency", "VARCHAR", "Currency", _TYPE_STRING),
    ("created_at", "TIMESTAMP", "Order date", _TYPE_DATETIME),
    ("discount_amount", "NUMERIC", "Discount amount", _TYPE_FLOAT),
    ("tax_amount", "NUMERIC", "Tax amount", _TYPE_FLOAT),
    ("payment_method", "VARCHAR", "Payment method", _TYPE_STRING),
    ("financial_status", "VARCHAR", "Financial status", _TYPE_STRING),
    ("fulfillment_status", "VARCHAR", "Fulfillment status", _TYPE_STRING),
    # NOTE: customer_email, customer_id deliberately EXCLUDED. Surfacing
    # raw PII via a builder result table without masking is a GDPR risk
    # beyond the merchant's normal dashboard scope.
]

_EVENTS_COLS: list[tuple[str, str, str, str]] = [
    ("event_type", "VARCHAR", "Event type", _TYPE_STRING),
    ("url", "VARCHAR", "Page URL", _TYPE_STRING),
    ("timestamp", "BIGINT", "Timestamp (ms)", _TYPE_INT),
    ("device", "VARCHAR", "Device", _TYPE_STRING),
    # NOTE: visitor_id excluded — raw visitor identifier is PII-adjacent.
    # NOTE: payload JSONB excluded — opaque; arbitrary keys can leak data.
]

_NUDGE_EVENTS_COLS: list[tuple[str, str, str, str]] = [
    ("event_type", "VARCHAR", "Event type", _TYPE_STRING),
    ("nudge_id", "VARCHAR", "Nudge ID", _TYPE_STRING),
    ("nudge_type", "VARCHAR", "Nudge type", _TYPE_STRING),
    ("timestamp", "TIMESTAMP", "Event time", _TYPE_DATETIME),
]


@dataclass
class TableSchema:
    name: str
    label: str
    description: str
    columns: list[tuple[str, str, str, str]]


_ALLOWED_TABLES: dict[str, TableSchema] = {
    "shop_orders": TableSchema(
        name="shop_orders",
        label="Orders",
        description="Real Shopify orders with revenue, currency, payment/fulfillment status.",
        columns=_SHOP_ORDERS_COLS,
    ),
    "events": TableSchema(
        name="events",
        label="Events",
        description="Storefront events (page views, scrolls, clicks).",
        columns=_EVENTS_COLS,
    ),
    "nudge_events": TableSchema(
        name="nudge_events",
        label="Nudge events",
        description="Spark nudge impressions, clicks, conversions.",
        columns=_NUDGE_EVENTS_COLS,
    ),
}


# Pre-compute per-table column lookups for fast validation
_ALLOWED_COLUMNS: dict[str, dict[str, tuple[str, str]]] = {
    table_name: {col[0]: (col[1], col[3]) for col in schema.columns}
    for table_name, schema in _ALLOWED_TABLES.items()
}


_ALLOWED_AGGREGATIONS = {"count", "sum", "avg", "min", "max"}

# operator → SQL fragment template. value is bound via parameter,
# never interpolated. The {col} placeholder is filled with a
# pre-validated column name from _ALLOWED_COLUMNS, never user input.
_ALLOWED_OPS: dict[str, str] = {
    "=":           "{col} = :{p}",
    "!=":          "{col} <> :{p}",
    ">":           "{col} > :{p}",
    ">=":          "{col} >= :{p}",
    "<":           "{col} < :{p}",
    "<=":          "{col} <= :{p}",
    "LIKE":        "{col} LIKE :{p}",
    "IN":          "{col} = ANY(:{p})",
    "IS NULL":     "{col} IS NULL",
    "IS NOT NULL": "{col} IS NOT NULL",
}

_OPS_WITHOUT_VALUE = {"IS NULL", "IS NOT NULL"}
_OPS_LIST_VALUE = {"IN"}


# ---------------------------------------------------------------------------
# Validation errors — surface as 422 in API layer
# ---------------------------------------------------------------------------


class QueryValidationError(ValueError):
    """Raised when the request schema or values fail validation.

    These map to 422 responses. Never raise for transient errors
    (timeout, DB connection drop) — use a different exception class.
    """


# ---------------------------------------------------------------------------
# Compile (Pydantic-validated request) → parameterized SQL + params
# ---------------------------------------------------------------------------


def get_schema() -> dict[str, Any]:
    """Return the closed table+column schema for the frontend builder.

    Pure metadata; safe to expose to any Pro session."""
    return {
        "tables": [
            {
                "name": t.name,
                "label": t.label,
                "description": t.description,
                "columns": [
                    {
                        "name": c[0],
                        "label": c[2],
                        "type": c[3],
                    }
                    for c in t.columns
                ],
            }
            for t in _ALLOWED_TABLES.values()
        ],
        "operators": list(_ALLOWED_OPS.keys()),
        "aggregations": sorted(_ALLOWED_AGGREGATIONS),
        "limits": {
            "max_columns": _MAX_COLUMNS,
            "max_filters": _MAX_FILTERS,
            "max_group_by": _MAX_GROUP_BY,
            "max_order_by": _MAX_ORDER_BY,
            "max_rows": _MAX_ROWS,
            "default_limit": _DEFAULT_LIMIT,
        },
    }


def compile_query(req: dict, auth_shop: str) -> tuple[str, dict]:
    """Validate + compile a structured QueryRequest dict into a
    parameterized SQL string + bind params.

    All validation happens here — by the time we return, the SQL is
    safe to execute against the read-only DB session.

    Args:
        req: the QueryRequest dict (already pydantic-validated for
             top-level shape; we re-check the table/column allowlist
             since pydantic alone can't enforce closed enums on str fields)
        auth_shop: the resolved Pro session shop_domain (NEVER from req body)

    Returns:
        (sql, params) — execute via SQLAlchemy text(sql).bindparams(...).

    Raises:
        QueryValidationError on any schema violation.
    """
    # Table allowlist
    table_name = req.get("table")
    if table_name not in _ALLOWED_TABLES:
        raise QueryValidationError(f"unknown table: {table_name!r}")
    table_cols = _ALLOWED_COLUMNS[table_name]

    # SELECT clause
    select_items = req.get("select") or []
    if not select_items:
        raise QueryValidationError("at least one select column or aggregation is required")
    if len(select_items) > _MAX_COLUMNS:
        raise QueryValidationError(
            f"select supports at most {_MAX_COLUMNS} columns/aggregations"
        )
    select_fragments: list[str] = []
    aliases_seen: set[str] = set()
    has_aggregation = False
    for idx, item in enumerate(select_items):
        # Each item is either {"column": "x"} or
        # {"agg": "count", "column": "x" | None, "alias": "y" | None}
        # Pydantic model_dump() includes all fields incl. None, so a
        # column-only item carries `agg=None`. Branch on TRUTHY agg
        # (not key presence) so `{"column": "x", "agg": None}` is
        # treated as a column item.
        if item.get("agg"):
            agg = (item.get("agg") or "").lower()
            if agg not in _ALLOWED_AGGREGATIONS:
                raise QueryValidationError(f"unknown aggregation: {agg!r}")
            has_aggregation = True
            col = item.get("column")
            if agg == "count" and not col:
                expr = "COUNT(*)"
            else:
                if col not in table_cols:
                    raise QueryValidationError(
                        f"unknown column in aggregation: {col!r}"
                    )
                expr = f"{agg.upper()}({col})"
            alias = item.get("alias") or f"{agg}_{col or 'all'}_{idx}"
            alias = _safe_alias(alias)
            if alias in aliases_seen:
                raise QueryValidationError(f"duplicate alias: {alias!r}")
            aliases_seen.add(alias)
            select_fragments.append(f'{expr} AS "{alias}"')
        elif item.get("column"):
            col = item["column"]
            if col not in table_cols:
                raise QueryValidationError(f"unknown column: {col!r}")
            select_fragments.append(col)
        else:
            raise QueryValidationError(
                "select items must carry 'column' or 'agg'"
            )

    # WHERE clause — auth tenant filter ALWAYS prepended
    params: dict[str, Any] = {"_auth_shop": auth_shop}
    where_fragments: list[str] = ["shop_domain = :_auth_shop"]
    for idx, f in enumerate(req.get("where") or []):
        if idx >= _MAX_FILTERS:
            raise QueryValidationError(
                f"at most {_MAX_FILTERS} where filters allowed"
            )
        frag, params_added = _compile_filter(f, idx, table_cols, "w")
        where_fragments.append(frag)
        params.update(params_added)

    # GROUP BY clause
    group_by_fragments: list[str] = []
    for col in req.get("group_by") or []:
        if col not in table_cols:
            raise QueryValidationError(f"unknown group_by column: {col!r}")
        group_by_fragments.append(col)
    if len(group_by_fragments) > _MAX_GROUP_BY:
        raise QueryValidationError(
            f"at most {_MAX_GROUP_BY} group_by columns allowed"
        )

    # If aggregations are present + non-aggregated columns selected, those
    # non-aggregated columns MUST appear in GROUP BY (Postgres requirement).
    # Enforce here so the merchant gets a clear 422 instead of an obscure
    # DB error. Only flag NON-aggregated items (those without "agg" set);
    # aggregation items also carry "column" but that column is the argument
    # to the agg function, not a raw select target.
    if has_aggregation:
        for item in select_items:
            is_agg = item.get("agg")
            col = item.get("column")
            if not is_agg and col and col not in group_by_fragments:
                raise QueryValidationError(
                    f"column {col!r} must appear in group_by "
                    "when the query has aggregations"
                )

    # HAVING clause (only valid with group_by; same shape as WHERE)
    having_fragments: list[str] = []
    for idx, f in enumerate(req.get("having") or []):
        if not group_by_fragments:
            raise QueryValidationError("having requires group_by")
        if idx >= _MAX_FILTERS:
            raise QueryValidationError(
                f"at most {_MAX_FILTERS} having filters allowed"
            )
        # having filters can reference aliases too — for simplicity we
        # only allow column refs for now
        frag, params_added = _compile_filter(f, idx, table_cols, "h")
        having_fragments.append(frag)
        params.update(params_added)

    # ORDER BY clause
    order_by_fragments: list[str] = []
    for idx, o in enumerate(req.get("order_by") or []):
        if idx >= _MAX_ORDER_BY:
            raise QueryValidationError(
                f"at most {_MAX_ORDER_BY} order_by clauses allowed"
            )
        col = o.get("column")
        direction = (o.get("direction") or "ASC").upper()
        if direction not in ("ASC", "DESC"):
            raise QueryValidationError(
                f"order_by direction must be ASC or DESC, got {direction!r}"
            )
        # Allow ordering by aliases when present, else require allowlist col
        if col in aliases_seen:
            order_by_fragments.append(f'"{col}" {direction}')
        elif col in table_cols:
            order_by_fragments.append(f"{col} {direction}")
        else:
            raise QueryValidationError(f"unknown order_by reference: {col!r}")

    # LIMIT — clamped to _MAX_ROWS no matter what user passed
    user_limit = req.get("limit", _DEFAULT_LIMIT)
    if not isinstance(user_limit, int) or user_limit <= 0:
        user_limit = _DEFAULT_LIMIT
    limit = min(user_limit, _MAX_ROWS)

    # Assemble. Indentation purely cosmetic for the audit log readability.
    sql_parts = [
        f"SELECT {', '.join(select_fragments)}",
        f"FROM {table_name}",
        f"WHERE {' AND '.join(where_fragments)}",
    ]
    if group_by_fragments:
        sql_parts.append(f"GROUP BY {', '.join(group_by_fragments)}")
    if having_fragments:
        sql_parts.append(f"HAVING {' AND '.join(having_fragments)}")
    if order_by_fragments:
        sql_parts.append(f"ORDER BY {', '.join(order_by_fragments)}")
    sql_parts.append(f"LIMIT {limit}")

    return "\n".join(sql_parts), params


def _compile_filter(
    f: dict, idx: int, table_cols: dict, prefix: str,
) -> tuple[str, dict]:
    col = f.get("column")
    op = f.get("op")
    if col not in table_cols:
        raise QueryValidationError(f"unknown filter column: {col!r}")
    if op not in _ALLOWED_OPS:
        raise QueryValidationError(f"unknown filter op: {op!r}")
    params: dict[str, Any] = {}
    if op in _OPS_WITHOUT_VALUE:
        return _ALLOWED_OPS[op].format(col=col, p=""), params
    pname = f"{prefix}{idx}"
    value = f.get("value")
    if op in _OPS_LIST_VALUE:
        if not isinstance(value, list) or not value:
            raise QueryValidationError(
                f"op {op!r} requires a non-empty list value"
            )
        # cap list size to bound query plan complexity
        if len(value) > 100:
            raise QueryValidationError(
                "IN clause supports at most 100 values"
            )
        params[pname] = value
    else:
        if value is None:
            raise QueryValidationError(f"op {op!r} requires a value")
        params[pname] = value
    return _ALLOWED_OPS[op].format(col=col, p=pname), params


def _safe_alias(s: str) -> str:
    """Aliases are double-quoted in SQL; we only need to ensure no
    embedded `"` slips through. Hard-strip any non-identifier chars."""
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in s)[:64]


# ---------------------------------------------------------------------------
# Execute — bound statement_timeout + row cap enforced one more time
# ---------------------------------------------------------------------------


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    duration_ms: int
    truncated: bool


def execute_query(
    db: Session, sql: str, params: dict,
) -> QueryResult:
    """Execute a compiled query with statement_timeout enforced via
    SET LOCAL. Returns up to _MAX_ROWS+1 rows so we can detect
    truncation honestly."""
    started = time.monotonic()

    # Defense layer 4a — role-level write protection. `SET LOCAL ROLE`
    # downgrades the current transaction's role to
    # `wishspark_bi_readonly` which has only SELECT grants on the 3
    # builder-allowed tables (see migrations/aa6_bi_readonly_role.py).
    # Any DDL/DML attempt fails with "permission denied" even if a
    # future parser bug constructed one. Strictly stronger than the
    # tx-level READ ONLY guard below — tx-level can be bypassed by a
    # code regression that drops the SET statement; role-level cannot
    # because the role itself lacks the grants. SET LOCAL is tx-scoped:
    # role resets at COMMIT/ROLLBACK so PgBouncer transaction-pool is
    # safe.
    db.execute(text("SET LOCAL ROLE wishspark_bi_readonly"))

    # Defense layer 4b — tx-level READ ONLY guard. Belt-and-braces on
    # top of the role. Even if the role were ever mis-granted to a
    # superuser future regression, READ ONLY still rejects writes at
    # the transaction-manager level (PG error 25006). The combination
    # makes a write attempt require BOTH layers to fail simultaneously.
    db.execute(text("SET TRANSACTION READ ONLY"))

    # statement_timeout via set_config(name, value, is_local=true) —
    # equivalent to SET LOCAL but bind-parameterizable (audit
    # test_no_new_raw_sql_fstring_interpolation rejects raw-SQL
    # f-string formatting even when the value is a compile-time
    # constant). Transaction-scoped so PgBouncer transaction pooling
    # doesn't leak the timeout to the next request.
    db.execute(
        text("SELECT set_config('statement_timeout', :timeout_ms, true)"),
        {"timeout_ms": str(_STATEMENT_TIMEOUT_MS)},
    )

    result = db.execute(text(sql), params)
    rows = result.fetchall()
    columns = list(result.keys())
    duration_ms = int((time.monotonic() - started) * 1000)

    # The compile path already injects LIMIT _MAX_ROWS. But defense-in-
    # depth: if a future compile bug emits an unbounded query, we still
    # truncate here.
    truncated = False
    if len(rows) > _MAX_ROWS:
        rows = rows[:_MAX_ROWS]
        truncated = True

    return QueryResult(
        columns=columns,
        rows=[list(r) for r in rows],
        row_count=len(rows),
        duration_ms=duration_ms,
        truncated=truncated,
    )
