"""drop 4 Stage 2-E scoring/learning tables

Stage 2-E supersession final wave (2026-05-09): scoring_calibration.py
+ 4 model files deleted along with their consumers (sip_engine,
sentry_triage, telegram_agent, invariant_monitor, system_diagnostic,
retention_task, agent_worker, aggregation_worker, merchant_chatbot,
learning_isolation). All consumers were dead-walking — querying tables
fed by the already-deleted bugfix_pipeline / adversarial_reviewer /
project_brain.

Founder direttiva 2026-05-09 («Quale direzione intraprende un CTO top1
al mondo? Non devi chiederlo a me» + «Ma se siamo a 0 consumers visto
che l'unico merchant siamo noi con uno shop») authorizes the
destructive drop. Historical rows are forensics nobody reads — keeping
the schema is exactly the cruft we're eliminating.

Tables dropped:
  - bugfix_candidates          (queried by 8 consumers, all dead-walking)
  - system_lessons              (queried by sentry_triage related-lessons)
  - autofix_promotions          (zero readers post Stage 2-E)
  - reviewer_assessments        (queried by telegram_agent reviewer-context
                                  helpers, removed in same commit)

Columns dropped:
  - sentry_incidents.linked_bugfix_candidate_id
  - support_incidents.linked_bugfix_candidate_id
  - support_incidents.linked_evolution_proposal_id
                                  (FK to evolution_proposals which was
                                  dropped in aa2_drop_stage2e_orphan_tables)

Revision ID: aa3_drop_stage2e_scoring_tables
Revises: aa2_drop_stage2e_orphan_tables
"""
from alembic import op
import sqlalchemy as sa

revision = "aa3_drop_stage2e_scoring_tables"
down_revision = "aa2_drop_stage2e_orphan_tables"
branch_labels = None
depends_on = None


_DROP_TABLES = [
    "bugfix_candidates",
    "system_lessons",
    "autofix_promotions",
    "reviewer_assessments",
]

_DROP_COLUMNS = [
    ("sentry_incidents", "linked_bugfix_candidate_id"),
    ("support_incidents", "linked_bugfix_candidate_id"),
    ("support_incidents", "linked_evolution_proposal_id"),
]


def upgrade() -> None:
    # Drop FK columns first so the table-DROP doesn't fail on dependent
    # constraints. CASCADE on the table drop covers any leftover.
    for table, column in _DROP_COLUMNS:
        op.execute(sa.text(
            f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}"
        ))
    for table in _DROP_TABLES:
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))


def downgrade() -> None:
    raise NotImplementedError(
        "aa3_drop_stage2e_scoring_tables is forward-only. Restore "
        "from a pre-2026-05-09 DB snapshot to recover the dropped "
        "schema + rows."
    )
