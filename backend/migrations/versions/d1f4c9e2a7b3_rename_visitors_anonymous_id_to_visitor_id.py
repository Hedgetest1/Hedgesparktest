"""rename visitors.anonymous_id to visitor_id

Revision ID: d1f4c9e2a7b3
Revises: c8f3a2b1d4e9
Create Date: 2026-03-18
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'd1f4c9e2a7b3'
down_revision = 'c8f3a2b1d4e9'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Drop old unique index
    op.drop_index('ix_visitors_anonymous_id', table_name='visitors')

    # 2. Rename column
    op.alter_column('visitors', 'anonymous_id', new_column_name='visitor_id')

    # 3. Set NOT NULL
    op.alter_column('visitors', 'visitor_id', nullable=False)

    # 4. Add composite unique constraint
    op.create_unique_constraint(
        'uq_visitor_shop',
        'visitors',
        ['visitor_id', 'shop_domain']
    )


def downgrade():
    # Reverse operations
    op.drop_constraint('uq_visitor_shop', 'visitors', type_='unique')

    op.alter_column('visitors', 'visitor_id', new_column_name='anonymous_id')

    op.create_index(
        'ix_visitors_anonymous_id',
        'visitors',
        ['anonymous_id'],
        unique=True
    )
