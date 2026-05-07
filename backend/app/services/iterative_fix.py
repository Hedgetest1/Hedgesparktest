# test-coverage: superseded by Brain Vero pivot 2026-05-07; Stage 2-C deletion pending
"""iterative_fix — Sprint C of CTO-brain pipeline upgrade.

When the adversarial_reviewer flags a BugFixCandidate with severity
>= `_ITERATE_THRESHOLD` (default 7) on ANY lens, this service schedules
a follow-up candidate (iteration v2+) that carries the adversarial
findings as context so propose_patch can generate a revised patch
addressing the concerns.

Async design (chosen over sync for pipeline-architecture symmetry):
  1. Adversarial findings trigger `maybe_schedule_iteration`
  2. New BugFixCandidate created with `status="open"`,
     `source_type="iteration"`, `parent_candidate_id=parent.id`,
     `iteration_num=parent.iteration_num + 1`
  3. `context_json` includes the parent's patch_diff + findings summary
  4. Next `run_bug_triage` cycle (10-min cadence) picks up the
     iteration candidate, proposes a new patch, runs adversarial
     again, and may iterate further (up to max depth).

Loop protection:
  * `_MAX_ITERATION_DEPTH` (default 3) — stops runaway loops
  * After max depth with unresolved severity >= 7, emits
    `iterative_fix_max_depth_escalation` ops_alert (critical) →
    human takes over
  * Feature-flagged `ITERATIVE_FIX_ENABLED` (default off — pipeline
    paused pre-merchant)

Cost projection (per feedback_external_software_cost_10_100_1k_10k):
  10   merchants: €0.01/mo (incremental over Sprint B)
  100  merchants: €0.05/mo
  1k   merchants: €0.30/mo
  10k  merchants: €3/mo
  (adds ~1 iteration × 1 propose + 3 adversarial lens = 4 calls × €0.001)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Sequence

from sqlalchemy.orm import Session

from app.models.adversarial_review_finding import AdversarialReviewFinding
from app.models.bugfix_candidate import BugFixCandidate

log = logging.getLogger("iterative_fix")

_ITERATE_THRESHOLD = int(os.getenv("ITERATIVE_FIX_SEVERITY_THRESHOLD", "7"))
_MAX_ITERATION_DEPTH = int(os.getenv("ITERATIVE_FIX_MAX_DEPTH", "3"))


def is_enabled() -> bool:
    return os.getenv("ITERATIVE_FIX_ENABLED", "0").lower() in ("1", "true", "yes")


def should_iterate(
    findings: Sequence[AdversarialReviewFinding],
    current_iteration: int,
) -> bool:
    """Trigger iteration iff (a) at least one finding hit the severity
    threshold AND (b) we're not at max depth yet."""
    if current_iteration >= _MAX_ITERATION_DEPTH:
        return False
    if not findings:
        return False
    return any(f.severity >= _ITERATE_THRESHOLD for f in findings)


def _build_iteration_context(
    parent: BugFixCandidate,
    findings: Sequence[AdversarialReviewFinding],
) -> str:
    """JSON blob persisted as the iteration's `context_json` — consumed
    by propose_patch at next triage cycle to augment the LLM prompt
    with the previous patch + adversarial concerns."""
    blocking = [
        {
            "lens": f.lens,
            "severity": f.severity,
            "concern": f.concern,
            "suggested_remediation": f.suggested_remediation,
        }
        for f in findings
        if f.severity >= _ITERATE_THRESHOLD
    ]
    return json.dumps({
        "iteration_parent_id": parent.id,
        "parent_iteration_num": parent.iteration_num,
        "parent_patch_summary": parent.patch_summary,
        "parent_patch_diff": (parent.patch_diff or "")[:4000],
        "adversarial_concerns": blocking,
        "instruction": (
            "This is an ITERATIVE re-propose. The previous patch was "
            "approved by deterministic review but failed adversarial "
            "DA (severity >= 7 on at least one lens). Your new patch "
            "MUST address the listed concerns without reintroducing "
            "the original bug. Reference the parent patch_diff above."
        ),
    })


def _write_ops_alert(
    db: Session, severity: str, alert_type: str, source: str,
    summary: str, detail: dict,
) -> None:
    try:
        from app.services.alerting import write_alert
        # heal-detection: fix attempt event — per-attempt log entry
        write_alert(
            db, severity=severity, source=source, alert_type=alert_type,
            summary=summary, detail=detail,
        )
    except Exception as exc:
        log.warning("iterative_fix: ops_alert write failed: %s", exc)


def maybe_schedule_iteration(
    db: Session,
    parent: BugFixCandidate,
    findings: Sequence[AdversarialReviewFinding],
) -> BugFixCandidate | None:
    """Create an iteration candidate if findings warrant it. Returns
    the new candidate or None if no iteration needed / feature off.

    On hitting max depth: emits escalation ops_alert instead of
    creating another iteration."""
    if not is_enabled():
        log.debug("iterative_fix: disabled (feature flag off)")
        return None

    if parent is None or parent.id is None:
        return None

    blocking_findings = [f for f in findings if f.severity >= _ITERATE_THRESHOLD]

    if not blocking_findings:
        log.debug(
            "iterative_fix: candidate=%d no blocking findings (max severity %d < threshold %d)",
            parent.id,
            max((f.severity for f in findings), default=0),
            _ITERATE_THRESHOLD,
        )
        return None

    current_iter = int(parent.iteration_num or 1)

    if current_iter >= _MAX_ITERATION_DEPTH:
        # Max depth reached without converging — escalate to human.
        log.warning(
            "iterative_fix: candidate=%d hit max depth %d, escalating",
            parent.id, _MAX_ITERATION_DEPTH,
        )
        _write_ops_alert(
            db,
            severity="critical",
            alert_type="iterative_fix_max_depth_escalation",
            source="iterative_fix:escalation",
            summary=(
                f"Bugfix candidate #{parent.id} reached iteration max "
                f"depth {_MAX_ITERATION_DEPTH} with unresolved adversarial "
                f"concerns — requires human review"
            ),
            detail={
                "parent_candidate_id": parent.id,
                "final_iteration_num": current_iter,
                "max_depth": _MAX_ITERATION_DEPTH,
                "unresolved_findings": [
                    {
                        "lens": f.lens,
                        "severity": f.severity,
                        "concern": f.concern,
                    }
                    for f in blocking_findings
                ],
            },
        )
        return None

    new_iteration_num = current_iter + 1
    child = BugFixCandidate(
        status="open",
        source_type="iteration",
        source_ref=f"iteration:{parent.id}:v{new_iteration_num}",
        title=(
            f"Iteration #{new_iteration_num} of bugfix #{parent.id}: "
            f"{parent.title[:160]}"
        ),
        summary=(
            f"Iterative re-propose triggered by adversarial reviewer. "
            f"{len(blocking_findings)} finding(s) at severity >= "
            f"{_ITERATE_THRESHOLD}. See context_json for the full "
            f"parent patch + concerns the next propose_patch must "
            f"address."
        ),
        context_json=_build_iteration_context(parent, findings),
        parent_candidate_id=parent.id,
        iteration_num=new_iteration_num,
        affected_domain=parent.affected_domain,
        evidence_source=parent.evidence_source or "pre_merchant",
    )
    db.add(child)
    db.flush()

    log.info(
        "iterative_fix: scheduled iteration candidate=%d iter=%d from parent=%d",
        child.id, new_iteration_num, parent.id,
    )
    return child


__all__ = [
    "is_enabled",
    "should_iterate",
    "maybe_schedule_iteration",
]
