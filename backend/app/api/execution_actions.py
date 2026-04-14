"""
execution_actions.py — Execution lifecycle + holdout enforcement API.

POST /products/executions/{execution_id}/confirm
  Transition status → executed, capture baseline snapshot.

POST /products/executions/{execution_id}/status
  Update execution_status (acknowledge, pause, complete).

GET /products/executions/eligibility?visitor_id=X
  Returns per-execution holdout eligibility for a storefront visitor.
  Used by storefront JS to enforce holdout suppression for onsite actions.

GET /products/executions/{execution_id}/audience?group=exposed
  Returns visitor_ids for export (Klaviyo). Filters to exposed only by default.

All writes are lightweight — no event scans.
Baseline capture reads from precomputed execution_tracking + product_metrics.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_merchant_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/products/executions", tags=["executions"])


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class ConfirmExecutionRequest(BaseModel):
    execution_mode: str = Field("manual", max_length=32)
    note: str | None = Field(None, max_length=1000)

class StatusUpdateRequest(BaseModel):
    status: str = Field(..., max_length=32)

class ExecutionConfirmResponse(BaseModel):
    execution_id: str
    execution_status: str
    executed_at: str
    baseline_captured: bool


# ---------------------------------------------------------------------------
# POST /products/executions/{execution_id}/confirm
# ---------------------------------------------------------------------------

@router.post("/{execution_id}/confirm", response_model=ExecutionConfirmResponse)
def confirm_execution(
    execution_id: str,
    body: ConfirmExecutionRequest,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(_get_db),
) -> ExecutionConfirmResponse:
    """
    Merchant confirms they executed the recommended action.

    1. Transitions execution_status → 'executed'
    2. Captures baseline snapshot (current proof rates + product_b metrics)
    3. Sets executed_at timestamp

    Baseline is captured ONCE and never overwritten — it's the "before" reference.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Verify opportunity exists and belongs to this shop (locked for update)
    opp = db.execute(
        text("""
            SELECT id, execution_status, product_b
            FROM execution_opportunities
            WHERE shop_domain = :shop AND execution_id = :eid
            FOR UPDATE
        """),
        {"shop": shop, "eid": execution_id},
    ).fetchone()

    if opp is None:
        raise HTTPException(404, f"Execution opportunity {execution_id} not found")

    current_status = opp[1]
    product_b = opp[2]

    if current_status == "executed":
        raise HTTPException(409, "Already marked as executed")
    if current_status == "completed":
        raise HTTPException(409, "Opportunity is already completed")

    # Step 1: Transition status
    db.execute(
        text("""
            UPDATE execution_opportunities
            SET execution_status = 'executed',
                executed_at = :now,
                execution_mode = :mode,
                execution_note = :note
            WHERE shop_domain = :shop AND execution_id = :eid
        """),
        {
            "shop": shop, "eid": execution_id, "now": now,
            "mode": body.execution_mode, "note": body.note,
        },
    )

    # Step 2: Capture baseline snapshot
    baseline_captured = _capture_baseline(db, shop, execution_id, product_b, now)

    db.commit()

    return ExecutionConfirmResponse(
        execution_id=execution_id,
        execution_status="executed",
        executed_at=now.isoformat(),
        baseline_captured=baseline_captured,
    )


# ---------------------------------------------------------------------------
# POST /products/executions/{execution_id}/status
# ---------------------------------------------------------------------------

@router.post("/{execution_id}/status")
def update_execution_status(
    execution_id: str,
    body: StatusUpdateRequest,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(_get_db),
):
    """Update execution status (acknowledge, pause, complete)."""
    allowed = {"acknowledged", "paused", "completed"}
    if body.status not in allowed:
        raise HTTPException(400, f"Status must be one of: {allowed}")

    result = db.execute(
        text("""
            UPDATE execution_opportunities
            SET execution_status = :status
            WHERE shop_domain = :shop AND execution_id = :eid
        """),
        {"shop": shop, "eid": execution_id, "status": body.status},
    )
    if result.rowcount == 0:
        raise HTTPException(404, f"Execution opportunity {execution_id} not found")

    db.commit()
    return {"execution_id": execution_id, "execution_status": body.status}


