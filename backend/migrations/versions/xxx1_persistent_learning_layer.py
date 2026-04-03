"""Create persistent learning layer — patch_fingerprints + system_lessons tables,
add affected_domain to bugfix_candidates, add missing feedback-loop indexes.

This migration enables:
  1. Patch fingerprint dedup — prevent retrying identical failing patches
  2. Institutional memory — lessons generated from measured outcomes
  3. Per-domain effectiveness tracking via affected_domain column
  4. Missing indexes for outcome/audit queries

Revision ID: xxx1_persistent_learning_layer
Revises: www1_tracker_delivery_method
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa

revision = "xxx1_persistent_learning_layer"
down_revision = "www1_tracker_delivery_method"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # Table 1: patch_fingerprints — immune system for failed approaches
    # -----------------------------------------------------------------------
    op.create_table(
        "patch_fingerprints",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("bugfix_candidate_id", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("measured_outcome", sa.String(32), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(32), nullable=True),
        sa.Column("source_ref", sa.String(256), nullable=True),
        sa.Column("affected_domain", sa.String(64), nullable=True),
        sa.Column("patch_files", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_patch_fp_fingerprint", "patch_fingerprints", ["fingerprint", "created_at"])
    op.create_index("ix_patch_fp_candidate", "patch_fingerprints", ["bugfix_candidate_id"])
    op.create_index("ix_patch_fp_outcome", "patch_fingerprints", ["outcome", "created_at"])
    op.create_index("ix_patch_fp_domain", "patch_fingerprints", ["affected_domain", "outcome"])

    # -----------------------------------------------------------------------
    # Table 2: system_lessons — persistent institutional memory
    # -----------------------------------------------------------------------
    op.create_table(
        "system_lessons",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("domain", sa.String(64), nullable=False),
        sa.Column("lesson_type", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=True),
        sa.Column("source_candidate_id", sa.Integer(), nullable=True),
        sa.Column("source_type", sa.String(32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_reinforced_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("dedup_key", sa.String(256), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lessons_domain_status", "system_lessons", ["domain", "status"])
    op.create_index("ix_lessons_type_status", "system_lessons", ["lesson_type", "status"])
    op.create_index("ix_lessons_confidence", "system_lessons", ["confidence", "status"])
    op.create_index("ix_lessons_dedup", "system_lessons", ["dedup_key"])
    op.create_index("ix_lessons_created", "system_lessons", ["created_at"])

    # -----------------------------------------------------------------------
    # Column: bugfix_candidates.affected_domain — per-domain effectiveness
    # -----------------------------------------------------------------------
    op.add_column(
        "bugfix_candidates",
        sa.Column("affected_domain", sa.String(64), nullable=True),
    )

    # -----------------------------------------------------------------------
    # Missing feedback-loop indexes
    # -----------------------------------------------------------------------
    op.create_index(
        "ix_bugfix_candidates_outcome",
        "bugfix_candidates",
        ["outcome_status", "outcome_measured_at"],
    )
    op.create_index(
        "ix_bugfix_candidates_domain",
        "bugfix_candidates",
        ["affected_domain", "outcome_status"],
    )
    op.create_index(
        "ix_audit_log_actor",
        "audit_log",
        ["actor_name", "created_at"],
    )
    op.create_index(
        "ix_ops_alerts_source_type",
        "ops_alerts",
        ["source", "alert_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ops_alerts_source_type", table_name="ops_alerts")
    op.drop_index("ix_audit_log_actor", table_name="audit_log")
    op.drop_index("ix_bugfix_candidates_domain", table_name="bugfix_candidates")
    op.drop_index("ix_bugfix_candidates_outcome", table_name="bugfix_candidates")
    op.drop_column("bugfix_candidates", "affected_domain")
    op.drop_index("ix_lessons_created", table_name="system_lessons")
    op.drop_index("ix_lessons_dedup", table_name="system_lessons")
    op.drop_index("ix_lessons_confidence", table_name="system_lessons")
    op.drop_index("ix_lessons_type_status", table_name="system_lessons")
    op.drop_index("ix_lessons_domain_status", table_name="system_lessons")
    op.drop_table("system_lessons")
    op.drop_index("ix_patch_fp_domain", table_name="patch_fingerprints")
    op.drop_index("ix_patch_fp_outcome", table_name="patch_fingerprints")
    op.drop_index("ix_patch_fp_candidate", table_name="patch_fingerprints")
    op.drop_index("ix_patch_fp_fingerprint", table_name="patch_fingerprints")
    op.drop_table("patch_fingerprints")
