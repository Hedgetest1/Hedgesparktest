"""
trust_contract.py — Delegated Autonomy engine.

THE killer feature: merchants pre-authorize the system to take
revenue-optimizing actions within bounds they control, so autonomous
optimization actually happens at scale instead of rotting in an
approval queue no one clicks.

Public API
----------
- get_active_contract(db, shop, action_type) -> TrustContract | None
- can_execute(db, shop, action_type, proposed) -> CanExecuteResult
- record_execution(db, contract, action_type, target_url, params) -> TrustExecutionLog
- create_contract(db, **kwargs) -> TrustContract
- update_contract(db, contract_id, **updates) -> TrustContract
- revoke_contract(db, contract_id, reason) -> TrustContract
- panic_stop(db, shop, reason) -> int                # emergency revoke-all
- list_contracts(db, shop) -> list[TrustContract]
- list_executions(db, shop, limit) -> list[TrustExecutionLog]
- attach_outcome(db, execution_id, outcome, revenue_delta)

Design notes
------------
- Quota counters are Redis-backed (hs:trust_quota:{shop}:{action_type}:{window})
  with TTL matching the window. Fail-closed on Redis down — if we can't
  verify the quota, we don't execute (safer than over-executing).
- `can_execute` is the single chokepoint; every auto-execute path must
  call it. Integration with action_agent + bugfix_pipeline lives there.
- Auto-pause on revenue drop is computed just-in-time inside can_execute
  so a bad run triggers immediate brake without requiring a background job.
- All writes emit audit_log entries so compliance + forensic trails are
  intact — this is the kind of automation that needs to be defensible.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.models.trust_contract import TrustContract, TrustExecutionLog

log = logging.getLogger("trust_contract")

_QUOTA_REDIS_PREFIX = "hs:trust_quota"
_REV_DROP_WINDOW_HOURS = 24
_DEFAULT_ALLOWED_ACTION_TYPES = frozenset({
    "SCARCITY_NUDGE",
    "RETARGET_HOT_TRAFFIC",
    "PRICE_TEST",
    "FLASH_INCENTIVE",
})


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class CanExecuteResult:
    allowed: bool
    reason: str
    contract_id: int | None = None
    remaining_today: int | None = None
    remaining_week: int | None = None


# ---------------------------------------------------------------------------
# Contract lookups
# ---------------------------------------------------------------------------

def get_active_contract(
    db: Session, shop_domain: str, action_type: str
) -> TrustContract | None:
    return (
        db.query(TrustContract)
        .filter(
            TrustContract.shop_domain == shop_domain,
            TrustContract.action_type == action_type,
            TrustContract.status == "active",
        )
        .order_by(TrustContract.id.desc())
        .first()
    )


def list_contracts(db: Session, shop_domain: str) -> list[TrustContract]:
    return (
        db.query(TrustContract)
        .filter(TrustContract.shop_domain == shop_domain)
        .order_by(TrustContract.created_at.desc())
        .all()
    )


def list_executions(
    db: Session, shop_domain: str, limit: int = 50
) -> list[TrustExecutionLog]:
    return (
        db.query(TrustExecutionLog)
        .filter(TrustExecutionLog.shop_domain == shop_domain)
        .order_by(TrustExecutionLog.executed_at.desc())
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# Quota counters (Redis)
# ---------------------------------------------------------------------------

def _quota_key(shop_domain: str, action_type: str, window: str) -> str:
    return f"{_QUOTA_REDIS_PREFIX}:{shop_domain}:{action_type}:{window}"


def _get_quota_usage(shop_domain: str, action_type: str) -> tuple[int, int] | None:
    """Return (today, week) counts or None if Redis unavailable."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("trust_contract.quota_read")
            return None
        today_raw = rc.get(_quota_key(shop_domain, action_type, "day"))
        week_raw = rc.get(_quota_key(shop_domain, action_type, "week"))
        return (
            int(today_raw) if today_raw else 0,
            int(week_raw) if week_raw else 0,
        )
    except Exception:
        return None