# ---------------------------------------------------------------------------
# Baseline capture (internal)
# ---------------------------------------------------------------------------

def _capture_baseline(
    db: Session, shop: str, execution_id: str, product_b: str, now: datetime
) -> bool:
    """
    Capture pre-execution baseline from execution_tracking + product_metrics.
    Returns True if baseline was captured, False if already exists or no data.
    """
    # Check if baseline already exists (idempotent)
    existing = db.execute(
        text("SELECT id FROM execution_baselines WHERE shop_domain = :shop AND execution_id = :eid"),
        {"shop": shop, "eid": execution_id},
    ).fetchone()
    if existing:
        return False

    # Current proof rates from execution_tracking
    tracking = db.execute(
        text("""
            SELECT
                COUNT(*) AS tracked,
                COUNT(*) FILTER (WHERE returned) AS returned,
                COUNT(*) FILTER (WHERE viewed_product_b) AS viewed,
                COUNT(*) FILTER (WHERE purchased_product_b) AS purchased
            FROM execution_tracking
            WHERE shop_domain = :shop AND execution_id = :eid
        """),
        {"shop": shop, "eid": execution_id},
    ).fetchone()

    tracked = int(tracking[0] or 0) if tracking else 0
    ret = int(tracking[1] or 0) if tracking else 0
    viewed = int(tracking[2] or 0) if tracking else 0
    purchased = int(tracking[3] or 0) if tracking else 0

    # Product B metrics from product_metrics
    pm = db.execute(
        text("""
            SELECT views_24h, cart_conversions_24h, purchases_24h, revenue_24h
            FROM product_metrics
            WHERE shop_domain = :shop AND product_url = :pb
        """),
        {"shop": shop, "pb": product_b},
    ).fetchone()

    # Audience size
    aud = db.execute(
        text("""
            SELECT COUNT(*) FROM execution_audiences
            WHERE shop_domain = :shop AND execution_id = :eid
        """),
        {"shop": shop, "eid": execution_id},
    ).fetchone()
    audience_size = int(aud[0] or 0) if aud else 0

    db.execute(
        text("""
            INSERT INTO execution_baselines (
                execution_id, shop_domain, captured_at,
                audience_size, return_rate, view_rate, purchase_rate, tracked_count,
                product_b, product_b_views_24h, product_b_carts_24h,
                product_b_purchases_24h, product_b_revenue_24h
            ) VALUES (
                :eid, :shop, :now,
                :aud_size, :ret_rate, :view_rate, :pur_rate, :tracked,
                :pb, :pb_views, :pb_carts, :pb_purchases, :pb_revenue
            )
            ON CONFLICT (shop_domain, execution_id) DO NOTHING
        """),
        {
            "eid": execution_id, "shop": shop, "now": now,
            "aud_size": audience_size,
            "ret_rate": round(ret / tracked, 4) if tracked > 0 else None,
            "view_rate": round(viewed / tracked, 4) if tracked > 0 else None,
            "pur_rate": round(purchased / tracked, 4) if tracked > 0 else None,
            "tracked": tracked,
            "pb": product_b,
            "pb_views": int(pm[0] or 0) if pm else None,
            "pb_carts": int(pm[1] or 0) if pm else None,
            "pb_purchases": int(pm[2] or 0) if pm else None,
            "pb_revenue": float(pm[3] or 0) if pm else None,
        },
    )
    return True


# ---------------------------------------------------------------------------
# GET /products/executions/eligibility — storefront holdout enforcement
# ---------------------------------------------------------------------------

class EligibilityItem(BaseModel):
    execution_id: str
    group_type: str          # exposed | holdout
    render_allowed: bool     # True = show action, False = suppress
    product_b: str

class EligibilityResponse(BaseModel):
    visitor_id: str
    executions: list[EligibilityItem]


import re as _re

_SHOP_RE = _re.compile(r"^[a-z0-9][a-z0-9\-]*\.myshopify\.com$")
_VID_MAX_LEN = 64


