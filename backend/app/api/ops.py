"""
ops.py — Internal operator API for ops alerts and GDPR export retrieval.

All endpoints require X-API-Key header (DASHBOARD_API_KEY).
These are NOT merchant-facing — they are for operators, scripts, and AI agents.

GET    /ops/alerts               — list unresolved alerts
GET    /ops/alerts/recent        — list recent alerts (resolved + unresolved)
POST   /ops/alerts/{id}/resolve  — mark an alert as resolved
GET    /ops/gdpr/exports         — list completed data exports
GET    /ops/gdpr/exports/{id}    — retrieve a specific export
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_operator

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ops", tags=["ops"])


# ---------------------------------------------------------------------------
# Orchestrator Readiness
# ---------------------------------------------------------------------------

@router.get("/readiness/orchestrator")
def orchestrator_readiness(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Check orchestrator activation readiness for supervised hybrid mode."""
    import os
    from app.services.orchestrator import ORCHESTRATOR_MODE, ACTION_REGISTRY, TIER_0, TIER_1, TIER_2

    mode = ORCHESTRATOR_MODE
    anthropic_key = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    openai_key = bool(os.getenv("OPENAI_API_KEY", "").strip())
    operator_key = bool(os.getenv("DASHBOARD_API_KEY", "").strip())
    slack_url = bool(os.getenv("OPS_SLACK_WEBHOOK_URL", "").strip())

    missing = []
    warnings = []

    if mode in ("proposal", "hybrid") and not anthropic_key and not openai_key:
        missing.append("ANTHROPIC_API_KEY or OPENAI_API_KEY required for LLM mode")
    if not operator_key:
        missing.append("DASHBOARD_API_KEY required for approval API")
    if mode == "hybrid" and not slack_url:
        warnings.append("OPS_SLACK_WEBHOOK_URL not set — approval notifications will be DB-only")
    if mode not in ("deterministic", "proposal", "hybrid"):
        missing.append(f"ORCHESTRATOR_MODE='{mode}' is invalid — must be deterministic|proposal|hybrid")

    # Summarize action registry
    tier_counts = {0: 0, 1: 0, 2: 0}
    for name, entry in ACTION_REGISTRY.items():
        tier = entry[2] if len(entry) >= 3 else 2
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    ready = len(missing) == 0

    return {
        "ready": ready,
        "mode": mode,
        "llm_available": anthropic_key or openai_key,
        "llm_provider": "anthropic" if anthropic_key else ("openai" if openai_key else "none"),
        "provider_policy": _get_provider_policy(),
        "slack_configured": slack_url,
        "operator_key_configured": operator_key,
        "actions": {
            "total": len(ACTION_REGISTRY),
            "tier_0_auto": tier_counts.get(0, 0),
            "tier_1_approval": tier_counts.get(1, 0),
            "tier_2_human_only": tier_counts.get(2, 0),
        },
        "missing_requirements": missing,
        "warnings": warnings,
        "promotion": _get_promotion_readiness(),
        "model_config": _get_model_config_summary(db),
    }


def _get_provider_policy() -> dict:
    from app.core.llm_router import get_provider_policy
    return get_provider_policy()


def _get_model_config_summary(db: Session) -> dict:
    """Model config visibility for readiness endpoint."""
    try:
        from app.services.model_config import get_all_active_configs
        configs = get_all_active_configs(db)
        return {
            "persistent": True,
            "modules": {c["module"]: {"provider": c["provider"], "model": c["model"], "activated_at": c["activated_at"], "activated_by": c["activated_by"]} for c in configs},
        }
    except Exception:
        return {"persistent": False, "modules": {}}


