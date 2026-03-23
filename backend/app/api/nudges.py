"""
nudges.py — Nudge delivery and management API.

Public endpoint (no auth)
--------------------------
GET /nudges/active?shop=<domain>&product_url=<path>[&visitor_id=<uuid>]

    Called by spark-nudge.js on every Shopify product page load.

    Without visitor_id (legacy / fallback mode):
      Returns nudge config if an active nudge exists for the product.
      All visitors see the same copy (control variant = index 0).
      Holdout is skipped (cannot assign without identity).
      Backward compatible.

    With visitor_id (visitor-level gating + holdout mode — default in v2+ script):
      1. Evaluates the visitor's behavioral quality against the shop's
         empirical calibration thresholds before returning nudge config.
      2. If eligible AND nudge has holdout_pct > 0: runs holdout assignment step.
             Holdout assignment: int(md5(visitor_id:holdout:nudge_id)[:8], 16) % 100
             If result < holdout_pct → visitor is in holdout group:
               - Server records a 'holdout_assigned' NudgeEvent.
               - Response: { active, eligible, render_allowed: false, holdout: true }
               - spark-nudge.js v5 suppresses rendering.
               - No copy_config or variant assigned.
      3. If eligible AND not in holdout: assigns one copy variant deterministically
         via hash(visitor_id + ":" + nudge_id) % n_variants.
         The same visitor always gets the same variant — stable across refreshes.
      4. Returns the assigned variant's copy_config + variant_name.

    Assignment step ordering (enforced):
        Step 1 — behavioral eligibility gate  (nudge_gating.py)
        Step 2 — holdout check                (this file, _assign_holdout)
        Step 3 — copy variant assignment      (this file, _assign_variant)

    Holdout hash namespace deliberately differs from variant hash namespace:
        Holdout:  md5(f"{visitor_id}:holdout:{nudge_id}")
        Variant:  md5(f"{visitor_id}:{nudge_id}")
    This ensures the two assignments are independent — a visitor's holdout
    status has no correlation with which variant they would have seen.

    Response contract (visitor eligible, NOT in holdout, A/B active):
        {
            "active":         true,
            "eligible":       true,
            "render_allowed": true,
            "nudge_id":       int,
            "copy_variant":   str,
            "copy_config":    { headline, subtext, badge, ... },
            "expires_at":     str,
            "ab_experiment":  true,
            "gating":         { ... }
        }

    Response contract (visitor eligible, IN holdout — nudge suppressed):
        {
            "active":         true,
            "eligible":       true,
            "render_allowed": false,
            "holdout":        true,
            "nudge_id":       int,
            "gating":         { ... }
        }

    Response contract (ineligible visitor):
        { "active": true, "eligible": false, "gating": { ... } }

    Response contract (no active nudge):
        { "active": false }

    400 — missing shop or product_url params.

Pro management endpoints (require_pro_plan)
-------------------------------------------
GET /pro/nudges?shop=<domain>&status=<active|expired|deactivated|all>

    List all nudges for the shop.

GET /pro/nudges/{nudge_id}/stats?window_hours=<1-168>

    Full measurement report:
    - Aggregate exposure/dismissal/click stats
    - Observational post-exposure purchase attribution
    - Per-variant stats breakdown + winner selection
    - Holdout lift report (when holdout_pct > 0 and holdout data exists)

PATCH /pro/nudges/{nudge_id}/holdout

    Enable or disable holdout on a running nudge.
    Body: { "holdout_pct": int }  (0 = disable, 1-50 = enable with that % holdout)

    Holdout takes effect immediately on the next GET /nudges/active for any
    visitor visiting this nudge's product page.

DELETE /pro/nudges/{nudge_id}?shop=<domain>

    Deactivate a specific nudge immediately.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_pro_plan
from app.models.active_nudge import ActiveNudge
from app.services.nudge_engine import (
    deactivate_nudge,
    get_active_nudge,
    list_active_nudges,
)
from app.services.nudge_gating import evaluate_visitor_nudge_eligibility
from app.services.nudge_measurement import (
    DEFAULT_ATTRIBUTION_WINDOW_HOURS,
    get_nudge_ab_report,
    get_nudge_lift_report,
    record_holdout_assignment,
)
from app.services.nudge_composer import compose_nudge_variants
from app.services.nudge_engine import create_or_refresh_nudge
from app.services.nudge_rank import compute_nudge_rank
from app.models.product import Product
from app.models.product_metrics import ProductMetrics

log = logging.getLogger(__name__)

router = APIRouter(tags=["nudges"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _nudge_to_dict(nudge) -> dict:
    return {
        "id":                       nudge.id,
        "shop_domain":              nudge.shop_domain,
        "product_url":              nudge.product_url,
        "action_type":              nudge.action_type,
        "trigger_source":           nudge.trigger_source,
        "copy_variant":             nudge.copy_variant,
        "copy_config":              nudge.copy_config_dict(),
        "is_ab_experiment":         nudge.is_ab_experiment(),
        "holdout_pct":              nudge.holdout_pct or 0,
        "is_holdout_active":        nudge.is_holdout_active(),
        "status":                   nudge.status,
        "created_at":               nudge.created_at.isoformat() if nudge.created_at else None,
        "updated_at":               nudge.updated_at.isoformat() if nudge.updated_at else None,
        "expires_at":               nudge.expires_at.isoformat() if nudge.expires_at else None,
        "deactivated_at":           nudge.deactivated_at.isoformat() if nudge.deactivated_at else None,
        "action_task_id":           nudge.action_task_id,
        "visitor_count":            nudge.visitor_count,
        "estimated_revenue_window": nudge.estimated_revenue_window,
        "calibration_state":        nudge.calibration_state,
    }


# ---------------------------------------------------------------------------
# Variant assignment — deterministic hash-based, server-side
# ---------------------------------------------------------------------------

def _assign_variant(
    visitor_id: str,
    nudge_id:   int,
    variants:   list[dict],
) -> dict:
    """
    Assign a copy variant deterministically using MD5(visitor_id:nudge_id).

    Properties:
      - Stable: same visitor always gets same variant for same nudge
      - Uniform: expected 50/50 split across large visitor populations
      - Fast: single hash, no DB storage needed
      - Independent of holdout assignment (different hash key namespace)

    Returns one variant dict: {"variant_name": str, "copy_config": dict}
    """
    if not variants:
        return {}
    if len(variants) == 1:
        return variants[0]

    key    = f"{visitor_id}:{nudge_id}".encode("utf-8")
    digest = hashlib.md5(key).hexdigest()
    idx    = int(digest[:8], 16) % len(variants)
    return variants[idx]


# ---------------------------------------------------------------------------
# Holdout assignment — deterministic hash-based, server-side
# ---------------------------------------------------------------------------

def _assign_holdout(
    visitor_id:  str,
    nudge_id:    int,
    holdout_pct: int,
) -> bool:
    """
    Deterministically assign a visitor to the holdout (control) group.

    Uses a different hash namespace from _assign_variant ("holdout:" prefix)
    to ensure holdout/exposed status is independent of variant assignment.

    Formula:
        int(md5(f"{visitor_id}:holdout:{nudge_id}")[:8], 16) % 100 < holdout_pct

    Properties:
      - Stable: same visitor always in same group for same nudge
      - Uniform: expected holdout_pct% in holdout across large populations
      - Independent: uncorrelated with variant assignment
      - No storage required: deterministic from inputs

    Parameters
    ----------
    visitor_id  : pseudonymous UUID from localStorage
    nudge_id    : active_nudges.id
    holdout_pct : integer 0-100 from active_nudges.holdout_pct

    Returns True if visitor is assigned to holdout group.
    Returns False (exposed) when holdout_pct = 0.
    """
    if holdout_pct <= 0:
        return False
    key    = f"{visitor_id}:holdout:{nudge_id}".encode("utf-8")
    digest = hashlib.md5(key).hexdigest()
    bucket = int(digest[:8], 16) % 100
    return bucket < holdout_pct


# ---------------------------------------------------------------------------
# Public: GET /nudges/active — storefront polling endpoint
# ---------------------------------------------------------------------------

@router.get("/nudges/active")
def get_active_nudge_public(
    response:    Response,
    shop:        str            = Query(..., description="Shop domain (*.myshopify.com)"),
    product_url: str            = Query(..., description="Canonical product path: /products/{handle}"),
    visitor_id:  Optional[str]  = Query(
        default=None,
        description=(
            "Pseudonymous visitor UUID from hedgespark_visitor_id localStorage key. "
            "Enables behavioral gating + holdout assignment + A/B variant assignment. "
            "Without this param: product-level check, holdout skipped, control variant returned."
        ),
    ),
    db:          Session        = Depends(get_db),
):
    """
    Storefront nudge delivery endpoint — polled by spark-nudge.js on product pages.

    With visitor_id:
      1. Behavioral gating: eligible visitors only (warm+ segment).
      2. Holdout check: deterministic suppression for control group (when holdout_pct > 0).
         - Holdout visitors → render_allowed=false; server records holdout_assigned event.
      3. A/B variant assignment: deterministic, stable per visitor+nudge.
      Returns assigned variant's copy_config and variant_name.

    Without visitor_id (product-level fallback):
      - No behavioral gating — all visitors see nudge.
      - No holdout assignment — cannot track without identity.
      - Control variant (index 0) returned.
      - Backward compatible.

    CORS: Access-Control-Allow-Origin: * — storefront cross-origin access.
    """
    response.headers["Access-Control-Allow-Origin"] = "*"

    if not shop or not product_url:
        raise HTTPException(status_code=400, detail="shop and product_url are required.")

    # Normalise product_url — strip query string, enforce /products/
    if not product_url.startswith("/products/"):
        try:
            from urllib.parse import urlparse
            import re
            parsed = urlparse(product_url)
            m = re.match(r"(/products/[^/?#]+)", parsed.path)
            product_url = m.group(1) if m else product_url
        except Exception:
            pass

    # 1. Check whether an active nudge exists for this (shop, product)
    nudge = get_active_nudge(db=db, shop_domain=shop, product_url=product_url)

    if nudge is None:
        log.debug(
            "nudges/active: no active nudge shop=%s product=%s",
            shop, product_url,
        )
        return {"active": False}

    # Resolve which variant to deliver
    variants      = nudge.copy_variants_list()
    is_ab         = len(variants) >= 2
    clean_visitor = (visitor_id or "").strip() or None

    # 2. No visitor_id — product-level fallback (legacy behavior)
    if not clean_visitor:
        # Use control variant (index 0) or the legacy primary variant
        if is_ab:
            assigned = variants[0]
        else:
            assigned = {"variant_name": nudge.copy_variant, "copy_config": nudge.copy_config_dict()}

        log.info(
            "nudges/active: PRODUCT-LEVEL delivery nudge_id=%d shop=%s product=%s "
            "variant=%s ab=%s holdout=skipped (no visitor_id)",
            nudge.id, shop, product_url, assigned.get("variant_name"), is_ab,
        )
        resp = {
            "active":         True,
            "eligible":       True,
            "render_allowed": True,
            "nudge_id":       nudge.id,
            "copy_variant":   assigned.get("variant_name"),
            "copy_config":    assigned.get("copy_config") if isinstance(assigned.get("copy_config"), dict)
                              else nudge.copy_config_dict(),
            "expires_at":     nudge.expires_at.isoformat() + "Z" if nudge.expires_at else None,
        }
        if is_ab:
            resp["ab_experiment"] = True
        return resp

    # 3. Visitor-level behavioral gating
    decision = evaluate_visitor_nudge_eligibility(
        db=db,
        shop_domain=shop,
        product_url=product_url,
        visitor_id=clean_visitor,
        nudge=nudge,
    )

    gating_block = {
        "source":                   decision["gating_source"],
        "visitor_behavioral_index": decision["visitor_behavioral_index"],
        "threshold_used":           decision["threshold_used"],
        "calibration_state":        decision["calibration_state"],
        "reason":                   decision["reason"],
        "data_points":              decision["data_points"],
    }

    if not decision["eligible"]:
        log.info(
            "nudges/active: SUPPRESSED (ineligible) nudge_id=%d shop=%s product=%s "
            "visitor=%s bi=%s threshold=%.4f source=%s reason=%s",
            nudge.id, shop, product_url,
            clean_visitor[:8] + "…",
            f"{decision['visitor_behavioral_index']:.4f}"
            if decision["visitor_behavioral_index"] is not None else "none",
            decision["threshold_used"],
            decision["gating_source"],
            decision["reason"],
        )
        return {"active": True, "eligible": False, "gating": gating_block}

    # 4. Holdout assignment — only for eligible visitors with a visitor_id
    holdout_pct = nudge.holdout_pct or 0
    if holdout_pct > 0:
        in_holdout = _assign_holdout(
            visitor_id=clean_visitor,
            nudge_id=nudge.id,
            holdout_pct=holdout_pct,
        )

        if in_holdout:
            # Record server-side holdout_assigned event.
            # This is the authoritative record that this visitor was eligible
            # but the nudge was suppressed for measurement purposes.
            ev = record_holdout_assignment(
                db          = db,
                shop_domain = shop,
                nudge_id    = nudge.id,
                visitor_id  = clean_visitor,
                product_url = product_url,
            )
            if ev is not None:
                try:
                    db.commit()
                except Exception as exc:
                    log.error(
                        "nudges/active: holdout event commit failed shop=%s nudge_id=%d: %s",
                        shop, nudge.id, exc,
                    )
                    try:
                        db.rollback()
                    except Exception:
                        pass

            log.info(
                "nudges/active: HOLDOUT nudge_id=%d shop=%s product=%s "
                "visitor=%s holdout_pct=%d — suppressed for control group",
                nudge.id, shop, product_url,
                clean_visitor[:8] + "…", holdout_pct,
            )
            return {
                "active":         True,
                "eligible":       True,
                "render_allowed": False,
                "holdout":        True,
                "nudge_id":       nudge.id,
                "gating":         gating_block,
            }

    # 5. Eligible and not in holdout — assign variant
    if is_ab:
        assigned = _assign_variant(clean_visitor, nudge.id, variants)
    else:
        assigned = {"variant_name": nudge.copy_variant, "copy_config": nudge.copy_config_dict()}

    assigned_variant_name = assigned.get("variant_name", nudge.copy_variant)
    assigned_copy_config  = assigned.get("copy_config")
    if not isinstance(assigned_copy_config, dict):
        assigned_copy_config = nudge.copy_config_dict()

    log.info(
        "nudges/active: ELIGIBLE nudge_id=%d shop=%s product=%s "
        "visitor=%s bi=%.4f threshold=%.4f source=%s variant=%s ab=%s holdout_pct=%d",
        nudge.id, shop, product_url,
        clean_visitor[:8] + "…",
        decision.get("visitor_behavioral_index") or 0,
        decision["threshold_used"],
        decision["gating_source"],
        assigned_variant_name,
        is_ab,
        holdout_pct,
    )

    resp = {
        "active":         True,
        "eligible":       True,
        "render_allowed": True,
        "nudge_id":       nudge.id,
        "copy_variant":   assigned_variant_name,
        "copy_config":    assigned_copy_config,
        "expires_at":     nudge.expires_at.isoformat() + "Z" if nudge.expires_at else None,
        "gating":         gating_block,
    }
    if is_ab:
        resp["ab_experiment"] = True
    return resp


# ---------------------------------------------------------------------------
# Pro: GET /pro/nudges — management listing
# ---------------------------------------------------------------------------

@router.get("/pro/nudges")
def list_pro_nudges(
    status: Optional[str] = Query(
        default="active",
        description="Filter by status: active | expired | deactivated | all",
    ),
    limit: int     = Query(default=50, ge=1, le=100),
    shop:  str     = Depends(require_pro_plan),
    db:    Session = Depends(get_db),
):
    """
    List nudges for the shop with full metadata including holdout configuration.
    Pro-only: require_pro_plan enforces plan + API key + shop domain.
    """
    filter_status = None if status == "all" else status
    nudges = list_active_nudges(
        db=db,
        shop_domain=shop,
        status=filter_status,
        limit=min(limit, 100),
    )
    return {
        "shop_domain":   shop,
        "status_filter": status,
        "total":         len(nudges),
        "nudges":        [_nudge_to_dict(n) for n in nudges],
    }


# ---------------------------------------------------------------------------
# Pro: POST /pro/nudges — AI-composed nudge creation
# ---------------------------------------------------------------------------

class NudgeComposeRequest(BaseModel):
    product_url: str = Field(
        ...,
        description="Canonical product path: /products/{handle}",
    )
    action_type: str = Field(
        default="social_proof",
        description="Nudge action type (social_proof | high_interest | etc.)",
    )
    holdout_pct: int = Field(
        default=20,
        ge=0,
        le=50,
        description=(
            "Holdout % for incremental lift measurement. Default 20 — "
            "AI-composed nudges should measure their own impact from day one. "
            "Set to 0 to disable holdout."
        ),
    )
    visitor_count: Optional[int] = Field(
        default=None,
        ge=0,
        description=(
            "Override visitor count used in copy. If absent, fetched from "
            "product_metrics for this product."
        ),
    )
    notes: Optional[str] = Field(
        default=None,
        max_length=300,
        description=(
            "Optional merchant context hint for the copy (e.g. 'this is a premium "
            "product targeting gift buyers'). Passed to the AI composer as context."
        ),
    )


@router.post("/pro/nudges")
async def compose_pro_nudge(
    payload: NudgeComposeRequest,
    shop:    str     = Depends(require_pro_plan),
    db:      Session = Depends(get_db),
):
    """
    Create a new AI-composed nudge for a product.

    The AI composer:
      1. Reads real behavioral signals from product_metrics (views, dwell,
         return visitors, scroll depth, cart conversions).
      2. Selects the 2 most signal-appropriate variant strategies.
      3. Calls OpenAI gpt-4o-mini with a strict truthfulness-constrained prompt.
      4. Validates the output field-by-field — rejects any fabricated claims.
      5. Falls back to rule-based copy if the AI output fails validation.

    Truthfulness constraints enforced:
      - visitor_count must equal the real metric value (never invented)
      - Forbidden: "viewing right now", "left in stock", "limited time",
        any claim not grounded in the available behavioral data

    The nudge is created with holdout_pct=20 by default so incremental
    revenue lift measurement starts immediately.  The A/B assignment
    runs deterministically at delivery time (GET /nudges/active).

    One-nudge-per-product rule applies: if an active nudge for this
    (shop, product, action_type) already exists, it is refreshed with
    the new AI-composed copy.

    Pro-only: require_pro_plan enforces plan + API key + shop domain.
    """
    # Normalize product_url
    product_url = payload.product_url.strip()
    if not product_url.startswith("/products/"):
        raise HTTPException(
            status_code=400,
            detail="product_url must be a canonical Shopify product path: /products/{handle}",
        )

    # Fetch behavioral signals from product_metrics
    metrics_row: Optional[ProductMetrics] = (
        db.query(ProductMetrics)
        .filter_by(shop_domain=shop, product_url=product_url)
        .first()
    )

    signals: dict = {}
    if metrics_row:
        signals = {
            "views_1h":               metrics_row.views_1h,
            "views_24h":              metrics_row.views_24h,
            "views_7d":               metrics_row.views_7d,
            "unique_visitors_24h":    metrics_row.unique_visitors_24h,
            "unique_visitors_7d":     metrics_row.unique_visitors_7d,
            "cart_conversions_24h":   metrics_row.cart_conversions_24h,
            "return_visitor_count_7d": metrics_row.return_visitor_count_7d,
            "avg_dwell_24h":          metrics_row.avg_dwell_24h,
            "avg_scroll_24h":         metrics_row.avg_scroll_24h,
        }
        # Remove None values
        signals = {k: v for k, v in signals.items() if v is not None}

    # Apply visitor_count override if merchant supplied one
    if payload.visitor_count is not None:
        signals["unique_visitors_24h"] = payload.visitor_count

    # Resolve visitor_count for context (for nudge_engine.create_or_refresh_nudge)
    visitor_count: Optional[int] = (
        payload.visitor_count
        or signals.get("unique_visitors_24h")
        or signals.get("views_24h")
    )
    if visitor_count:
        visitor_count = int(visitor_count)

    # Fetch product title for the prompt
    product_row: Optional[Product] = (
        db.query(Product)
        .filter_by(shop_domain=shop, product_url=product_url)
        .first()
    )
    product_title: str = (
        product_row.title.strip() if product_row and product_row.title else
        product_url.replace("/products/", "").replace("-", " ").title()
    )

    # Inject merchant notes into signals as context hint (sanitized)
    if payload.notes:
        signals["merchant_context"] = payload.notes.strip()[:300]

    # Call the AI composer — async
    variants, composer_meta = await compose_nudge_variants(
        product_title     = product_title,
        product_url       = product_url,
        signals           = signals,
        data_window_hours = 72,
    )

    # Create or refresh the nudge with AI-composed variants
    try:
        nudge, created = create_or_refresh_nudge(
            db                = db,
            shop_domain       = shop,
            product_url       = product_url,
            action_type       = payload.action_type,
            trigger_source    = "ai_composer",
            visitor_count     = visitor_count,
            revenue_window    = None,
            calibration_state = "ai_composed",
            prebuilt_variants = variants,
            holdout_pct       = payload.holdout_pct,
        )
    except Exception as exc:
        log.error(
            "nudges: create_or_refresh failed shop=%s product=%s: %s",
            shop, product_url, exc,
        )
        raise HTTPException(status_code=500, detail="Failed to persist nudge.")

    log.info(
        "nudges: AI-composed nudge %s nudge_id=%d shop=%s product=%s "
        "variants=%d holdout_pct=%d fallback=%s",
        "CREATED" if created else "REFRESHED",
        nudge.id, shop, product_url,
        len(variants), payload.holdout_pct,
        composer_meta["fallback_used"],
    )

    return {
        "nudge_id":     nudge.id,
        "created":      created,
        "shop_domain":  shop,
        "product_url":  product_url,
        "action_type":  payload.action_type,
        "holdout_pct":  nudge.holdout_pct,
        "is_holdout_active":  nudge.is_holdout_active(),
        "is_ab_experiment":   nudge.is_ab_experiment(),
        "expires_at":   nudge.expires_at.isoformat() + "Z" if nudge.expires_at else None,
        "variants":     variants,
        "composer":     composer_meta,
        "signals_used": {k: v for k, v in signals.items() if k != "merchant_context"},
        "measurement_ready":  True,
        "rank_eligible":      True,
        "note": (
            f"Nudge {'created' if created else 'refreshed'} with AI-composed copy. "
            f"{'Holdout active — incremental lift measurement starts immediately. ' if nudge.holdout_pct > 0 else ''}"
            f"A/B assignment runs at delivery time via GET /nudges/active. "
            f"Use GET /pro/nudges/{nudge.id}/stats to view measurement after traffic accumulates."
        ),
    }


# ---------------------------------------------------------------------------
# Pro: GET /pro/nudges/rank — autonomous revenue prioritization feed
# ---------------------------------------------------------------------------

@router.get("/pro/nudges/rank")
def get_nudge_rank(
    window_hours: int     = Query(
        default=DEFAULT_ATTRIBUTION_WINDOW_HOURS,
        ge=1,
        le=168,
        description="Attribution window in hours (1–168). Default: 24.",
    ),
    status: str           = Query(
        default="active",
        description="Filter by nudge status: active | expired | deactivated | all",
    ),
    limit: int            = Query(default=50, ge=1, le=100),
    shop:  str            = Depends(require_pro_plan),
    db:    Session        = Depends(get_db),
):
    """
    Autonomous nudge revenue prioritization feed.

    Ranks all nudges for the shop by estimated economic impact and assigns
    a machine-readable action recommendation to each.  Designed to be
    consumed by AI agents and the merchant dashboard.

    Performance: 3 batch DB queries total regardless of nudge count.

    Ranking signal fallback chain (highest → lowest fidelity):
      "incremental_revenue" — estimated_incremental_revenue (has order data + holdout)
      "incremental_rpv"     — incremental revenue per visitor
      "cvr_fallback"        — post-exposure CVR (no revenue data)
      "no_data"             — no exposure data yet

    Recommendation labels (in decision engine priority order):
      investigate_negative_lift  — nudge may be hurting revenue; review immediately
      promote_winner_variant      — A/B winner ready to promote (p < 0.10)
      expand_eligible_segment     — positive lift confirmed; consider wider audience
      enable_holdout              — holdout not configured; causal measurement absent
      collect_more_data           — sample below minimum for reliable measurement
      deactivate_low_value        — no positive lift; low CVR; consider removing
      monitor                     — performing normally; continue observing

    Each result includes an agent_action block:
      { method, endpoint, payload, available, description }
    Callable directly against this API to execute the recommendation.

    Attribution notes:
      - Revenue attribution is observational first-exposure (not causal).
      - Holdout lift is quasi-experimental (hash-based deterministic assignment).
      - Neither proves causation.  All estimates are labeled accordingly.
      - "method": "quasi_experimental_holdout" in revenue_lift blocks.

    Pro-only: require_pro_plan enforces plan + API key + shop domain.
    """
    filter_status = None if status == "all" else status
    nudges = list_active_nudges(
        db=db,
        shop_domain=shop,
        status=filter_status,
        limit=min(limit, 100),
    )

    ranked = compute_nudge_rank(
        db=db,
        shop_domain=shop,
        nudges=nudges,
        window_hours=window_hours,
    )

    return {
        "shop_domain":             shop,
        "status_filter":           status,
        "attribution_window_hours": window_hours,
        "total":                   len(ranked),
        "nudges":                  ranked,
        "meta": {
            "ranking_basis_options": [
                "incremental_revenue",
                "incremental_rpv",
                "cvr_fallback",
                "no_data",
            ],
            "recommendation_labels": [
                "investigate_negative_lift",
                "promote_winner_variant",
                "expand_eligible_segment",
                "enable_holdout",
                "collect_more_data",
                "deactivate_low_value",
                "monitor",
            ],
            "attribution_note": (
                "Revenue attribution is observational first-exposure. "
                "Holdout lift is quasi-experimental (hash-based assignment). "
                "Neither proves causation."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Pro: GET /pro/nudges/{nudge_id}/stats — full measurement report
# ---------------------------------------------------------------------------

@router.get("/pro/nudges/{nudge_id}/stats")
def get_nudge_stats_pro(
    nudge_id:     int,
    window_hours: int     = Query(
        default=DEFAULT_ATTRIBUTION_WINDOW_HOURS,
        ge=1,
        le=168,
        description="Attribution window in hours (1–168).",
    ),
    shop:         str     = Depends(require_pro_plan),
    db:           Session = Depends(get_db),
):
    """
    Full measurement report for one nudge.

    Includes:
    - Aggregate exposure / dismissal / click counts
    - Observational post-exposure purchase attribution (all variants combined)
    - Per-variant breakdown: exposures, dismissals, post-exposure CVR
    - Winner selection: proportion z-test with honest observational labeling
    - Holdout lift report: exposed vs holdout CVR comparison + estimated
      incremental lift (when holdout_pct > 0 and holdout data is available)

    Attribution notes:
    - Aggregate/variant attribution is observational (no holdout group).
    - Holdout lift report is quasi-experimental (hash-based control assignment).
    - Neither proves causation.  Both are labeled accordingly.

    Pro-only: require_pro_plan enforces plan + API key + shop domain.
    """
    nudge = db.query(ActiveNudge).filter_by(id=nudge_id, shop_domain=shop).first()
    if nudge is None:
        raise HTTPException(status_code=404, detail="Nudge not found.")

    report = get_nudge_ab_report(
        db           = db,
        shop_domain  = shop,
        nudge_id     = nudge_id,
        window_hours = window_hours,
    )

    # Holdout lift report — always included; holdout_active=False when not enabled
    lift_report = get_nudge_lift_report(
        db           = db,
        shop_domain  = shop,
        nudge_id     = nudge_id,
        window_hours = window_hours,
    )
    report["holdout_experiment"] = lift_report

    report["nudge"] = {
        "id":                nudge.id,
        "product_url":       nudge.product_url,
        "action_type":       nudge.action_type,
        "copy_variant":      nudge.copy_variant,
        "is_ab_experiment":  nudge.is_ab_experiment(),
        "holdout_pct":       nudge.holdout_pct or 0,
        "is_holdout_active": nudge.is_holdout_active(),
        "status":            nudge.status,
        "visitor_count":     nudge.visitor_count,
        "created_at":        nudge.created_at.isoformat() if nudge.created_at else None,
        "expires_at":        nudge.expires_at.isoformat()  if nudge.expires_at  else None,
    }
    return report


# ---------------------------------------------------------------------------
# Pro: PATCH /pro/nudges/{nudge_id}/holdout — enable / update holdout
# ---------------------------------------------------------------------------

class HoldoutUpdatePayload(BaseModel):
    holdout_pct: int = Field(
        ...,
        ge=0,
        le=50,
        description=(
            "Percentage of eligible visitors to assign to holdout (control) group. "
            "0 = disable holdout. "
            "Recommended: 10–25. Max: 50 (do not suppress more than half of eligible visitors). "
            "Takes effect immediately on next GET /nudges/active for this nudge."
        ),
    )


@router.patch("/pro/nudges/{nudge_id}/holdout")
def update_nudge_holdout(
    nudge_id: int,
    payload:  HoldoutUpdatePayload,
    shop:     str     = Depends(require_pro_plan),
    db:       Session = Depends(get_db),
):
    """
    Enable, update, or disable the holdout (control) group for a nudge.

    Effect is immediate — the next visitor request to GET /nudges/active
    for this nudge will use the new holdout_pct.

    Assignment is deterministic: the same visitor always falls in the same
    group for a given nudge_id.  Changing holdout_pct changes the threshold
    but does not re-randomize existing assignments — visitors near the
    threshold boundary may shift groups.  For clean measurement, set
    holdout_pct once before exposures accumulate.

    Setting holdout_pct = 0 disables holdout — all eligible visitors see
    the nudge.  Existing holdout_assigned events are preserved for historical
    analysis.

    Pro-only: require_pro_plan enforces plan + API key + shop domain.
    """
    nudge = db.query(ActiveNudge).filter_by(id=nudge_id, shop_domain=shop).first()
    if nudge is None:
        raise HTTPException(status_code=404, detail="Nudge not found.")

    previous_pct  = nudge.holdout_pct or 0
    nudge.holdout_pct = payload.holdout_pct

    try:
        db.commit()
        db.refresh(nudge)
    except Exception as exc:
        log.error(
            "nudges: holdout update failed nudge_id=%d shop=%s: %s",
            nudge_id, shop, exc,
        )
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update holdout configuration.")

    log.info(
        "nudges: holdout updated nudge_id=%d shop=%s previous_pct=%d new_pct=%d",
        nudge_id, shop, previous_pct, payload.holdout_pct,
    )

    return {
        "nudge_id":          nudge.id,
        "holdout_pct":       nudge.holdout_pct,
        "is_holdout_active": nudge.is_holdout_active(),
        "previous_pct":      previous_pct,
        "note": (
            "Holdout is now active. Eligible visitors will be deterministically "
            "split between exposed and control groups. Use GET /pro/nudges/{id}/stats "
            "to view lift measurement once sufficient data is collected "
            f"(≥30 visitors per group recommended)."
        ) if nudge.holdout_pct > 0 else (
            "Holdout disabled. All eligible visitors will see the nudge. "
            "Historical holdout_assigned events are preserved."
        ),
    }


# ---------------------------------------------------------------------------
# Pro: DELETE /pro/nudges/{nudge_id} — deactivate a specific nudge
# ---------------------------------------------------------------------------

@router.delete("/pro/nudges/{nudge_id}")
def deactivate_pro_nudge(
    nudge_id: int,
    shop:     str     = Depends(require_pro_plan),
    db:       Session = Depends(get_db),
):
    """
    Immediately deactivate a nudge by ID.

    The storefront script stops showing the nudge on the next page load.
    Returns 404 for cross-tenant nudge IDs.

    Pro-only: require_pro_plan enforces plan + API key + shop domain.
    """
    nudge, conflict = deactivate_nudge(
        db=db,
        nudge_id=nudge_id,
        shop_domain=shop,
    )

    if conflict == "not_found":
        raise HTTPException(status_code=404, detail="Nudge not found.")

    return {
        "deactivated": True,
        "nudge":       _nudge_to_dict(nudge),
    }
