"""create merchants table

Revision ID: aefbbe8acc06
Revises:
Create Date: 2026-03-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic
revision: str = "aefbbe8acc06"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "merchants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("shop_domain", sa.String(), nullable=False),
        sa.Column("access_token", sa.String(), nullable=True),
        sa.Column("plan", sa.String(), nullable=False, server_default="lite"),
        sa.Column(
            "installed_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "billing_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shop_domain", name="uq_merchants_shop_domain"),
    )
    op.create_index(
        "ix_merchants_shop_domain",
        "merchants",
        ["shop_domain"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_merchants_shop_domain", table_name="merchants")
    op.drop_table("merchants")
