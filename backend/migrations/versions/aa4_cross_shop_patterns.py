"""cross_shop_patterns — Sprint 3 #3 cross-shop pattern memory

Per-shop deterministic learning engine moat extension. Sprint 3 of the
2026-05-09 founder-approved per-shop learning roadmap:

  Shop A measures retention_outreach_email lift +6.8% p=0.023 (Sprint 1 #6
  closed-loop). The outcome row sits in brain_decisions with outcome_status
  ∈ {effective, ineffective, neutral}.

  Sprint 3 aggregates those measured lifts at the vertical level. Shop B
  new in the same vertical inherits a Bayesian prior derived from the
  aggregate — anti-cold-start beyond the static industry baselines wired
  by Sprint 2 #4 (vertical_blend at n_prior=200).

  Network effect deterministic: more merchants → richer prior per vertical
  → smarter day-1 for every new merchant of the same vertical.

GDPR-clean by design:
  - No shop_domain in the row (aggregate-only).
  - k-anonymity hard constraint: n_shops >= 3 enforced at SQL level.
  - aggregator deletes a row that falls below n_shops=3 after recompute.

Schema:
  - (vertical, action_kind, metric_kind) is the unique signal address.
  - lift_pct_avg + lift_pct_std + p_value carry the aggregate statistic.
  - n_shops + n_decisions carry sample-size context for downstream
    confidence weighting.
  - last_aggregated_at lets SIP / merchant_brain check freshness.

Revision ID: aa4_cross_shop_patterns
Revises: aa3_drop_stage2e_scoring_tables
"""
from alembic import op


revision = "aa4_cross_shop_patterns"
down_revision = "aa3_drop_stage2e_scoring_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS cross_shop_patterns (
            id BIGSERIAL PRIMARY KEY,
            vertical VARCHAR(64) NOT NULL,
            action_kind VARCHAR(64) NOT NULL,
            metric_kind VARCHAR(64) NOT NULL,
            lift_pct_avg DOUBLE PRECISION NOT NULL,
            lift_pct_std DOUBLE PRECISION,
            n_shops INTEGER NOT NULL,
            n_decisions INTEGER NOT NULL,
            p_value DOUBLE PRECISION,
            confidence VARCHAR(16) NOT NULL,
            last_aggregated_at TIMESTAMP NOT NULL DEFAULT now(),
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            CONSTRAINT cross_shop_patterns_n_shops_min
                CHECK (n_shops >= 3),
            CONSTRAINT cross_shop_patterns_unique_signal
                UNIQUE (vertical, action_kind, metric_kind)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_cross_shop_patterns_vertical_signal
        ON cross_shop_patterns (vertical, action_kind)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_cross_shop_patterns_vertical_last_agg
        ON cross_shop_patterns (vertical, last_aggregated_at)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_cross_shop_patterns_vertical_last_agg")
    op.execute("DROP INDEX IF EXISTS ix_cross_shop_patterns_vertical_signal")
    op.execute("DROP TABLE IF EXISTS cross_shop_patterns")