def _increment_quota(shop_domain: str, action_type: str) -> None:
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("trust_contract.quota_incr")
            return
        day_key = _quota_key(shop_domain, action_type, "day")
        week_key = _quota_key(shop_domain, action_type, "week")
        pipe = rc.pipeline(transaction=False)
        pipe.incr(day_key)
        pipe.expire(day_key, 86400)
        pipe.incr(week_key)
        pipe.expire(week_key, 7 * 86400)
        pipe.execute()
    except Exception as exc:
        log.warning("trust_contract: quota increment failed: %s", exc)


# ---------------------------------------------------------------------------
# Auto-pause detection (rev drop)
# ---------------------------------------------------------------------------

def _recent_revenue_drop_pct(db: Session, shop_domain: str) -> float | None:
    """Compare last 24h revenue vs preceding 24h.
    Returns drop_pct (positive number means drop), or None if insufficient data.
    """
    try:
        now = _now()
        row = db.execute(
            sql_text(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN created_at >= :c24 THEN total_price ELSE 0 END), 0) AS recent,
                    COALESCE(SUM(CASE WHEN created_at >= :c48 AND created_at < :c24
                                      THEN total_price ELSE 0 END), 0) AS prior
                FROM shop_orders
                WHERE shop_domain = :shop AND created_at >= :c48
                """
            ),
            {
                "shop": shop_domain,
                "c24": now - timedelta(hours=24),
                "c48": now - timedelta(hours=48),
            },
        ).fetchone()
        if not row:
            return None
        recent = float(row[0] or 0)
        prior = float(row[1] or 0)
        if prior <= 0:
            return None
        drop_pct = ((prior - recent) / prior) * 100.0
        return drop_pct
    except Exception as exc:
        log.warning("trust_contract: rev drop computation failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Central gate
# ---------------------------------------------------------------------------

def can_execute(
    db: Session,
    *,
    shop_domain: str,
    action_type: str,
    confidence: float | None = None,
    discount_pct: float | None = None,
    has_holdout: bool = False,
    target_url: str | None = None,
) -> CanExecuteResult:
    """Single chokepoint. Every auto-execute path must call this.

    Returns (allowed, reason). If not allowed, the action must be queued
    for human approval OR dropped, per the caller's policy.
    """
    contract = get_active_contract(db, shop_domain, action_type)
    if contract is None:
        return CanExecuteResult(False, "no_active_contract")

    if contract.status != "active":
        return CanExecuteResult(False, f"contract_{contract.status}", contract.id)

    # --- Scope check ---
    if contract.scope_type != "all":
        try:
            scope_values = json.loads(contract.scope_values or "[]")
        except Exception:
            scope_values = []
        if target_url and contract.scope_type == "products":
            if target_url not in scope_values:
                return CanExecuteResult(False, "target_out_of_scope", contract.id)
        # collections/tags scope resolution would need the Product model —
        # left as a v1.1 extension. For now, all-or-products is enough.

    # --- Quota check ---
    usage = _get_quota_usage(shop_domain, action_type)
    if usage is None:
        # Fail-closed on Redis failure — cannot verify quota
        return CanExecuteResult(False, "quota_check_unavailable", contract.id)
    today_used, week_used = usage
    if today_used >= contract.max_per_day:
        return CanExecuteResult(
            False, f"quota_day_exhausted:{today_used}/{contract.max_per_day}",
            contract.id, 0, max(0, contract.max_per_week - week_used),
        )
    if week_used >= contract.max_per_week:
        return CanExecuteResult(
            False, f"quota_week_exhausted:{week_used}/{contract.max_per_week}",
            contract.id, max(0, contract.max_per_day - today_used), 0,
        )

    # --- Confidence gate ---
    if confidence is not None and confidence < contract.confidence_threshold:
        return CanExecuteResult(
            False,
            f"confidence_below_threshold:{confidence:.2f}<{contract.confidence_threshold:.2f}",
            contract.id,
        )

    # --- Discount bounds ---
    if discount_pct is not None:
        if discount_pct < contract.discount_floor_pct:
            return CanExecuteResult(
                False,
                f"discount_below_floor:{discount_pct:.1f}<{contract.discount_floor_pct:.1f}",
                contract.id,
            )
        if discount_pct > contract.discount_ceiling_pct:
            return CanExecuteResult(
                False,
                f"discount_above_ceiling:{discount_pct:.1f}>{contract.discount_ceiling_pct:.1f}",
                contract.id,
            )

        # β3 — COGS-aware margin guard. Even if a discount is inside the
        # contract's own floor, we refuse if the merchant's true profit
        # margin (from pnl_engine) would drop below the safety threshold.
        # This protects merchants from themselves: a -5% contract is fine
        # for a 70%-margin shop but would wreck a 22%-margin shop.
        try:
            from app.services.margin_guard import check_discount_safe
            margin_result = check_discount_safe(db, shop_domain, discount_pct)
            if not margin_result.allowed:
                return CanExecuteResult(
                    False,
                    f"margin_guard:{margin_result.reason}",
                    contract.id,
                )
        except Exception as exc:
            log.debug("trust_contract: margin guard errored (non-fatal): %s", exc)
            # Fail-closed on error — safer than letting a potentially
            # under-margin discount through.
            return CanExecuteResult(
                False, "margin_guard_error", contract.id,
            )

    # --- Holdout requirement ---
    if contract.require_holdout and not has_holdout:
        return CanExecuteResult(False, "holdout_required", contract.id)

    # --- Auto-pause on revenue drop ---
    drop_pct = _recent_revenue_drop_pct(db, shop_domain)
    if drop_pct is not None and drop_pct >= contract.auto_pause_on_drop_pct:
        # Auto-pause the contract as a side effect — the next call will
        # see status=paused and skip the expensive checks.
        contract.status = "paused"
        contract.revoked_at = _now()
        contract.revoked_reason = f"auto_pause:rev_drop_{drop_pct:.1f}%"
        try:
            db.flush()
        except Exception:
            pass
        return CanExecuteResult(
            False, f"auto_paused_rev_drop:{drop_pct:.1f}%", contract.id,
        )

    return CanExecuteResult(
        True, "allowed",
        contract.id,
        max(0, contract.max_per_day - today_used - 1),
        max(0, contract.max_per_week - week_used - 1),
    )


def record_execution(
    db: Session,
    *,
    contract: TrustContract,
    target_url: str | None,
    confidence: float | None,
    discount_pct: float | None,
    holdout_pct: int | None,
    params: dict[str, Any] | None = None,
) -> TrustExecutionLog:
    """Log an auto-execution and bump quota counters. Call AFTER can_execute
    returned allowed=True and AFTER the action was successfully created."""
    _increment_quota(contract.shop_domain, contract.action_type)

    log_row = TrustExecutionLog(
        contract_id=contract.id,
        shop_domain=contract.shop_domain,
        action_type=contract.action_type,
        target_url=target_url,
        confidence=confidence,
        discount_pct_applied=discount_pct,
        holdout_pct_applied=holdout_pct,
        params_json=json.dumps(params, default=str) if params else None,
        outcome="pending",
    )
    db.add(log_row)
    db.flush()

    # Audit trail — defensible record of autonomous action
    try:
        from app.services.audit import write_audit_log
        write_audit_log(
            db,
            actor_type="system",
            actor_name="trust_contract_executor",
            action_type="trust_auto_execute",
            target_type="trust_contract",
            target_id=str(contract.id),
            status="completed",
            approval_mode="delegated_autonomous",
            shop_domain=contract.shop_domain,
            metadata={
                "execution_id": log_row.id,
                "action_type": contract.action_type,
                "target_url": target_url,
                "confidence": confidence,
                "discount_pct": discount_pct,
                "holdout_pct": holdout_pct,
            },
        )
    except Exception as exc:
        log.debug("trust_contract: audit log write failed (non-fatal): %s", exc)

    # Phase Ω'' — outbound webhook fan-out
    try:
        from app.services.event_emitter import emit
        emit(db, contract.shop_domain, "trust_contract.executed", {
            "execution_id": log_row.id,
            "contract_id": contract.id,
            "action_type": contract.action_type,
            "target_url": target_url,
            "confidence": confidence,
            "discount_pct": discount_pct,
            "holdout_pct": holdout_pct,
        })
    except Exception:
        pass

    # β6 — emit to the internal event bus (ClickHouse-shaped), so the
    # analytics dashboards can aggregate trust executions alongside
    # visitor events without querying the transactional tables.
    try:
        from app.services.event_bus import emit as bus_emit
        bus_emit(
            "trust_action_executed",
            shop_domain=contract.shop_domain,
            product_url=target_url,
            props={
                "contract_id": contract.id,
                "action_type": contract.action_type,
                "confidence": confidence,
                "discount_pct": discount_pct,
                "execution_id": log_row.id,
            },
        )
    except Exception:
        pass

    # β4 — forward to Klaviyo so merchants can build flows on autonomous
    # executions ("when HedgeSpark fires a price test, notify my team").
    try:
        from app.services.klaviyo_events import forward_event_async, is_shop_connected
        if is_shop_connected(db, contract.shop_domain):
            from app.models.merchant import Merchant
            m = db.query(Merchant).filter(Merchant.shop_domain == contract.shop_domain).first()
            merchant_email = getattr(m, "contact_email", None) if m else None
            if merchant_email:
                forward_event_async(
                    shop_domain=contract.shop_domain,
                    event_name="trust_action_executed",
                    email=merchant_email,
                    properties={
                        "contract_id": contract.id,
                        "action_type": contract.action_type,
                        "target_url": target_url,
                        "confidence": confidence,
                        "discount_pct": discount_pct,
                        "execution_id": log_row.id,
                    },
                )
    except Exception as exc:
        log.warning("trust_contract: klaviyo forward failed (non-fatal): %s", exc)

    return log_row


def attach_outcome(
    db: Session,
    execution_id: int,
    *,
    outcome: str,
    revenue_delta_eur: float | None = None,
) -> None:
    """Attach an outcome measurement to a prior execution. Called by the
    outcome evaluator 48h post-execution."""
    row = db.get(TrustExecutionLog, execution_id)
    if row is None:
        return
    row.outcome = outcome
    row.revenue_delta_eur = revenue_delta_eur
    row.measured_at = _now()
    db.flush()


# ---------------------------------------------------------------------------
# Contract CRUD
# ---------------------------------------------------------------------------

def create_contract(
    db: Session,
    *,
    shop_domain: str,
    action_type: str,
    max_per_day: int = 3,
    max_per_week: int = 10,
    discount_floor_pct: float = -5.0,
    discount_ceiling_pct: float = 0.0,
    confidence_threshold: float = 0.80,
    auto_pause_on_drop_pct: float = 15.0,
    require_holdout: bool = True,
    scope_type: str = "all",
    scope_values: list[str] | None = None,
    created_by: str | None = None,
    note: str | None = None,
) -> TrustContract:
    """Create a new active contract. Revokes any existing active contract
    for the same (shop, action_type) — there's always at most one active
    contract per (shop, action_type)."""
    if action_type not in _DEFAULT_ALLOWED_ACTION_TYPES:
        raise ValueError(f"action_type not allowed: {action_type}")
    if not (0.0 <= confidence_threshold <= 1.0):
        raise ValueError("confidence_threshold must be in [0, 1]")
    if max_per_day < 0 or max_per_week < 0:
        raise ValueError("quotas must be non-negative")
    if max_per_day > max_per_week:
        raise ValueError("max_per_day cannot exceed max_per_week")
    if discount_floor_pct > discount_ceiling_pct:
        raise ValueError("discount_floor_pct cannot exceed discount_ceiling_pct")
    if scope_type not in ("all", "products", "collections", "tags"):
        raise ValueError(f"invalid scope_type: {scope_type}")
    if scope_type != "all" and not scope_values:
        raise ValueError(f"scope_values required for scope_type={scope_type}")

    # Revoke any existing active contract (single-active invariant)
    existing = get_active_contract(db, shop_domain, action_type)
    if existing is not None:
        existing.status = "revoked"
        existing.revoked_at = _now()
        existing.revoked_reason = "superseded"
        db.flush()

    contract = TrustContract(
        shop_domain=shop_domain,
        action_type=action_type,
        max_per_day=max_per_day,
        max_per_week=max_per_week,
        discount_floor_pct=discount_floor_pct,
        discount_ceiling_pct=discount_ceiling_pct,
        confidence_threshold=confidence_threshold,
        auto_pause_on_drop_pct=auto_pause_on_drop_pct,
        require_holdout=require_holdout,
        scope_type=scope_type,
        scope_values=json.dumps(scope_values) if scope_values else None,
        created_by=created_by,
        note=note,
        status="active",
    )
    db.add(contract)
    db.flush()

    # Audit trail — merchant granting autonomy is a material event
    try:
        from app.services.audit import write_audit_log
        write_audit_log(
            db,
            actor_type="merchant" if created_by else "system",
            actor_name=created_by or "system",
            action_type="trust_contract_created",
            target_type="trust_contract",
            target_id=str(contract.id),
            status="completed",
            approval_mode="explicit",
            shop_domain=shop_domain,
            metadata={
                "action_type": action_type,
                "max_per_day": max_per_day,
                "max_per_week": max_per_week,
                "confidence_threshold": confidence_threshold,
            },
        )
    except Exception as exc:
        log.debug("trust_contract: audit log write failed (non-fatal): %s", exc)

    return contract


def update_contract(
    db: Session, contract_id: int, **updates: Any
) -> TrustContract | None:
    contract = db.get(TrustContract, contract_id)
    if contract is None:
        return None
    _ALLOWED_UPDATES = {
        "max_per_day", "max_per_week",
        "discount_floor_pct", "discount_ceiling_pct",
        "confidence_threshold", "auto_pause_on_drop_pct", "require_holdout",
        "scope_type", "scope_values", "note", "status",
    }
    for k, v in updates.items():
        if k not in _ALLOWED_UPDATES:
            continue
        if k == "scope_values" and isinstance(v, list):
            v = json.dumps(v)
        setattr(contract, k, v)
    contract.updated_at = _now()
    db.flush()
    return contract


def revoke_contract(
    db: Session, contract_id: int, reason: str = "merchant"
) -> TrustContract | None:
    contract = db.get(TrustContract, contract_id)
    if contract is None:
        return None
    if contract.status == "revoked":
        return contract
    contract.status = "revoked"
    contract.revoked_at = _now()
    contract.revoked_reason = reason
    db.flush()
    try:
        from app.services.audit import write_audit_log
        write_audit_log(
            db,
            actor_type="merchant" if reason == "merchant" else "system",
            actor_name=reason,
            action_type="trust_contract_revoked",
            target_type="trust_contract",
            target_id=str(contract.id),
            status="completed",
            approval_mode="explicit",
            shop_domain=contract.shop_domain,
            metadata={"reason": reason, "action_type": contract.action_type},
        )
    except Exception:
        pass
    return contract


def panic_stop(db: Session, shop_domain: str, reason: str = "panic") -> int:
    """Revoke every active trust contract for a shop. The red button.

    Returns the number of contracts revoked.
    """
    rows = (
        db.query(TrustContract)
        .filter(
            TrustContract.shop_domain == shop_domain,
            TrustContract.status == "active",
        )
        .all()
    )
    now = _now()
    for c in rows:
        c.status = "revoked"
        c.revoked_at = now
        c.revoked_reason = reason
    db.flush()
    try:
        from app.services.audit import write_audit_log
        write_audit_log(
            db,
            actor_type="merchant",
            actor_name="panic_stop",
            action_type="trust_panic_stop",
            target_type="shop",
            target_id=shop_domain,
            status="completed",
            approval_mode="explicit",
            shop_domain=shop_domain,
            metadata={"revoked_count": len(rows), "reason": reason},
        )
    except Exception:
        pass
    return len(rows)
