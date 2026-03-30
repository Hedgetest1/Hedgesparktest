"""Create active_model_configs table for persistent model selection.

Revision ID: kkk1_active_model_configs
Revises: jjj1_model_upgrade_proposals
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "kkk1_active_model_configs"
down_revision = "jjj1_model_upgrade_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "active_model_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("module", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("activated_at", sa.DateTime(), nullable=False),
        sa.Column("activated_by", sa.String(128), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(), nullable=True),
        sa.Column("replaced_by_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_active_model_module_active", "active_model_configs", ["module", "is_active"])

    # Seed defaults
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    op.execute(
        f"INSERT INTO active_model_configs (module, provider, model_name, is_active, activated_at, activated_by) VALUES "
        f"('orchestrator', 'anthropic', 'claude-sonnet-4-20250514', true, '{now}', 'system_default'), "
        f"('bugfix_proposal', 'anthropic', 'claude-sonnet-4-20250514', true, '{now}', 'system_default'), "
        f"('evolution_audit', 'anthropic', 'claude-sonnet-4-20250514', true, '{now}', 'system_default')"
    )


def downgrade() -> None:
    op.drop_index("ix_active_model_module_active")
    op.drop_table("active_model_configs")
