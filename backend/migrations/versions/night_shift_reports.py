"""night_shift_reports — persistent archive for Night Shift Agent (Phase Ω⁵+)

Every generated Night Shift Agent report is archived here so:
  1. Redis can flush without losing history
  2. Calibration of Sleep Confidence has ground truth across a long window
  3. Audit can prove the agent's reasoning for any given day

One row per (shop_domain, day). Upsert on conflict so re-running the
generator replaces the cached doc but keeps the historical intent.

Non-destructive migration: new table, no existing data touched.

Revision ID: night_shift_reports
Revises: sprint_b_cost_config
Create Date: 2026-04-13
"""
from alembic import op
import sqlalchemy as sa


revision = "night_shift_reports"
down_revision = "sprint_b_cost_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "night_shift_reports",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("shop_domain", sa.String(255), nullable=False),
        sa.Column("day", sa.String(10), nullable=False),  # YYYY-MM-DD
        sa.Column("generated_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),  # quiet/active/alarm
        sa.Column("headline", sa.Text, nullable=True),
        sa.Column("narrative", sa.Text, nullable=True),
        sa.Column("sleep_confidence", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sleep_confidence_label", sa.String(120), nullable=True),
        sa.Column("top_action", sa.JSON, nullable=True),
        sa.Column("journal", sa.JSON, nullable=True),
        sa.Column("metrics", sa.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("shop_domain", "day", name="uq_night_shift_reports_shop_day"),
    )
    op.create_index(
        "ix_night_shift_reports_shop_created",
        "night_shift_reports",
        ["shop_domain", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_night_shift_reports_shop_created", table_name="night_shift_reports")
    op.drop_table("night_shift_reports")
