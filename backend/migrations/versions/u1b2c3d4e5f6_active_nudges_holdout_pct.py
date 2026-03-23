"""u1b2c3d4e5f6_active_nudges_holdout_pct

Add holdout_pct column to active_nudges for holdout/control measurement.

holdout_pct (integer, 0–100, default 0):
  0  — holdout disabled (default). All eligible visitors see the nudge.
       Existing nudges are fully backward compatible — they behave identically
       to before this migration.
  >0 — percentage of eligible visitors deterministically assigned to the
       holdout (control) group at delivery time.  Holdout visitors are
       suppressed from seeing the nudge; their visit + purchase behavior is
       recorded as the control baseline.

Assignment is deterministic and stable per (visitor_id, nudge_id):
    int(md5(f"{visitor_id}:holdout:{nudge_id}")[:8], 16) % 100 < holdout_pct

This hash namespace ("holdout:") is intentionally different from the variant
assignment namespace ("{visitor_id}:{nudge_id}") to ensure the two
assignments are independent — a visitor's holdout/exposed status is
unrelated to which copy variant they would have seen.

Assignment step ordering (enforced in app/api/nudges.py):
    Step 1 — behavioral eligibility gate  (existing: nudge_gating.py)
    Step 2 — holdout check               (new: this feature)
    Step 3 — copy variant assignment      (existing: _assign_variant)

Only eligible-and-exposed visitors reach step 3.  Holdout group is never
assigned a copy variant.  No contamination between the two A/B arms and
the holdout/control dimension.

Lift measurement (app/services/nudge_measurement.py):
    get_nudge_lift_report() compares:
      exposed group  — visitors with a 'shown' nudge_event
      holdout group  — visitors with a 'holdout_assigned' nudge_event
    on post-event purchase rate within a configurable attribution window.
    Result is labeled quasi_experimental_holdout — not a true RCT.

Revision ID: u1b2c3d4e5f6
Revises:     t6f7a8b9c0d1
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision      = "u1b2c3d4e5f6"
down_revision = "t6f7a8b9c0d1"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.add_column(
        "active_nudges",
        sa.Column(
            "holdout_pct",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment=(
                "Percentage of eligible visitors assigned to holdout (control) group. "
                "0 = holdout disabled (default, backward compatible). "
                "1-100 = enable holdout; that fraction of eligible visitors are "
                "deterministically suppressed and recorded for lift measurement. "
                "Recommended range: 10-25. Assignment: "
                "int(md5(visitor_id:holdout:nudge_id)[:8], 16) % 100 < holdout_pct."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("active_nudges", "holdout_pct")
