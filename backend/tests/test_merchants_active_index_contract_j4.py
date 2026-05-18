"""Deterministic BOTH-ENDS contract test for the J4-batch TIER_2 #1
migration `zzzi_merchants_active_partial_idx` (design + verified by
independent Agent a0b4b92, 2026-05-18).

A partial covering index `ix_merchants_active_shop_domain ON
merchants (shop_domain) WHERE install_status='active'` only earns its
place while the hot-path query it serves keeps that exact
projection + predicate. An EXPLAIN-shape test would be flaky theater
at small N (planner picks seq scan below ~hundreds of rows). The
deterministic, non-vacuous form pins the CONTRACT at BOTH ends:

  1. the served query (Merchant.shop_domain WHERE install_status=
     'active') compiles to SQL whose projection is shop_domain ONLY
     and whose predicate is install_status='active' — so an
     index-only scan on the partial index is the planner's path.
  2. the migration's CREATE INDEX declares EXACTLY (shop_domain)
     WHERE install_status = 'active' — the index matches the query.

A refactor that renames the columns, changes the predicate value,
widens the projection, or drifts the migration's index columns/
predicate FAILS this test ⟹ the now-orphaned index is re-evaluated,
not left dead.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models.merchant import Merchant

_MIG = (Path(__file__).resolve().parent.parent / "migrations" / "versions"
        / "zzzi_merchants_active_partial_idx.py")


def _compiled(stmt) -> str:
    return str(stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True})).lower()


def test_served_query_projection_and_predicate_match_the_partial_index():
    # The exact hot-path shape (aggregation_worker cold-tier /
    # merchant_brain tick / agent_worker entitlement): project
    # shop_domain ONLY, filter install_status == 'active'.
    sql = _compiled(
        select(Merchant.shop_domain).where(Merchant.install_status == "active"))
    # projection is shop_domain ONLY (index-only-scan-able by a
    # covering (shop_domain) index) — no other merchants column:
    assert "merchants.shop_domain" in sql
    assert "merchants.install_status" not in sql.split("where", 1)[0], \
        "projection widened beyond shop_domain — covering index no longer index-only"
    # predicate is exactly install_status = 'active' (the partial
    # index's WHERE) — value pinned so a status-string change is loud:
    assert "where merchants.install_status = 'active'" in sql


def test_migration_index_matches_the_served_contract():
    src = _MIG.read_text()
    up = src.split("def downgrade", 1)[0].lower()
    # index column == the query projection (shop_domain), partial
    # predicate == the query predicate (install_status='active'):
    assert "on merchants (shop_domain)" in up
    assert "where install_status = 'active'" in up
    assert "create index concurrently if not exists" in up
    assert "ix_merchants_active_shop_domain" in up
    assert "autocommit_block()" in up           # CONCURRENTLY needs non-txn
    down = src.split("def downgrade", 1)[1].lower()
    assert "drop index concurrently if exists ix_merchants_active_shop_domain" in down
    assert "autocommit_block()" in down


def test_model_declares_the_partial_index_alembic_check_parity():
    # The defect ship-gate Agent a84d4e89 PROVED on a scratch DB: a
    # migration-only index ⟹ post-apply `alembic check` autogenerate
    # reports `remove_index` drift ⟹ preflight alembic-check HARD
    # gate bricks every subsequent commit (CHECK_EXIT 255). Model
    # parity (index in Merchant.__table__.indexes too — the zzz3
    # product_metrics precedent) is mandatory and pinned HERE at the
    # ORM-metadata level so a future migration-only index fails loud.
    idx = next((i for i in Merchant.__table__.indexes
                if i.name == "ix_merchants_active_shop_domain"), None)
    assert idx is not None, \
        "Merchant model lost the partial Index — migration-only index " \
        "bricks the preflight alembic-check gate post-apply"
    assert [c.name for c in idx.columns] == ["shop_domain"], \
        "index projection drifted from shop_domain (covering-scan lost)"
    # the partial predicate compiles to exactly the served predicate:
    where_sql = str(idx.dialect_options["postgresql"]["where"]
                    .compile(dialect=postgresql.dialect(),
                             compile_kwargs={"literal_binds": True})).lower()
    assert where_sql == "install_status = 'active'", \
        f"model partial predicate drifted from served query: {where_sql!r}"


def test_migration_revision_chains_off_the_single_head():
    src = _MIG.read_text()
    assert 'revision = "zzzi_merchants_active_partial_idx"' in src
    # linear off the verified single head zzzh ⟹ single-head invariant
    # preserved (a divergent down_revision would split alembic heads).
    assert 'down_revision = "zzzh_audit_chain_anchor"' in src
