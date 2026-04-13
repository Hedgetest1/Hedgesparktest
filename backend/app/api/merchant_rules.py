"""
merchant_rules.py — Low-code rule builder API (ζ2).

Pro-only. CRUD + evaluate endpoints for merchant-authored rules.

Endpoints
---------
GET    /pro/rules                  — list rules for this shop
POST   /pro/rules                  — create rule
PATCH  /pro/rules/{id}             — update rule (status, conditions, action, name)
DELETE /pro/rules/{id}             — delete rule
POST   /pro/rules/{id}/test        — dry-run against a payload (no fire)
GET    /pro/rules/catalog          — list of allowed triggers + actions for the UI builder
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_pro_session
from app.models.merchant_rule import MerchantRule
from app.services.rule_engine import _eval_all, _ALLOWED_ACTIONS

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro/rules", tags=["merchant_rules"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


ALLOWED_TRIGGERS = [
    {"id": "high_intent_abandon", "label": "High-intent visitor left without buying"},
    {"id": "cart_abandoned", "label": "Cart abandoned"},
    {"id": "goal_at_risk", "label": "Monthly goal is at risk"},
    {"id": "rars_spike", "label": "Revenue-at-risk jumped"},
    {"id": "semantic_drift", "label": "Store behaviour changed suddenly"},
    {"id": "churn_risk_escalated", "label": "Customer about to go silent"},
    {"id": "nudge_recovered", "label": "A nudge recovered a sale"},
    {"id": "price_test_winner", "label": "A price test produced a winner"},
]


class ConditionSchema(BaseModel):
    field: str = Field(..., max_length=64)
    op: str = Field(..., pattern="^(eq|ne|gt|lt|gte|lte|contains|in|regex)$")
    value: object | None = None


class RuleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    trigger_signal: str = Field(..., min_length=2, max_length=64)
    conditions: list[ConditionSchema] = Field(default_factory=list)
    action: dict = Field(...)
    status: str = Field("draft", pattern="^(draft|active|paused)$")
    max_per_hour: int = Field(30, ge=1, le=500)


class RulePatch(BaseModel):
    name: str | None = None
    trigger_signal: str | None = None
    conditions: list[ConditionSchema] | None = None
    action: dict | None = None
    status: str | None = Field(None, pattern="^(draft|active|paused|disabled)$")
    max_per_hour: int | None = Field(None, ge=1, le=500)


class RuleResponse(BaseModel):
    id: int
    shop_domain: str
    name: str
    trigger_signal: str
    conditions: list[dict]
    action: dict
    status: str
    max_per_hour: int
    fired_count: int
    last_fired_at: str | None
    created_at: str | None


class TestRulePayload(BaseModel):
    payload: dict


def _to_response(r: MerchantRule) -> RuleResponse:
    return RuleResponse(
        id=r.id,
        shop_domain=r.shop_domain,
        name=r.name,
        trigger_signal=r.trigger_signal,
        conditions=list(r.conditions or []),
        action=dict(r.action or {}),
        status=r.status,
        max_per_hour=r.max_per_hour,
        fired_count=r.fired_count or 0,
        last_fired_at=r.last_fired_at.isoformat() if r.last_fired_at else None,
        created_at=r.created_at.isoformat() if r.created_at else None,
    )


def _validate_action(action: dict) -> None:
    if not isinstance(action, dict):
        raise HTTPException(422, "action must be a dict")
    kind = action.get("type")
    if kind not in _ALLOWED_ACTIONS:
        raise HTTPException(
            422, f"action.type must be one of {sorted(_ALLOWED_ACTIONS)}"
        )


@router.get("/catalog")
def catalog():
    return {
        "triggers": ALLOWED_TRIGGERS,
        "actions": [
            {"id": "send_klaviyo_event", "label": "Send event to Klaviyo"},
            {"id": "notify_slack", "label": "Notify Slack channel"},
            {"id": "create_nudge", "label": "Show a nudge to the visitor"},
            {"id": "write_note", "label": "Write a note in the dashboard"},
            {"id": "emit_ops_alert", "label": "Raise an internal alert"},
        ],
        "ops": ["eq", "ne", "gt", "lt", "gte", "lte", "contains", "in", "regex"],
    }


@router.get("", response_model=list[RuleResponse])
def list_rules(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(MerchantRule)
        .filter(MerchantRule.shop_domain == shop)
        .order_by(MerchantRule.created_at.desc())
        .all()
    )
    return [_to_response(r) for r in rows]


@router.post("", response_model=RuleResponse, status_code=201)
def create_rule(
    payload: RuleCreate,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    _validate_action(payload.action)
    rule = MerchantRule(
        shop_domain=shop,
        name=payload.name,
        trigger_signal=payload.trigger_signal,
        conditions=[c.model_dump() for c in payload.conditions],
        action=payload.action,
        status=payload.status,
        max_per_hour=payload.max_per_hour,
        created_by=shop,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _to_response(rule)


@router.patch("/{rule_id}", response_model=RuleResponse)
def patch_rule(
    rule_id: int,
    payload: RulePatch,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    rule = db.query(MerchantRule).filter(MerchantRule.id == rule_id).first()
    if not rule or rule.shop_domain != shop:
        raise HTTPException(404, "rule_not_found")
    if payload.action is not None:
        _validate_action(payload.action)
        rule.action = payload.action
    if payload.name is not None:
        rule.name = payload.name
    if payload.trigger_signal is not None:
        rule.trigger_signal = payload.trigger_signal
    if payload.conditions is not None:
        rule.conditions = [c.model_dump() for c in payload.conditions]
    if payload.status is not None:
        rule.status = payload.status
    if payload.max_per_hour is not None:
        rule.max_per_hour = payload.max_per_hour
    db.commit()
    db.refresh(rule)
    return _to_response(rule)


@router.delete("/{rule_id}")
def delete_rule(
    rule_id: int,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    rule = db.query(MerchantRule).filter(MerchantRule.id == rule_id).first()
    if not rule or rule.shop_domain != shop:
        raise HTTPException(404, "rule_not_found")
    db.delete(rule)
    db.commit()
    return {"ok": True, "id": rule_id}


@router.post("/{rule_id}/test")
def test_rule(
    rule_id: int,
    payload: TestRulePayload,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Dry-run: check if a rule's conditions match a given payload. No fire."""
    rule = db.query(MerchantRule).filter(MerchantRule.id == rule_id).first()
    if not rule or rule.shop_domain != shop:
        raise HTTPException(404, "rule_not_found")
    matched = _eval_all(list(rule.conditions or []), payload.payload)
    return {
        "rule_id": rule_id,
        "matched": matched,
        "would_fire": matched and rule.status == "active",
        "action_type": (rule.action or {}).get("type"),
    }
