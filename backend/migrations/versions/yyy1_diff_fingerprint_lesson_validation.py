"""Add diff-level fingerprinting, lesson promotion validation, and lesson effectiveness tracking.

1. patch_fingerprints.diff_fingerprint — normalized diff hash (strips whitespace/comments/headers)
2. system_lessons.promotion_status — pending_promotion | promoted | rejected_promotion | NULL
3. system_lessons.promoted_at — when promotion was confirmed (auto or human)
4. system_lessons.promotion_decided_by — operator | auto_confirm | NULL
5. bugfix_candidates.lesson_ids_used — JSON list of lesson IDs injected into proposal context

Revision ID: yyy1_diff_fingerprint_lesson_validation
Revises: xxx1_persistent_learning_layer
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa

revision = "yyy1_diff_fingerprint_lesson_validation"
down_revision = "xxx1_persistent_learning_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # patch_fingerprints: normalized diff fingerprint for semantic dedup
    op.add_column(
        "patch_fingerprints",
        sa.Column("diff_fingerprint", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_patch_fp_diff_fingerprint",
        "patch_fingerprints",
        ["diff_fingerprint", "created_at"],
    )

    # system_lessons: promotion validation columns
    op.add_column(
        "system_lessons",
        sa.Column("promotion_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "system_lessons",
        sa.Column("promoted_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "system_lessons",
        sa.Column("promotion_decided_by", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_lessons_promotion_status",
        "system_lessons",
        ["promotion_status"],
    )

    # bugfix_candidates: lesson effectiveness tracking
    op.add_column(
        "bugfix_candidates",
        sa.Column("lesson_ids_used", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bugfix_candidates", "lesson_ids_used")
    op.drop_index("ix_lessons_promotion_status", table_name="system_lessons")
    op.drop_column("system_lessons", "promotion_decided_by")
    op.drop_column("system_lessons", "promoted_at")
    op.drop_column("system_lessons", "promotion_status")
    op.drop_index("ix_patch_fp_diff_fingerprint", table_name="patch_fingerprints")
    op.drop_column("patch_fingerprints", "diff_fingerprint")
