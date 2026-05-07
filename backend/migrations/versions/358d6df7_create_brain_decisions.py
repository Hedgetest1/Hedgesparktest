"""create brain_decisions table — Brain Vero v0.1 ledger

Revision ID: 358d6df7
Revises: c4e5520c
Create Date: 2026-05-07

Backs the MerchantBrain coordination cycle (sense→synthesize→decide→
coordinate→learn) with a persistent ledger. Closes the founder direttiva
"shippa Brain Vero" — every brain tick that produces a decision writes a
row, every outcome window closes the LEARN loop.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "358d6df7"
down_revision = "c4e5520c"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE IF NOT EXISTS brain_decisions (
    id BIGSERIAL PRIMARY KEY,
    decision_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    shop_domain VARCHAR NOT NULL,
    sense_snapshot JSONB,
    synthesis VARCHAR(2000),
    action_kind VARCHAR(64) NOT NULL,
    action_payload JSONB,
    rationale VARCHAR(500),
    limb_dispatched VARCHAR(64),
    limb_response JSONB,
    expected_outcome_metric VARCHAR(64),
    outcome_window_hours INTEGER NOT NULL DEFAULT 24,
    baseline_value DOUBLE PRECISION,
    measured_value DOUBLE PRECISION,
    outcome_status VARCHAR(32),
    outcome_evaluated_at TIMESTAMP WITHOUT TIME ZONE
);
""")
    op.execute("""
CREATE INDEX IF NOT EXISTS ix_brain_decisions_shop_at
    ON brain_decisions (shop_domain, decision_at);
""")
    op.execute("""
CREATE INDEX IF NOT EXISTS ix_brain_decisions_status_at
    ON brain_decisions (outcome_status, decision_at);
""")
    op.execute("""
CREATE INDEX IF NOT EXISTS ix_brain_decisions_action
    ON brain_decisions (action_kind);
""")


def downgrade():
    op.execute("DROP TABLE IF EXISTS brain_decisions CASCADE;")
