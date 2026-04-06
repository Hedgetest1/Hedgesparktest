"""
evolution_proposal_outcomes.py — Close the learning loop for evolution proposals.

The bugfix pipeline already measures outcomes on every BugFixCandidate
48h after apply (see evolution_outcomes.py). This module propagates
those measurements BACK to the EvolutionProposal that spawned each
candidate, so Monthly Opus can see — cycle after cycle — which of its
past strategic proposals actually delivered impact.

Data flow
---------
  1. Monthly Opus creates EvolutionProposal (outcome_status=NULL).
  2. Converter creates BugFixCandidate with source_type='evolution'
     and source_ref='evolution_{proposal_id}', and (via link_bugfix_
     to_proposal) sets EvolutionProposal.linked_bugfix_candidate_id.
  3. Bugfix pipeline runs (apply or rollback). BugFixCandidate.status
     becomes 'applied' + git_commit_sha is recorded.
  4. evolution_outcomes.evaluate_bugfix_outcomes measures the bugfix
     48h later, setting BugFixCandidate.outcome_status.
  5. propagate_proposal_outcomes() (THIS MODULE, called each agent
     cycle) copies the bugfix's outcome onto the EvolutionProposal:
     applied_at, applied_commit_sha, outcome_status, evidence.
  6. Next Monthly Opus audit reads these fields and feeds them back
     into its prompt (see monthly_evolution_audit._build_prior_monthly_audits).

No new measurement logic — reuses BugFixCandidate's existing 48h
before/after alert-count measurement. Zero extra LLM calls. Zero hot-
path DB work: runs in the agent_worker cycle alongside other outcome
evaluators, bounded to 50 proposals per call.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.bugfix_candidate import BugFixCandidate
from app.models.evolution_proposal import EvolutionProposal

log = logging.getLogger("evolution_proposal_outcomes")

_BATCH_SIZE = 50


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def link_bugfix_to_proposal(db: Session, proposal_id: int, bugfix_candidate_id: int) -> bool:
    """
    Set the linked_bugfix_candidate_id on an EvolutionProposal.

    Idempotent: if the proposal is already linked to the same bugfix,
    returns True without writing. If already linked to a DIFFERENT
    bugfix, does not overwrite and returns False (the first linkage
    wins — prevents a stale re-conversion from losing provenance).
    Returns True on successful link, False otherwise.
    """
    prop = db.query(EvolutionProposal).filter(EvolutionProposal.id == proposal_id).first()
    if prop is None:
        return False
    if prop.linked_bugfix_candidate_id == bugfix_candidate_id:
        return True
    if prop.linked_bugfix_candidate_id is not None:
        log.info(
            "evolution_proposal_outcomes: proposal=%d already linked to bugfix=%d, refusing overwrite with %d",
            proposal_id, prop.linked_bugfix_candidate_id, bugfix_candidate_id,
        )
        return False
    prop.linked_bugfix_candidate_id = bugfix_candidate_id
    db.flush()
    return True


def propagate_proposal_outcomes(db: Session) -> dict:
    """
    Copy bugfix outcomes onto linked EvolutionProposal rows.

    Scan up to _BATCH_SIZE proposals that have a linked bugfix but no
    outcome yet. For each, look up the linked BugFixCandidate and, if
    the bugfix is applied and has a measured outcome, mirror those
    values onto the proposal.

    Returns: {"scanned": n, "updated": n, "still_pending": n}
    """
    summary = {"scanned": 0, "updated": 0, "still_pending": 0}

    rows = (
        db.query(EvolutionProposal)
        .filter(
            EvolutionProposal.linked_bugfix_candidate_id.isnot(None),
            EvolutionProposal.outcome_status.is_(None),
        )
        .limit(_BATCH_SIZE)
        .all()
    )

    if not rows:
        return summary

    bugfix_ids = [r.linked_bugfix_candidate_id for r in rows]
    bugfixes_by_id = {
        b.id: b for b in (
            db.query(BugFixCandidate).filter(BugFixCandidate.id.in_(bugfix_ids)).all()
        )
    }

    for prop in rows:
        summary["scanned"] += 1
        bug = bugfixes_by_id.get(prop.linked_bugfix_candidate_id)
        if bug is None:
            # Dangling link (bugfix deleted). Mark outcome as inconclusive
            # with a note so Monthly Opus doesn't keep waiting forever.
            prop.outcome_status = "inconclusive"
            prop.outcome_measured_at = _now()
            prop.outcome_evidence = json.dumps({
                "source": "bugfix_candidate",
                "bugfix_id": prop.linked_bugfix_candidate_id,
                "note": "linked bugfix no longer exists",
            })
            summary["updated"] += 1
            continue

        # Mirror apply metadata as soon as it's available — even before
        # the outcome is measured, so operators can see that the proposal
        # was adopted and deployed.
        if prop.applied_at is None and bug.applied_at is not None:
            prop.applied_at = bug.applied_at
            prop.applied_commit_sha = bug.git_commit_sha

        # Outcome not measured yet on the bugfix — wait.
        if not bug.outcome_status:
            summary["still_pending"] += 1
            continue

        prop.outcome_status = bug.outcome_status
        prop.outcome_measured_at = bug.outcome_measured_at or _now()
        prop.outcome_evidence = json.dumps({
            "source": "bugfix_candidate",
            "bugfix_id": bug.id,
            "bugfix_source_type": bug.source_type,
            "bugfix_outcome_evidence": _parse_json(bug.outcome_evidence),
            "bugfix_git_commit_sha": bug.git_commit_sha,
        })
        summary["updated"] += 1

    db.flush()
    if summary["updated"] > 0:
        log.info(
            "evolution_proposal_outcomes: scanned=%d updated=%d still_pending=%d",
            summary["scanned"], summary["updated"], summary["still_pending"],
        )
    return summary


def _parse_json(raw: str | None):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


# ---------------------------------------------------------------------------
# Aggregate stats — feeds the Monthly Opus prompt
# ---------------------------------------------------------------------------

def get_proposal_effectiveness_stats(db: Session, limit_cycles: int = 6) -> dict:
    """
    Return monthly_opus proposal effectiveness over the most recent N cycles.

    Returns:
        {
          "total_proposals": int,
          "by_outcome": {
            "effective": int, "ineffective": int,
            "inconclusive": int, "unmeasured": int,
          },
          "effectiveness_rate": float,  # effective / (effective+ineffective), or 0
          "by_cycle": {
            "2026-M04": {"total": n, "effective": n, "ineffective": n, "inconclusive": n, "unmeasured": n},
            ...
          },
        }

    Only considers proposals with dedup_key starting 'monthly_opus:'.
    "unmeasured" = outcome_status IS NULL (pending or never linked).
    """
    rows = (
        db.query(EvolutionProposal)
        .filter(EvolutionProposal.dedup_key.like("monthly_opus:%"))
        .order_by(EvolutionProposal.created_at.desc())
        .limit(500)
        .all()
    )

    by_cycle: dict[str, dict[str, int]] = {}
    by_outcome = {"effective": 0, "ineffective": 0, "inconclusive": 0, "unmeasured": 0}
    for r in rows:
        cycle = r.audit_cycle or "unknown"
        bucket = r.outcome_status if r.outcome_status in ("effective", "ineffective", "inconclusive") else "unmeasured"
        by_outcome[bucket] += 1
        cycle_row = by_cycle.setdefault(cycle, {"total": 0, "effective": 0, "ineffective": 0, "inconclusive": 0, "unmeasured": 0})
        cycle_row["total"] += 1
        cycle_row[bucket] += 1

    recent_cycles = sorted(by_cycle.keys(), reverse=True)[:limit_cycles]
    by_cycle_trimmed = {c: by_cycle[c] for c in recent_cycles}

    denom = by_outcome["effective"] + by_outcome["ineffective"]
    effectiveness_rate = round(by_outcome["effective"] / denom, 3) if denom > 0 else 0.0

    return {
        "total_proposals": len(rows),
        "by_outcome": by_outcome,
        "effectiveness_rate": effectiveness_rate,
        "by_cycle": by_cycle_trimmed,
    }
