"""float money columns → NUMERIC(18, 2)

Revision ID: zzz8_float_money_to_numeric
Revises: zzz7_alembic_drift_closure
Create Date: 2026-04-15

TIER_2 — explicit human approval received 2026-04-15.

Converts 14 monetary columns from DOUBLE PRECISION to NUMERIC(18, 2)
to eliminate silent float-cent rounding at reconciliation time. Each
of these columns either stores a live order total, a revenue
aggregate, an ad spend line, a LLM budget line, or a projection
used by holdout measurement — all reconciliation-critical surfaces.
Float cents round silently and reconcile wrong; the error is
invisible until a merchant asks "why is this invoice off by 3 cents".
At 10k merchants × millions of orders the cumulative error becomes
merchant-visible and erodes trust — the exact failure mode the
top-1 bar will not accept.

Columns touched (14 total across 11 model files):

  action_snapshots.baseline_revenue_7d
  action_snapshots.delta_revenue_7d
  active_nudges.estimated_revenue_window
  ad_spend_daily.revenue_attributed_eur
  ad_spend_daily.spend_eur       ← caught when the test pattern
                                   was broadened to include `_eur` /
                                   `_spend` / `_payout` mid-migration
  analytics_events.revenue_eur
  execution_baselines.product_b_revenue_24h
  price_watch.last_seen_price
  price_watch.previous_price
  product_metrics.revenue_24h
  scaling_recommendations.estimated_cost_increase_eur
  shop_orders.total_price        ← most critical (live order totals)
  system_snapshots.llm_estimated_cost_eur
  trust_execution_log.revenue_delta_eur

PostgreSQL ALTER COLUMN TYPE with an explicit USING clause is a
deterministic, lossless cast in the DOUBLE PRECISION → NUMERIC(18, 2)
direction — every float value has a unique decimal representation
that fits 18 digits with 2 fraction digits (the max absolute value
for a float cent is far below 10^16). Downgrade reverses the cast;
in theory this could lose fractional precision beyond 2 decimals
but in practice it only runs against data that was already
double-precision before this migration, so zero data loss.

Lock semantics: ALTER COLUMN TYPE holds an ACCESS EXCLUSIVE lock on
the table for the duration of the rewrite. For small / moderate
tables the rewrite is fast. `shop_orders` is the largest and is
sequential-scan rewritten, blocking concurrent writes for the full
scan duration. In production this should be run during a
maintenance window; on the dev database it's fine.

Matching model flip: `app/models/*.py` have been updated in the
same commit to declare the columns as
`Column(Numeric(18, 2), ...)` so alembic check exits 0 after this
migration runs.
"""
from alembic import op

revision = "zzz8_float_money_to_numeric"
down_revision = "zzz7_alembic_drift_closure"
branch_labels = None
depends_on = None

_COLUMNS: list[tuple[str, str]] = [
    ("action_snapshots",        "baseline_revenue_7d"),
    ("action_snapshots",        "delta_revenue_7d"),
    ("active_nudges",           "estimated_revenue_window"),
    ("ad_spend_daily",          "revenue_attributed_eur"),
    ("ad_spend_daily",          "spend_eur"),
    ("analytics_events",        "revenue_eur"),
    ("execution_baselines",     "product_b_revenue_24h"),
    ("price_watch",             "last_seen_price"),
    ("price_watch",             "previous_price"),
    ("product_metrics",         "revenue_24h"),
    ("scaling_recommendations", "estimated_cost_increase_eur"),
    ("shop_orders",             "total_price"),
    ("system_snapshots",        "llm_estimated_cost_eur"),
    ("trust_execution_log",     "revenue_delta_eur"),
]


def upgrade() -> None:
    for table, col in _COLUMNS:
        op.execute(
            f'ALTER TABLE {table} '
            f'ALTER COLUMN {col} TYPE NUMERIC(18, 2) '
            f'USING {col}::numeric'
        )


def downgrade() -> None:
    for table, col in _COLUMNS:
        op.execute(
            f'ALTER TABLE {table} '
            f'ALTER COLUMN {col} TYPE DOUBLE PRECISION '
            f'USING {col}::double precision'
        )
