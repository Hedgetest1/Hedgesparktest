"""
nudge_engine.py — Active nudge lifecycle management.

Public interface
----------------
    create_or_refresh_nudge(
        db, shop_domain, product_url, action_type, trigger_source,
        visitor_count, revenue_window, calibration_state, action_task_id,
    ) -> tuple[ActiveNudge, bool]

        Upsert a nudge for the (shop_domain, product_url, action_type) triple.
        If an active, non-expired nudge already exists: extend its expires_at
        and update visitor_count + estimated_revenue_window (the latest scan
        data is always fresher than the stored data).
        If not: create a new nudge with a fresh TTL.
        Returns (nudge, True) if created, (nudge, False) if refreshed.
        Never raises — errors degrade to a logged warning.

    get_active_nudge(db, shop_domain, product_url) -> ActiveNudge | None

        Return the single active, non-expired nudge for a (shop, product) pair.
        Returns None if no nudge is active.  Used by the /nudges/active endpoint.

    expire_stale_nudges(db) -> int

        Mark nudges whose expires_at has passed as status='expired'.
        Returns the count of rows updated.  Called by aggregation_worker on
        every cycle.  Safe to call concurrently — UPDATE is atomic.

    deactivate_nudge(db, nudge_id, shop_domain) -> tuple[ActiveNudge | None, str | None]

        Deactivate a specific nudge (Pro management endpoint).
        Returns (nudge, None) on success, (None, "not_found") otherwise.

Copy variant system
-------------------
All nudges now carry BOTH variants — always. A/B assignment is handled at
delivery time (GET /nudges/active) using deterministic visitor_id hashing,
not at creation time.  This means:

  a. Every nudge runs as an A/B experiment by default.
  b. The nudge_engine does NOT pick a winner — that is the measurement layer's
     job.  The engine only builds all truthful variants.
  c. The segment monitor does not need to change — it calls
     create_or_refresh_nudge() and the engine handles variant building.

Two variants (v1):
  high_interest  — "High interest right now" / badge "Popular"
  social_proof   — "Popular choice" / badge "Trending"

Both variants use the same real visitor_count and data_window_hours.
The difference is only in the framing of the claim.

Copy truthfulness rules (unchanged):
  ✓  Real visitor_count from the hot segment (72-hour window)
  ✓  data_window_hours qualifies the claim ("in the past 3 days")
  ✗  Never claim "X people viewing right now" (implies live concurrent viewers)
  ✗  Never invent inventory scarcity
  ✗  Never claim "limited time offer" without a real discount

Nudge TTL
---------
Default: NUDGE_TTL_HOURS = 4.

A nudge expires 4 hours after creation (or the last refresh).  The segment
monitor refreshes every 5 minutes while hot conditions persist.

One-nudge rule
--------------
One active nudge per (shop_domain, product_url, action_type) at a time.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.active_nudge import ActiveNudge

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NUDGE_TTL_HOURS: float = 4.0

# Minimum visitor count to include the real number in copy text.
# Below this, generic language is used to avoid surfacing small counts.
_MIN_VISITORS_FOR_COUNT_COPY: int = 3

# Maximum inventory quantity to trigger real scarcity copy.
# Only applied when inventory_count is provided from the Shopify Admin API.
# NEVER apply scarcity copy without a real inventory_count value.
_LOW_STOCK_THRESHOLD: int = 10

# The behavioral data window used in copy to qualify claims.
# Must match HOT_SEGMENT_WINDOW_HOURS in segment_monitor_worker.py.
_DATA_WINDOW_HOURS: int = 72

# Variant names — defined here so callers can reference them by constant
VARIANT_HIGH_INTEREST = "high_interest"
VARIANT_SOCIAL_PROOF  = "social_proof"

# Ordered list of variants for A/B assignment.
# Index 0 = control.  Order is stable — never reorder this list.
AB_VARIANTS: list[str] = [VARIANT_HIGH_INTEREST, VARIANT_SOCIAL_PROOF]


# ---------------------------------------------------------------------------
# Copy builder — ONLY truthful claims allowed
# ---------------------------------------------------------------------------

def _build_copy_config(
    variant_name:     str,
    visitor_count:    Optional[int],
    revenue_window:   Optional[float],   # unused in copy for now; kept for future
    inventory_count:  Optional[int] = None,
) -> dict:
    """
    Build the copy_config payload for one specific variant.

    All claims are grounded in real behavioral data passed from the hot-segment
    scan.  No synthetic numbers, no invented scarcity, no fake urgency.

    visitor_count is the real hot segment size from segment_product_visitors()
    over the last HOT_SEGMENT_WINDOW_HOURS hours.  Included in copy only when
    >= _MIN_VISITORS_FOR_COUNT_COPY (avoids surfacing embarrassingly small numbers).

    inventory_count is the REAL stock level from the Shopify Admin API.
    When provided and <= _LOW_STOCK_THRESHOLD, a scarcity badge is added.
    NEVER generate scarcity copy without a real inventory_count — the comment
    in the docstring is a hard rule, not a guideline.
    """
    has_count   = bool(visitor_count and visitor_count >= _MIN_VISITORS_FOR_COUNT_COPY)
    days        = _DATA_WINDOW_HOURS // 24
    low_stock   = (inventory_count is not None and 0 < inventory_count <= _LOW_STOCK_THRESHOLD)

    if variant_name == VARIANT_SOCIAL_PROOF:
        headline = "Popular choice"
        badge    = "Trending"
        subtext  = (
            f"{visitor_count} people have been actively engaged with this "
            f"product in the past {days} days."
        ) if has_count else (
            "This product is attracting significant interest from recent visitors."
        )
    else:
        # VARIANT_HIGH_INTEREST — the default / control
        headline = "High interest right now"
        badge    = "Popular"
        subtext  = (
            f"{visitor_count} people have been actively exploring this product "
            f"in the past {days} days."
        ) if has_count else (
            "This product is seeing strong interest from recent visitors."
        )

    # Real scarcity badge — ONLY when backed by actual inventory data from Shopify Admin API.
    # inventory_count=None means no Admin API data available; never assume low stock.
    scarcity_text = None
    if low_stock:
        scarcity_text = f"Only {inventory_count} left in stock."
        # Scarcity overrides the badge when stock is genuinely low
        badge = "Low stock"

    config = {
        "headline":          headline,
        "subtext":           subtext,
        "badge":             badge,
        "visitor_count":     visitor_count if has_count else None,
        "data_window_hours": _DATA_WINDOW_HOURS,
    }

    if scarcity_text:
        config["scarcity_text"] = scarcity_text
        config["inventory_count"] = inventory_count

    return config


def _build_all_variants(
    visitor_count:   Optional[int],
    revenue_window:  Optional[float],
    inventory_count: Optional[int] = None,
) -> list[dict]:
    """
    Build copy configs for ALL experiment variants.

    Returns a list ordered by AB_VARIANTS:
        [
            {"variant_name": "high_interest", "copy_config": {...}},
            {"variant_name": "social_proof",  "copy_config": {...}},
        ]

    Both variants receive the same visitor_count, revenue_window, and
    inventory_count — the A/B experiment isolates copy framing, not data.

    inventory_count: real stock level from Shopify Admin API.  When provided
    and <= _LOW_STOCK_THRESHOLD, scarcity copy is added.  When None, no
    scarcity copy is generated.
    """
    return [
        {
            "variant_name": v,
            "copy_config":  _build_copy_config(v, visitor_count, revenue_window, inventory_count),
        }
        for v in AB_VARIANTS
    ]


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def create_or_refresh_nudge(
    db:               Session,
    shop_domain:      str,
    product_url:      str,
    action_type:      str,
    trigger_source:   str,
    visitor_count:    Optional[int],
    revenue_window:   Optional[float],
    calibration_state: str,
    action_task_id:   Optional[int]   = None,
    prebuilt_variants: Optional[list] = None,
    holdout_pct:       int            = 0,
    inventory_count:  Optional[int]   = None,
) -> tuple[ActiveNudge, bool]:
    """
    Upsert an active nudge for (shop_domain, product_url, action_type).

    Refresh path (existing active nudge found):
      - Extend expires_at to now + NUDGE_TTL_HOURS.
      - Rebuild copy configs for both variants with fresh visitor_count.
      - Returns (nudge, False).

    Create path (no active nudge, or only expired/deactivated ones):
      - Create a new nudge with full TTL and both variants.
      - Returns (nudge, True).

    copy_variant + copy_config: always set to the control variant (index 0)
    for backward compatibility with any reader that only inspects these fields.

    copy_variants: JSON list of all variants — used by GET /nudges/active for
    deterministic A/B assignment.

    On any error: logs the exception and re-raises.
    """
    now     = datetime.now(timezone.utc).replace(tzinfo=None)
    new_ttl = now + timedelta(hours=NUDGE_TTL_HOURS)

    # Use pre-built variants when provided (AI composer path);
    # fall back to rule-based builder (system/worker path).
    # inventory_count is only passed to the rule-based builder — AI composer
    # variants are pre-built with whatever data was available at compose time.
    all_variants    = prebuilt_variants if prebuilt_variants else _build_all_variants(visitor_count, revenue_window, inventory_count)
    primary_variant = all_variants[0]   # first variant = control = index 0

    copy_variants_json = json.dumps(all_variants)

    # Check for existing active, non-expired nudge
    existing: Optional[ActiveNudge] = (
        db.query(ActiveNudge)
        .filter(
            ActiveNudge.shop_domain == shop_domain,
            ActiveNudge.product_url == product_url,
            ActiveNudge.action_type == action_type,
            ActiveNudge.status      == "active",
            ActiveNudge.expires_at  >  now,
        )
        .first()
    )

    if existing is not None:
        existing.expires_at               = new_ttl
        existing.updated_at               = now
        existing.visitor_count            = visitor_count
        existing.estimated_revenue_window = revenue_window
        existing.calibration_state        = calibration_state
        # Update primary variant fields (backward compat)
        existing.copy_variant             = primary_variant["variant_name"]
        existing.copy_config              = json.dumps(primary_variant["copy_config"])
        # Rebuild/replace all variants
        existing.copy_variants            = copy_variants_json
        # Update holdout_pct only when explicitly supplied (non-zero)
        if holdout_pct > 0:
            existing.holdout_pct = holdout_pct

        db.commit()
        db.refresh(existing)

        log.info(
            "nudge_engine: REFRESHED nudge_id=%d shop=%s product=%s "
            "visitors=%s revenue=%.2f variants=%d expires_at=%s",
            existing.id, shop_domain, product_url,
            visitor_count, revenue_window or 0,
            len(all_variants), new_ttl.isoformat(),
        )
        return existing, False

    # Create new nudge
    nudge = ActiveNudge(
        shop_domain              = shop_domain,
        product_url              = product_url,
        action_type              = action_type,
        trigger_source           = trigger_source,
        copy_variant             = primary_variant["variant_name"],
        copy_config              = json.dumps(primary_variant["copy_config"]),
        copy_variants            = copy_variants_json,
        holdout_pct              = holdout_pct,
        status                   = "active",
        created_at               = now,
        updated_at               = now,
        expires_at               = new_ttl,
        deactivated_at           = None,
        action_task_id           = action_task_id,
        visitor_count            = visitor_count,
        estimated_revenue_window = revenue_window,
        calibration_state        = calibration_state,
    )
    db.add(nudge)
    db.commit()
    db.refresh(nudge)

    log.info(
        "nudge_engine: CREATED nudge_id=%d shop=%s product=%s "
        "variants=%d visitors=%s revenue=%.2f task_id=%s expires_at=%s",
        nudge.id, shop_domain, product_url,
        len(all_variants), visitor_count, revenue_window or 0,
        action_task_id, new_ttl.isoformat(),
    )
    return nudge, True


def get_active_nudge(
    db:          Session,
    shop_domain: str,
    product_url: str,
) -> Optional[ActiveNudge]:
    """
    Return the active, non-expired nudge for a (shop, product) pair.

    Used by GET /nudges/active — the storefront polling endpoint.
    Returns None when no nudge is live (renders nothing client-side).
    """
    return (
        db.query(ActiveNudge)
        .filter(
            ActiveNudge.shop_domain == shop_domain,
            ActiveNudge.product_url == product_url,
            ActiveNudge.status      == "active",
            ActiveNudge.expires_at  >  datetime.now(timezone.utc).replace(tzinfo=None),
        )
        .order_by(ActiveNudge.created_at.desc())
        .first()
    )


def expire_stale_nudges(db: Session) -> int:
    """
    Mark nudges whose expires_at has passed as status='expired'.

    Called by the aggregation_worker on every cycle.
    Returns the number of rows updated.
    """
    result = db.execute(
        text("""
            UPDATE active_nudges
               SET status     = 'expired',
                   updated_at = now()
             WHERE status     = 'active'
               AND expires_at < now()
        """)
    )
    db.commit()
    count = result.rowcount

    if count > 0:
        log.info("nudge_engine: expired %d stale nudges", count)

    return count


def deactivate_nudge(
    db:          Session,
    nudge_id:    int,
    shop_domain: str,
) -> tuple[Optional[ActiveNudge], Optional[str]]:
    """
    Deactivate a specific nudge by ID, scoped to the shop.

    Returns:
      (nudge, None)        — deactivated successfully
      (None, "not_found")  — no nudge with this ID belonging to this shop
    """
    nudge = (
        db.query(ActiveNudge)
        .filter(
            ActiveNudge.id          == nudge_id,
            ActiveNudge.shop_domain == shop_domain,
        )
        .first()
    )

    if nudge is None:
        return None, "not_found"

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    nudge.status         = "deactivated"
    nudge.deactivated_at = now
    nudge.updated_at     = now

    db.commit()
    db.refresh(nudge)

    log.info(
        "nudge_engine: DEACTIVATED nudge_id=%d shop=%s product=%s",
        nudge.id, shop_domain, nudge.product_url,
    )
    return nudge, None


def list_active_nudges(
    db:          Session,
    shop_domain: str,
    status:      Optional[str] = "active",
    limit:       int           = 50,
) -> list[ActiveNudge]:
    """
    Return nudges for a shop, newest first.

    status=None returns all statuses.
    Used by GET /pro/nudges.
    """
    q = (
        db.query(ActiveNudge)
        .filter(ActiveNudge.shop_domain == shop_domain)
    )
    if status:
        if status == "active":
            q = q.filter(
                ActiveNudge.status     == "active",
                ActiveNudge.expires_at >  datetime.now(timezone.utc).replace(tzinfo=None),
            )
        else:
            q = q.filter(ActiveNudge.status == status)

    return q.order_by(ActiveNudge.created_at.desc()).limit(limit).all()
