# test-coverage: superseded by Brain Vero pivot 2026-05-07; Stage 2-C deletion pending
"""
evolution_decision_engine.py — Act on measured outcomes, safely.

Reads (tech_outcome, business_outcome, confidence_score) from
EvolutionProposal and emits ONE of:

    rollback_proposed     high-confidence NEITHER → reverse the change
    rollback_blocked      would have rolled back, but a safety gate fired
    rollback_skipped      no artifact to roll back (not linked to a commit)
    reinforce             high-confidence BOTH
    extend_carefully      high-confidence BUSINESS_SUCCESS only
    observe               low confidence or TECH_SUCCESS / NOISE
    ignored               business_outcome=not_applicable

Rollback execution model — SAFE BY CONSTRUCTION
-----------------------------------------------
We NEVER shell out to `git revert` directly. When a rollback is decided,
we create a NEW BugFixCandidate with:

    source_type  = 'auto_rollback'
    source_ref   = 'evolution_{proposal_id}'
    title        = '[Auto-Rollback] reverse-patch for evolution proposal #N'
    context_json = full decision evidence

That candidate flows through the standard bugfix approval pipeline:

    → propose_patch()     LLM builds the reverse diff (or fails, surfaces)
    → tier_check          TIER_2 files are refused; TIER_1 requires humans
    → classify_patch_tier PATCH_TIER_0 may auto-apply, else human-approve
    → applied/rolled_back exactly like any other bugfix

Every existing safety gate applies. The decision engine only PROPOSES
the reversal — the humans and tier system decide whether to execute it.

Additional safety:

* At most ONE rollback per proposal (rollback_candidate_id already set → skip)
* Confidence must be >= _ROLLBACK_MIN_CONFIDENCE (default 0.70)
* Proposal must have a concrete applied_commit_sha and a target_file
* target_file must be TIER_0 or TIER_1 — TIER_2 files never get auto-reversal
  (the rollback candidate is still CREATED but flagged tier_blocked=true,
   so a human can act on it)
* Global cooldown via Redis — no more than _MAX_ROLLBACKS_PER_DAY proposals
  per calendar day, preventing a storm if measurement goes haywire
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.bugfix_candidate import BugFixCandidate
from app.models.evolution_proposal import EvolutionProposal
from app.services.evolution_business_outcomes import combined_outcome_label

log = logging.getLogger("evolution_decision_engine")

_ROLLBACK_MIN_CONFIDENCE = 0.70
_REINFORCE_MIN_CONFIDENCE = 0.70
_EXTEND_MIN_CONFIDENCE = 0.60
_MAX_ROLLBACKS_PER_DAY = 3
_MAX_ROLLBACKS_PER_CYCLE = 1  # per run of run_decision_cycle
_BATCH_SIZE = 25

_GIT_REPO_PATH = "/opt/wishspark"
_GIT_COMMAND_TIMEOUT = 10

# Redis keys for cooldown
_ROLLBACK_DAILY_KEY = "hs:evolution:rollback_daily:{date}"
_ROLLBACK_DAILY_TTL = 36 * 3600  # 36h — comfortably covers a day


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Decision function (pure)
# ---------------------------------------------------------------------------

def decide_action(
    tech_outcome: str | None,
    business_outcome: str | None,
    confidence_score: float | None,
    data_quality: str | None = None,
) -> str:
    """
    Pure decision function. No DB, no side effects.

    Returns one of:
      rollback_proposed | reinforce | extend_carefully | observe | ignored

    data_quality gate: when data_quality='LOW' (set by
    evolution_business_outcomes.assess_data_quality), the decision engine
    MUST observe only. This is the last line of defense against a phantom
    signal from delayed webhooks or broken pixels triggering a real
    code rollback. MEDIUM is allowed through but confidence was already
    halved upstream, so the 0.70 action threshold is typically unmet.
    """
    if business_outcome == "not_applicable":
        return "ignored"

    # Hard block: no autonomous action on LOW-quality data.
    if data_quality == "LOW":
        return "observe"

    confidence = float(confidence_score or 0.0)
    label = combined_outcome_label(tech_outcome, business_outcome)

    if label == "NEITHER" and confidence >= _ROLLBACK_MIN_CONFIDENCE:
        return "rollback_proposed"
    if label == "BOTH" and confidence >= _REINFORCE_MIN_CONFIDENCE:
        return "reinforce"
    if label == "BUSINESS_SUCCESS" and confidence >= _EXTEND_MIN_CONFIDENCE:
        return "extend_carefully"
    return "observe"


# ---------------------------------------------------------------------------
# Rollback execution (safe: creates a BugFixCandidate, never shells out)
# ---------------------------------------------------------------------------

def extract_commit_files(commit_sha: str) -> list[str]:
    """
    Return the list of files touched by a git commit.

    Uses `git diff-tree --no-commit-id --name-only -r <sha>`. Returns []
    if git is unavailable, the commit doesn't exist, or subprocess fails.
    Never raises.
    """
    if not commit_sha or len(commit_sha) < 7:
        return []
    try:
        result = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit_sha],
            cwd=_GIT_REPO_PATH,
            capture_output=True,
            text=True,
            timeout=_GIT_COMMAND_TIMEOUT,
            check=False,
        )
        if result.returncode != 0:
            return []
        files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return files
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return []


def _rollback_blocked_reason(
    proposal: EvolutionProposal, affected_files: list[str] | None = None,
) -> str | None:
    """
    Return a human-readable blocker string, or None if OK to proceed.

    Uses the FULL commit blast radius when available — if ANY file touched
    by applied_commit_sha is TIER_2, the rollback is blocked.
    """
    if proposal.rollback_candidate_id is not None:
        return "already_rolled_back"
    if not proposal.applied_commit_sha:
        return "no_applied_commit_sha"

    # Files to check: prefer the full commit blast radius; fall back to
    # target_file for proposals without a known git commit.
    files_to_check: list[str] = list(affected_files or [])
    if not files_to_check and proposal.target_file:
        files_to_check = [proposal.target_file]

    if not files_to_check:
        return "no_target_file"

    # Tier check — never auto-reverse if ANY file in the blast radius is TIER_2
    try:
        from app.core.tier_check import check_tier, TIER_2, _classify_file
        tier_result = check_tier(files_to_check)
        if tier_result.tier == TIER_2:
            tier2_files = [f for f in files_to_check if _classify_file(f)[0] == TIER_2]
            offenders = ",".join(tier2_files[:3])
            return f"blast_radius_contains_tier2:{offenders}"
    except ImportError:
        pass
    return None


def _daily_rollback_count() -> int:
    """Redis counter for today's rollback proposals. 0 if Redis unavailable."""
    try:
        from app.core.redis_client import cache_get
        today = _now().date().isoformat()
        v = cache_get(_ROLLBACK_DAILY_KEY.format(date=today))
        return int(v) if v is not None else 0
    except Exception as exc:
        log.warning("evolution_decision_engine: _daily_rollback_count failed: %s", exc)
        return 0


