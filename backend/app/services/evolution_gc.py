"""
evolution_gc.py — Evolution Proposal Garbage Collector.

Deterministic, no-LLM cleanup layer that keeps the evolution backlog
current by detecting stale, duplicate, or indirectly-resolved proposals.

Statuses assigned by this module:
    obsolete             — target changed materially AND superseded by another proposal/fix
    resolved_indirectly  — merged bugfix already covers the same target area
    needs_revalidation   — target area changed enough that original suggestion may be inaccurate

Rules are conservative: prefer needs_revalidation over resolved_indirectly.
Proposals are NEVER hard-deleted.

Cooldown: once per 24 hours (in-process monotonic clock).
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path as _Path
from typing import NamedTuple

# Derive backend root dynamically so GC checks work in CI (checked-out repo)
# and on production (/opt/wishspark/backend/).
_BACKEND_DIR = _Path(os.environ.get("REPO_ROOT", _Path(__file__).parent.parent.parent.parent)) / "backend"

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.evolution_proposal import EvolutionProposal, GC_SOURCE_STATUSES
from app.services.audit import write_audit_log

log = logging.getLogger("evolution_gc")

# ---------------------------------------------------------------------------
# Cooldown — once per 24 hours
# ---------------------------------------------------------------------------

_GC_COOLDOWN_SECONDS = 24 * 3600
_last_gc_run: float | None = None


def should_run_gc() -> bool:
    if _last_gc_run is None:
        return True
    return (time.monotonic() - _last_gc_run) >= _GC_COOLDOWN_SECONDS


def mark_gc_run():
    global _last_gc_run
    _last_gc_run = time.monotonic()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _Transition(NamedTuple):
    proposal_id: int
    new_status: str
    reason: str


def _transition(db: Session, proposal: EvolutionProposal, new_status: str, reason: str) -> None:
    """Safely transition a proposal and write audit log."""
    old_status = proposal.status
    proposal.status = new_status
    proposal.gc_reason = reason
    proposal.gc_updated_at = _now()
    proposal.decided_by = "evolution_gc"
    proposal.decided_at = proposal.gc_updated_at

    write_audit_log(
        db,
        actor_type="system",
        actor_name="evolution_gc",
        action_type="evolution_gc_transition",
        target_type="evolution_proposal",
        target_id=str(proposal.id),
        before_state={"status": old_status},
        after_state={"status": new_status, "gc_reason": reason},
        status="completed",
        approval_mode="autonomous",
    )


# ---------------------------------------------------------------------------
# Detection rules
# ---------------------------------------------------------------------------

def _collapse_duplicates(db: Session, open_proposals: list[EvolutionProposal]) -> int:
    """
    Rule 1: If multiple open proposals share the same dedup_key,
    keep the newest one and mark older ones as obsolete.
    """
    by_key: dict[str, list[EvolutionProposal]] = {}
    for p in open_proposals:
        if p.dedup_key:
            by_key.setdefault(p.dedup_key, []).append(p)

    count = 0
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        # Sort by created_at descending — keep the newest
        group.sort(key=lambda x: x.created_at or datetime.min, reverse=True)
        for dup in group[1:]:
            _transition(db, dup, "obsolete",
                        f"Duplicate dedup_key '{key}' — superseded by proposal #{group[0].id}")
            count += 1
    return count


def _detect_merged_fix_coverage(db: Session, open_proposals: list[EvolutionProposal]) -> int:
    """
    Rule 2: If a merged bugfix (via promotion pipeline) touched the same
    target_file AFTER the proposal was created, mark as resolved_indirectly.

    Only applies when there is a concrete merged fix for the same file.
    """
    from app.models.bugfix_candidate import BugFixCandidate
    from app.models.autofix_promotion import AutoFixPromotion

    # Get all merged promotions with their bugfix candidate patch_files
    merged = (
        db.query(AutoFixPromotion, BugFixCandidate)
        .join(BugFixCandidate, AutoFixPromotion.bugfix_candidate_id == BugFixCandidate.id)
        .filter(AutoFixPromotion.merged_at.isnot(None))
        .all()
    )
    if not merged:
        return 0

    # Build a map: file_path -> latest merge datetime
    merged_files: dict[str, datetime] = {}
    for promo, candidate in merged:
        if not candidate.patch_files:
            continue
        try:
            import json
            files = json.loads(candidate.patch_files)
        except (ValueError, TypeError):
            continue
        for f in files:
            existing = merged_files.get(f)
            if existing is None or promo.merged_at > existing:
                merged_files[f] = promo.merged_at

    count = 0
    for p in open_proposals:
        if not p.target_file:
            continue
        # Strip line number suffix (e.g. "app/services/foo.py:42" → "app/services/foo.py")
        target = p.target_file.split(":")[0]
        merge_time = merged_files.get(target)
        if merge_time and p.created_at and merge_time > p.created_at:
            _transition(db, p, "resolved_indirectly",
                        f"Merged fix touched '{target}' at {merge_time.isoformat()}Z, after proposal creation")
            count += 1
    return count


def _detect_target_file_changes(db: Session, open_proposals: list[EvolutionProposal]) -> int:
    """
    Rule 3: If target_file has been modified (git log) after proposal creation
    but NOT covered by a merged bugfix (rule 2 handles that), mark as
    needs_revalidation.

    Uses git log to check file modification times. Safe and read-only.
    """
    backend_dir = _BACKEND_DIR
    count = 0

    for p in open_proposals:
        if not p.target_file or not p.created_at:
            continue
        # Already transitioned by an earlier rule in this cycle
        if p.status != "open":
            continue

        target = p.target_file.split(":")[0]
        target_path = backend_dir / target
        if not target_path.exists():
            # File was deleted — the proposal is definitely stale
            _transition(db, p, "needs_revalidation",
                        f"Target file '{target}' no longer exists")
            count += 1
            continue

        # Check if file has commits after proposal creation
        since = p.created_at.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", f"--since={since}", "--", target],
                capture_output=True, text=True, timeout=10,
                cwd=str(backend_dir),
            )
            if result.returncode == 0 and result.stdout.strip():
                commit_count = len(result.stdout.strip().splitlines())
                _transition(db, p, "needs_revalidation",
                            f"Target file '{target}' has {commit_count} commit(s) since proposal creation")
                count += 1
        except (subprocess.TimeoutExpired, OSError):
            pass  # skip — don't mark on infra failure

    return count


def _detect_accepted_siblings(db: Session, open_proposals: list[EvolutionProposal]) -> int:
    """
    Rule 4: If another proposal with the same dedup_key was already accepted
    (and possibly merged), mark remaining open siblings as obsolete.
    """
    # Find accepted/applied proposals that have dedup_keys
    accepted = (
        db.query(EvolutionProposal)
        .filter(
            EvolutionProposal.status.in_(["accepted", "applied"]),
            EvolutionProposal.dedup_key.isnot(None),
        )
        .all()
    )
    accepted_keys = {a.dedup_key for a in accepted}
    if not accepted_keys:
        return 0

    count = 0
    for p in open_proposals:
        if p.status != "open":
            continue
        if p.dedup_key and p.dedup_key in accepted_keys:
            _transition(db, p, "obsolete",
                        f"Another proposal with dedup_key '{p.dedup_key}' was already accepted")
            count += 1
    return count


def _detect_stale_proposals(db: Session, open_proposals: list[EvolutionProposal], max_age_days: int = 90) -> int:
    """
    Rule 5: Open proposals older than max_age_days with no action
    are marked needs_revalidation. Conservative — does not claim resolved.
    """
    cutoff = _now() - timedelta(days=max_age_days)
    count = 0
    for p in open_proposals:
        if p.status != "open":
            continue
        if p.created_at and p.created_at < cutoff:
            age_days = (_now() - p.created_at).days
            _transition(db, p, "needs_revalidation",
                        f"Proposal is {age_days} days old with no action — may be stale")
            count += 1
    return count


# ---------------------------------------------------------------------------
# Main GC runner
# ---------------------------------------------------------------------------

def run_evolution_gc(db: Session, max_age_days: int = 90) -> dict:
    """
    Run all GC detection rules against open proposals.
    Returns summary of transitions made.

    Rules are applied in order — once a proposal is transitioned by one rule,
    later rules skip it (checked via status != "open").
    """
    # Load all open proposals once
    open_proposals = (
        db.query(EvolutionProposal)
        .filter(EvolutionProposal.status.in_(GC_SOURCE_STATUSES))
        .all()
    )

    if not open_proposals:
        return {"scanned": 0, "obsolete": 0, "resolved_indirectly": 0, "needs_revalidation": 0}

    total = len(open_proposals)

    # Apply rules in priority order
    # Rule 1: Collapse duplicates (cheap, deterministic)
    obsolete_dupes = _collapse_duplicates(db, open_proposals)

    # Rule 4: Accepted siblings (cheap, deterministic — run before file checks)
    obsolete_siblings = _detect_accepted_siblings(db, open_proposals)

    # Rule 2: Merged fix coverage (DB query, no git)
    resolved = _detect_merged_fix_coverage(db, open_proposals)

    # Rule 3: Target file changes (git log — only for proposals not already transitioned)
    revalidate_files = _detect_target_file_changes(db, open_proposals)

    # Rule 5: Age staleness (only for proposals still open after all above)
    revalidate_age = _detect_stale_proposals(db, open_proposals, max_age_days)

    total_obsolete = obsolete_dupes + obsolete_siblings
    total_resolved = resolved
    total_revalidate = revalidate_files + revalidate_age

    if total_obsolete + total_resolved + total_revalidate > 0:
        db.flush()
        log.info(
            "evolution_gc: scanned=%d obsolete=%d resolved=%d revalidate=%d",
            total, total_obsolete, total_resolved, total_revalidate,
        )

    return {
        "scanned": total,
        "obsolete": total_obsolete,
        "resolved_indirectly": total_resolved,
        "needs_revalidation": total_revalidate,
    }
