"""merchants active partial covering idx — kill the per-cycle Seq Scan on install_status='active'

§12 ("every per-shop WHERE has a matching index") closure. The
aggregation_worker cold-tier scan (every 5 min, aggregation_worker.py)
and merchant_brain.tick_all_active_merchants (every 15 min brain tick,
merchant_brain.py) + agent_worker entitlement scan run
`SELECT shop_domain FROM merchants WHERE install_status='active'`.
EXPLAIN on the live DB showed `Seq Scan on merchants` — live indexes
were only merchants_pkey + ix_merchants_shop_domain, none on
install_status (independent READ-Agent finding; design led + verified
by independent Agent a0b4b92, 2026-05-18).

A PARTIAL COVERING index on (shop_domain) WHERE install_status='active'
is selectivity-INDEPENDENT (the index *is* the active subset, so the
planner uses it regardless of what fraction is 'active' — a plain
(install_status) btree would be planner-IGNORED if 'active' is the
dominant value, which it plausibly is given the column default). The
projection at every hot site is shop_domain ONLY ⟹ index-only scan;
the index order also satisfies the downstream sorted() / ORDER BY
shop_domain ASC for free. `merchants` is tiny + slow-changing
(write maintenance only on install/uninstall) ⟹ negligible
write-amplification. NOT a no-op (the rejected plain (install_status)
btree would have been; the partial form is chosen precisely to
eliminate the selectivity gamble).

CREATE INDEX CONCURRENTLY so the migration does not lock `merchants`
while it builds (online, no long lock at 10k). CONCURRENTLY cannot
run inside a transaction ⟹ AUTOCOMMIT isolation (mirrors the
zzz3_scale_10k_indexes in-repo precedent exactly, §2 r1).
IF NOT EXISTS ⟹ a re-run / partial-failure resume is safe; the
INVALID-index reaper is: run downgrade() then re-run upgrade().

TIER_2 (migrations/) — founder session-scoped approval 2026-05-18
(the jewel TIER_2 batch: this index + J5 index + J4-part-2
autovacuum). Additive, non-destructive, online.

Revision ID: zzzi_merchants_active_partial_idx
Revises: zzzh_audit_chain_anchor
Create Date: 2026-05-18
"""
from alembic import op

revision = "zzzi_merchants_active_partial_idx"
down_revision = "zzzh_audit_chain_anchor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY must run outside a transaction.
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_merchants_active_shop_domain
            ON merchants (shop_domain)
            WHERE install_status = 'active'
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_merchants_active_shop_domain"
        )