def _validate_shop(shop: str) -> str:
    """Validate Shopify domain format. Raises 400 on invalid."""
    s = (shop or "").strip().lower()
    if not s or not _SHOP_RE.match(s):
        raise HTTPException(400, "Invalid shop domain")
    return s


def _validate_visitor_id(vid: str) -> str:
    """Validate visitor_id format. Raises 400 on invalid."""
    v = (vid or "").strip()
    if not v or len(v) > _VID_MAX_LEN:
        raise HTTPException(400, "Invalid visitor_id")
    return v


@router.get("/eligibility", response_model=EligibilityResponse)
def get_visitor_eligibility(
    visitor_id: str,
    shop: str,
    db: Session = Depends(_get_db),
) -> EligibilityResponse:
    """
    Storefront holdout enforcement endpoint.

    Called by storefront JS before rendering any cross-sell or upsell action.
    Returns the visitor's group assignment for each active executed opportunity.

    - exposed → render_allowed=True (show the action)
    - holdout → render_allowed=False (suppress silently)

    Security: shop domain format validated, visitor_id length-bounded,
    response LIMIT 20 (max concurrent executions per visitor).
    Performance: uses composite index (shop_domain, visitor_id).
    """
    shop = _validate_shop(shop)
    visitor_id = _validate_visitor_id(visitor_id)

    rows = db.execute(
        text("""
            SELECT
                ea.execution_id,
                ea.group_type,
                eo.product_b
            FROM execution_audiences ea
            INNER JOIN execution_opportunities eo
                ON eo.execution_id = ea.execution_id
               AND eo.shop_domain  = ea.shop_domain
            WHERE ea.shop_domain = :shop
              AND ea.visitor_id  = :vid
              AND eo.is_active   = true
              AND eo.execution_status = 'executed'
            LIMIT 20
        """),
        {"shop": shop, "vid": visitor_id},
    ).fetchall()

    items = [
        EligibilityItem(
            execution_id=r[0],
            group_type=r[1],
            render_allowed=(r[1] == "exposed"),
            product_b=r[2],
        )
        for r in rows
    ]

    return EligibilityResponse(visitor_id=visitor_id, executions=items)


# ---------------------------------------------------------------------------
# GET /products/executions/{execution_id}/audience — Klaviyo export
# ---------------------------------------------------------------------------

class AudienceExportResponse(BaseModel):
    execution_id: str
    group: str
    visitor_ids: list[str]
    count: int


@router.get("/{execution_id}/audience", response_model=AudienceExportResponse)
def get_execution_audience(
    execution_id: str,
    group: str = "exposed",
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(_get_db),
) -> AudienceExportResponse:
    """
    Export audience for an execution opportunity.

    Default: group=exposed (only users who SHOULD see the action).
    This is the ENFORCEMENT POINT for email/push channels.
    Holdout visitors are NEVER included when group=exposed.

    Used for Klaviyo segment creation, email campaigns, push notifications.
    """
    if group not in ("exposed", "holdout", "all"):
        raise HTTPException(400, "group must be: exposed, holdout, or all")

    if group == "all":
        where_clause = ""
    else:
        where_clause = "AND ea.group_type = :group"

    rows = db.execute(
        text(f"""
            SELECT ea.visitor_id
            FROM execution_audiences ea
            WHERE ea.shop_domain = :shop
              AND ea.execution_id = :eid
              {where_clause}
            ORDER BY ea.created_at DESC
            LIMIT 1000
        """),
        {"shop": shop, "eid": execution_id, "group": group},
    ).fetchall()

    vids = [r[0] for r in rows]
    return AudienceExportResponse(
        execution_id=execution_id,
        group=group,
        visitor_ids=vids,
        count=len(vids),
    )


# ---------------------------------------------------------------------------
# POST /products/executions/{execution_id}/sync-klaviyo — Klaviyo channel sync
# ---------------------------------------------------------------------------

class KlaviyoSyncResponse(BaseModel):
    execution_id: str
    list_id: str | None = None
    synced: int = 0
    anonymous: int = 0
    errors: int = 0
    total_exposed: int = 0
    enforcement_mode: str = "email"


