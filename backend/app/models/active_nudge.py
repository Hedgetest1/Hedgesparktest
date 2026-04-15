"""
active_nudge.py — Storefront nudge execution artifact.

One row = one live nudge configuration for a specific (shop, product) pair.

Lifecycle
---------
  active      → nudge is live; storefront script will render it
  expired     → expires_at has passed; set by the aggregation_worker sweep
  deactivated → manually deactivated by the system or merchant action

One active nudge per (shop_domain, product_url, action_type) at a time.
The nudge_engine enforces this at write time — if an active nudge for the
same triple already exists, it is refreshed (expires_at extended) rather
than replaced.  This prevents duplicate nudges on the same product.

copy_config (primary / backward-compatible)
--------------------------------------------
JSON-encoded dict containing the primary variant's storefront render payload:
    {
        "headline":          str,
        "subtext":           str | null,
        "badge":             str | null,
        "visitor_count":     int | null,
        "data_window_hours": int,
    }

copy_variants (A/B experiment payload)
--------------------------------------
JSON-encoded list of all experiment variants — populated when a nudge is
configured for A/B testing (migration t6f7a8b9c0d1+):

    [
        {
            "variant_name": "high_interest",
            "copy_config":  { headline, subtext, badge, visitor_count, data_window_hours }
        },
        {
            "variant_name": "social_proof",
            "copy_config":  { ... }
        }
    ]

When copy_variants is present and has >= 2 items:
  - GET /nudges/active assigns one variant per visitor deterministically
    via hash(visitor_id + ":" + nudge_id) % n_variants
  - The assigned variant_name is returned in the response and echoed back
    in nudge_events.event_meta by the storefront script
  - Per-variant stats are computed from nudge_events WHERE event_meta->>'copy_variant' = ?

When copy_variants is NULL (legacy nudges):
  - Single-variant behavior using copy_variant + copy_config fields
  - Backward compatible — no client or measurement changes needed

All copy values are derived from real behavioral data only.

holdout_pct — quasi-experimental control group
----------------------------------------------
Integer 0-100.  Default 0 (holdout disabled, backward compatible).

When > 0, a deterministic fraction of eligible visitors are assigned to
the holdout (control) group at delivery time.  Holdout visitors do NOT
see the nudge.  The system records a 'holdout_assigned' nudge_event for
each holdout visitor so post-event purchase behavior can be compared to
the exposed group's post-exposure purchase behavior.

Assignment is stable per (visitor_id, nudge_id):
    int(md5(f"{visitor_id}:holdout:{nudge_id}")[:8], 16) % 100 < holdout_pct

This hash namespace is intentionally different from the variant assignment
namespace so holdout/exposed status is independent of variant assignment.

Assignment order in GET /nudges/active:
    1. Behavioral eligibility gate  (nudge_gating.py)
    2. Holdout check                (this field, nudges.py)
    3. Copy variant assignment      (nudges.py, only for exposed visitors)

Holdout visitors never receive a copy variant.

trigger_source
--------------
"hot_segment_monitor"  — created automatically by the segment monitor worker
"manual"               — created manually by a Pro merchant or agent

action_task_id
--------------
FK (non-enforced) to action_tasks.id.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, Numeric, String, Text, text

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class ActiveNudge(Base):
    __tablename__ = "active_nudges"

    id            = Column(Integer, primary_key=True)

    shop_domain   = Column(String,  nullable=False)
    product_url   = Column(String,  nullable=False)
    action_type   = Column(String,  nullable=False)
    trigger_source = Column(String, nullable=False)

    # Primary / control variant — always populated; backward-compatible readers
    copy_variant  = Column(String,  nullable=False)   # high_interest | social_proof
    copy_config   = Column(Text,    nullable=False)   # JSON primary variant render payload

    # A/B experiment — all variant configs; NULL on legacy single-variant nudges
    # JSON: [{variant_name: str, copy_config: dict}, ...]
    copy_variants = Column(
        Text,
        nullable=True,
        comment=(
            "JSON array of all A/B copy variants: [{variant_name, copy_config}]. "
            "NULL on legacy single-variant nudges."
        ),
    )

    # Holdout / control group — quasi-experimental incremental lift measurement
    # 0 = disabled (default); 1-100 = % of eligible visitors assigned to holdout
    holdout_pct   = Column(
        Integer,
        nullable=False,
        default=0,
        comment=(
            "Percentage of eligible visitors assigned to holdout (control) group. "
            "0 = holdout disabled (default, backward compatible). "
            "1-100 = enable holdout; that fraction of eligible visitors are "
            "deterministically suppressed and recorded for lift measurement. "
            "Recommended range: 10-25. "
            "Assignment: int(md5(visitor_id:holdout:nudge_id)[:8], 16) % 100 < holdout_pct."
        ),
    )

    # Lifecycle
    status        = Column(String,  nullable=False, default="active")
    created_at    = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at    = Column(DateTime, nullable=False, default=utc_now_naive)
    expires_at    = Column(DateTime, nullable=False)
    deactivated_at = Column(DateTime, nullable=True)

    # Linkage
    action_task_id = Column(Integer, nullable=True)

    # AI composition state — True when nudge has baseline variants and needs
    # AI-composed replacements from the aggregation_worker loop
    ai_compose_pending       = Column(Boolean, nullable=True, default=False)

    # Bootstrap flag: manually-forced experiments excluded from SIP learning
    is_bootstrap             = Column(Boolean, nullable=False, default=False)

    # Segment context at time of last refresh
    visitor_count            = Column(Integer, nullable=True)
    estimated_revenue_window = Column(Numeric(18, 2), nullable=True)
    calibration_state        = Column(String,  nullable=True)

    __table_args__ = (
        Index("ix_active_nudges_shop_product", "shop_domain", "product_url"),
        Index("ix_active_nudges_shop_status",  "shop_domain", "status"),
        Index("ix_active_nudges_expires_at",   "expires_at"),
        Index("ix_active_nudges_ai_compose_pending", "ai_compose_pending"),
        Index(
            "ix_active_nudges_unique_active",
            "shop_domain", "product_url", "action_type",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    def copy_config_dict(self) -> dict:
        """Return primary copy_config as a Python dict."""
        try:
            return json.loads(self.copy_config)
        except (json.JSONDecodeError, TypeError):
            return {}

    def copy_variants_list(self) -> list[dict]:
        """
        Return the A/B variants list, or an empty list for legacy single-variant nudges.

        Each item: {"variant_name": str, "copy_config": dict}
        """
        if not self.copy_variants:
            return []
        try:
            parsed = json.loads(self.copy_variants)
            if isinstance(parsed, list) and len(parsed) >= 2:
                return parsed
            return []
        except (json.JSONDecodeError, TypeError):
            return []

    def is_ab_experiment(self) -> bool:
        """True when this nudge has 2+ copy variants configured for A/B testing."""
        return len(self.copy_variants_list()) >= 2

    def is_holdout_active(self) -> bool:
        """True when a holdout (control) group is configured for this nudge."""
        return (self.holdout_pct or 0) > 0
