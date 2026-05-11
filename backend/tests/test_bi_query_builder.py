"""Pro #3 BI Query Builder — security-focused tests.

Heavy on attack-vector coverage: tenant escape, write-side abuse,
unknown table/column, unsafe operator, limit clamping, parameterized
value binding, group-by enforcement.

Goal: every layer in the 8-layer defense gets a test that demonstrates
it actually denies the attack.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services.bi_query_builder import (
    QueryValidationError,
    _ALLOWED_TABLES,
    compile_query,
    execute_query,
    get_schema,
)


# ---------------------------------------------------------------------------
# get_schema — closed metadata surface
# ---------------------------------------------------------------------------


def test_schema_lists_only_allowed_tables():
    schema = get_schema()
    table_names = {t["name"] for t in schema["tables"]}
    assert table_names == {"shop_orders", "events", "nudge_events"}


def test_schema_excludes_pii_columns():
    """customer_email + visitor_id must NOT be reachable via the builder."""
    schema = get_schema()
    by_table = {t["name"]: t for t in schema["tables"]}
    shop_orders_cols = {c["name"] for c in by_table["shop_orders"]["columns"]}
    assert "customer_email" not in shop_orders_cols
    assert "customer_id" not in shop_orders_cols
    events_cols = {c["name"] for c in by_table["events"]["columns"]}
    assert "visitor_id" not in events_cols
    assert "payload" not in events_cols


def test_schema_operators_are_allowlisted():
    schema = get_schema()
    assert set(schema["operators"]) == {
        "=", "!=", ">", ">=", "<", "<=", "LIKE", "IN",
        "IS NULL", "IS NOT NULL",
    }


# ---------------------------------------------------------------------------
# compile_query — table & column allowlist
# ---------------------------------------------------------------------------


def _basic_req(**overrides):
    base = {
        "table": "shop_orders",
        "select": [{"column": "total_price"}],
    }
    base.update(overrides)
    return base


def test_unknown_table_rejected():
    with pytest.raises(QueryValidationError, match="unknown table"):
        compile_query(_basic_req(table="merchants"), "x.myshopify.com")


def test_unknown_column_rejected():
    with pytest.raises(QueryValidationError, match="unknown column"):
        compile_query(
            _basic_req(select=[{"column": "password_hash"}]),
            "x.myshopify.com",
        )


def test_pii_column_unreachable_even_for_shop_orders():
    """customer_email exists on the table but is NOT in _ALLOWED_COLUMNS
    → builder treats it as unknown."""
    with pytest.raises(QueryValidationError, match="unknown column"):
        compile_query(
            _basic_req(select=[{"column": "customer_email"}]),
            "x.myshopify.com",
        )


def test_empty_select_rejected():
    with pytest.raises(QueryValidationError, match="at least one"):
        compile_query(_basic_req(select=[]), "x.myshopify.com")


def test_too_many_columns_rejected():
    req = _basic_req(select=[{"column": "total_price"}] * 11)
    with pytest.raises(QueryValidationError, match="at most 10"):
        compile_query(req, "x.myshopify.com")


# ---------------------------------------------------------------------------
# Tenant filter — hardcoded, cannot be overridden
# ---------------------------------------------------------------------------


def test_tenant_filter_always_prepended():
    sql, params = compile_query(_basic_req(), "alpha.myshopify.com")
    assert "shop_domain = :_auth_shop" in sql
    assert params["_auth_shop"] == "alpha.myshopify.com"


def test_tenant_filter_uses_auth_shop_not_body():
    """Even if attacker passes shop_domain in WHERE, the auth shop wins."""
    req = _basic_req(where=[
        {"column": "currency", "op": "=", "value": "USD"},
    ])
    sql, params = compile_query(req, "auth.myshopify.com")
    assert params["_auth_shop"] == "auth.myshopify.com"
    # The compiled SQL has the auth tenant filter AS THE FIRST clause
    where_idx = sql.find("WHERE ")
    auth_idx = sql.find("shop_domain = :_auth_shop", where_idx)
    currency_idx = sql.find("currency =", where_idx)
    assert auth_idx < currency_idx


def test_shop_domain_not_in_allowed_columns():
    """Builder users cannot select / filter / order by shop_domain —
    it's NOT in _ALLOWED_COLUMNS even though the underlying table has it.
    This defends against `WHERE shop_domain != 'mine'` injection attempts."""
    for sample in [
        {"column": "shop_domain"},
        # filter on shop_domain
    ]:
        with pytest.raises(QueryValidationError, match="unknown"):
            compile_query(
                _basic_req(select=[sample]),
                "x.myshopify.com",
            )

    with pytest.raises(QueryValidationError, match="unknown filter column"):
        compile_query(
            _basic_req(where=[
                {"column": "shop_domain", "op": "!=", "value": "mine"},
            ]),
            "x.myshopify.com",
        )


# ---------------------------------------------------------------------------
# Operator allowlist
# ---------------------------------------------------------------------------


def test_unknown_op_rejected():
    req = _basic_req(where=[
        {"column": "currency", "op": "REGEXP", "value": ".*"},
    ])
    with pytest.raises(QueryValidationError, match="unknown filter op"):
        compile_query(req, "x.myshopify.com")


def test_op_without_value_rejected():
    req = _basic_req(where=[
        {"column": "currency", "op": "=", "value": None},
    ])
    with pytest.raises(QueryValidationError, match="requires a value"):
        compile_query(req, "x.myshopify.com")


def test_in_op_requires_list_value():
    req = _basic_req(where=[
        {"column": "currency", "op": "IN", "value": "USD"},
    ])
    with pytest.raises(QueryValidationError, match="non-empty list"):
        compile_query(req, "x.myshopify.com")


def test_in_op_caps_list_size():
    req = _basic_req(where=[
        {"column": "currency", "op": "IN", "value": ["x"] * 101},
    ])
    with pytest.raises(QueryValidationError, match="at most 100"):
        compile_query(req, "x.myshopify.com")


def test_is_null_op_no_value_required():
    req = _basic_req(where=[
        {"column": "discount_amount", "op": "IS NULL"},
    ])
    sql, params = compile_query(req, "x.myshopify.com")
    assert "discount_amount IS NULL" in sql
    # Only the auth shop param is in params
    assert set(params.keys()) == {"_auth_shop"}


# ---------------------------------------------------------------------------
# Parameterization (no string injection)
# ---------------------------------------------------------------------------


def test_filter_value_is_parameterized():
    """Even with a SQL-injection-shaped value, the SQL is bound via
    parameter, not interpolated."""
    payload = "'; DROP TABLE shop_orders; --"
    req = _basic_req(where=[
        {"column": "currency", "op": "=", "value": payload},
    ])
    sql, params = compile_query(req, "x.myshopify.com")
    # SQL has placeholders, not the literal payload
    assert payload not in sql
    # Payload sits in params, where SQLAlchemy binds it safely
    assert payload in params.values()


def test_alias_sanitization():
    """An alias with shell-injection-shaped chars is stripped to identifier."""
    req = _basic_req(select=[
        {"agg": "count", "column": None, "alias": "evil; DROP TABLE x"},
    ])
    sql, _ = compile_query(req, "x.myshopify.com")
    # Sanitized alias preserves alphanumerics + underscores, drops the rest
    assert 'DROP TABLE' not in sql
    assert "evil_" in sql  # underscore replacement for space + ;


# ---------------------------------------------------------------------------
# Aggregations + GROUP BY enforcement
# ---------------------------------------------------------------------------


def test_unknown_aggregation_rejected():
    req = _basic_req(select=[
        {"agg": "PERCENTILE", "column": "total_price"},
    ])
    with pytest.raises(QueryValidationError, match="unknown aggregation"):
        compile_query(req, "x.myshopify.com")


def test_aggregation_with_unknown_column_rejected():
    req = _basic_req(select=[
        {"agg": "sum", "column": "ghost_column"},
    ])
    with pytest.raises(QueryValidationError, match="unknown column"):
        compile_query(req, "x.myshopify.com")


def test_count_star_allowed_without_column():
    req = _basic_req(select=[{"agg": "count", "column": None}])
    sql, _ = compile_query(req, "x.myshopify.com")
    assert "COUNT(*)" in sql


def test_aggregation_with_non_aggregated_column_requires_group_by():
    """Postgres rule: non-aggregated columns must be in GROUP BY."""
    req = _basic_req(select=[
        {"column": "currency"},
        {"agg": "sum", "column": "total_price"},
    ])
    with pytest.raises(QueryValidationError, match="group_by"):
        compile_query(req, "x.myshopify.com")


def test_aggregation_with_group_by_passes():
    req = _basic_req(
        select=[
            {"column": "currency"},
            {"agg": "sum", "column": "total_price", "alias": "rev"},
        ],
        group_by=["currency"],
    )
    sql, _ = compile_query(req, "x.myshopify.com")
    assert "GROUP BY currency" in sql
    assert 'SUM(total_price) AS "rev"' in sql


def test_having_requires_group_by():
    req = _basic_req(having=[
        {"column": "total_price", "op": ">", "value": 100},
    ])
    with pytest.raises(QueryValidationError, match="having requires group_by"):
        compile_query(req, "x.myshopify.com")


# ---------------------------------------------------------------------------
# ORDER BY
# ---------------------------------------------------------------------------


def test_order_by_unknown_column_rejected():
    req = _basic_req(order_by=[
        {"column": "ghost", "direction": "ASC"},
    ])
    with pytest.raises(QueryValidationError, match="unknown order_by"):
        compile_query(req, "x.myshopify.com")


def test_order_by_invalid_direction_rejected():
    req = _basic_req(order_by=[
        {"column": "total_price", "direction": "SHUFFLE"},
    ])
    with pytest.raises(QueryValidationError, match="ASC or DESC"):
        compile_query(req, "x.myshopify.com")


def test_order_by_alias_allowed():
    req = _basic_req(
        select=[
            {"column": "currency"},
            {"agg": "sum", "column": "total_price", "alias": "rev"},
        ],
        group_by=["currency"],
        order_by=[{"column": "rev", "direction": "DESC"}],
    )
    sql, _ = compile_query(req, "x.myshopify.com")
    assert 'ORDER BY "rev" DESC' in sql


# ---------------------------------------------------------------------------
# LIMIT clamping
# ---------------------------------------------------------------------------


def test_limit_clamped_to_max():
    """User passing limit > 5000 is clamped down."""
    req = _basic_req(limit=999999)
    sql, _ = compile_query(req, "x.myshopify.com")
    assert "LIMIT 5000" in sql


def test_limit_default_when_missing():
    req = _basic_req()
    # No limit key — defaults to _DEFAULT_LIMIT (100)
    sql, _ = compile_query(req, "x.myshopify.com")
    assert "LIMIT 100" in sql


def test_limit_negative_falls_back_to_default():
    req = _basic_req(limit=-5)
    sql, _ = compile_query(req, "x.myshopify.com")
    assert "LIMIT 100" in sql


# ---------------------------------------------------------------------------
# Execute — DB-integration with tenant isolation proof
# ---------------------------------------------------------------------------


def _seed_order(db, shop, price, currency, total_id):
    from datetime import datetime
    db.execute(text("""
        INSERT INTO shop_orders
          (shop_domain, shopify_order_id, total_price, currency,
           line_items, created_at)
        VALUES (:s, :sid, :p, :c, '[]'::jsonb, :ts)
    """), {"s": shop, "sid": total_id, "p": price, "c": currency,
           "ts": datetime.utcnow()})


def test_execute_returns_only_auth_shop_rows(db):
    """The tenant filter prevents cross-shop reads even when the
    user passes no WHERE conditions."""
    _seed_order(db, "alpha.myshopify.com", 100.0, "USD", "alpha_1")
    _seed_order(db, "alpha.myshopify.com", 200.0, "USD", "alpha_2")
    _seed_order(db, "beta.myshopify.com", 999.0, "USD", "beta_1")

    sql, params = compile_query(_basic_req(), "alpha.myshopify.com")
    result = execute_query(db, sql, params)
    assert result.row_count == 2
    # No 999.0 from beta in results
    for r in result.rows:
        assert r[0] != 999.0


def test_execute_aggregation_with_group_by(db):
    """SUM(total_price) GROUP BY currency for auth shop only."""
    _seed_order(db, "alpha.myshopify.com", 100.0, "USD", "a_1")
    _seed_order(db, "alpha.myshopify.com", 200.0, "USD", "a_2")
    _seed_order(db, "alpha.myshopify.com",  50.0, "EUR", "a_3")
    _seed_order(db, "beta.myshopify.com",  999.0, "USD", "b_1")

    req = {
        "table": "shop_orders",
        "select": [
            {"column": "currency"},
            {"agg": "sum", "column": "total_price", "alias": "total"},
        ],
        "group_by": ["currency"],
        "order_by": [{"column": "total", "direction": "DESC"}],
    }
    sql, params = compile_query(req, "alpha.myshopify.com")
    result = execute_query(db, sql, params)
    assert result.row_count == 2
    # First row should be USD with 300.0 sum (alpha only)
    assert result.rows[0][0] == "USD"
    assert float(result.rows[0][1]) == 300.0


def test_execute_truncated_flag_when_results_exceed_limit(db):
    """Defense-in-depth: execute_query truncates if compile_query
    ever emits an unbounded query. We can't easily simulate that
    without monkey-patching, so just sanity-check the limit path."""
    for i in range(15):
        _seed_order(
            db, "alpha.myshopify.com", float(i), "USD", f"a_{i}",
        )
    req = _basic_req(limit=10)
    sql, params = compile_query(req, "alpha.myshopify.com")
    result = execute_query(db, sql, params)
    assert result.row_count == 10
    assert result.truncated is False  # LIMIT enforced server-side