@router.post("/{execution_id}/sync-klaviyo", response_model=KlaviyoSyncResponse)
def sync_execution_to_klaviyo_endpoint(
    execution_id: str,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(_get_db),
) -> KlaviyoSyncResponse:
    """
    Sync an execution opportunity's exposed audience to Klaviyo.

    1. Reads ONLY exposed audience (holdout strictly excluded)
    2. Resolves visitor_id → email via purchase history
    3. Creates/finds Klaviyo list "HS_EXEC_{execution_id}"
    4. Batch-pushes profiles to the list
    5. Sets enforcement_mode = "email" on success

    Uses the merchant's stored Klaviyo key (encrypted at rest).
    Configure via PUT /merchant/integrations/klaviyo.

    Fail-safe: if Klaviyo fails, execution_status is NOT rolled back.
    The opportunity remains "executed" — sync can be retried.
    """
    from app.services.klaviyo_connection import resolve_klaviyo_key
    klaviyo_key = resolve_klaviyo_key(db, shop)
    if not klaviyo_key:
        raise HTTPException(400, "Klaviyo not connected — save your API key in Settings → Integrations")

    # Verify opportunity exists and is executed
    opp = db.execute(
        text("""
            SELECT execution_status, product_a, product_b, suggested_message
            FROM execution_opportunities
            WHERE shop_domain = :shop AND execution_id = :eid
        """),
        {"shop": shop, "eid": execution_id},
    ).fetchone()

    if opp is None:
        raise HTTPException(404, f"Execution {execution_id} not found")

    if opp[0] not in ("executed", "acknowledged", "suggested"):
        raise HTTPException(409, f"Cannot sync — status is {opp[0]}")

    product_a = opp[1] or ""
    product_b = opp[2] or ""
    suggested_msg = opp[3] or ""

    # Perform Klaviyo sync
    from app.services.klaviyo_export import sync_execution_to_klaviyo
    from app.services.klaviyo_connection import record_sync_success, record_sync_failure

    result = sync_execution_to_klaviyo(
        db=db,
        shop_domain=shop,
        execution_id=execution_id,
        klaviyo_api_key=klaviyo_key,
        product_a=product_a,
        product_b=product_b,
        suggested_message=suggested_msg,
    )

    # Track sync outcome on merchant row (AI-inspectable)
    if result.get("errors", 0) == 0:
        record_sync_success(db, shop)
    else:
        record_sync_failure(db, shop, f"execution sync errors={result.get('errors', 0)}")

    # On successful sync, set enforcement_mode = "email"
    if result.get("synced", 0) > 0:
        db.execute(
            text("""
                UPDATE execution_opportunities
                SET enforcement_mode = 'email'
                WHERE shop_domain = :shop AND execution_id = :eid
            """),
            {"shop": shop, "eid": execution_id},
        )
        # If not yet executed, auto-transition to executed
        if opp[0] in ("suggested", "acknowledged"):
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            db.execute(
                text("""
                    UPDATE execution_opportunities
                    SET execution_status = 'executed', executed_at = :now, execution_mode = 'assisted'
                    WHERE shop_domain = :shop AND execution_id = :eid
                      AND execution_status IN ('suggested', 'acknowledged')
                """),
                {"shop": shop, "eid": execution_id, "now": now},
            )
            # Capture baseline if not yet captured
            from app.api.execution_actions import _capture_baseline
            _capture_baseline(db, shop, execution_id, product_b, now)

        db.commit()
    else:
        db.commit()
        # Sync had zero results but didn't fail — still safe
        if result.get("errors", 0) > 0:
            db.execute(
                text("""
                    UPDATE execution_opportunities
                    SET execution_note = 'Klaviyo sync had errors — retry possible'
                    WHERE shop_domain = :shop AND execution_id = :eid
                """),
                {"shop": shop, "eid": execution_id},
            )
            db.commit()

    return KlaviyoSyncResponse(
        execution_id=execution_id,
        list_id=result.get("list_id"),
        synced=result.get("synced", 0),
        anonymous=result.get("anonymous", 0),
        errors=result.get("errors", 0),
        total_exposed=result.get("total_exposed", 0),
        enforcement_mode="email" if result.get("synced", 0) > 0 else "unknown",
    )
