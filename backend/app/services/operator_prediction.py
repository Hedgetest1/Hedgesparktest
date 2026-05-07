# test-coverage: superseded by Brain Vero pivot 2026-05-07; Stage 2-C deletion pending
"""
operator_prediction.py — D6: predict the operator's likely decision
on a TIER_2 bugfix candidate from historical audit-log patterns.

Contract
--------
Given a candidate, return a structured recommendation so the TIER_2
weekly review can pre-highlight the expected action for each row:

    {
        "recommendation": "approve" | "reject" | "unknown",
        "confidence": 0.0..1.0,
        "posterior_mean": 0.0..1.0,
        "sample_size": int,
        "signal": "file_pattern:app/services" | "domain:pipeline" | "prior",
    }

The prediction is a Beta posterior on (file_pattern → approved rate).
File pattern = the directory prefix of the candidate's first patched
file. We fall back to `affected_domain` if no files are declared, and
to the global prior if neither key has enough historical decisions.

Zero LLM. Pure SQL. Deterministic.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

log = logging.getLogger("operator_prediction")

# Decisions older than this are not load-bearing evidence for how the
# operator thinks today. Long enough to have data, short enough that a
# strategy shift propagates within a month.
_LOOKBACK_DAYS = 90

# Minimum total decisions for a file-pattern posterior to be usable.
# Below this threshold we escalate to the affected_domain fallback.
_MIN_SAMPLE = 3

# Posterior mean threshold: above → recommend approve, below → reject.
# Cone of uncertainty: if |posterior_mean - 0.5| < _NEUTRAL_BAND we
# return "unknown" regardless of sample size.
_APPROVE_THRESHOLD = 0.65
_REJECT_THRESHOLD = 0.35
_NEUTRAL_BAND = 0.10


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _file_pattern(candidate) -> str | None:
    """Return the directory prefix of the candidate's first patched
    file, e.g. `app/services/`. None if we can't parse."""
    try:
        files = json.loads(candidate.patch_files) if candidate.patch_files else []
    except Exception:
        return None
    if not isinstance(files, list):
        return None
    for f in files:
        if not isinstance(f, str) or not f:
            continue
        parts = f.split("/")
        if len(parts) >= 2:
            return "/".join(parts[:2]) + "/"
        return parts[0]
    return None


def _count_decisions(
    db: Session, *, target_ids: list[str] | None = None,
    candidate_ids_sql: str | None = None,
) -> tuple[int, int]:
    """Return (approved_count, rejected_count) from audit_log for the
    given set of candidate target_ids within the lookback window."""
    from app.models.audit_log import AuditLog

    cutoff = _now() - timedelta(days=_LOOKBACK_DAYS)

    q = (
        db.query(AuditLog.action_type, func.count(AuditLog.id))
        .filter(
            AuditLog.target_type == "bugfix",
            AuditLog.action_type.in_([
                "bugfix_approved", "bugfix_rejected",
            ]),
            AuditLog.created_at >= cutoff,
        )
    )
    if target_ids is not None:
        if not target_ids:
            return 0, 0
        q = q.filter(AuditLog.target_id.in_(target_ids))
    rows = q.group_by(AuditLog.action_type).all()

    approved = 0
    rejected = 0
    for action, n in rows:
        if action == "bugfix_approved":
            approved = int(n or 0)
        elif action == "bugfix_rejected":
            rejected = int(n or 0)
    return approved, rejected


def _candidates_in_pattern(
    db: Session, *, pattern_prefix: str | None,
    affected_domain: str | None,
) -> list[str]:
    """Return the list of candidate ids (as strings) whose first patched
    file starts with `pattern_prefix`, OR whose affected_domain matches.
    Pattern takes priority; domain is the fallback query path."""
    from app.models.bugfix_candidate import BugFixCandidate

    cutoff = _now() - timedelta(days=_LOOKBACK_DAYS)

    if pattern_prefix:
        # SQL LIKE across the JSON-serialized patch_files column is
        # strictly a substring match — good enough because the pattern
        # prefix is anchored to the start of a real file path.
        rows = (
            db.query(BugFixCandidate.id)
            .filter(
                BugFixCandidate.created_at >= cutoff,
                BugFixCandidate.patch_risk_tier == 2,
                BugFixCandidate.patch_files.like(f'%"{pattern_prefix}%'),
            )
            .all()
        )
        return [str(r[0]) for r in rows]

    if affected_domain:
        rows = (
            db.query(BugFixCandidate.id)
            .filter(
                BugFixCandidate.created_at >= cutoff,
                BugFixCandidate.patch_risk_tier == 2,
                BugFixCandidate.affected_domain == affected_domain,
            )
            .all()
        )
        return [str(r[0]) for r in rows]

    return []


def _beta_posterior_mean(approved: int, rejected: int) -> float:
    """Beta(approved + 1, rejected + 1) posterior mean."""
    a = approved + 1
    b = rejected + 1
    return a / (a + b)


def _classify(posterior_mean: float, sample_size: int) -> tuple[str, float]:
    """Return (recommendation, confidence) given a posterior mean and n."""
    # Confidence scales with sample size up to ~20 decisions then saturates
    confidence = min(1.0, sample_size / 20.0)

    if abs(posterior_mean - 0.5) < _NEUTRAL_BAND or sample_size < _MIN_SAMPLE:
        return "unknown", max(0.1, confidence * 0.5)
    if posterior_mean >= _APPROVE_THRESHOLD:
        return "approve", confidence
    if posterior_mean <= _REJECT_THRESHOLD:
        return "reject", confidence
    return "unknown", max(0.1, confidence * 0.5)


def predict_decision_for_candidate(
    db: Session, candidate,
) -> dict:
    """Predict the operator's likely decision on this candidate.

    Uses the candidate's file pattern (directory prefix) as the primary
    evidence bucket; falls back to affected_domain; falls back to the
    global prior if neither bucket has enough decisions.
    """
    file_pattern = _file_pattern(candidate)
    signal = "prior"

    # 1. Try file pattern
    if file_pattern:
        ids = _candidates_in_pattern(
            db, pattern_prefix=file_pattern,
            affected_domain=None,
        )
        if ids:
            approved, rejected = _count_decisions(db, target_ids=ids)
            if approved + rejected >= _MIN_SAMPLE:
                posterior = _beta_posterior_mean(approved, rejected)
                rec, conf = _classify(posterior, approved + rejected)
                return {
                    "recommendation": rec,
                    "confidence": round(conf, 3),
                    "posterior_mean": round(posterior, 3),
                    "sample_size": approved + rejected,
                    "signal": f"file_pattern:{file_pattern}",
                }

    # 2. Fallback: affected_domain
    domain = getattr(candidate, "affected_domain", None)
    if domain:
        ids = _candidates_in_pattern(
            db, pattern_prefix=None, affected_domain=domain,
        )
        if ids:
            approved, rejected = _count_decisions(db, target_ids=ids)
            if approved + rejected >= _MIN_SAMPLE:
                posterior = _beta_posterior_mean(approved, rejected)
                rec, conf = _classify(posterior, approved + rejected)
                return {
                    "recommendation": rec,
                    "confidence": round(conf, 3),
                    "posterior_mean": round(posterior, 3),
                    "sample_size": approved + rejected,
                    "signal": f"domain:{domain}",
                }

    # 3. Fallback: global prior — Beta(1, 1) uniform → 0.5
    return {
        "recommendation": "unknown",
        "confidence": 0.0,
        "posterior_mean": 0.5,
        "sample_size": 0,
        "signal": "prior",
    }
