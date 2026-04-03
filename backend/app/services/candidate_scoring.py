"""
candidate_scoring.py — Priority + confidence scoring for bugfix candidates.

Two deterministic scoring functions:
    compute_priority_score(incident, packet) → (score, detail_dict)
    compute_fix_confidence(db, candidate) → (score, detail_dict)

Both return 0-100 integer scores with full explainability via detail dicts.

Priority score drives queue ordering: which bugs to fix first.
Confidence score gates auto-apply: how much to trust a proposed fix.

No LLM calls. No magic. Every weight is explicit and documented.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

log = logging.getLogger("candidate_scoring")


# ---------------------------------------------------------------------------
# Priority scoring — "which bug deserves attention first?"
# ---------------------------------------------------------------------------
#
# Formula:
#   priority_score = (
#       severity_weight              # 0-30 points
#       + merchant_impact_weight     # 0-25 points
#       + recurrence_weight          # 0-20 points
#       + subsystem_weight           # 0-10 points
#       + regression_bonus           # 0-15 points
#   )
#
# Max = 100.  Score is clamped to [0, 100].

_SEVERITY_WEIGHTS = {
    "critical": 30,
    "error": 20,
    "warning": 8,
    "info": 2,
}

_MERCHANT_IMPACT_WEIGHTS = {
    "high": 25,
    "medium": 15,
    "low": 5,
    "none": 0,
}

_SUBSYSTEM_WEIGHTS = {
    "backend_api": 10,
    "worker": 6,
    "frontend_dashboard": 3,
    "unknown": 5,
}

# Recurrence points: logarithmic — diminishing returns after 10
# 1→2, 2→5, 3→8, 5→12, 10→16, 20+→20
def _recurrence_points(count: int) -> int:
    if count <= 0:
        return 0
    if count == 1:
        return 2
    if count <= 3:
        return 2 + (count - 1) * 3  # 5, 8
    if count <= 10:
        return 8 + (count - 3) * 1  # 9..15
    return min(20, 15 + (count - 10) // 5)  # 16..20

_REGRESSION_BONUS = 15


def compute_priority_score(
    severity: str | None,
    merchant_impact: str | None,
    recurrence_count: int,
    subsystem_class: str | None,
    is_regression_candidate: str | None,
    *,
    calibration=None,
    impact_signal: float | None = None,
) -> tuple[int, dict]:
    """
    Compute a priority score (0-100) for a bugfix candidate.

    If calibration is provided (ScoringCalibration from scoring_calibration.py),
    applies adaptive offsets learned from real outcomes. Base formula unchanged.

    If impact_signal is provided (from merchant metrics), adds a bounded bonus.

    Returns (score, detail_dict) where detail_dict explains each component.
    """
    sev_pts = _SEVERITY_WEIGHTS.get(severity or "error", 10)
    imp_pts = _MERCHANT_IMPACT_WEIGHTS.get(merchant_impact or "low", 5)
    rec_pts = _recurrence_points(recurrence_count)
    sub_pts = _SUBSYSTEM_WEIGHTS.get(subsystem_class or "unknown", 5)
    reg_pts = _REGRESSION_BONUS if is_regression_candidate == "yes" else 0

    # Adaptive calibration offset (0 if no calibration provided)
    cal_offset = 0
    if calibration:
        cal_offset = calibration.get_priority_offset(severity, subsystem_class)

    # Merchant impact signal from real revenue data
    impact_pts = int(impact_signal) if impact_signal else 0

    raw_score = sev_pts + imp_pts + rec_pts + sub_pts + reg_pts + cal_offset + impact_pts
    score = max(0, min(100, raw_score))

    detail = {
        "severity": {"value": severity, "points": sev_pts},
        "merchant_impact": {"value": merchant_impact, "points": imp_pts},
        "recurrence": {"count": recurrence_count, "points": rec_pts},
        "subsystem": {"value": subsystem_class, "points": sub_pts},
        "regression": {"is_candidate": is_regression_candidate == "yes", "points": reg_pts},
        "calibration_offset": cal_offset,
        "impact_signal": impact_pts,
        "total": score,
    }

    return score, detail


# ---------------------------------------------------------------------------
# Confidence scoring — "how much should we trust this fix?"
# ---------------------------------------------------------------------------
#
# Formula:
#   fix_confidence = (
#       base_confidence           # 30 (we always start cautious)
#       + lesson_bonus            # 0-25 (past effective lessons for this domain)
#       + evidence_quality        # 0-20 (stack trace + culprit clarity)
#       + recurrence_confidence   # 0-15 (more recurrences = more sure it's real)
#       - criticality_penalty     # 0-20 (critical subsystems get lower confidence)
#       - novelty_penalty         # 0-15 (first-ever error type in domain = less trust)
#   )
#
# Clamped to [5, 95]. Never 0 (always leave room for review), never 100.

_BASE_CONFIDENCE = 30
_MAX_LESSON_BONUS = 25
_MAX_EVIDENCE_BONUS = 20
_MAX_RECURRENCE_BONUS = 15
_MAX_CRITICALITY_PENALTY = 20
_MAX_NOVELTY_PENALTY = 15

_CRITICALITY_PENALTIES = {
    "critical": 20,
    "high": 12,
    "medium": 5,
    "low": 0,
}


def compute_fix_confidence(
    db: Session,
    candidate,
    *,
    calibration=None,
) -> tuple[int, dict]:
    """
    Compute how trustworthy a proposed fix is (0-100).

    High confidence = safe for auto-apply.
    Low confidence = require human review.

    If calibration is provided, applies adaptive offsets learned from outcomes.

    Returns (score, detail_dict).
    """
    context = {}
    if candidate.context_json:
        try:
            context = json.loads(candidate.context_json)
        except (json.JSONDecodeError, ValueError):
            pass

    # --- Lesson bonus: effective lessons in this domain = more trust ---
    lesson_bonus = 0
    effective_lesson_count = 0
    try:
        from app.models.system_lesson import SystemLesson
        domain = candidate.affected_domain or "unknown"
        if domain != "unknown":
            effective_lessons = (
                db.query(SystemLesson)
                .filter(
                    SystemLesson.domain == domain,
                    SystemLesson.lesson_type == "effective_pattern",
                    SystemLesson.status == "active",
                    SystemLesson.confidence >= 0.5,
                )
                .count()
            )
            effective_lesson_count = effective_lessons
            # Each effective lesson adds trust, diminishing returns
            lesson_bonus = min(_MAX_LESSON_BONUS, effective_lessons * 8)
    except Exception:
        pass

    # --- Evidence quality: clear stack trace + culprit = better fix ---
    evidence_pts = 0
    stack_trace = context.get("stack_trace", "")
    culprit = context.get("culprit", "")

    if stack_trace and len(stack_trace) > 100:
        evidence_pts += 10  # meaningful stack trace
    if stack_trace and "File " in stack_trace and "line " in stack_trace:
        evidence_pts += 5   # has file+line references
    if culprit and "/" in culprit:
        evidence_pts += 5   # specific file path
    evidence_pts = min(_MAX_EVIDENCE_BONUS, evidence_pts)

    # --- Recurrence confidence: more instances = more confident it's real ---
    recurrence = context.get("recurrence_count", 1)
    if recurrence >= 10:
        recurrence_pts = 15
    elif recurrence >= 5:
        recurrence_pts = 10
    elif recurrence >= 3:
        recurrence_pts = 7
    elif recurrence >= 2:
        recurrence_pts = 4
    else:
        recurrence_pts = 0

    # --- Criticality penalty: critical subsystems get lower confidence ---
    criticality = context.get("criticality", "medium")
    crit_penalty = _CRITICALITY_PENALTIES.get(criticality, 5)

    # --- Novelty penalty: first time seeing this error type in this domain ---
    novelty_penalty = 0
    try:
        from app.models.bugfix_candidate import BugFixCandidate
        domain = candidate.affected_domain or "unknown"
        if domain != "unknown":
            past_fixes = (
                db.query(BugFixCandidate)
                .filter(
                    BugFixCandidate.affected_domain == domain,
                    BugFixCandidate.outcome_status.in_(["effective", "ineffective"]),
                    BugFixCandidate.id != candidate.id,
                )
                .count()
            )
            if past_fixes == 0:
                novelty_penalty = _MAX_NOVELTY_PENALTY  # no history = less trust
            elif past_fixes <= 2:
                novelty_penalty = 8
            else:
                novelty_penalty = 0
    except Exception:
        novelty_penalty = 10  # conservative on error

    # Adaptive calibration offset (includes domain + remediation class)
    cal_offset = 0
    remediation_class = getattr(candidate, "remediation_class", None)
    if calibration:
        cal_offset = calibration.get_confidence_offset(candidate.affected_domain, remediation_class)

    raw = (_BASE_CONFIDENCE + lesson_bonus + evidence_pts + recurrence_pts
           - crit_penalty - novelty_penalty + cal_offset)
    score = max(5, min(95, raw))

    detail = {
        "base": _BASE_CONFIDENCE,
        "lesson_bonus": {"count": effective_lesson_count, "points": lesson_bonus},
        "evidence_quality": {"has_trace": bool(stack_trace), "has_culprit": bool(culprit), "points": evidence_pts},
        "recurrence": {"count": recurrence, "points": recurrence_pts},
        "criticality_penalty": {"value": criticality, "points": -crit_penalty},
        "novelty_penalty": {"past_fixes_in_domain": past_fixes if 'past_fixes' in dir() else None, "points": -novelty_penalty},
        "calibration_offset": cal_offset,
        "remediation_class": remediation_class,
        "total": score,
    }

    return score, detail
