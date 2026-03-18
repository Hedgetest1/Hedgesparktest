"""create opportunity_signals table

Revision ID: c8f3a2b1d4e9
Revises: 3ecadb4f7e1d
Create Date: 2026-03-17

Stores persisted output from the rule-based opportunity detection engine
(Batch 8/9).  One row per (shop_domain, product_url, signal_type).

Indexes:
  ix_opportunity_signals_shop_domain  — fast per-shop reads
  ix_opportunity_signals_refreshed_at — fast stale-signal cleanup queries
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8f3a2b1d4e9"
down_revision: Union[str, None] = "3ecadb4f7e1d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "opportunity_signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("shop_domain", sa.String(), nullable=False),
        sa.Column("product_url", sa.String(), nullable=False),
        sa.Column("signal_type", sa.String(), nullable=False),
        sa.Column(
            "signal_strength",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("explanation", sa.String(), nullable=True),
        sa.Column(
            "detected_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "refreshed_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shop_domain",
            "product_url",
            "signal_type",
            name="uq_opportunity_signal_shop_product_type",
        ),
    )
    op.create_index(
        "ix_opportunity_signals_shop_domain",
        "opportunity_signals",
        ["shop_domain"],
    )
    op.create_index(
        "ix_opportunity_signals_refreshed_at",
        "opportunity_signals",
        ["refreshed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_opportunity_signals_refreshed_at",
        table_name="opportunity_signals",
    )
    op.drop_index(
        "ix_opportunity_signals_shop_domain",
        table_name="opportunity_signals",
    )
    op.drop_table("opportunity_signals")