def _get_promotion_readiness() -> dict:
    from app.services.promotion_pipeline import is_promotion_ready
    ready, reasons = is_promotion_ready()
    from sqlalchemy import text as _text
    from app.core.database import SessionLocal
    stats = {"pending": 0, "pushed_awaiting_ci": 0, "prs_open": 0}
    try:
        _db = SessionLocal()
        from app.models.autofix_promotion import AutoFixPromotion
        stats["pending"] = _db.query(AutoFixPromotion).filter(AutoFixPromotion.status.in_(["pending", "branch_created"])).count()
        stats["pushed_awaiting_ci"] = _db.query(AutoFixPromotion).filter(
            AutoFixPromotion.status == "pushed",
            AutoFixPromotion.remote_ci_status.in_([None, "queued", "in_progress"]),
        ).count()
        stats["prs_open"] = _db.query(AutoFixPromotion).filter(
            AutoFixPromotion.pr_url.isnot(None),
            AutoFixPromotion.status.notin_(["merged", "rejected"]),
        ).count()
        _db.close()
    except Exception:
        pass
    return {
        "auto_promotion_ready": ready,
        "not_ready_reasons": reasons,
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# LLM Budget
# ---------------------------------------------------------------------------

@router.get("/llm-budget")
def get_llm_budget(
    _auth: bool = Depends(require_operator),
):
    """Return current LLM usage summary and limits."""
    from app.core.llm_budget import get_usage_summary
    return get_usage_summary()


# ---------------------------------------------------------------------------
# Ops Alerts
# ---------------------------------------------------------------------------

@router.get("/alerts")
def list_unresolved_alerts(
    severity: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """List unresolved operational alerts."""
    from app.services.alerting import get_unresolved_alerts
    alerts = get_unresolved_alerts(db, severity=severity, limit=limit)
    return [
        {
            "id": a.id,
            "created_at": a.created_at.isoformat() + "Z" if a.created_at else None,
            "severity": a.severity,
            "source": a.source,
            "alert_type": a.alert_type,
            "shop_domain": a.shop_domain,
            "summary": a.summary,
            "detail": a.detail,
            "resolved": a.resolved,
        }
        for a in alerts
    ]


@router.get("/alerts/recent")
def list_recent_alerts(
    limit: int = Query(default=50, ge=1, le=200),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """List recent alerts (both resolved and unresolved)."""
    from app.models.ops_alert import OpsAlert
    alerts = (
        db.query(OpsAlert)
        .order_by(OpsAlert.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": a.id,
            "created_at": a.created_at.isoformat() + "Z" if a.created_at else None,
            "severity": a.severity,
            "source": a.source,
            "alert_type": a.alert_type,
            "shop_domain": a.shop_domain,
            "summary": a.summary,
            "resolved": a.resolved,
            "resolved_at": a.resolved_at.isoformat() + "Z" if a.resolved_at else None,
        }
        for a in alerts
    ]


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert_endpoint(
    alert_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Mark an alert as resolved."""
    from app.services.alerting import resolve_alert
    from app.models.ops_alert import OpsAlert
    alert = db.query(OpsAlert).get(alert_id)
    if not alert:
        raise HTTPException(404, "Alert not found")
    if alert.resolved:
        return {"status": "already_resolved", "id": alert_id}
    resolve_alert(db, alert_id)
    db.commit()
    return {"status": "resolved", "id": alert_id}


# ---------------------------------------------------------------------------
# GDPR Export Retrieval
# ---------------------------------------------------------------------------

@router.get("/gdpr/exports")
def list_gdpr_exports(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """List GDPR data request exports (customers_data_request type only)."""
    from app.models.gdpr_request import GdprRequest
    q = db.query(GdprRequest).filter(GdprRequest.request_type == "customers_data_request")
    if status:
        q = q.filter(GdprRequest.status == status)
    rows = q.order_by(GdprRequest.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "shop_domain": r.shop_domain,
            "customer_id": r.customer_id,
            "status": r.status,
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
            "processed_at": r.processed_at.isoformat() + "Z" if r.processed_at else None,
            "has_export": r.result_summary is not None and r.status == "completed",
        }
        for r in rows
    ]


@router.get("/gdpr/exports/{request_id}")
def get_gdpr_export(
    request_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Retrieve a specific GDPR customer data export.

    Returns the structured export payload for completed requests.
    Pending/failed requests return status only (no data).
    """
    from app.models.gdpr_request import GdprRequest
    req = db.query(GdprRequest).get(request_id)
    if not req:
        raise HTTPException(404, "GDPR request not found")
    if req.request_type != "customers_data_request":
        raise HTTPException(400, "Not a data request export")

    base = {
        "id": req.id,
        "shop_domain": req.shop_domain,
        "customer_id": req.customer_id,
        "status": req.status,
        "created_at": req.created_at.isoformat() + "Z" if req.created_at else None,
        "processed_at": req.processed_at.isoformat() + "Z" if req.processed_at else None,
    }

    if req.status == "completed" and req.result_summary:
        try:
            base["export"] = json.loads(req.result_summary)
        except (json.JSONDecodeError, ValueError):
            base["export"] = req.result_summary
    elif req.status == "failed":
        base["error"] = req.error_detail
    elif req.status == "pending":
        base["note"] = "Export is queued and will be processed within the next worker cycle."

    return base


# ---------------------------------------------------------------------------
# Action Approvals (TIER_1 human-gated execution)
# ---------------------------------------------------------------------------

@router.get("/approvals")
def list_pending_approvals(
    limit: int = Query(default=20, ge=1, le=100),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """List pending action approvals awaiting human decision."""
    from app.models.action_approval import ActionApproval
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Expire old approvals first
    db.execute(text(
        "UPDATE action_approvals SET status = 'expired' "
        "WHERE status = 'pending' AND expires_at < :now"
    ), {"now": now})
    db.commit()

    approvals = (
        db.query(ActionApproval)
        .filter(ActionApproval.status == "pending")
        .order_by(ActionApproval.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": a.id,
            "action_type": a.action_type,
            "target_id": a.target_id,
            "shop_domain": a.shop_domain,
            "status": a.status,
            "created_at": a.created_at.isoformat() + "Z" if a.created_at else None,
            "expires_at": a.expires_at.isoformat() + "Z" if a.expires_at else None,
            "audit_log_id": a.audit_log_id,
        }
        for a in approvals
    ]


@router.post("/approvals/{approval_id}/approve")
def approve_action(
    approval_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Approve and execute a pending TIER_1 action.

    Executes the action through ACTION_REGISTRY (same safety path as orchestrator).
    Writes audit_log with actor_name="human_approval".
    """
    from app.models.action_approval import ActionApproval
    from app.services.orchestrator import ACTION_REGISTRY, _is_on_cooldown, _set_cooldown
    from app.services.audit import write_audit_log
    from app.services.outcome_evaluator import record_pending_outcome
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    approval = db.query(ActionApproval).get(approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")
    if approval.status != "pending":
        raise HTTPException(409, f"Approval already {approval.status}")
    if approval.expires_at < now:
        approval.status = "expired"
        db.commit()
        raise HTTPException(410, "Approval expired")

    # Validate action still exists in registry
    entry = ACTION_REGISTRY.get(approval.action_type)
    if not entry:
        raise HTTPException(400, f"Unknown action: {approval.action_type}")
    action_fn = entry[0]

    # Cooldown check (advisory — human can override but we warn)
    if _is_on_cooldown(approval.action_type, approval.target_id or ""):
        log.warning("ops: executing approved action despite cooldown: %s %s", approval.action_type, approval.target_id)

    # Execute
    try:
        exec_result = action_fn(db, approval.target_id or "")
    except Exception as exc:
        approval.status = "approved"
        approval.decided_at = now
        approval.decided_by = "operator"
        approval.reason = f"approved but execution failed: {str(exc)[:200]}"
        db.commit()
        raise HTTPException(500, f"Action execution failed: {str(exc)[:200]}")

    # Update approval
    approval.status = "approved"
    approval.decided_at = now
    approval.decided_by = "operator"

    # Audit
    audit_entry = write_audit_log(
        db,
        actor_type="human",
        actor_name="human_approval",
        action_type=f"approved_{approval.action_type}",
        target_type="system",
        target_id=approval.target_id,
        shop_domain=approval.shop_domain,
        after_state={"result": exec_result, "approval_id": approval_id},
        status="completed",
        approval_mode="human_approved",
    )

    # Outcome tracking
    record_pending_outcome(
        db,
        audit_log_id=audit_entry.id,
        action_type=f"approved_{approval.action_type}",
        target_id=approval.target_id,
        shop_domain=approval.shop_domain,
    )

    _set_cooldown(approval.action_type, approval.target_id or "")
    db.commit()

    return {
        "status": "approved_and_executed",
        "approval_id": approval_id,
        "action_type": approval.action_type,
        "target_id": approval.target_id,
        "result": exec_result,
    }


@router.post("/approvals/{approval_id}/reject")
def reject_action(
    approval_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Reject a pending action approval."""
    from app.models.action_approval import ActionApproval
    from app.services.audit import write_audit_log
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    approval = db.query(ActionApproval).get(approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")
    if approval.status != "pending":
        raise HTTPException(409, f"Approval already {approval.status}")

    approval.status = "rejected"
    approval.decided_at = now
    approval.decided_by = "operator"

    write_audit_log(
        db,
        actor_type="human",
        actor_name="human_approval",
        action_type=f"rejected_{approval.action_type}",
        target_type="system",
        target_id=approval.target_id,
        shop_domain=approval.shop_domain,
        status="rejected",
        approval_mode="human_approved",
    )

    db.commit()
    return {"status": "rejected", "approval_id": approval_id}


# ---------------------------------------------------------------------------
# Bug Fix Candidates
# ---------------------------------------------------------------------------

@router.get("/bugfixes")
def list_bugfixes(
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """List bug fix candidates."""
    from app.models.bugfix_candidate import BugFixCandidate
    q = db.query(BugFixCandidate)
    if status:
        q = q.filter(BugFixCandidate.status == status)
    rows = q.order_by(BugFixCandidate.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
            "status": r.status,
            "source_type": r.source_type,
            "source_ref": r.source_ref,
            "title": r.title,
            "has_patch": r.patch_diff is not None and len(r.patch_diff or "") > 0,
            "patch_risk_tier": getattr(r, "patch_risk_tier", None),
        }
        for r in rows
    ]


@router.get("/bugfixes/{candidate_id}")
def get_bugfix(
    candidate_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Get full bug fix candidate details including patch."""
    from app.models.bugfix_candidate import BugFixCandidate
    c = db.query(BugFixCandidate).get(candidate_id)
    if not c:
        raise HTTPException(404, "Bug fix candidate not found")
    return {
        "id": c.id,
        "created_at": c.created_at.isoformat() + "Z" if c.created_at else None,
        "status": c.status,
        "source_type": c.source_type,
        "source_ref": c.source_ref,
        "title": c.title,
        "summary": c.summary,
        "context": c.context_json,
        "patch_summary": c.patch_summary,
        "patch_diff": c.patch_diff,
        "patch_files": c.patch_files,
        "test_command": c.test_command,
        "test_result": c.test_result,
        "decided_by": c.decided_by,
        "failure_reason": c.failure_reason,
        "proposal_attempted_at": c.proposal_attempted_at.isoformat() + "Z" if getattr(c, "proposal_attempted_at", None) else None,
        "proposal_error": getattr(c, "proposal_error", None),
        "proposal_provider": getattr(c, "proposal_provider", None),
        "git_commit_sha": getattr(c, "git_commit_sha", None),
        "reviewer_assessment_id": getattr(c, "reviewer_assessment_id", None),
    }


@router.post("/bugfixes/{candidate_id}/propose")
def trigger_patch_proposal(
    candidate_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Trigger LLM patch proposal for an open bug fix candidate."""
    from app.services.bugfix_pipeline import propose_patch
    success = propose_patch(db, candidate_id)
    db.commit()
    if success:
        return {"status": "patch_proposed", "candidate_id": candidate_id}
    raise HTTPException(400, "Proposal failed — check candidate status and logs")


@router.post("/bugfixes/{candidate_id}/approve")
def approve_bugfix(
    candidate_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Approve a proposed bug fix (marks as approved, does NOT apply)."""
    from app.models.bugfix_candidate import BugFixCandidate
    from app.services.audit import write_audit_log
    from datetime import datetime, timezone

    c = db.query(BugFixCandidate).get(candidate_id)
    if not c:
        raise HTTPException(404, "Not found")
    if c.status != "patch_proposed":
        raise HTTPException(409, f"Cannot approve — status is {c.status}")

    c.status = "approved"
    c.decided_by = "operator"
    c.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)

    write_audit_log(
        db, actor_type="human", actor_name="human_approval",
        action_type="bugfix_approved", target_type="bugfix",
        target_id=str(c.id), status="completed", approval_mode="human_approved",
        metadata={"title": c.title, "files": c.patch_files},
    )
    db.commit()
    return {"status": "approved", "candidate_id": candidate_id, "note": "Apply patch manually using the diff from GET /ops/bugfixes/{id}"}


@router.post("/bugfixes/{candidate_id}/reject")
def reject_bugfix(
    candidate_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Reject a proposed bug fix."""
    from app.models.bugfix_candidate import BugFixCandidate
    from app.services.audit import write_audit_log
    from datetime import datetime, timezone

    c = db.query(BugFixCandidate).get(candidate_id)
    if not c:
        raise HTTPException(404, "Not found")
    if c.status not in ("open", "analyzed", "patch_proposed"):
        raise HTTPException(409, f"Cannot reject — status is {c.status}")

    c.status = "rejected"
    c.decided_by = "operator"
    c.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)

    write_audit_log(
        db, actor_type="human", actor_name="human_approval",
        action_type="bugfix_rejected", target_type="bugfix",
        target_id=str(c.id), status="rejected", approval_mode="human_approved",
    )
    db.commit()
    return {"status": "rejected", "candidate_id": candidate_id}


@router.post("/bugfixes/{candidate_id}/apply")
def apply_bugfix(
    candidate_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Apply an approved bug fix patch with test verification and rollback.

    Only approved candidates can be applied. The patch is applied to the
    working tree, tests are run, and the backend is restarted. If tests
    or health check fail, the patch is automatically rolled back.
    """
    from app.services.bugfix_pipeline import apply_bugfix_candidate
    result = apply_bugfix_candidate(db, candidate_id)
    db.commit()
    return {
        "status": result.status,
        "candidate_id": candidate_id,
        "test_passed": result.test_passed,
        "health_ok": result.health_ok,
        "failure_reason": result.failure_reason,
        "test_output": result.test_output[:500] if result.test_output else None,
    }


# ---------------------------------------------------------------------------
# AutoFix Promotions
# ---------------------------------------------------------------------------

@router.get("/promotions")
def list_promotions(
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """List autofix promotions."""
    from app.models.autofix_promotion import AutoFixPromotion
    q = db.query(AutoFixPromotion)
    if status:
        q = q.filter(AutoFixPromotion.status == status)
    rows = q.order_by(AutoFixPromotion.created_at.desc()).limit(limit).all()
    return [
        {
            "id": p.id,
            "created_at": p.created_at.isoformat() + "Z" if p.created_at else None,
            "bugfix_candidate_id": p.bugfix_candidate_id,
            "git_commit_sha": p.git_commit_sha,
            "branch_name": p.branch_name,
            "status": p.status,
            "ci_url": p.ci_url,
        }
        for p in rows
    ]


@router.get("/promotions/{promo_id}")
def get_promotion(
    promo_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Get promotion detail."""
    from app.models.autofix_promotion import AutoFixPromotion
    p = db.query(AutoFixPromotion).get(promo_id)
    if not p:
        raise HTTPException(404, "Promotion not found")
    return {
        "id": p.id,
        "created_at": p.created_at.isoformat() + "Z" if p.created_at else None,
        "bugfix_candidate_id": p.bugfix_candidate_id,
        "git_commit_sha": p.git_commit_sha,
        "branch_name": p.branch_name,
        "status": p.status,
        "ci_url": p.ci_url,
        "ci_result": p.ci_result,
        "decided_by": p.decided_by,
        "pushed_at": p.pushed_at.isoformat() + "Z" if p.pushed_at else None,
        "failure_reason": p.failure_reason,
        "pr_url": getattr(p, "pr_url", None),
        "pr_number": getattr(p, "pr_number", None),
        "remote_ci_status": getattr(p, "remote_ci_status", None),
        "remote_ci_url": getattr(p, "remote_ci_url", None),
        "merged_at": p.merged_at.isoformat() + "Z" if getattr(p, "merged_at", None) else None,
        "merge_commit_sha": getattr(p, "merge_commit_sha", None),
        "merge_recommendation": _get_merge_recommendation(db, p.id),
        "merge_outcome": _get_merge_outcome(db, p.id),
    }


def _get_merge_recommendation(db, promo_id):
    try:
        from app.services.merge_intelligence import compute_merge_recommendation
        rec = compute_merge_recommendation(db, promo_id)
        return {"recommend": rec.recommend, "reasons": rec.reasons}
    except Exception:
        return None


def _get_merge_outcome(db, promo_id):
    try:
        from app.models.merge_outcome import MergeOutcome
        o = db.query(MergeOutcome).filter(MergeOutcome.promotion_id == promo_id).first()
        if not o:
            return None
        return {
            "evaluation_status": o.evaluation_status,
            "evaluated_at": o.evaluated_at.isoformat() + "Z" if o.evaluated_at else None,
            "detail": o.detail,
        }
    except Exception:
        return None


@router.post("/promotions/{promo_id}/branch")
def create_branch(
    promo_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Create a local git branch for the promotion."""
    from app.services.promotion_pipeline import create_promotion_branch
    result = create_promotion_branch(db, promo_id)
    db.commit()
    if result.startswith("error") or result.startswith("not_found") or result.startswith("wrong_status"):
        raise HTTPException(400, result)
    return {"status": "branch_created", "branch": result}


@router.post("/promotions/{promo_id}/ci")
def trigger_ci(
    promo_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Run CI verification for the promotion."""
    from app.services.promotion_pipeline import run_promotion_ci_check
    result = run_promotion_ci_check(db, promo_id)
    db.commit()
    return {"status": result, "promotion_id": promo_id}


@router.post("/promotions/{promo_id}/approve")
def approve_promotion(
    promo_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Approve a promotion for push."""
    from app.models.autofix_promotion import AutoFixPromotion
    from datetime import datetime, timezone
    p = db.query(AutoFixPromotion).get(promo_id)
    if not p:
        raise HTTPException(404, "Not found")
    if p.status not in ("ci_passed", "branch_created"):
        raise HTTPException(409, f"Cannot approve — status is {p.status}")
    p.status = "approved"
    p.decided_by = "operator"
    p.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"status": "approved", "promotion_id": promo_id}


@router.post("/promotions/{promo_id}/reject")
def reject_promotion(
    promo_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Reject a promotion."""
    from app.models.autofix_promotion import AutoFixPromotion
    from datetime import datetime, timezone
    p = db.query(AutoFixPromotion).get(promo_id)
    if not p:
        raise HTTPException(404, "Not found")
    if p.status in ("pushed", "rejected"):
        raise HTTPException(409, f"Already {p.status}")
    p.status = "rejected"
    p.decided_by = "operator"
    p.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"status": "rejected", "promotion_id": promo_id}


@router.post("/promotions/{promo_id}/push")
def push_promotion_endpoint(
    promo_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Push the promotion branch to origin. Human-gated."""
    from app.services.promotion_pipeline import push_promotion
    result = push_promotion(db, promo_id)
    db.commit()
    if result == "pushed":
        return {"status": "pushed", "promotion_id": promo_id}
    raise HTTPException(400, result)


@router.get("/promotions/{promo_id}/remote-ci")
def get_remote_ci(
    promo_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Check remote CI status for a pushed promotion."""
    from app.services.promotion_pipeline import check_remote_ci_status
    result = check_remote_ci_status(db, promo_id)
    db.commit()
    return {"status": result, "promotion_id": promo_id}


@router.post("/promotions/{promo_id}/pr")
def create_pr(
    promo_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Create a GitHub PR for the promotion branch."""
    from app.services.promotion_pipeline import create_promotion_pr
    result = create_promotion_pr(db, promo_id)
    db.commit()
    if result.startswith("http"):
        return {"status": "pr_created", "pr_url": result}
    raise HTTPException(400, result)


@router.post("/promotions/{promo_id}/merge")
def merge_pr(
    promo_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Merge the promotion PR. Human-gated."""
    from app.services.promotion_pipeline import merge_promotion
    result = merge_promotion(db, promo_id)
    db.commit()
    if result == "merged":
        return {"status": "merged", "promotion_id": promo_id}
    raise HTTPException(400, result)


# ---------------------------------------------------------------------------
# Evolution Proposals
# ---------------------------------------------------------------------------

@router.get("/evolution")
def list_evolution_proposals(
    status: str | None = Query(default=None),
    risk_level: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """List evolution proposals. Supports GC statuses: obsolete, resolved_indirectly, needs_revalidation."""
    from app.models.evolution_proposal import EvolutionProposal, ENGINE_DEDUP_STATUSES
    q = db.query(EvolutionProposal)
    if status:
        q = q.filter(EvolutionProposal.status == status)
    if risk_level:
        q = q.filter(EvolutionProposal.risk_level == risk_level)
    rows = q.order_by(EvolutionProposal.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
            "proposal_type": r.proposal_type,
            "target_file": r.target_file,
            "risk_level": r.risk_level,
            "reason": r.reason,
            "expected_impact": r.expected_impact,
            "auto_applicable": r.auto_applicable,
            "status": r.status,
            "decided_by": r.decided_by,
            "decided_at": r.decided_at.isoformat() + "Z" if r.decided_at else None,
            "audit_cycle": r.audit_cycle,
            "converted_to_bugfix": r.status == "accepted" and r.decided_by == "evolution_converter",
            "gc_reason": r.gc_reason,
            "gc_updated_at": r.gc_updated_at.isoformat() + "Z" if r.gc_updated_at else None,
            # True while the engine considers this proposal "live" and will
            # not recreate a duplicate.  False means the engine may recreate
            # a fresh proposal with the same dedup_key on next audit.
            "active_for_engine": r.status in ENGINE_DEDUP_STATUSES,
        }
        for r in rows
    ]


@router.post("/evolution/{proposal_id}/accept")
def accept_evolution(
    proposal_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Accept an evolution proposal."""
    from app.models.evolution_proposal import EvolutionProposal
    from datetime import datetime, timezone
    p = db.query(EvolutionProposal).get(proposal_id)
    if not p:
        raise HTTPException(404, "Not found")
    if p.status != "open":
        raise HTTPException(409, f"Already {p.status}")
    p.status = "accepted"
    p.decided_by = "operator"
    p.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"status": "accepted", "proposal_id": proposal_id}


@router.post("/evolution/{proposal_id}/reject")
def reject_evolution(
    proposal_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Reject an evolution proposal."""
    from app.models.evolution_proposal import EvolutionProposal
    from datetime import datetime, timezone
    p = db.query(EvolutionProposal).get(proposal_id)
    if not p:
        raise HTTPException(404, "Not found")
    if p.status != "open":
        raise HTTPException(409, f"Already {p.status}")
    p.status = "rejected"
    p.decided_by = "operator"
    p.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"status": "rejected", "proposal_id": proposal_id}


@router.post("/evolution/{proposal_id}/revalidate")
def revalidate_evolution(
    proposal_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Re-open a proposal that was marked needs_revalidation, obsolete, or resolved_indirectly by the GC."""
    from app.models.evolution_proposal import EvolutionProposal, GC_STATUSES
    from app.services.audit import write_audit_log
    from datetime import datetime, timezone
    p = db.query(EvolutionProposal).get(proposal_id)
    if not p:
        raise HTTPException(404, "Not found")
    if p.status not in GC_STATUSES:
        raise HTTPException(409, f"Cannot revalidate — status is '{p.status}', expected one of {sorted(GC_STATUSES)}")
    old_status = p.status
    p.status = "open"
    p.gc_reason = None
    p.gc_updated_at = None
    p.decided_by = "operator_revalidate"
    p.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
    write_audit_log(
        db,
        actor_type="admin",
        actor_name="operator",
        action_type="evolution_revalidate",
        target_type="evolution_proposal",
        target_id=str(proposal_id),
        before_state={"status": old_status},
        after_state={"status": "open"},
        status="completed",
        approval_mode="human_approved",
    )
    db.commit()
    return {"status": "open", "proposal_id": proposal_id, "revalidated_from": old_status}


# ---------------------------------------------------------------------------
# Model Upgrade Proposals
# ---------------------------------------------------------------------------

@router.get("/model-upgrades")
def list_model_upgrades(
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """List model upgrade proposals."""
    from app.models.model_upgrade import ModelUpgradeProposal
    q = db.query(ModelUpgradeProposal)
    if status:
        q = q.filter(ModelUpgradeProposal.status == status)
    rows = q.order_by(ModelUpgradeProposal.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "current_model": r.current_model,
            "candidate_model": r.candidate_model,
            "target_module": r.target_module,
            "reason": r.reason,
            "status": r.status,
            "eval_result": r.eval_result,
            "risk_level": r.risk_level,
        }
        for r in rows
    ]


@router.get("/model-upgrades/{upgrade_id}")
def get_model_upgrade(
    upgrade_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Get model upgrade detail."""
    from app.models.model_upgrade import ModelUpgradeProposal
    p = db.query(ModelUpgradeProposal).get(upgrade_id)
    if not p:
        raise HTTPException(404, "Not found")
    return {
        "id": p.id,
        "current_provider": p.current_provider,
        "current_model": p.current_model,
        "candidate_provider": p.candidate_provider,
        "candidate_model": p.candidate_model,
        "target_module": p.target_module,
        "reason": p.reason,
        "expected_benefit": p.expected_benefit,
        "risk_level": p.risk_level,
        "status": p.status,
        "eval_result": p.eval_result,
        "eval_detail": p.eval_detail,
        "decided_by": p.decided_by,
        "activated_at": p.activated_at.isoformat() + "Z" if p.activated_at else None,
    }


@router.post("/model-upgrades/{upgrade_id}/evaluate")
def trigger_model_eval(
    upgrade_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Run benchmark evaluation for a model upgrade candidate."""
    from app.services.model_upgrade_agent import evaluate_upgrade
    result = evaluate_upgrade(db, upgrade_id)
    db.commit()
    return {"status": result, "upgrade_id": upgrade_id}


@router.post("/model-upgrades/{upgrade_id}/approve")
def approve_model_upgrade(
    upgrade_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Approve a model upgrade (does NOT activate — separate step)."""
    from app.models.model_upgrade import ModelUpgradeProposal
    from app.services.model_upgrade_agent import generate_upgrade_evolution_proposals
    from datetime import datetime, timezone

    p = db.query(ModelUpgradeProposal).get(upgrade_id)
    if not p:
        raise HTTPException(404, "Not found")
    if p.status != "evaluated":
        raise HTTPException(409, f"Cannot approve — status is {p.status}")

    p.status = "approved"
    p.decided_by = "operator"
    p.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)

    # Generate evolution proposals for approved upgrade
    evo_count = generate_upgrade_evolution_proposals(db, upgrade_id)
    db.commit()
    return {"status": "approved", "upgrade_id": upgrade_id, "evolution_proposals_created": evo_count}


@router.post("/model-upgrades/{upgrade_id}/reject")
def reject_model_upgrade(
    upgrade_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Reject a model upgrade proposal."""
    from app.models.model_upgrade import ModelUpgradeProposal
    from datetime import datetime, timezone
    p = db.query(ModelUpgradeProposal).get(upgrade_id)
    if not p:
        raise HTTPException(404, "Not found")
    if p.status in ("rejected", "activated"):
        raise HTTPException(409, f"Already {p.status}")
    p.status = "rejected"
    p.decided_by = "operator"
    p.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"status": "rejected", "upgrade_id": upgrade_id}


@router.post("/model-upgrades/{upgrade_id}/activate")
def activate_model_upgrade(
    upgrade_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Activate a model upgrade — persists to DB via model_config.
    Requires prior approval. Separate from approve for safety.
    """
    from app.models.model_upgrade import ModelUpgradeProposal
    from app.services.model_config import activate_model
    from datetime import datetime, timezone

    p = db.query(ModelUpgradeProposal).get(upgrade_id)
    if not p:
        raise HTTPException(404, "Not found")
    if p.status != "approved":
        raise HTTPException(409, f"Cannot activate — status is {p.status}, must be approved")

    # Persist activation to DB (deactivates previous config)
    activate_model(
        db,
        module=p.target_module,
        provider=p.candidate_provider,
        model_name=p.candidate_model,
        activated_by="operator",
    )

    p.status = "activated"
    p.activated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    from app.services.audit import write_audit_log
    write_audit_log(
        db, actor_type="human", actor_name="operator",
        action_type="model_activated", target_type="model",
        target_id=f"{p.candidate_provider}:{p.candidate_model}",
        after_state={"module": p.target_module, "previous": p.current_model},
        status="completed", approval_mode="human_approved",
    )
    db.commit()
    return {"status": "activated", "module": p.target_module, "model": p.candidate_model}


@router.post("/model-config/{module}/rollback")
def rollback_model_config(
    module: str,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Rollback a module's model config to the previous active model.
    """
    from app.services.model_config import rollback_model

    result = rollback_model(db, module=module, rolled_back_by="operator")

    if result["status"] in ("no_active_config", "no_previous_config"):
        raise HTTPException(404, result["status"])

    from app.services.audit import write_audit_log
    write_audit_log(
        db, actor_type="human", actor_name="operator",
        action_type="model_rolled_back", target_type="model",
        target_id=f"{result['restored_provider']}:{result['restored_model']}",
        after_state={"module": module},
        status="completed", approval_mode="human_approved",
    )
    db.commit()
    return result


@router.get("/model-config")
def get_model_config(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Return all active model configs per module."""
    from app.services.model_config import get_all_active_configs
    return {"configs": get_all_active_configs(db)}


# ---------------------------------------------------------------------------
# Scaling Intelligence
# ---------------------------------------------------------------------------

@router.get("/scaling/snapshots")
def get_scaling_snapshots(
    limit: int = Query(default=14, le=90),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Return recent daily system snapshots."""
    from app.services.scaling_intelligence import get_recent_snapshots
    return {"snapshots": get_recent_snapshots(db, limit)}


@router.get("/scaling/forecast")
def get_scaling_forecast(
    horizon: int = Query(default=30, le=90),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Return scaling forecast projections."""
    from app.services.scaling_intelligence import build_forecast
    return build_forecast(db, horizon)


@router.get("/scaling/recommendations")
def get_scaling_recommendations(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Return active scaling recommendations."""
    from app.services.scaling_intelligence import get_active_recommendations
    return {"recommendations": get_active_recommendations(db)}


# ---------------------------------------------------------------------------
# Project Brain
# ---------------------------------------------------------------------------

@router.get("/project-brain/summary")
def project_brain_summary(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Return current project brain state — what the reviewer knows."""
    from app.services.project_brain import get_brain_summary
    return get_brain_summary(db)


@router.post("/project-brain/refresh")
def project_brain_refresh(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Force a brain snapshot refresh (operator-triggered)."""
    from app.services.project_brain import build_full_snapshot
    snapshot = build_full_snapshot(db)
    db.commit()
    return {
        "status": "refreshed",
        "snapshot_id": snapshot.id,
        "total_files": snapshot.total_files,
        "critical_files": snapshot.critical_files,
    }


@router.get("/project-brain/constitution")
def project_brain_constitution(
    _auth: bool = Depends(require_operator),
):
    """Return the strategic constitution the reviewer uses."""
    from app.services.project_brain import get_constitution
    return get_constitution()


# ---------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------

@router.post("/reviewer/assess")
def reviewer_assess(
    entity_type: str = Query(...),
    entity_id: int = Query(...),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Run the reviewer on a specific entity. Returns structured assessment."""
    from app.services.reviewer_layer import review_entity, format_for_operator

    valid_types = {
        "bugfix_candidate", "evolution_proposal", "action_approval",
        "model_upgrade", "scaling_recommendation",
    }
    if entity_type not in valid_types:
        raise HTTPException(400, f"Invalid entity_type. Must be one of: {sorted(valid_types)}")

    assessment = review_entity(db, entity_type, entity_id)
    if not assessment:
        raise HTTPException(404, f"{entity_type} #{entity_id} not found")

    db.commit()

    notes = json.loads(assessment.notes_json) if assessment.notes_json else []
    blocking = json.loads(assessment.blocking_concerns_json) if assessment.blocking_concerns_json else []
    domains = json.loads(assessment.affected_domains_json) if assessment.affected_domains_json else []

    return {
        "assessment_id": assessment.id,
        "entity_type": assessment.entity_type,
        "entity_id": assessment.entity_id,
        "verdict": assessment.verdict,
        "risk_level": assessment.risk_level,
        "strategic_alignment": assessment.strategic_alignment,
        "confidence": assessment.confidence,
        "auto_approvable": assessment.auto_approvable,
        "summary": assessment.summary,
        "notes": notes,
        "blocking_concerns": blocking,
        "affected_domains": domains,
        "reviewer_mode": assessment.reviewer_mode,
        "brain_snapshot_id": assessment.brain_snapshot_id,
        "operator_message": format_for_operator(assessment),
    }


# ---------------------------------------------------------------------------
# Support incidents
# ---------------------------------------------------------------------------

@router.get("/incidents")
def list_support_incidents(
    status: str = Query(default="active"),
    limit: int = Query(default=20, ge=1, le=100),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    List support incidents. status=active returns open/triaged/investigating.
    status=all returns all. status=resolved returns resolved only.
    """
    from app.models.support_incident import SupportIncident
    from sqlalchemy import desc

    q = db.query(SupportIncident)
    if status == "active":
        q = q.filter(SupportIncident.status.in_(["open", "triaged", "investigating"]))
    elif status == "resolved":
        q = q.filter(SupportIncident.status == "resolved")
    # status=all → no filter

    incidents = q.order_by(desc(SupportIncident.created_at)).limit(limit).all()

    return {
        "count": len(incidents),
        "incidents": [
            {
                "id": i.id,
                "created_at": i.created_at.isoformat() + "Z" if i.created_at else None,
                "shop_domain": i.shop_domain,
                "classification": i.classification,
                "severity": i.severity,
                "affected_area": i.affected_area,
                "status": i.status,
                "linked_bugfix_candidate_id": i.linked_bugfix_candidate_id,
                "linked_ops_alert_id": i.linked_ops_alert_id,
                "resolved_by": i.resolved_by,
                "message_preview": (i.original_message or "")[:120],
            }
            for i in incidents
        ],
    }


# ---------------------------------------------------------------------------
# Meta-review
# ---------------------------------------------------------------------------

@router.get("/meta-review")
def get_meta_review(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Return the latest completed meta-review."""
    from app.services.meta_reviewer import get_latest_meta_review

    review = get_latest_meta_review(db)
    if not review:
        return {"status": "no_review_available"}
    return review


# ---------------------------------------------------------------------------
# Sentry verification (operator-only)
# ---------------------------------------------------------------------------

@router.post("/sentry-test")
def sentry_test_error(
    _auth: bool = Depends(require_operator),
):
    """
    Intentionally raise an exception to verify Sentry is capturing errors
    with correct tags (request_id, shop_domain, route).

    Operator-only. Returns 500 if Sentry is active (exception propagates).
    Returns 200 with status if Sentry is not configured.
    """
    try:
        import sentry_sdk
        if sentry_sdk.is_initialized():
            raise RuntimeError("Sentry verification test — this error is intentional")
        return {"status": "sentry_not_initialized", "detail": "Set SENTRY_DSN in .env and restart"}
    except ImportError:
        return {"status": "sentry_not_installed", "detail": "pip install sentry-sdk[fastapi]"}
