"""Create project_brain_snapshots and reviewer_assessments tables.

Revision ID: ooo1_project_brain
Revises: nnn1_evolution_gc_lifecycle
Create Date: 2026-03-28
"""
from alembic import op
import sqlalchemy as sa

revision = "ooo1_project_brain"
down_revision = "nnn1_evolution_gc_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_brain_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("snapshot_type", sa.String(16), nullable=False),
        sa.Column("codebase_json", sa.Text(), nullable=True),
        sa.Column("runtime_json", sa.Text(), nullable=True),
        sa.Column("total_files", sa.Integer(), nullable=True),
        sa.Column("critical_files", sa.Integer(), nullable=True),
        sa.Column("open_alerts", sa.Integer(), nullable=True),
        sa.Column("open_bugfixes", sa.Integer(), nullable=True),
        sa.Column("open_evolution", sa.Integer(), nullable=True),
        sa.Column("constitution_version", sa.String(16), nullable=False, server_default="v1"),
    )
    op.create_index("ix_brain_snapshots_type_created", "project_brain_snapshots",
                    ["snapshot_type", "created_at"])

    op.create_table(
        "reviewer_assessments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("strategic_alignment", sa.String(16), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("auto_approvable", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("notes_json", sa.Text(), nullable=True),
        sa.Column("blocking_concerns_json", sa.Text(), nullable=True),
        sa.Column("affected_domains_json", sa.Text(), nullable=True),
        sa.Column("reviewer_mode", sa.String(16), nullable=False),
        sa.Column("brain_snapshot_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_reviewer_entity", "reviewer_assessments",
                    ["entity_type", "entity_id"])
    op.create_index("ix_reviewer_verdict", "reviewer_assessments",
                    ["verdict", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_reviewer_verdict")
    op.drop_index("ix_reviewer_entity")
    op.drop_table("reviewer_assessments")
    op.drop_index("ix_brain_snapshots_type_created")
    op.drop_table("project_brain_snapshots")