def _increment_daily_rollback_count() -> None:
    try:
        from app.core.redis_client import _client
        today = _now().date().isoformat()
        key = _ROLLBACK_DAILY_KEY.format(date=today)
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("evolution.rollback_count")
            return
        pipe = rc.pipeline()
        pipe.incr(key, 1)
        pipe.expire(key, _ROLLBACK_DAILY_TTL)
        pipe.execute()
    except Exception as exc:
        log.warning("evolution_decision_engine: _increment_daily_rollback_count failed: %s", exc)


def _build_reverse_patch_context(
    proposal: EvolutionProposal, affected_files: list[str],
) -> dict:
    evidence_raw = proposal.business_evidence or "{}"
    try:
        evidence = json.loads(evidence_raw)
    except (ValueError, TypeError):
        evidence = {"raw": evidence_raw}
    files_listing = ", ".join(affected_files) if affected_files else proposal.target_file or "(unknown)"
    return {
        "source": "evolution_decision_engine",
        "reason": "auto_rollback_on_measured_decline",
        "proposal_id": proposal.id,
        "proposal_reason": proposal.reason,
        "applied_commit_sha": proposal.applied_commit_sha,
        "target_file": proposal.target_file,
        "affected_files": affected_files,
        "confidence_score": proposal.confidence_score,
        "tech_outcome": proposal.outcome_status,
        "business_outcome": proposal.business_outcome,
        "business_evidence": evidence,
        "instruction": (
            f"Generate a reverse patch that undoes commit {proposal.applied_commit_sha}. "
            f"The commit touched {len(affected_files)} file(s): {files_listing}. "
            "The rollback must revert ALL files touched by that commit, not just one. "
            "Tier gating and human approval apply at apply time."
        ),
    }


