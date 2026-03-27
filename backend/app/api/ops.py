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
    }


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
