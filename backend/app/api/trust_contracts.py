"""
trust_contracts.py — Delegated Autonomy API.

Merchant-facing endpoints to create, view, tighten, and revoke trust
contracts (the pre-approval bounds that let HedgeSpark execute revenue
actions autonomously within merchant-controlled guardrails).

Pro-only. Every write emits an audit log entry so the merchant has a
defensible trail of every autonomy grant + revocation.

Endpoints
---------
GET    /pro/trust/contracts                — list all contracts for this shop
POST   /pro/trust/contracts                — grant trust for an action_type
PATCH  /pro/trust/contracts/{id}           — adjust quotas/bounds/status
DELETE /pro/trust/contracts/{id}           — revoke a single contract
POST   /pro/trust/panic                    — revoke ALL active contracts
GET    /pro/trust/executions               — audit log of what ran under trust
GET    /pro/trust/summary                  — quick-at-a-glance summary for UI
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session
from app.services import trust_contract as tc_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro/trust", tags=["trust_contracts"])




# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class TrustContractResponse(BaseModel):
    id: int
    shop_domain: str
    action_type: str
    max_per_day: int
    max_per_week: int
    discount_floor_pct: float
    discount_ceiling_pct: float
    confidence_threshold: float
    auto_pause_on_drop_pct: float
    require_holdout: bool
    scope_type: str
    scope_values: list[str] | None
    status: str
    created_at: str | None
    updated_at: str | None
    revoked_at: str | None
    revoked_reason: str | None
    created_by: str | None
    note: str | None


class TrustContractCreate(BaseModel):
    action_type: str = Field(..., pattern="^(SCARCITY_NUDGE|RETARGET_HOT_TRAFFIC|PRICE_TEST|FLASH_INCENTIVE)$")
    max_per_day: int = Field(3, ge=0, le=100)
    max_per_week: int = Field(10, ge=0, le=500)
    discount_floor_pct: float = Field(-5.0, ge=-50.0, le=50.0)
    discount_ceiling_pct: float = Field(0.0, ge=-50.0, le=50.0)
    confidence_threshold: float = Field(0.80, ge=0.0, le=1.0)
    auto_pause_on_drop_pct: float = Field(15.0, ge=0.0, le=100.0)
    require_holdout: bool = True
    scope_type: str = Field("all", pattern="^(all|products|collections|tags)$")
    scope_values: list[str] | None = Field(None, max_length=500)
    note: str | None = Field(None, max_length=500)


class TrustContractPatch(BaseModel):
    max_per_day: int | None = Field(None, ge=0, le=100)
    max_per_week: int | None = Field(None, ge=0, le=500)
    discount_floor_pct: float | None = Field(None, ge=-50.0, le=50.0)
    discount_ceiling_pct: float | None = Field(None, ge=-50.0, le=50.0)
    confidence_threshold: float | None = Field(None, ge=0.0, le=1.0)
    auto_pause_on_drop_pct: float | None = Field(None, ge=0.0, le=100.0)
    require_holdout: bool | None = None
    scope_type: str | None = Field(None, pattern="^(all|products|collections|tags)$")
    scope_values: list[str] | None = Field(None, max_length=500)
    status: str | None = Field(None, pattern="^(active|paused)$")
    note: str | None = Field(None, max_length=500)


class TrustExecutionResponse(BaseModel):
    id: int
    contract_id: int
    action_type: str
    target_url: str | None
    executed_at: str
    confidence: float | None
    discount_pct_applied: float | None
    holdout_pct_applied: int | None
    outcome: str | None
    revenue_delta_eur: float | None
    measured_at: str | None


class TrustSummaryResponse(BaseModel):
    shop_domain: str
    active_contracts: int
    paused_contracts: int
    executions_last_30d: int
    revenue_impact_eur: float
    effective_rate: float
    contracts: list[TrustContractResponse]


class PanicResponse(BaseModel):
    revoked_count: int


class AutopilotPreset(BaseModel):
    mode: str = Field(..., pattern="^(conservative|balanced|aggressive)$")


class AutopilotResponse(BaseModel):
    mode: str
    contracts_created: int
    contract_ids: list[int]
    summary: str


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def _contract_to_response(c) -> TrustContractResponse:
    try:
        scope_values = json.loads(c.scope_values) if c.scope_values else None
    except Exception:
        scope_values = None
    return TrustContractResponse(
        id=c.id,
        shop_domain=c.shop_domain,
        action_type=c.action_type,
        max_per_day=c.max_per_day,
        max_per_week=c.max_per_week,
        discount_floor_pct=c.discount_floor_pct,
        discount_ceiling_pct=c.discount_ceiling_pct,
        confidence_threshold=c.confidence_threshold,
        auto_pause_on_drop_pct=c.auto_pause_on_drop_pct,
        require_holdout=c.require_holdout,
        scope_type=c.scope_type,
        scope_values=scope_values,
        status=c.status,
        created_at=c.created_at.isoformat() if c.created_at else None,
        updated_at=c.updated_at.isoformat() if c.updated_at else None,
        revoked_at=c.revoked_at.isoformat() if c.revoked_at else None,
        revoked_reason=c.revoked_reason,
        created_by=c.created_by,
        note=c.note,
    )


def _execution_to_response(x) -> TrustExecutionResponse:
    return TrustExecutionResponse(
        id=x.id,
        contract_id=x.contract_id,
        action_type=x.action_type,
        target_url=x.target_url,
        executed_at=x.executed_at.isoformat() if x.executed_at else "",
        confidence=x.confidence,
        discount_pct_applied=x.discount_pct_applied,
        holdout_pct_applied=x.holdout_pct_applied,
        outcome=x.outcome,
        revenue_delta_eur=x.revenue_delta_eur,
        measured_at=x.measured_at.isoformat() if x.measured_at else None,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/contracts", response_model=list[TrustContractResponse])
def list_contracts(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    rows = tc_service.list_contracts(db, shop)
    return [_contract_to_response(r) for r in rows]


@router.post("/contracts", response_model=TrustContractResponse, status_code=201)
def create_contract(
    payload: TrustContractCreate,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    try:
        contract = tc_service.create_contract(
            db,
            shop_domain=shop,
            action_type=payload.action_type,
            max_per_day=payload.max_per_day,
            max_per_week=payload.max_per_week,
            discount_floor_pct=payload.discount_floor_pct,
            discount_ceiling_pct=payload.discount_ceiling_pct,
            confidence_threshold=payload.confidence_threshold,
            auto_pause_on_drop_pct=payload.auto_pause_on_drop_pct,
            require_holdout=payload.require_holdout,
            scope_type=payload.scope_type,
            scope_values=payload.scope_values,
            created_by=shop,  # merchant-granted, audit uses shop as actor
            note=payload.note,
        )
        db.commit()
        return _contract_to_response(contract)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.patch("/contracts/{contract_id}", response_model=TrustContractResponse)
def patch_contract(
    contract_id: int,
    payload: TrustContractPatch,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.models.trust_contract import TrustContract
    existing = db.get(TrustContract, contract_id)
    if existing is None or existing.shop_domain != shop:
        raise HTTPException(status_code=404, detail="contract_not_found")
    if existing.status in ("revoked", "expired"):
        raise HTTPException(status_code=409, detail="contract_terminal")
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    contract = tc_service.update_contract(db, contract_id, **updates)
    if contract is None:
        raise HTTPException(status_code=404, detail="contract_not_found")
    db.commit()
    return _contract_to_response(contract)


@router.delete("/contracts/{contract_id}", response_model=TrustContractResponse)
def revoke_contract(
    contract_id: int,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.models.trust_contract import TrustContract
    existing = db.get(TrustContract, contract_id)
    if existing is None or existing.shop_domain != shop:
        raise HTTPException(status_code=404, detail="contract_not_found")
    contract = tc_service.revoke_contract(db, contract_id, reason="merchant")
    db.commit()
    return _contract_to_response(contract)


_AUTOPILOT_PRESETS: dict[str, dict] = {
    "conservative": {
        "summary": "Small, safe moves. HedgeSpark nudges returning visitors and runs tiny price tests.",
        "contracts": [
            {
                "action_type": "SCARCITY_NUDGE",
                "max_per_day": 2, "max_per_week": 8,
                "discount_floor_pct": 0.0, "discount_ceiling_pct": 0.0,
                "confidence_threshold": 0.85, "auto_pause_on_drop_pct": 10.0,
                "require_holdout": True,
            },
            {
                "action_type": "RETARGET_HOT_TRAFFIC",
                "max_per_day": 2, "max_per_week": 8,
                "discount_floor_pct": 0.0, "discount_ceiling_pct": 0.0,
                "confidence_threshold": 0.85, "auto_pause_on_drop_pct": 10.0,
                "require_holdout": True,
            },
            {
                "action_type": "PRICE_TEST",
                "max_per_day": 1, "max_per_week": 3,
                "discount_floor_pct": -3.0, "discount_ceiling_pct": 0.0,
                "confidence_threshold": 0.90, "auto_pause_on_drop_pct": 8.0,
                "require_holdout": True,
            },
        ],
    },
    "balanced": {
        "summary": "The default for most stores. Nudges + price tests + flash offers, always proof-first.",
        "contracts": [
            {
                "action_type": "SCARCITY_NUDGE",
                "max_per_day": 4, "max_per_week": 20,
                "discount_floor_pct": 0.0, "discount_ceiling_pct": 0.0,
                "confidence_threshold": 0.80, "auto_pause_on_drop_pct": 15.0,
                "require_holdout": True,
            },
            {
                "action_type": "RETARGET_HOT_TRAFFIC",
                "max_per_day": 4, "max_per_week": 20,
                "discount_floor_pct": 0.0, "discount_ceiling_pct": 0.0,
                "confidence_threshold": 0.80, "auto_pause_on_drop_pct": 15.0,
                "require_holdout": True,
            },
            {
                "action_type": "PRICE_TEST",
                "max_per_day": 2, "max_per_week": 8,
                "discount_floor_pct": -7.0, "discount_ceiling_pct": 0.0,
                "confidence_threshold": 0.80, "auto_pause_on_drop_pct": 12.0,
                "require_holdout": True,
            },
            {
                "action_type": "FLASH_INCENTIVE",
                "max_per_day": 1, "max_per_week": 3,
                "discount_floor_pct": -10.0, "discount_ceiling_pct": 0.0,
                "confidence_threshold": 0.80, "auto_pause_on_drop_pct": 15.0,
                "require_holdout": True,
            },
        ],
    },
    "aggressive": {
        "summary": "Maximum firepower. Deeper discounts allowed, wider confidence, more frequent actions.",
        "contracts": [
            {
                "action_type": "SCARCITY_NUDGE",
                "max_per_day": 10, "max_per_week": 50,
                "discount_floor_pct": 0.0, "discount_ceiling_pct": 0.0,
                "confidence_threshold": 0.72, "auto_pause_on_drop_pct": 20.0,
                "require_holdout": True,
            },
            {
                "action_type": "RETARGET_HOT_TRAFFIC",
                "max_per_day": 10, "max_per_week": 50,
                "discount_floor_pct": 0.0, "discount_ceiling_pct": 0.0,
                "confidence_threshold": 0.72, "auto_pause_on_drop_pct": 20.0,
                "require_holdout": True,
            },
            {
                "action_type": "PRICE_TEST",
                "max_per_day": 4, "max_per_week": 15,
                "discount_floor_pct": -15.0, "discount_ceiling_pct": 0.0,
                "confidence_threshold": 0.72, "auto_pause_on_drop_pct": 18.0,
                "require_holdout": True,
            },
            {
                "action_type": "FLASH_INCENTIVE",
                "max_per_day": 3, "max_per_week": 10,
                "discount_floor_pct": -20.0, "discount_ceiling_pct": 0.0,
                "confidence_threshold": 0.72, "auto_pause_on_drop_pct": 20.0,
                "require_holdout": True,
            },
        ],
    },
}


@router.post("/autopilot", response_model=AutopilotResponse)
def autopilot_grant(
    payload: AutopilotPreset,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """✨1 Autopilot Mode — atomically create a bundle of trust contracts
    tuned for one of three preset intensity levels. Safer than a manual
    5-slider grant modal: merchants pick intent, we pick the numbers."""
    preset = _AUTOPILOT_PRESETS.get(payload.mode)
    if preset is None:
        raise HTTPException(422, "invalid_mode")

    created_ids: list[int] = []
    try:
        for cfg in preset["contracts"]:
            contract = tc_service.create_contract(
                db,
                shop_domain=shop,
                created_by=f"{shop}:autopilot:{payload.mode}",
                note=f"Autopilot {payload.mode}",
                **cfg,
            )
            created_ids.append(contract.id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc))

    return AutopilotResponse(
        mode=payload.mode,
        contracts_created=len(created_ids),
        contract_ids=created_ids,
        summary=preset["summary"],
    )


@router.post("/panic", response_model=PanicResponse)
def panic_stop(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    n = tc_service.panic_stop(db, shop, reason="panic")
    db.commit()
    return PanicResponse(revoked_count=n)


@router.get("/executions", response_model=list[TrustExecutionResponse])
def list_executions(
    limit: int = 50,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 200))
    rows = tc_service.list_executions(db, shop, limit=limit)
    return [_execution_to_response(r) for r in rows]


@router.get("/summary", response_model=TrustSummaryResponse)
def trust_summary(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from datetime import datetime, timedelta, timezone
    from app.models.trust_contract import TrustContract, TrustExecutionLog
    from sqlalchemy import func

    contracts = tc_service.list_contracts(db, shop)
    active = sum(1 for c in contracts if c.status == "active")
    paused = sum(1 for c in contracts if c.status == "paused")

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    executions_count = (
        db.query(func.count(TrustExecutionLog.id))
        .filter(
            TrustExecutionLog.shop_domain == shop,
            TrustExecutionLog.executed_at >= cutoff,
        )
        .scalar()
        or 0
    )
    revenue_impact = (
        db.query(func.coalesce(func.sum(TrustExecutionLog.revenue_delta_eur), 0))
        .filter(
            TrustExecutionLog.shop_domain == shop,
            TrustExecutionLog.executed_at >= cutoff,
        )
        .scalar()
        or 0.0
    )
    effective_rows = (
        db.query(func.count(TrustExecutionLog.id))
        .filter(
            TrustExecutionLog.shop_domain == shop,
            TrustExecutionLog.executed_at >= cutoff,
            TrustExecutionLog.outcome == "effective",
        )
        .scalar()
        or 0
    )
    measured_rows = (
        db.query(func.count(TrustExecutionLog.id))
        .filter(
            TrustExecutionLog.shop_domain == shop,
            TrustExecutionLog.executed_at >= cutoff,
            TrustExecutionLog.outcome.in_(["effective", "ineffective", "inconclusive"]),
        )
        .scalar()
        or 0
    )
    effective_rate = (effective_rows / measured_rows) if measured_rows > 0 else 0.0

    return TrustSummaryResponse(
        shop_domain=shop,
        active_contracts=active,
        paused_contracts=paused,
        executions_last_30d=int(executions_count),
        revenue_impact_eur=float(revenue_impact),
        effective_rate=float(effective_rate),
        contracts=[_contract_to_response(c) for c in contracts],
    )
