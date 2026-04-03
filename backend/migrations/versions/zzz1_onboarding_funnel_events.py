"""onboarding funnel events

Revision ID: zzz1_onboarding_funnel_events
Revises: yyy1_diff_fingerprint_lesson_validation
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa

revision = "zzz1_onboarding_funnel_events"
down_revision = "yyy1_diff_fingerprint_lesson_validation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "onboarding_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("shop_domain", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("elapsed_seconds", sa.Float(), nullable=True),
        sa.Column("session_number", sa.Integer(), nullable=True),
        sa.Column("context", sa.String(512), nullable=True),
    )
    op.create_index("ix_onboarding_events_shop", "onboarding_events", ["shop_domain"])
    op.create_index("ix_onboarding_events_type", "onboarding_events", ["event_type"])
    op.create_index("ix_onboarding_events_shop_type", "onboarding_events", ["shop_domain", "event_type"])
    op.create_index("ix_onboarding_events_created", "onboarding_events", ["created_at"])
    # Partial unique index: enforce milestone idempotency at the DB level.
    # Only covers milestone event_types (not interaction events which allow duplicates).
    # This prevents race conditions where concurrent requests both pass the
    # application-level dedup check and insert duplicate milestones.
    op.execute(sa.text("""
        CREATE UNIQUE INDEX uq_onboarding_milestone_per_shop
        ON onboarding_events (shop_domain, event_type)
        WHERE event_type IN (
            'install_completed', 'setup_completed', 'pixel_viewed',
            'pixel_copy_clicked', 'pixel_confirmed', 'pixel_detected',
            'first_visitor_detected', 'first_insight_generated',
            'onboarding_complete'
        )
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_onboarding_milestone_per_shop"))
    op.drop_index("ix_onboarding_events_created", "onboarding_events")
    op.drop_index("ix_onboarding_events_shop_type", "onboarding_events")
    op.drop_index("ix_onboarding_events_type", "onboarding_events")
    op.drop_index("ix_onboarding_events_shop", "onboarding_events")
    op.drop_table("onboarding_events")
