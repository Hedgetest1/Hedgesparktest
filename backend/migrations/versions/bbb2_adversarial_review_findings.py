"""Create adversarial_review_findings table — Sprint B of CTO-brain
pipeline upgrade (project_cto_brain_pipeline_gap.md).

Revision ID: bbb2_adversarial_review_findings
Revises: bbb1_bugfix_candidate_parent_id
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa

revision = "bbb2_adversarial_review_findings"
down_revision = "bbb1_bugfix_candidate_parent_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adversarial_review_findings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column(
            "bugfix_candidate_id", sa.Integer(),
            sa.ForeignKey(
                "bugfix_candidates.id",
                ondelete="CASCADE",
                name="fk_adv_review_findings_candidate",
            ),
            nullable=False,
        ),
        sa.Column("lens", sa.String(32), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("concern", sa.Text(), nullable=True),
        sa.Column("suggested_remediation", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False,
                  server_default="open"),
        sa.Column("llm_provider", sa.String(32), nullable=True),
        sa.Column("llm_model", sa.String(64), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("addressed_by_candidate_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_adv_review_findings_candidate",
        "adversarial_review_findings",
        ["bugfix_candidate_id", "lens"],
    )
    op.create_index(
        "ix_adv_review_findings_status",
        "adversarial_review_findings",
        ["status", "severity"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_adv_review_findings_status",
        table_name="adversarial_review_findings",
    )
    op.drop_index(
        "ix_adv_review_findings_candidate",
        table_name="adversarial_review_findings",
    )
    op.drop_table("adversarial_review_findings")