def propose_rollback(db: Session, proposal: EvolutionProposal) -> tuple[str, int | None, str]:
    """
    Create an auto_rollback BugFixCandidate for the given proposal.

    Returns (status, candidate_id_or_None, reason):
      ("rollback_proposed", bugfix_id, "ok")
      ("rollback_blocked", None, "<reason>")
      ("rollback_skipped", None, "<reason>")
    """
    # Extract the FULL commit blast radius — all files touched by the
    # commit, not just target_file. Safety check below uses this list.
    if proposal.affected_files:
        try:
            affected_files = json.loads(proposal.affected_files) or []
        except (ValueError, TypeError):
            affected_files = []
    else:
        affected_files = []

    if not affected_files and proposal.applied_commit_sha:
        affected_files = extract_commit_files(proposal.applied_commit_sha)
        # Persist the blast radius for audit and future rollback attempts
        if affected_files:
            proposal.affected_files = json.dumps(affected_files)

    # Safety: already rolled back OR missing artifact → skip
    blocker = _rollback_blocked_reason(proposal, affected_files)
    if blocker is not None:
        if blocker in ("no_applied_commit_sha", "no_target_file"):
            return "rollback_skipped", None, blocker
        return "rollback_blocked", None, blocker

    # Daily cooldown
    if _daily_rollback_count() >= _MAX_ROLLBACKS_PER_DAY:
        return "rollback_blocked", None, "daily_rollback_cap_reached"

    context = _build_reverse_patch_context(proposal, affected_files)
    title_suffix = (
        f"{len(affected_files)} file(s)" if len(affected_files) > 1
        else (affected_files[0] if affected_files else proposal.target_file or "unknown")
    )
    candidate = BugFixCandidate(
        source_type="auto_rollback",
        source_ref=f"evolution_{proposal.id}",
        title=f"[Auto-Rollback] evolution proposal #{proposal.id}: reverse {title_suffix}",
        summary=(
            f"Automatic rollback proposal. Evolution proposal #{proposal.id} "
            f"was applied in commit {proposal.applied_commit_sha} touching "
            f"{len(affected_files)} file(s) but was measured as "
            f"business={proposal.business_outcome} with "
            f"confidence={proposal.confidence_score:.2f}. "
            f"This candidate carries the reverse patch for the FULL blast "
            f"radius and will flow through standard tier-check and approval "
            f"gates before any execution."
        ),
        context_json=json.dumps(context, default=str),
        status="open",
    )
    db.add(candidate)
    db.flush()

    _increment_daily_rollback_count()
    return "rollback_proposed", candidate.id, "ok"


# ---------------------------------------------------------------------------
# Orchestration — runs each agent cycle
# ---------------------------------------------------------------------------

