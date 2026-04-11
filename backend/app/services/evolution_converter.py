"""
evolution_converter.py — Converts LEVEL_1 evolution proposals into bugfix candidates.

Only converts proposals where:
    risk_level == "LEVEL_1"
    auto_applicable == True
    status == "open"

Safety guards:
    - Dedup: no duplicate open/analyzed/patch_proposed bugfix candidate for same proposal
    - Max conversions per cycle (default: 2)
    - Proposals marked as "converted" after mapping (prevents re-conversion)
    - Failed proposals (proposal_error set, patch generation failed 2+ times) are skipped

Priority ordering:
    If a fresh meta-review exists, proposals are converted in meta-review priority
    order (highest score first) instead of creation date.
    If no fresh meta-review exists, falls back to creation date (FIFO).

The converted bugfix candidate flows through the exact same pipeline:
    auto-propose → classify → auto-apply (if PATCH_TIER_0) → human-approve otherwise
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.evolution_proposal import EvolutionProposal
from app.models.bugfix_candidate import BugFixCandidate
from app.services.audit import write_audit_log

log = logging.getLogger("evolution_converter")

_MAX_PROPOSAL_FAILURES = 2  # skip proposals whose bugfix candidates failed this many times


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_priority_ordered_ids(db: Session) -> list[int]:
    """
    Get proposal IDs ordered by meta-review priority.
    Returns empty list if no fresh meta-review exists.
    """
    try:
        from app.services.meta_reviewer import get_proposal_priority_order
        return get_proposal_priority_order(db)
    except Exception:
        return []


def convert_eligible_proposals(db: Session, max_per_cycle: int = 2) -> dict:
    """
    Convert eligible LEVEL_1 evolution proposals into bugfix candidates.
    Returns: {"scanned": N, "converted": N, "skipped_dedup": N, "skipped_ineligible": N}
    """
    summary = {"scanned": 0, "converted": 0, "skipped_dedup": 0, "skipped_ineligible": 0}

    proposals = (
        db.query(EvolutionProposal)
        .filter(
            EvolutionProposal.risk_level == "LEVEL_1",
            EvolutionProposal.auto_applicable == True,
            EvolutionProposal.status == "open",
        )
        .order_by(EvolutionProposal.created_at)
        .limit(max_per_cycle * 3)  # fetch more than needed to account for dedup/skip
        .all()
    )

    # Apply meta-review priority ordering if a fresh review exists
    priority_ids = _get_priority_ordered_ids(db)
    if priority_ids:
        # Build a priority map: proposal_id → rank (lower = higher priority)
        priority_map = {pid: rank for rank, pid in enumerate(priority_ids)}
        # Sort proposals by meta-review priority (lowest rank = highest priority)
        # Proposals not in meta-review go to the end (high rank value)
        proposals.sort(key=lambda p: priority_map.get(p.id, 99999))
        log.info("evolution_converter: using meta-review priority order (%d proposals ranked)", len(priority_ids))

    converted = 0
    for proposal in proposals:
        summary["scanned"] += 1

        if converted >= max_per_cycle:
            break

        # Dedup: check if bugfix candidate already exists for this proposal
        source_ref = f"evolution_{proposal.id}"
        existing = (
            db.query(BugFixCandidate)
            .filter(
                BugFixCandidate.source_type == "evolution",
                BugFixCandidate.source_ref == source_ref,
                BugFixCandidate.status.in_(["open", "analyzed", "patch_proposed", "approved", "applying", "applied"]),
            )
            .first()
        )
        if existing:
            summary["skipped_dedup"] += 1
            continue

        # Check if previous conversions for this proposal failed too many times
        failed_count = (
            db.query(BugFixCandidate)
            .filter(
                BugFixCandidate.source_type == "evolution",
                BugFixCandidate.source_ref == source_ref,
                BugFixCandidate.status.in_(["apply_failed", "rolled_back"]),
            )
            .count()
        )
        if failed_count >= _MAX_PROPOSAL_FAILURES:
            summary["skipped_ineligible"] += 1
            continue

        # === UX-SENSITIVE PROPOSALS ARE NEVER AUTO-CONVERTED ===
        # UX-sensitive proposals require human review and cannot flow
        # through the autonomous conversion pipeline.
        if getattr(proposal, "ux_sensitive", False):
            log.info(
                "evolution_converter: UX_SENSITIVE BLOCKED proposal=%d",
                proposal.id,
            )
            summary["skipped_ineligible"] += 1
            continue

        # === EXECUTION POLICY ENFORCEMENT ===
        # Tier-check the target file BEFORE conversion to avoid wasting
        # LLM proposal calls on files the guard will block at apply time.
        if proposal.target_file:
            try:
                from app.core.tier_check import check_tier, TIER_2
                tier_result = check_tier([proposal.target_file])
                if tier_result.tier == TIER_2:
                    log.info(
                        "evolution_converter: TIER_2 BLOCKED proposal=%d file=%s",
                        proposal.id, proposal.target_file,
                    )
                    summary["skipped_ineligible"] += 1
                    continue
            except ImportError:
                pass

        # Reviewer gate — assess proposal before conversion
        try:
            from app.services.reviewer_layer import review_entity
            assessment = review_entity(db, "evolution_proposal", proposal.id)
            if assessment:
                proposal.reviewer_assessment_id = assessment.id
                db.flush()
                if assessment.verdict in ("reject", "refine"):
                    log.info(
                        "evolution_converter: REVIEWER %s proposal=%d",
                        assessment.verdict.upper(), proposal.id,
                    )
                    summary["skipped_ineligible"] += 1
                    continue
        except Exception as exc:
            log.warning("evolution_converter: reviewer error (non-fatal): %s", exc)

        # Create bugfix candidate
        context = {
            "evolution_proposal_id": proposal.id,
            "target_file": proposal.target_file,
            "reason": proposal.reason,
            "expected_impact": proposal.expected_impact,
            "audit_cycle": proposal.audit_cycle,
            "dedup_key": proposal.dedup_key,
        }

        candidate = BugFixCandidate(
            source_type="evolution",
            source_ref=source_ref,
            title=f"[Evolution] {proposal.reason[:200]}",
            summary=f"Auto-generated from evolution proposal #{proposal.id}: {proposal.expected_impact or proposal.reason}",
            context_json=json.dumps(context, default=str),
            status="open",
        )
        db.add(candidate)

        # Mark proposal as converted
        proposal.status = "accepted"
        proposal.decided_by = "evolution_converter"
        proposal.decided_at = _now()

        write_audit_log(
            db,
            actor_type="system",
            actor_name="evolution_converter",
            action_type="evolution_to_bugfix",
            target_type="evolution_proposal",
            target_id=str(proposal.id),
            after_state={"bugfix_source_ref": source_ref, "title": candidate.title[:100]},
            status="completed",
            approval_mode="autonomous",
        )

        db.flush()
        converted += 1
        summary["converted"] += 1

    if summary["converted"] > 0:
        log.info(
            "evolution_converter: scanned=%d converted=%d dedup=%d ineligible=%d",
            summary["scanned"], summary["converted"],
            summary["skipped_dedup"], summary["skipped_ineligible"],
        )

    return summary
