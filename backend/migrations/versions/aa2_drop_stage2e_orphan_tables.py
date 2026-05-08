"""drop 7 zero-importer Stage 2-E orphan tables

Stage 2-E supersession (2026-05-07/08) deleted bugfix_pipeline +
adversarial_reviewer + meta_reviewer + project_brain + evolution_engine
+ promotion_pipeline. The 7 SQLAlchemy models that fed those services
became zero-importer in app/ + scripts/ + tests/ (verified via
`grep -rln 'from app.models.<X>\\|app\\.models\\.<X>\\b'` excluding
the model file itself + __init__.py + main.py registry).

Founder direttiva 2026-05-08 ("TIER 1 + TIER_2 #7 ALL approved" +
"Procedi") authorizes the destructive drop. Historical rows
(616 total across 5 of the 7 tables) are forensics nobody reads —
keeping them as orphan schema is exactly the cruft the code-jewel
directive eliminates.

Tables dropped:
  - adversarial_review_findings  (0 rows)
  - evolution_proposals          (327 rows historical)
  - merge_outcomes               (0 rows)
  - meta_reviews                 (6 rows historical)
  - model_upgrade_proposals      (4 rows historical — `__tablename__`
                                  on app.models.model_upgrade.ModelUpgradeProposal
                                  was `model_upgrade_proposals`, not
                                  `model_upgrades` as the original
                                  audit listed)
  - patch_fingerprints           (5 rows historical)
  - project_brain_snapshots      (274 rows historical)

Revision ID: aa2_drop_stage2e_orphan_tables
Revises: aa1_pro_perf_composite_indexes
"""
from alembic import op
import sqlalchemy as sa

revision = "aa2_drop_stage2e_orphan_tables"
down_revision = "aa1_pro_perf_composite_indexes"
branch_labels = None
depends_on = None


_DROP_TABLES = [
    "adversarial_review_findings",
    "evolution_proposals",
    "merge_outcomes",
    "meta_reviews",
    "model_upgrade_proposals",
    "patch_fingerprints",
    "project_brain_snapshots",
]


def upgrade() -> None:
    # `IF EXISTS` so the migration is idempotent across environments
    # where a table may have been dropped manually OR never created
    # (model_upgrades was listed in the original plan but doesn't exist).
    for table in _DROP_TABLES:
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))


def downgrade() -> None:
    # No-op: the dropped tables had zero writers post Stage 2-E. Restoring
    # an empty schema would not restore the deleted data, and the schema
    # itself is no longer referenced by any Python model. Operators
    # rolling back this migration should restore from a pre-drop DB
    # snapshot if they need the historical rows.
    raise NotImplementedError(
        "aa2_drop_stage2e_orphan_tables is forward-only. Restore "
        "from a pre-2026-05-08 DB snapshot to recover the dropped "
        "schema + rows."
    )