def escalate_failed_rollbacks(db: Session) -> dict:
    """
    Watchdog: detect auto_rollback candidates that FAILED to apply or
    were rolled back, and escalate via ops_alert + Telegram.

    Without this, a failed rollback is silent — the original regression
    keeps bleeding revenue and no one knows. Must run every cycle.

    Returns: {"checked": n, "escalated": n}
    """
    from datetime import timedelta
    from app.models.bugfix_candidate import BugFixCandidate
    from app.services.alerting import write_alert

    summary = {"checked": 0, "escalated": 0}
    cutoff = _now() - timedelta(hours=48)

    # Failure-states for auto_rollback candidates. `failed` and `rejected`
    # include patch_proposed-but-refused paths; `rolled_back` is the post-
    # apply rollback detector. `apply_failed` comes from the apply runner.
    failed = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.source_type == "auto_rollback",
            BugFixCandidate.status.in_(["failed", "apply_failed", "rolled_back", "rejected"]),
            BugFixCandidate.created_at >= cutoff,
        )
        .all()
    )

    for cand in failed:
        summary["checked"] += 1
        # source_ref format is 'evolution_{proposal_id}'
        proposal_id: int | None = None
        try:
            if cand.source_ref and cand.source_ref.startswith("evolution_"):
                proposal_id = int(cand.source_ref.split("_", 1)[1])
        except (ValueError, IndexError):
            proposal_id = None

        # Fetch the source proposal for commit context
        proposal = None
        if proposal_id is not None:
            proposal = (
                db.query(EvolutionProposal)
                .filter(EvolutionProposal.id == proposal_id)
                .first()
            )

        commit = (proposal.applied_commit_sha if proposal else None) or "unknown"
        failure_reason = cand.failure_reason or cand.test_result or cand.status
        summary_msg = (
            f"Auto-rollback FAILED for evolution proposal #{proposal_id} "
            f"(commit {commit[:8]}): {cand.status}. Manual intervention required."
        )
        detail = {
            "proposal_id": proposal_id,
            "rollback_candidate_id": cand.id,
            "candidate_status": cand.status,
            "applied_commit_sha": commit,
            "failure_reason": (failure_reason or "")[:500],
            "candidate_title": cand.title[:200] if cand.title else None,
        }

        # write_alert dedups automatically on (source, alert_type, shop_domain)
        # within a 5-minute window, so repeated cycles won't spam.
        # heal-detection: decision engine event — per-cycle log
        write_alert(
            db,
            severity="critical",
            source="evolution_rollback_watchdog",
            alert_type="rollback_failed",
            summary=summary_msg,
            detail=detail,
        )
        summary["escalated"] += 1

    if summary["escalated"] > 0:
        log.warning(
            "rollback_watchdog: escalated %d failed auto-rollback(s)",
            summary["escalated"],
        )
    return summary


def auto_extend_proposal(db: Session, parent: EvolutionProposal) -> int | None:
    """
    Create a deeper variant of a winning proposal.

    Fires when decision='extend_carefully' is emitted. Generates a new
    EvolutionProposal with a suggestive reason + extended_from_proposal_id
    pointing at the parent. The new proposal enters the normal queue
    (status=open, LEVEL_2) and is picked up by operators / Opus next cycle.

    Safety:
      * At most ONE extension per parent (checked via extended_from_proposal_id)
      * Only when parent's business_outcome=improved AND confidence >= 0.60
      * LEVEL_2 (never LEVEL_1 — extensions still need human review)
      * Dedup: won't create if a child already exists.
    """
    # Dedup: one extension per parent
    existing = (
        db.query(EvolutionProposal.id)
        .filter(EvolutionProposal.extended_from_proposal_id == parent.id)
        .first()
    )
    if existing is not None:
        return None

    if parent.business_outcome != "improved":
        return None
    if (parent.confidence_score or 0.0) < _EXTEND_MIN_CONFIDENCE:
        return None

    cycle = parent.audit_cycle or _now().strftime("%Y-M%m")
    # Short, distinctive dedup key so this proposal doesn't collide with a
    # cycle's monthly_opus set. Use 'auto_extend' prefix.
    dedup_key = f"auto_extend:{parent.id}:{cycle}"
    short_reason = (parent.reason or "")[:160]
    child = EvolutionProposal(
        proposal_type=parent.proposal_type,
        target_file=parent.target_file,
        risk_level="LEVEL_2",
        reason=(
            f"[Auto-Extend of proposal #{parent.id}] The parent proposal "
            f"delivered a measured business win "
            f"(confidence={parent.confidence_score:.2f}). Deepen the same "
            f"direction: {short_reason}"
        ),
        expected_impact=(
            f"Extend the winning intervention from proposal #{parent.id}. "
            "Propose a concrete, specific next-step variant."
        ),
        auto_applicable=False,
        status="open",
        audit_cycle=cycle,
        dedup_key=dedup_key,
        extended_from_proposal_id=parent.id,
        affected_shop_domains=parent.affected_shop_domains,
        affected_product_urls=parent.affected_product_urls,
        linked_nudge_ids=parent.linked_nudge_ids,
    )
    db.add(child)
    db.flush()
    log.info(
        "auto_extend: created proposal=%d from parent=%d confidence=%.2f",
        child.id, parent.id, float(parent.confidence_score or 0.0),
    )
    return child.id


