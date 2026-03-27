"""Add composite index for eligibility lookups.

execution_audiences needs (shop_domain, visitor_id) for the
storefront eligibility endpoint to be efficient.
"""

from alembic import op

revision = "ss1_eligibility_index"
down_revision = "rr1_holdout_enforcement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_exec_aud_shop_visitor",
        "execution_audiences",
        ["shop_domain", "visitor_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_exec_aud_shop_visitor", table_name="execution_audiences")
