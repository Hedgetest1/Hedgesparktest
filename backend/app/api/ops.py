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
from datetime import datetime, timezone

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


@router.get("/compliance")
def get_compliance_score(
    force_refresh: bool = Query(default=False),
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Return the live security + GDPR compliance score.

    Components:
      * security_probes       — security heartbeat pass rate
      * gdpr_sla              — active SLA breach count
      * consent_rate          — 7d tracker consent ratio
      * retention_sweep       — last sweep freshness
      * security_guard_wall   — preflight guard health
      * learning_isolation    — evidence_source gate status
      * pii_masking_coverage  — static PII-in-log scan

    Passing `force_refresh=true` recomputes instead of reading the cache.
    The daily digest renders a one-line summary built from the same data.
    """
    from app.services.compliance_score import (
        compute_compliance_score,
        get_cached_compliance_score,
    )
    if not force_refresh:
        cached = get_cached_compliance_score()
        if cached is not None:
            return cached
    return compute_compliance_score(db)


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
    from app.services.audit import write_audit_log
    success = propose_patch(db, candidate_id)
    write_audit_log(
        db, actor_type="human", actor_name="operator",
        action_type="bugfix_propose_triggered", target_type="bugfix",
        target_id=str(candidate_id), status="completed" if success else "failed",
        approval_mode="human_approved",
    )
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
    from app.services.audit import write_audit_log
    result = apply_bugfix_candidate(db, candidate_id)
    write_audit_log(
        db, actor_type="human", actor_name="operator",
        action_type="bugfix_apply_triggered", target_type="bugfix",
        target_id=str(candidate_id), status=result.status,
        approval_mode="human_approved",
        metadata={"test_passed": result.test_passed, "health_ok": result.health_ok,
                  "failure_reason": result.failure_reason},
    )
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
    except Exception as exc:
        log.warning(
            "ops._get_merge_recommendation: promo_id=%s failed (%s): %s",
            promo_id, type(exc).__name__, str(exc)[:200],
        )
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
    except Exception as exc:
        log.warning(
            "ops._get_merge_outcome: promo_id=%s failed (%s): %s",
            promo_id, type(exc).__name__, str(exc)[:200],
        )
        return None


@router.post("/promotions/{promo_id}/branch")
def create_branch(
    promo_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Create a local git branch for the promotion."""
    from app.services.promotion_pipeline import create_promotion_branch
    from app.services.audit import write_audit_log
    result = create_promotion_branch(db, promo_id)
    write_audit_log(
        db, actor_type="human", actor_name="operator",
        action_type="promotion_branch_created", target_type="promotion",
        target_id=str(promo_id), status="completed" if not result.startswith("error") else "failed",
        approval_mode="human_approved", metadata={"branch": result},
    )
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
    from app.services.audit import write_audit_log
    result = run_promotion_ci_check(db, promo_id)
    write_audit_log(
        db, actor_type="human", actor_name="operator",
        action_type="promotion_ci_triggered", target_type="promotion",
        target_id=str(promo_id), status=result,
        approval_mode="human_approved",
    )
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
    from app.services.audit import write_audit_log
    from datetime import datetime, timezone
    p = db.query(AutoFixPromotion).get(promo_id)
    if not p:
        raise HTTPException(404, "Not found")
    if p.status not in ("ci_passed", "branch_created"):
        raise HTTPException(409, f"Cannot approve — status is {p.status}")
    p.status = "approved"
    p.decided_by = "operator"
    p.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
    write_audit_log(
        db, actor_type="human", actor_name="operator",
        action_type="promotion_approved", target_type="promotion",
        target_id=str(promo_id), status="completed",
        approval_mode="human_approved",
    )
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
    from app.services.audit import write_audit_log
    from datetime import datetime, timezone
    p = db.query(AutoFixPromotion).get(promo_id)
    if not p:
        raise HTTPException(404, "Not found")
    if p.status in ("pushed", "rejected"):
        raise HTTPException(409, f"Already {p.status}")
    p.status = "rejected"
    p.decided_by = "operator"
    p.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
    write_audit_log(
        db, actor_type="human", actor_name="operator",
        action_type="promotion_rejected", target_type="promotion",
        target_id=str(promo_id), status="rejected",
        approval_mode="human_approved",
    )
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
    from app.services.audit import write_audit_log
    result = push_promotion(db, promo_id)
    write_audit_log(
        db, actor_type="human", actor_name="operator",
        action_type="promotion_pushed", target_type="promotion",
        target_id=str(promo_id), status="completed" if result == "pushed" else "failed",
        approval_mode="human_approved", metadata={"result": result},
    )
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
# ---------------------------------------------------------------------------
# Adaptive governance observability
# ---------------------------------------------------------------------------

@router.get("/governance")
def get_governance_state(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Inspect current adaptive governance thresholds.

    Returns:
    - global thresholds with current value, default, bounds, reason, evidence
    - per-domain profiles with budget, effectiveness, operator feedback
    """
    from app.services.adaptive_governance import get_adaptive_thresholds, get_domain_profiles
    thresholds = get_adaptive_thresholds(db)
    result = thresholds.to_dict()

    # Add per-domain profiles
    try:
        profiles = get_domain_profiles(db)
        result["domain_profiles"] = {
            domain: profile.to_dict() for domain, profile in profiles.items()
        }
        result["domain_count"] = len(profiles)
        adapted_domains = [d for d, p in profiles.items() if p.adapted]
        result["adapted_domains"] = adapted_domains
    except Exception:
        result["domain_profiles"] = {}
        result["domain_count"] = 0
        result["adapted_domains"] = []

    return result


# ---------------------------------------------------------------------------
# Lesson management — human validation for promoted lessons
# ---------------------------------------------------------------------------

@router.post("/lessons/{lesson_id}/promote")
def approve_lesson_promotion(
    lesson_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Approve a pending lesson promotion to regression_warning."""
    from app.models.system_lesson import SystemLesson
    from app.services.audit import write_audit_log
    from datetime import datetime, timezone

    lesson = db.query(SystemLesson).get(lesson_id)
    if not lesson:
        raise HTTPException(404, "Lesson not found")
    if lesson.promotion_status != "pending_promotion":
        raise HTTPException(409, f"Lesson is not pending promotion (status: {lesson.promotion_status})")

    lesson.lesson_type = "regression_warning"
    lesson.promotion_status = "promoted"
    lesson.promoted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    lesson.promotion_decided_by = "operator"

    write_audit_log(
        db, actor_type="human", actor_name="operator",
        action_type="lesson_promotion_approved", target_type="system_lesson",
        target_id=str(lesson_id), status="completed",
        approval_mode="human_approved",
        metadata={"domain": lesson.domain, "summary": lesson.summary[:200]},
    )
    db.commit()
    return {"status": "promoted", "lesson_id": lesson_id, "domain": lesson.domain}


@router.post("/lessons/{lesson_id}/reject")
def reject_lesson_promotion(
    lesson_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Reject a pending lesson promotion. Lesson remains active but not promoted."""
    from app.models.system_lesson import SystemLesson
    from app.services.audit import write_audit_log
    from datetime import datetime, timezone

    lesson = db.query(SystemLesson).get(lesson_id)
    if not lesson:
        raise HTTPException(404, "Lesson not found")
    if lesson.promotion_status not in ("pending_promotion", "promoted"):
        raise HTTPException(409, f"Cannot reject — promotion_status is {lesson.promotion_status}")

    # If already promoted, demote back
    if lesson.lesson_type == "regression_warning":
        lesson.lesson_type = "ineffective_pattern"

    lesson.promotion_status = "rejected_promotion"
    lesson.promotion_decided_by = "operator"

    write_audit_log(
        db, actor_type="human", actor_name="operator",
        action_type="lesson_promotion_rejected", target_type="system_lesson",
        target_id=str(lesson_id), status="rejected",
        approval_mode="human_approved",
        metadata={"domain": lesson.domain, "summary": lesson.summary[:200]},
    )
    db.commit()
    return {"status": "rejected", "lesson_id": lesson_id, "domain": lesson.domain}


# Webhook fleet status
# ---------------------------------------------------------------------------

@router.get("/diagnostic")
def get_system_diagnostic(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Unified system diagnostic — single-call comprehensive health assessment.

    Returns ALL operational signals in one response:
    vitals, LLM budget, attribution pipeline, alerts, onboarding funnel,
    webhook fleet, evolution pipeline, merchant data health.

    Each section is independently resilient — one subsystem failure
    doesn't block the others.
    """
    from app.services.system_diagnostic import build_system_diagnostic
    return build_system_diagnostic(db)


@router.get("/system-health")
def get_system_health(
    _auth: bool = Depends(require_operator),
):
    """
    Unified CTO-level system health state.

    Returns the latest synthesized health assessment from the agent worker's
    Phase 0 CTO check.  Includes all dimensions, trends, urgent items, and
    recommendations.  Updated every 15 minutes (agent_worker cycle).
    """
    from app.core.redis_client import cache_get
    cached = cache_get("hs:system_health")
    if cached is not None:
        return cached

    # Fallback: compute live
    from app.core.database import SessionLocal
    from app.services.system_health_synthesizer import synthesize_health
    db = SessionLocal()
    try:
        return synthesize_health(db).to_dict()
    finally:
        db.close()


@router.get("/attribution/health")
def get_attribution_pipeline_health(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Attribution pipeline health — shows whether the data flow is working."""
    from sqlalchemy import func
    from app.models.shop_order import ShopOrder
    from app.models.visitor_purchase_session import VisitorPurchaseSession

    orders_total = db.query(func.count(ShopOrder.id)).scalar() or 0
    orders_by_source = db.execute(text(
        "SELECT source, COUNT(*) FROM shop_orders GROUP BY source"
    )).fetchall()

    vps_total = db.query(func.count(VisitorPurchaseSession.id)).scalar() or 0
    vps_attributed = db.execute(text(
        "SELECT COUNT(*) FROM visitor_purchase_sessions WHERE first_source IS NOT NULL"
    )).fetchone()

    return {
        "orders_total": orders_total,
        "orders_by_source": {r[0]: r[1] for r in orders_by_source},
        "visitor_purchase_sessions": vps_total,
        "attributed_sessions": vps_attributed[0] if vps_attributed else 0,
        "attribution_rate": round(
            (vps_attributed[0] if vps_attributed else 0) / max(orders_total, 1), 3
        ),
        "pipeline_status": "healthy" if vps_total > 0 else (
            "no_bridges" if orders_total > 0 else "no_data"
        ),
        "diagnosis": (
            "Orders exist but no visitor-purchase bridges. "
            "The Custom Pixel may not be reading the _hs_vid cookie (ITP, cross-origin). "
            "Consider asking the merchant to add spark-attribution.js to the checkout page."
        ) if orders_total > 0 and vps_total == 0 else (
            "No orders yet." if orders_total == 0 else "Pipeline is flowing."
        ),
    }


@router.get("/tracker/status")
def get_tracker_fleet_status(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Fleet-wide tracker delivery status."""
    from sqlalchemy import func
    from app.models.merchant import Merchant

    rows = (
        db.query(
            Merchant.tracker_delivery_method,
            func.count().label("count"),
            func.count(Merchant.script_tag_id).label("with_tag"),
        )
        .filter(Merchant.install_status == "active")
        .group_by(Merchant.tracker_delivery_method)
        .all()
    )

    methods = {}
    total = 0
    total_with_tag = 0
    for r in rows:
        methods[r[0]] = {"count": r[1], "with_script_tag": r[2]}
        total += r[1]
        total_with_tag += r[2]

    # Merchants with no script_tag_id (potentially broken)
    missing_tag = (
        db.query(Merchant.shop_domain)
        .filter(
            Merchant.install_status == "active",
            Merchant.script_tag_id.is_(None),
            Merchant.access_token.isnot(None),
        )
        .all()
    )

    return {
        "total_active": total,
        "with_script_tag": total_with_tag,
        "missing_script_tag": [r[0] for r in missing_tag],
        "by_delivery_method": methods,
        "tracker_version": __import__("app.core.tracker_version", fromlist=["TRACKER_VERSION"]).TRACKER_VERSION,
    }


@router.get("/digest/status")
def get_digest_delivery_status(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Merchant digest delivery status for current week."""
    from app.services.merchant_digest import get_digest_delivery_status
    return get_digest_delivery_status(db)


@router.get("/webhooks/status")
def get_webhook_fleet_status(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Fleet-wide webhook health summary."""
    from app.services.webhook_monitor import get_fleet_webhook_summary
    return get_fleet_webhook_summary(db)


@router.get("/webhooks/status/{shop_domain}")
def get_merchant_webhook_status(
    shop_domain: str,
    _auth: bool = Depends(require_operator),
):
    """Single merchant webhook status."""
    from app.services.webhook_monitor import get_merchant_webhook_status as get_status
    status = get_status(shop_domain)
    if not status:
        return {"status": "not_checked", "shop": shop_domain}
    return status


# ---------------------------------------------------------------------------
# Sentry verification (operator-only)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Loop health (operator-only)
# ---------------------------------------------------------------------------

@router.get("/loop-health")
def ops_loop_health(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Full autonomous loop health snapshot: queue depths, stuck items,
    throughput, failure rates, thrashing sources, recurrences,
    and top 5 weakest subsystems.
    """
    from app.services.loop_health import get_loop_health
    return get_loop_health(db)


@router.get("/onboarding-health")
def ops_onboarding_health(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Onboarding pipeline health: stuck merchants, pixel abandonment,
    slow activation, and overall onboarding funnel metrics.
    """
    from app.services.onboarding_health import check_onboarding_health
    return check_onboarding_health(db)


@router.get("/onboarding-funnel")
def ops_onboarding_funnel(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    days: int = Query(default=30, ge=1, le=365),
):
    """
    Aggregate onboarding funnel: step-by-step conversion rates,
    drop-off points, median times, and session counts.
    """
    from app.services.onboarding_funnel import get_aggregate_funnel
    return get_aggregate_funnel(db, days)


@router.get("/onboarding-funnel/{shop_domain}")
def ops_onboarding_funnel_shop(
    shop_domain: str,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Per-shop onboarding funnel state with milestones and interaction counts."""
    from app.services.onboarding_funnel import get_shop_funnel
    return get_shop_funnel(db, shop_domain)


@router.get("/onboarding-friction")
def ops_onboarding_friction(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Active friction signals: merchants exhibiting stall, confusion,
    or drop-off patterns. Includes improvement insights.
    """
    from app.services.onboarding_funnel import detect_friction, generate_insights
    return {
        "friction_signals": detect_friction(db),
        "insights": generate_insights(db),
    }


@router.get("/weakness")
def ops_weakness(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    days: int = 30,
):
    """
    Ranked subsystem weakness scores based on failure patterns.
    Weakest subsystem first. Each entry includes domain, score,
    criticality, signal breakdown, and human-readable reasons.
    """
    from app.services.loop_health import score_subsystem_weakness
    ranking = score_subsystem_weakness(db, lookback_days=days)
    return {
        "lookback_days": days,
        "weakest_first": ranking,
        "count": len(ranking),
    }


# ---------------------------------------------------------------------------
# Governance observability (operator-only)
# ---------------------------------------------------------------------------

@router.get("/tier-check")
def ops_tier_check(
    files: str,
    _auth: bool = Depends(require_operator),
):
    """
    Check execution tier for a comma-separated list of file paths.
    Returns tier classification, reasons, and whether agent modification is allowed.
    """
    from app.core.tier_check import check_tier
    file_list = [f.strip() for f in files.split(",") if f.strip()]
    if not file_list:
        return {"error": "No files provided. Use ?files=path1,path2"}
    result = check_tier(file_list)
    return {
        "tier": result.tier,
        "label": result.label,
        "blocked": result.blocked,
        "block_reason": result.block_reason,
        "affected_domains": result.affected_domains,
        "reasons": result.reasons,
    }


@router.get("/file-locks")
def ops_file_locks(
    _auth: bool = Depends(require_operator),
):
    """List all currently held file locks. Returns empty list when no locks active."""
    from app.core.file_lock import list_active_locks
    locks = list_active_locks()
    return {"active_locks": locks, "count": len(locks)}


@router.get("/sentry-intake/health")
def ops_sentry_intake_health(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    hours: int = Query(default=24, ge=1, le=168),
):
    """
    Sentry intake health dashboard — migration readiness visibility.

    Shows:
    - webhook vs email counts in the last N hours
    - last webhook/email timestamps
    - parse error count
    - webhook health status (healthy / degraded / dark)
    - migration readiness assessment
    """
    from app.models.sentry_incident import SentryIncident
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import func

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(hours=hours)

    # Count by source_type in window
    source_counts = dict(
        db.query(SentryIncident.source_type, func.count(SentryIncident.id))
        .filter(SentryIncident.created_at >= cutoff)
        .group_by(SentryIncident.source_type)
        .all()
    )

    webhook_count = source_counts.get("sentry_webhook", 0)
    email_count = source_counts.get("email", 0)
    total = webhook_count + email_count

    # Last timestamp per source
    last_webhook = (
        db.query(func.max(SentryIncident.created_at))
        .filter(SentryIncident.source_type == "sentry_webhook")
        .scalar()
    )
    last_email = (
        db.query(func.max(SentryIncident.created_at))
        .filter(SentryIncident.source_type == "email")
        .scalar()
    )

    # Parse errors in window
    parse_errors = (
        db.query(func.count(SentryIncident.id))
        .filter(
            SentryIncident.status == "parse_error",
            SentryIncident.created_at >= cutoff,
        )
        .scalar() or 0
    )

    # Webhook health assessment
    if webhook_count > 0 and email_count == 0:
        webhook_status = "healthy"
        migration_ready = True
    elif webhook_count > 0 and email_count > 0:
        webhook_status = "active_with_email_fallback"
        migration_ready = True  # webhook is working, email can be disabled
    elif webhook_count == 0 and email_count > 0:
        webhook_status = "dark"
        migration_ready = False
    elif total == 0:
        webhook_status = "no_incidents"
        migration_ready = None  # can't assess with no data
    else:
        webhook_status = "unknown"
        migration_ready = False

    # Hours since last webhook (for staleness detection)
    hours_since_webhook = None
    if last_webhook:
        hours_since_webhook = round((now - last_webhook).total_seconds() / 3600, 1)

    return {
        "window_hours": hours,
        "webhook_count": webhook_count,
        "email_fallback_count": email_count,
        "total_incidents": total,
        "parse_errors": parse_errors,
        "webhook_pct": round(webhook_count / total * 100, 1) if total > 0 else None,
        "last_webhook_at": last_webhook.isoformat() + "Z" if last_webhook else None,
        "last_email_at": last_email.isoformat() + "Z" if last_email else None,
        "hours_since_last_webhook": hours_since_webhook,
        "webhook_status": webhook_status,
        "migration_ready": migration_ready,
        "migration_note": (
            "Safe to disable Sentry email alerts"
            if migration_ready
            else "Webhook not receiving — keep email alerts active"
            if migration_ready is False
            else "No incidents in window — configure Sentry webhook first"
        ),
    }


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


# ---------------------------------------------------------------------------
# Merchant lifecycle email visibility
# ---------------------------------------------------------------------------

@router.get("/emails")
def ops_email_history(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    shop: str | None = Query(default=None),
    email_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """
    Email delivery history — what was sent, when, to whom, and why
    it was suppressed. Filterable by shop and email type.
    """
    from app.services.merchant_email_service import get_email_history
    return get_email_history(db, shop_domain=shop, email_type=email_type, limit=limit)


# ---------------------------------------------------------------------------
# Email journey visibility
# ---------------------------------------------------------------------------

@router.get("/journey")
def ops_journey(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    shop: str | None = Query(default=None),
):
    """
    Merchant email journey state — per-merchant lifecycle tracking.
    Shows invite/open/click/onboarding/followup/activation timestamps.
    """
    from app.services.email_journey import get_journey_summary
    return get_journey_summary(db, shop_domain=shop)


@router.get("/journey/stats")
def ops_journey_stats(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Journey funnel stats — count of merchants in each stage.
    Answers "how many merchants are in each stage?" without fetching all rows.
    """
    from sqlalchemy import func as sqlfunc
    from app.models.merchant_journey_state import MerchantJourneyState
    rows = (
        db.query(
            MerchantJourneyState.current_stage,
            sqlfunc.count(MerchantJourneyState.id),
        )
        .group_by(MerchantJourneyState.current_stage)
        .all()
    )
    stages = {row[0]: row[1] for row in rows}
    total = sum(stages.values())
    return {"total": total, "stages": stages}


@router.get("/email-events")
def ops_email_events(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    shop: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """
    Resend delivery events — delivered, opened, clicked, bounced, complained.
    Critical for monitoring bounce/complaint rates.
    """
    from app.models.email_event import EmailEvent

    def _ts(dt):
        return dt.isoformat() + "Z" if dt else None

    q = db.query(EmailEvent).order_by(EmailEvent.created_at.desc())
    if shop:
        q = q.filter(EmailEvent.shop_domain == shop)
    if event_type:
        q = q.filter(EmailEvent.event_type == event_type)
    rows = q.limit(limit).all()
    return [
        {
            "id": r.id,
            "created_at": _ts(r.created_at),
            "resend_email_id": r.resend_email_id,
            "event_type": r.event_type,
            "to_email": r.to_email,
            "shop_domain": r.shop_domain,
            "email_type": r.email_type,
            "event_timestamp": _ts(r.event_timestamp),
        }
        for r in rows
    ]


@router.get("/email-events/stats")
def ops_email_event_stats(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Email event breakdown — count by event_type.
    Quick health check: are bounces/complaints growing?
    """
    from sqlalchemy import func as sqlfunc
    from app.models.email_event import EmailEvent
    rows = (
        db.query(
            EmailEvent.event_type,
            sqlfunc.count(EmailEvent.id),
        )
        .group_by(EmailEvent.event_type)
        .all()
    )
    return {row[0]: row[1] for row in rows}


@router.get("/merchant-scores")
def ops_merchant_scores(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
):
    """
    Merchant priority scores — ranked by revenue opportunity.
    Shows which merchants to focus on for conversion and retention.
    """
    from app.services.merchant_scoring import score_all_merchants
    from dataclasses import asdict
    scores = score_all_merchants(db, limit=limit)
    return [asdict(s) for s in scores]


@router.get("/merchant/{shop_domain}/score")
def ops_merchant_score(
    shop_domain: str,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Single merchant priority score with sub-score breakdown."""
    from app.services.merchant_scoring import score_merchant
    from dataclasses import asdict
    try:
        return asdict(score_merchant(db, shop_domain))
    except Exception as exc:
        return {"error": str(exc), "shop_domain": shop_domain}


@router.get("/feedback/themes")
def ops_feedback_themes(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Aggregated merchant feedback themes — recurring feature requests and suggestions
    grouped by product area. Shows demand signals for product roadmap.
    """
    from app.services.feedback_intelligence import get_feedback_summary
    return get_feedback_summary(db)


@router.get("/merchant/{shop_domain}/profile")
def ops_merchant_profile(
    shop_domain: str,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Unified merchant profile — everything an operator needs to understand
    a merchant's full state in one call.

    Answers: what state are they in? what did we send? what did they do?
    what did they reply? are they stuck / silent / at risk?
    """
    from app.models.merchant import Merchant
    from app.models.merchant_email import MerchantEmail
    from app.models.merchant_journey_state import MerchantJourneyState
    from app.models.inbound_email import InboundEmail
    from sqlalchemy import text as sa_text

    def _ts(dt):
        return dt.isoformat() + "Z" if dt else None

    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if not merchant:
        return {"error": "merchant_not_found"}

    # Merchant identity + status
    identity = {
        "shop_domain": merchant.shop_domain,
        "contact_email": merchant.contact_email,
        "plan": merchant.plan,
        "billing_active": merchant.billing_active,
        "install_status": merchant.install_status,
        "installed_at": _ts(merchant.installed_at),
        "uninstalled_at": _ts(merchant.uninstalled_at),
        "onboarding_status": merchant.onboarding_status,
        "onboarding_error": merchant.onboarding_error,
        "onboarding_retry_count": merchant.onboarding_retry_count,
    }

    # Journey state
    journey = db.query(MerchantJourneyState).filter(
        MerchantJourneyState.shop_domain == shop_domain
    ).first()
    journey_info = None
    if journey:
        from app.services.email_journey import _journey_to_dict
        journey_info = _journey_to_dict(journey)
        journey_info["email_suppressed"] = journey.email_suppressed

    # Recent emails sent (last 10)
    emails_sent = (
        db.query(MerchantEmail)
        .filter(MerchantEmail.shop_domain == shop_domain)
        .order_by(MerchantEmail.created_at.desc())
        .limit(10)
        .all()
    )
    sent_list = [
        {"type": e.email_type, "status": e.status, "at": _ts(e.created_at),
         "suppressed_by": e.suppressed_by}
        for e in emails_sent
    ]

    # Inbound emails from merchant (last 10)
    inbound = (
        db.query(InboundEmail)
        .filter(InboundEmail.shop_domain == shop_domain)
        .order_by(InboundEmail.created_at.desc())
        .limit(10)
        .all()
    )
    inbound_list = [
        {"subject": ie.subject, "classification": ie.classification,
         "routing_status": ie.routing_status, "at": _ts(ie.created_at),
         "responded": ie.agent_response_sent_at is not None}
        for ie in inbound
    ]

    # Open incidents for this merchant
    incidents = db.execute(sa_text("""
        SELECT id, severity, alert_type, summary, created_at, resolved
        FROM ops_alerts
        WHERE shop_domain = :shop
        ORDER BY created_at DESC LIMIT 10
    """), {"shop": shop_domain}).fetchall()
    incident_list = [
        {"id": r[0], "severity": r[1], "type": r[2],
         "summary": r[3][:100] if r[3] else None,
         "at": r[4].isoformat() + "Z" if r[4] else None,
         "resolved": r[5]}
        for r in incidents
    ]

    # Activity: recent event count
    event_count_7d = db.execute(sa_text("""
        SELECT COUNT(*) FROM events
        WHERE shop_domain = :shop
          AND timestamp > :cutoff
    """), {
        "shop": shop_domain,
        "cutoff": int((_now_utc() - __import__('datetime').timedelta(days=7)).timestamp() * 1000),
    }).scalar() or 0

    # Risk assessment
    risk_signals = []
    if merchant.install_status != "active":
        risk_signals.append("UNINSTALLED")
    if merchant.onboarding_status == "failed":
        risk_signals.append(f"ONBOARDING_FAILED (retry {merchant.onboarding_retry_count or 0})")
    if journey and journey.email_suppressed:
        risk_signals.append(f"EMAIL_SUPPRESSED ({journey.email_suppressed})")
    if event_count_7d == 0 and merchant.onboarding_status == "ready":
        risk_signals.append("SILENT — 0 events in 7 days")
    if not merchant.contact_email:
        risk_signals.append("NO_CONTACT_EMAIL")
    if merchant.plan == "pro" and not merchant.billing_active:
        risk_signals.append("PRO_BUT_BILLING_INACTIVE")
    if not risk_signals:
        risk_signals.append("HEALTHY")

    return {
        "merchant": identity,
        "journey": journey_info,
        "emails_sent": sent_list,
        "inbound_emails": inbound_list,
        "incidents": incident_list,
        "activity": {"events_7d": event_count_7d},
        "risk_signals": risk_signals,
    }


def _now_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("/inbound-emails")
def ops_inbound_emails(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    shop: str | None = Query(default=None),
    classification: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """
    Inbound email log — merchant replies with classification and routing status.
    Includes body preview for operator triage without DB access.
    """
    from app.models.inbound_email import InboundEmail
    q = db.query(InboundEmail).order_by(InboundEmail.created_at.desc())
    if shop:
        q = q.filter(InboundEmail.shop_domain == shop)
    if classification:
        q = q.filter(InboundEmail.classification == classification)
    if status:
        q = q.filter(InboundEmail.routing_status == status)
    rows = q.limit(limit).all()

    def _ts(dt):
        return dt.isoformat() + "Z" if dt else None

    return [
        {
            "id": r.id,
            "created_at": _ts(r.created_at),
            "from_email": r.from_email,
            "shop_domain": r.shop_domain,
            "subject": r.subject,
            "body_preview": (r.body_text or "")[:200] or None,
            "classification": r.classification,
            "classification_confidence": r.classification_confidence,
            "classification_method": r.classification_method,
            "routing_status": r.routing_status,
            "routing_action": r.routing_action,
            "processed_at": _ts(r.processed_at),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Per-merchant email diagnostics — full trace without SSH
# ---------------------------------------------------------------------------

@router.get("/merchant/{shop_domain}/email-trace")
def ops_merchant_email_trace(
    shop_domain: str,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Complete email diagnostic trace for a single merchant.

    Returns everything needed to answer "why didn't merchant X get their email?"
    without SSH access.
    """
    from app.models.merchant import Merchant
    from app.models.merchant_email import MerchantEmail
    from app.models.merchant_journey_state import MerchantJourneyState
    from app.models.email_event import EmailEvent
    from app.models.inbound_email import InboundEmail

    def _ts(dt):
        return dt.isoformat() + "Z" if dt else None

    # 1. Merchant basics
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if not merchant:
        return {"error": "merchant_not_found", "shop_domain": shop_domain}

    merchant_info = {
        "shop_domain": merchant.shop_domain,
        "contact_email": merchant.contact_email,
        "install_status": merchant.install_status,
        "plan": merchant.plan,
        "billing_active": merchant.billing_active,
        "onboarding_status": merchant.onboarding_status,
        "onboarding_error": merchant.onboarding_error,
        "onboarding_retry_count": merchant.onboarding_retry_count,
    }

    # 2. Journey state
    journey = db.query(MerchantJourneyState).filter(
        MerchantJourneyState.shop_domain == shop_domain
    ).first()
    journey_info = None
    if journey:
        from app.services.email_journey import _journey_to_dict
        journey_info = _journey_to_dict(journey)
        journey_info["email_suppressed"] = journey.email_suppressed
        journey_info["email_suppressed_at"] = _ts(journey.email_suppressed_at)

    # 3. Sent / suppressed emails (last 20)
    emails = (
        db.query(MerchantEmail)
        .filter(MerchantEmail.shop_domain == shop_domain)
        .order_by(MerchantEmail.created_at.desc())
        .limit(20)
        .all()
    )
    email_history = [
        {
            "id": e.id,
            "created_at": _ts(e.created_at),
            "email_type": e.email_type,
            "to_email": e.to_email,
            "status": e.status,
            "suppressed_by": e.suppressed_by,
            "resend_id": e.resend_id,
        }
        for e in emails
    ]

    # 4. Resend delivery events (last 20)
    events = (
        db.query(EmailEvent)
        .filter(EmailEvent.shop_domain == shop_domain)
        .order_by(EmailEvent.created_at.desc())
        .limit(20)
        .all()
    )
    delivery_events = [
        {
            "event_type": ev.event_type,
            "resend_email_id": ev.resend_email_id,
            "event_timestamp": _ts(ev.event_timestamp),
            "email_type": ev.email_type,
        }
        for ev in events
    ]

    # 5. Bounce/complaint suppression check (Redis)
    redis_suppressed = None
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc:
            redis_suppressed = rc.get(f"hs:email_suppressed:{shop_domain}")
            if isinstance(redis_suppressed, bytes):
                redis_suppressed = redis_suppressed.decode()
    except Exception:
        pass

    # 6. Inbound emails from this merchant (last 10)
    inbound = (
        db.query(InboundEmail)
        .filter(InboundEmail.shop_domain == shop_domain)
        .order_by(InboundEmail.created_at.desc())
        .limit(10)
        .all()
    )
    inbound_list = [
        {
            "id": ie.id,
            "created_at": _ts(ie.created_at),
            "subject": ie.subject,
            "classification": ie.classification,
            "routing_status": ie.routing_status,
        }
        for ie in inbound
    ]

    # 7. Diagnosis summary
    diagnosis = []
    if not merchant.contact_email:
        diagnosis.append("NO_CONTACT_EMAIL — merchant has no email address")
    if merchant.install_status != "active":
        diagnosis.append(f"UNINSTALLED — status={merchant.install_status}")
    if journey and journey.email_suppressed:
        diagnosis.append(f"EMAIL_SUPPRESSED — reason={journey.email_suppressed}")
    if redis_suppressed:
        diagnosis.append(f"REDIS_SUPPRESSED — {redis_suppressed}")
    if not emails:
        diagnosis.append("NO_EMAILS_EVER — no email attempts found")
    if not diagnosis:
        diagnosis.append("HEALTHY — no issues detected")

    return {
        "merchant": merchant_info,
        "journey": journey_info,
        "email_history": email_history,
        "delivery_events": delivery_events,
        "redis_suppression": redis_suppressed,
        "inbound_emails": inbound_list,
        "diagnosis": diagnosis,
    }


# ---------------------------------------------------------------------------
# Sentry incident triage visibility
# ---------------------------------------------------------------------------

@router.get("/incidents")
def ops_incidents(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """
    List Sentry incident families — grouped by fingerprint.
    Each entry is a family head with recurrence count.
    Filter by status: received, parsed, parse_error, triaged, linked, resolved, ignored.
    """
    from app.services.sentry_triage import get_incident_families
    return get_incident_families(db, status=status, limit=limit)


@router.get("/incidents/{incident_id}")
def ops_incident_detail(
    incident_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Full detail for a single incident, including parsed fields,
    raw email snapshot, and triage packet if generated.
    """
    from app.models.sentry_incident import SentryIncident

    inc = db.query(SentryIncident).get(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    packet = None
    if inc.triage_packet:
        try:
            packet = json.loads(inc.triage_packet)
        except (json.JSONDecodeError, ValueError):
            packet = inc.triage_packet

    return {
        "id": inc.id,
        "created_at": inc.created_at.isoformat() + "Z" if inc.created_at else None,
        "source_message_id": inc.source_message_id,
        "source_type": inc.source_type,
        "status": inc.status,
        "parse_error": inc.parse_error,

        # Parsed
        "error_type": inc.error_type,
        "error_title": inc.error_title,
        "project": inc.project,
        "environment": inc.environment,
        "severity": inc.severity,
        "culprit": inc.culprit,
        "stack_trace": inc.stack_trace,
        "sentry_issue_url": inc.sentry_issue_url,

        # Fingerprint
        "fingerprint": inc.fingerprint,
        "fingerprint_input": inc.fingerprint_input,
        "family_head_id": inc.family_head_id,
        "recurrence_count": inc.recurrence_count,

        # Classification
        "subsystem_class": inc.subsystem_class,
        "merchant_impact": inc.merchant_impact,
        "affected_shop": inc.affected_shop,

        # AI triage
        "ai_triage_status": inc.ai_triage_status,
        "triage_packet": packet,

        # Integration
        "linked_bugfix_candidate_id": inc.linked_bugfix_candidate_id,
        "linked_ops_alert_id": inc.linked_ops_alert_id,
        "lesson_candidate_status": inc.lesson_candidate_status,

        # Raw (truncated for API response)
        "raw_subject": inc.raw_subject,
        "raw_from": inc.raw_from,
        "raw_body_length": len(inc.raw_body) if inc.raw_body else 0,
    }


@router.get("/incidents/{incident_id}/family")
def ops_incident_family(
    incident_id: int,
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    List all incidents in the same family (same fingerprint).
    Shows recurrence timeline for a specific error pattern.
    """
    from app.models.sentry_incident import SentryIncident

    head = db.query(SentryIncident).get(incident_id)
    if not head:
        raise HTTPException(status_code=404, detail="Incident not found")

    fp = head.fingerprint
    if not fp:
        return {"family_head_id": incident_id, "members": [], "total": 0}

    members = (
        db.query(SentryIncident)
        .filter(SentryIncident.fingerprint == fp)
        .order_by(SentryIncident.created_at.desc())
        .limit(100)
        .all()
    )

    return {
        "family_head_id": incident_id,
        "fingerprint": fp,
        "fingerprint_input": head.fingerprint_input,
        "total": len(members),
        "members": [
            {
                "id": m.id,
                "created_at": m.created_at.isoformat() + "Z" if m.created_at else None,
                "status": m.status,
                "error_title": m.error_title,
                "source_message_id": m.source_message_id,
            }
            for m in members
        ],
    }


@router.get("/incidents/triage/queue")
def ops_triage_queue(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
):
    """
    Incidents with generated triage packets ready for AI consumption.
    This is the handoff point for future Claude/OpenClaw integration.
    """
    from app.services.sentry_triage import get_triage_queue
    return get_triage_queue(db, limit=limit)


@router.get("/incidents/parse-errors")
def ops_parse_errors(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
):
    """
    Incidents that failed parsing — for debugging the parser.
    """
    from app.models.sentry_incident import SentryIncident

    errors = (
        db.query(SentryIncident)
        .filter(SentryIncident.status == "parse_error")
        .order_by(SentryIncident.created_at.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "id": e.id,
            "created_at": e.created_at.isoformat() + "Z" if e.created_at else None,
            "parse_error": e.parse_error,
            "raw_subject": e.raw_subject,
            "raw_from": e.raw_from,
            "raw_body_preview": (e.raw_body or "")[:500],
        }
        for e in errors
    ]


@router.get("/incidents/consumer/stats")
def ops_consumer_stats(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Triage consumer pipeline statistics — how many incidents at each stage,
    how many candidates were created, how many were suppressed/deduped.
    """
    from app.models.sentry_incident import SentryIncident
    from sqlalchemy import func

    # Count by ai_triage_status
    status_counts = dict(
        db.query(SentryIncident.ai_triage_status, func.count(SentryIncident.id))
        .group_by(SentryIncident.ai_triage_status)
        .all()
    )

    # Count by incident status
    incident_status_counts = dict(
        db.query(SentryIncident.status, func.count(SentryIncident.id))
        .group_by(SentryIncident.status)
        .all()
    )

    # Count linked candidates
    linked = (
        db.query(func.count(SentryIncident.id))
        .filter(SentryIncident.linked_bugfix_candidate_id.isnot(None))
        .scalar() or 0
    )

    # Total incidents
    total = db.query(func.count(SentryIncident.id)).scalar() or 0

    # Family count (distinct fingerprints)
    families = (
        db.query(func.count(func.distinct(SentryIncident.fingerprint)))
        .filter(SentryIncident.fingerprint.isnot(None))
        .scalar() or 0
    )

    # Count by source_type (email / sentry_webhook / manual)
    source_counts = dict(
        db.query(SentryIncident.source_type, func.count(SentryIncident.id))
        .group_by(SentryIncident.source_type)
        .all()
    )

    # Parse error count
    parse_errors = (
        db.query(func.count(SentryIncident.id))
        .filter(SentryIncident.status == "parse_error")
        .scalar() or 0
    )

    return {
        "total_incidents": total,
        "unique_families": families,
        "linked_to_candidates": linked,
        "by_triage_status": status_counts,
        "by_incident_status": incident_status_counts,
        "by_source_type": source_counts,
        "parse_errors": parse_errors,
    }


# ---------------------------------------------------------------------------
# Simulation — synthetic merchant observability
# ---------------------------------------------------------------------------

@router.get("/simulation/status")
def simulation_status(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Synthetic merchant simulation status.

    Shows all synthetic merchants, their archetypes, and how much
    synthetic data exists across each pipeline stage.
    Clearly separated from real merchant data.
    """
    from app.services.simulation_engine import get_simulation_status
    return get_simulation_status(db)


# ---------------------------------------------------------------------------
# Unified Pipeline Health — one-glance view of the self-healing system
# ---------------------------------------------------------------------------

@router.get("/pipeline-health")
def get_pipeline_health(
    _auth: bool = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """
    Single endpoint returning every signal an operator needs to reason
    about the self-healing pipeline:

      * loop_health snapshot (queue depths, throughput, stuck items,
        thrashing sources, recurrence, weakest subsystems, trend)
      * protection_state (LLM budget, Redis, DB pool, worker staleness)
      * live candidate state counts by (status, source_type)
      * active alert storm aggregation counter sums
      * visibility-only backlog (frontend_error candidates awaiting human)
      * top 5 most recent high-impact ops_alerts
      * last agent_worker cycle timestamp + cycle freshness
      * last data_integrity_probe run timestamp
      * auto_merge cooldown state

    Built entirely on existing deterministic sources — no new
    computation. Cheap enough to poll every 30s from the operator
    dashboard without impacting DB.
    """
    from datetime import timedelta as _td
    from app.services.loop_health import get_loop_health
    from app.core.protection_state import protection_state
    from app.services.bugfix_pipeline import _VISIBILITY_ONLY_SOURCE_TYPES
    from app.models.bugfix_candidate import BugFixCandidate
    from app.models.ops_alert import OpsAlert
    from app.models.worker_log import WorkerLog

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Loop health — the main snapshot
    try:
        loop = get_loop_health(db)
    except Exception as exc:
        loop = {"error": f"loop_health_failed: {type(exc).__name__}: {exc}"}

    # Protection state — cheap, cached 30s internally
    try:
        prot = protection_state()
    except Exception as exc:
        prot = {"error": f"protection_state_failed: {type(exc).__name__}"}

    # Candidates grouped by (status, source_type) for the last 48h
    candidate_48h = []
    try:
        rows = db.execute(text("""
            SELECT status, source_type, COUNT(*) AS n
            FROM bugfix_candidates
            WHERE created_at >= :cutoff
            GROUP BY status, source_type
            ORDER BY n DESC
        """), {"cutoff": now - _td(hours=48)}).fetchall()
        candidate_48h = [
            {"status": r[0], "source_type": r[1], "count": int(r[2])}
            for r in rows
        ]
    except Exception as exc:
        candidate_48h = [{"error": f"candidates_48h_failed: {type(exc).__name__}"}]

    # Visibility-only backlog (awaiting human triage)
    try:
        visibility_backlog = (
            db.query(BugFixCandidate)
            .filter(
                BugFixCandidate.status.in_(["open", "analyzed"]),
                BugFixCandidate.proposal_attempted_at.is_(None),
                BugFixCandidate.source_type.in_(list(_VISIBILITY_ONLY_SOURCE_TYPES)),
            )
            .count()
        )
    except Exception:
        visibility_backlog = None

    # Alert storm summary: aggregated occurrence counts for unresolved chronic alerts
    storm_totals = []
    try:
        alerts_24h = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.resolved == False,
                OpsAlert.created_at >= now - _td(hours=24),
            )
            .order_by(OpsAlert.created_at.desc())
            .limit(100)
            .all()
        )
        for a in alerts_24h:
            occ = 1
            if a.detail:
                try:
                    parsed = json.loads(a.detail)
                    if isinstance(parsed, dict):
                        occ = int(parsed.get("occurrence_count", 1))
                except (ValueError, TypeError):
                    pass
            if occ >= 5:
                storm_totals.append({
                    "id": a.id,
                    "alert_type": a.alert_type,
                    "source": a.source,
                    "severity": a.severity,
                    "shop_domain": a.shop_domain,
                    "occurrence_count": occ,
                    "summary": (a.summary or "")[:200],
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                })
        storm_totals.sort(key=lambda s: s["occurrence_count"], reverse=True)
        storm_totals = storm_totals[:10]
    except Exception as exc:
        storm_totals = [{"error": f"storm_query_failed: {type(exc).__name__}"}]

    # Last cycles
    last_agent_cycle = None
    last_agg_cycle = None
    try:
        ag = (
            db.query(WorkerLog)
            .filter(WorkerLog.worker_name == "agent_worker")
            .order_by(WorkerLog.started_at.desc())
            .first()
        )
        if ag:
            last_agent_cycle = {
                "started_at": ag.started_at.isoformat() if ag.started_at else None,
                "age_seconds": int((now - ag.started_at).total_seconds()) if ag.started_at else None,
                "errors": ag.errors,
            }
        agg = (
            db.query(WorkerLog)
            .filter(WorkerLog.worker_name == "aggregation_worker")
            .order_by(WorkerLog.started_at.desc())
            .first()
        )
        if agg:
            last_agg_cycle = {
                "started_at": agg.started_at.isoformat() if agg.started_at else None,
                "age_seconds": int((now - agg.started_at).total_seconds()) if agg.started_at else None,
                "errors": agg.errors,
            }
    except Exception:
        pass

    # Auto-merge cooldown state
    auto_merge_info = {}
    try:
        import os as _os
        import app.services.promotion_pipeline as pp
        import time as _time
        auto_merge_info = {
            "enabled_env_flag": _os.getenv("AUTO_MERGE_TIER0", "").strip() == "1",
            "on_cooldown": pp._is_auto_merge_on_cooldown(),
            "cooldown_seconds_remaining": (
                max(0, int(pp._AUTO_MERGE_COOLDOWN_S - (_time.monotonic() - pp._auto_merge_last)))
                if pp._auto_merge_last is not None else 0
            ),
        }
    except Exception:
        auto_merge_info = {"error": "auto_merge_state_unavailable"}

    # Stale worker detection — freshness thresholds
    freshness_warnings: list[str] = []
    if last_agent_cycle and last_agent_cycle.get("age_seconds") is not None:
        if last_agent_cycle["age_seconds"] > 30 * 60:  # 30 min
            freshness_warnings.append(
                f"agent_worker last ran {last_agent_cycle['age_seconds'] // 60}m ago "
                "(expected every 15m)"
            )
    if last_agg_cycle and last_agg_cycle.get("age_seconds") is not None:
        if last_agg_cycle["age_seconds"] > 15 * 60:  # 15 min
            freshness_warnings.append(
                f"aggregation_worker last ran {last_agg_cycle['age_seconds'] // 60}m ago "
                "(expected every 5m)"
            )

    return {
        "generated_at": now.isoformat(),
        "loop_health": loop,
        "protection_state": prot,
        "candidates_48h_by_status_source": candidate_48h,
        "visibility_only_backlog": visibility_backlog,
        "alert_storms_top10": storm_totals,
        "last_agent_cycle": last_agent_cycle,
        "last_aggregation_cycle": last_agg_cycle,
        "auto_merge": auto_merge_info,
        "freshness_warnings": freshness_warnings,
    }