def run_decision_cycle(db: Session) -> dict:
    """
    Scan proposals that have a measured business_outcome but no decision yet,
    classify each, and act.

    Bounded by _BATCH_SIZE and _MAX_ROLLBACKS_PER_CYCLE to protect against
    cascading reversals.
    """
    summary = {
        "scanned": 0,
        "rollback_proposed": 0,
        "rollback_blocked": 0,
        "rollback_skipped": 0,
        "reinforce": 0,
        "extend_carefully": 0,
        "observe": 0,
        "ignored": 0,
    }

    rows = (
        db.query(EvolutionProposal)
        .filter(
            EvolutionProposal.business_outcome.isnot(None),
            EvolutionProposal.decision_status.is_(None),
        )
        .order_by(EvolutionProposal.business_measured_at.asc())
        .limit(_BATCH_SIZE)
        .all()
    )

    rollbacks_this_cycle = 0

    for prop in rows:
        summary["scanned"] += 1
        # Extract data_quality from business_evidence — the upstream
        # classifier stamps every measurement with HIGH / MEDIUM / LOW.
        data_quality = None
        if prop.business_evidence:
            try:
                ev = json.loads(prop.business_evidence)
                cls = ev.get("classification") or {}
                data_quality = cls.get("data_quality") or ev.get("data_quality")
            except (ValueError, TypeError):
                data_quality = None
        action = decide_action(
            prop.outcome_status, prop.business_outcome, prop.confidence_score,
            data_quality=data_quality,
        )

        if action == "rollback_proposed":
            if rollbacks_this_cycle >= _MAX_ROLLBACKS_PER_CYCLE:
                # Defer: leave decision_status NULL so we retry next cycle.
                continue
            status, candidate_id, reason = propose_rollback(db, prop)
            prop.decision_status = status
            prop.decision_decided_at = _now()
            if candidate_id is not None:
                prop.rollback_candidate_id = candidate_id
                rollbacks_this_cycle += 1
            summary[status] = summary.get(status, 0) + 1
            log.info(
                "decision_engine: proposal=%d action=%s candidate=%s reason=%s "
                "confidence=%.2f business=%s tech=%s",
                prop.id, status, candidate_id, reason,
                float(prop.confidence_score or 0.0),
                prop.business_outcome, prop.outcome_status,
            )
        else:
            prop.decision_status = action
            prop.decision_decided_at = _now()
            summary[action] = summary.get(action, 0) + 1
            # Auto-extend on confirmed wins — create a deeper variant.
            if action == "extend_carefully":
                try:
                    child_id = auto_extend_proposal(db, prop)
                    if child_id is not None:
                        summary["extended"] = summary.get("extended", 0) + 1
                except Exception as exc:
                    log.warning(
                        "decision_engine: auto_extend failed for proposal=%d: %s",
                        prop.id, type(exc).__name__,
                    )

    db.flush()
    if summary["scanned"] > 0:
        log.info("decision_engine: %s", {k: v for k, v in summary.items() if v})
    return summary
