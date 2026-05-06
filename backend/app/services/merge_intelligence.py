"""
merge_intelligence.py — Merge recommendation + post-merge outcome tracking.

Recommendation: deterministic gates → recommend_merge true/false with reason.
Outcome: evaluates merged promotions for regression after 15+ minutes.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.autofix_promotion import AutoFixPromotion
from app.models.merge_outcome import MergeOutcome

log = logging.getLogger("merge_intelligence")

_MIN_EVAL_DELAY_MINUTES = 15


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Merge recommendation
# ---------------------------------------------------------------------------

@dataclass
class MergeRecommendation:
    recommend: bool = False
    reasons: list[str] = None

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []


def compute_merge_recommendation(db: Session, promotion_id: int) -> MergeRecommendation:
    """
    Compute whether a promotion should be merged. All gates must pass.
    """
    rec = MergeRecommendation()

    promo = db.get(AutoFixPromotion, promotion_id)
    if not promo:
        rec.reasons.append("promotion_not_found")
        return rec

    # Gate 1: PR exists
    if not promo.pr_url:
        rec.reasons.append("no_pr")

    # Gate 2: Remote CI passed
    ci = getattr(promo, "remote_ci_status", None)
    if ci != "passed":
        rec.reasons.append(f"remote_ci_not_passed: {ci or 'unknown'}")

    # Gate 3: Promotion in pushable state
    if promo.status not in ("pushed", "approved"):
        rec.reasons.append(f"wrong_promotion_status: {promo.status}")

    # Gate 4: Bugfix candidate was successfully applied
    from app.models.bugfix_candidate import BugFixCandidate
    candidate = db.get(BugFixCandidate, promo.bugfix_candidate_id)
    if not candidate or candidate.status != "applied":
        rec.reasons.append(f"candidate_not_applied: {candidate.status if candidate else 'not_found'}")

    # Gate 5: Patch is TIER_0 (ultra-safe)
    if candidate and getattr(candidate, "patch_risk_tier", None) != 0:
        rec.reasons.append(f"patch_risk_tier_not_0: {getattr(candidate, 'patch_risk_tier', 'unknown')}")

    # Gate 6: No critical alerts in the evaluation window after apply
    # Only checks alerts within 24 hours of apply — older alerts are pre-existing.
    # Excludes infrastructure alerts (worker health, circuit breakers) that are
    # unrelated to code changes.
    _INFRA_ALERT_TYPES = {
        "circuit_breaker_tripped", "worker_stale", "worker_error_rate",
        "merge_intelligence", "redis_unavailable", "health_check_failed",
    }
    if candidate and candidate.applied_at:
        from app.models.ops_alert import OpsAlert
        from datetime import timedelta
        window_end = candidate.applied_at + timedelta(hours=24)
        critical_after = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.severity == "critical",
                OpsAlert.created_at > candidate.applied_at,
                OpsAlert.created_at <= window_end,
                ~OpsAlert.alert_type.in_(_INFRA_ALERT_TYPES),
            )
            .count()
        )
        if critical_after > 0:
            rec.reasons.append(f"critical_alerts_after_apply: {critical_after}")

    # Gate 7: No rollback happened
    if candidate and candidate.status == "rolled_back":
        rec.reasons.append("candidate_was_rolled_back")

    # All gates passed
    if not rec.reasons:
        rec.recommend = True
        rec.reasons.append("all_gates_passed")

    return rec


# ---------------------------------------------------------------------------
# Post-merge outcome creation
# ---------------------------------------------------------------------------

def create_merge_outcome(db: Session, promotion_id: int) -> MergeOutcome | None:
    """Create a pending outcome for a merged promotion. Dedup by promotion_id."""
    existing = db.query(MergeOutcome).filter(MergeOutcome.promotion_id == promotion_id).first()
    if existing:
        return existing

    promo = db.get(AutoFixPromotion, promotion_id)
    if not promo or promo.status != "merged":
        return None

    outcome = MergeOutcome(
        promotion_id=promotion_id,
        bugfix_candidate_id=promo.bugfix_candidate_id,
        merge_commit_sha=getattr(promo, "merge_commit_sha", None),
        evaluation_status="pending",
    )
    db.add(outcome)
    db.flush()
    return outcome


# ---------------------------------------------------------------------------
# Post-merge evaluation
# ---------------------------------------------------------------------------

def evaluate_merge_outcomes(db: Session) -> dict:
    """
    Evaluate pending merge outcomes that are old enough (15+ minutes after creation).
    Checks for regressions: new alerts, same bug reappearing, health issues.
    """
    summary = {"evaluated": 0, "healthy": 0, "regressed": 0, "unknown": 0}
    cutoff = _now() - timedelta(minutes=_MIN_EVAL_DELAY_MINUTES)

    pending = (
        db.query(MergeOutcome)
        .filter(
            MergeOutcome.evaluation_status == "pending",
            MergeOutcome.created_at <= cutoff,
        )
        .limit(10)
        .all()
    )

    for outcome in pending:
        summary["evaluated"] += 1
        status, detail = _evaluate_single(db, outcome)
        outcome.evaluation_status = status
        outcome.evaluated_at = _now()
        outcome.detail = detail
        summary[status] = summary.get(status, 0) + 1

        if status == "regressed":
            try:
                from app.services.alerting import write_alert
                # heal-detection: merge operation event — per-op log
                write_alert(
                    db, severity="critical", source="merge_intelligence",
                    alert_type="post_merge_regression",
                    summary=f"Regression detected after merge of promotion #{outcome.promotion_id}",
                    detail={"promotion_id": outcome.promotion_id, "detail": detail},
                )
            except Exception as exc:
                log.warning("merge_intelligence: evaluate_merge_outcomes failed: %s", exc)
            try:
                _notify_regression(outcome)
            except Exception as exc:
                log.warning("merge_intelligence: evaluate_merge_outcomes failed: %s", exc)

    if summary["evaluated"] > 0:
        db.flush()
        log.info(
            "merge_eval: evaluated=%d healthy=%d regressed=%d unknown=%d",
            summary["evaluated"], summary["healthy"], summary["regressed"], summary["unknown"],
        )

    return summary


def _evaluate_single(db: Session, outcome: MergeOutcome) -> tuple[str, str]:
    """Evaluate a single merge outcome. Returns (status, detail)."""
    reasons = []

    # Check 1: Did the same bug source reappear?
    from app.models.bugfix_candidate import BugFixCandidate
    original = db.get(BugFixCandidate, outcome.bugfix_candidate_id)
    if not original:
        return "unknown", "original_candidate_deleted — cannot verify regression"
    if original:
        new_candidates = (
            db.query(BugFixCandidate)
            .filter(
                BugFixCandidate.source_type == original.source_type,
                BugFixCandidate.source_ref == original.source_ref,
                BugFixCandidate.created_at > outcome.created_at,
                BugFixCandidate.id != original.id,
            )
            .count()
        )
        if new_candidates > 0:
            reasons.append(f"same_bug_reappeared: {new_candidates} new candidates")

    # Check 2: New critical alerts since merge (within evaluation window)?
    # Only count alerts from sources that could indicate code regression,
    # excluding infrastructure monitors like circuit breakers.
    from app.models.ops_alert import OpsAlert
    _INFRA_ALERT_TYPES = {
        "circuit_breaker_tripped", "worker_stale", "webhook_monitor",
        "budget_exceeded", "rate_limit",
    }
    eval_window_end = outcome.created_at + timedelta(minutes=_MIN_EVAL_DELAY_MINUTES + 5)
    new_critical = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.severity == "critical",
            OpsAlert.created_at > outcome.created_at,
            OpsAlert.created_at <= eval_window_end,
            OpsAlert.resolved == False,
            OpsAlert.source != "merge_intelligence",  # avoid self-referencing
            OpsAlert.alert_type.notin_(_INFRA_ALERT_TYPES),
        )
        .count()
    )
    if new_critical > 0:
        reasons.append(f"new_critical_alerts: {new_critical}")

    if reasons:
        return "regressed", "; ".join(reasons)
    return "healthy", "no_regressions_detected"


def _notify_regression(outcome: MergeOutcome) -> None:
    """Slack notify on regression."""
    try:
        from app.core.alert_delivery import _SLACK_URL
        if not _SLACK_URL:
            return
        import httpx
        httpx.post(_SLACK_URL, json={
            "text": (
                f":rotating_light: *POST-MERGE REGRESSION*\n"
                f"*Promotion:* #{outcome.promotion_id}\n"
                f"*Detail:* {outcome.detail}\n"
                f"_Review: GET /ops/promotions/{outcome.promotion_id}_"
            ),
        }, timeout=5.0)
    except Exception as exc:
        log.warning("merge_intelligence: _notify_regression failed: %s", exc)


# ---------------------------------------------------------------------------
# Context for LLM feedback
# ---------------------------------------------------------------------------

def get_merge_outcome_summary(db: Session) -> str:
    """Compact summary of recent merge outcomes for orchestrator context."""
    rows = db.execute(text("""
        SELECT evaluation_status, COUNT(*) FROM merge_outcomes
        WHERE created_at >= NOW() - INTERVAL '7 days'
        GROUP BY evaluation_status
    """)).fetchall()

    if not rows:
        return "No merge outcomes in last 7 days."

    counts = {r[0]: r[1] for r in rows}
    total = sum(counts.values())
    healthy = counts.get("healthy", 0)
    regressed = counts.get("regressed", 0)
    rate = round(100 * healthy / total) if total > 0 else 0

    return f"Merge outcomes (7d): {total} total, {healthy} healthy, {regressed} regressed, success_rate={rate}%"
