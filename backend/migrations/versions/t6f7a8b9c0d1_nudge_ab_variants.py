"""t6f7a8b9c0d1_nudge_ab_variants

Add copy_variants column to active_nudges for A/B copy experimentation.

copy_variants stores a JSON array of all configured copy variants for a nudge:

    [
        {
            "variant_name": "high_interest",
            "copy_config": { "headline": "...", "subtext": "...", "badge": "..." }
        },
        {
            "variant_name": "social_proof",
            "copy_config": { "headline": "...", "subtext": "...", "badge": "..." }
        }
    ]

When copy_variants is present and has >= 2 items, GET /nudges/active performs
deterministic variant assignment:
    idx = hash(visitor_id + ":" + nudge_id) % len(copy_variants)

The assigned variant's copy_config + variant_name are returned in the response
(same response shape as before — client is unaware of the experiment).

The returned variant_name is included in nudge_events.event_meta so per-variant
stats can be computed without schema changes to nudge_events.

Backward compatibility
----------------------
existing active_nudges rows have copy_variants = NULL — they use the legacy
copy_variant + copy_config columns (single-variant mode, unchanged behavior).
New nudges created after this migration always populate copy_variants.

The existing copy_variant + copy_config columns are kept as the "primary/control"
variant reference for backward compatibility and for consumers that only read
the simple fields.

Revision ID: t6f7a8b9c0d1
Revises: s5e6f7a8b9c0
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "t6f7a8b9c0d1"
down_revision = "s5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "active_nudges",
        sa.Column(
            "copy_variants",
            sa.Text(),
            nullable=True,
            comment=(
                "JSON array of all A/B copy variants: "
                "[{variant_name, copy_config}]. "
                "NULL on legacy single-variant nudges."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("active_nudges", "copy_variants")
