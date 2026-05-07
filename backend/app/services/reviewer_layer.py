# test-coverage: superseded by Brain Vero pivot 2026-05-07; Stage 2-C deletion pending
"""
reviewer_layer.py — Structured review engine for proposed changes.

Accepts a proposed change (bugfix, evolution proposal, action approval,
model upgrade, scaling recommendation) and produces a structured assessment
using the project brain's knowledge, domain criticality, and strategic
constitution.

PRIMARY MODE: deterministic (no LLM, no token cost).
    Domain classification → criticality check → constitution rules →
    history check → blast radius → verdict. This is the only mode
    currently implemented; the `mode` parameter accepts "llm_assisted"
    for forward-compat but routes through the same deterministic path.
    A future enhancement may add LLM nuance on top, gated via
    `app.core.llm_budget.check_budget`. Until then, any caller that
    passes `mode="llm_assisted"` gets the deterministic result with
    the mode string stored on the assessment row for reporting.

Public interface:
    review_entity(db, entity_type, entity_id, mode="deterministic") -> ReviewerAssessment
    format_for_operator(assessment) -> str  (Telegram/operator-ready summary)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.reviewer_assessment import ReviewerAssessment
from app.services.project_brain import (
    classify_file,
    get_latest_snapshot,
    get_constitution,
    SENSITIVE_DOMAINS,
    _DOMAIN_CRITICALITY,
)

log = logging.getLogger("reviewer_layer")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Entity loaders — fetch the entity being reviewed
# ---------------------------------------------------------------------------

def _load_entity(db: Session, entity_type: str, entity_id: int) -> dict | None:
    """Load an entity and return a normalized dict for review."""
    if entity_type == "bugfix_candidate":
        return _load_bugfix(db, entity_id)
    elif entity_type == "evolution_proposal":
        return _load_evolution(db, entity_id)
    elif entity_type == "action_approval":
        return _load_approval(db, entity_id)
    elif entity_type == "model_upgrade":
        return _load_model_upgrade(db, entity_id)
    elif entity_type == "scaling_recommendation":
        return _load_scaling(db, entity_id)
    return None


def _load_bugfix(db: Session, entity_id: int) -> dict | None:
    from app.models.bugfix_candidate import BugFixCandidate
    c = db.get(BugFixCandidate, entity_id)
    if not c:
        return None
    files = []
    if c.patch_files:
        try:
            files = json.loads(c.patch_files)
        except (ValueError, TypeError):
            pass
    return {
        "type": "bugfix_candidate",
        "id": c.id,
        "title": c.title,
        "summary": c.summary,
        "status": c.status,
        "source_type": c.source_type,
        "risk_tier": c.patch_risk_tier,
        "files": files,
        "patch_diff": c.patch_diff,
        "patch_lines": len((c.patch_diff or "").splitlines()),
    }


def _load_evolution(db: Session, entity_id: int) -> dict | None:
    from app.models.evolution_proposal import EvolutionProposal
    p = db.get(EvolutionProposal, entity_id)
    if not p:
        return None
    target = p.target_file.split(":")[0] if p.target_file else None
    return {
        "type": "evolution_proposal",
        "id": p.id,
        "proposal_type": p.proposal_type,
        "target_file": target,
        "risk_level": p.risk_level,
        "reason": p.reason,
        "expected_impact": p.expected_impact,
        "auto_applicable": p.auto_applicable,
        "status": p.status,
        "files": [target] if target else [],
    }


def _load_approval(db: Session, entity_id: int) -> dict | None:
    from app.models.action_approval import ActionApproval
    from app.models.audit_log import AuditLog
    a = db.get(ActionApproval, entity_id)
    if not a:
        return None
    # Get the action details from the linked audit_log
    action_type = None
    target = None
    if a.audit_log_id:
        al = db.get(AuditLog, a.audit_log_id)
        if al:
            action_type = al.action_type
            target = al.target_id
    return {
        "type": "action_approval",
        "id": a.id,
        "action_type": action_type,
        "target": target,
        "status": a.status,
        "files": [],
    }


def _load_model_upgrade(db: Session, entity_id: int) -> dict | None:
    from app.models.model_upgrade import ModelUpgradeProposal
    m = db.get(ModelUpgradeProposal, entity_id)
    if not m:
        return None
    return {
        "type": "model_upgrade",
        "id": m.id,
        "target_module": m.target_module,
        "current_provider": m.current_provider,
        "current_model": m.current_model,
        "candidate_provider": m.candidate_provider,
        "candidate_model": m.candidate_model,
        "risk_level": m.risk_level,
        "eval_result": m.eval_result,
        "status": m.status,
        "files": [],
    }


def _load_scaling(db: Session, entity_id: int) -> dict | None:
    from app.models.scaling_recommendation import ScalingRecommendation
    s = db.get(ScalingRecommendation, entity_id)
    if not s:
        return None
    return {
        "type": "scaling_recommendation",
        "id": s.id,
        "resource_type": s.resource_type,
        "title": s.title,
        "severity": s.severity,
        "confidence": s.confidence,
        "estimated_cost_increase_eur": float(s.estimated_cost_increase_eur) if s.estimated_cost_increase_eur is not None else None,
        "files": [],
    }


# ---------------------------------------------------------------------------
# Deterministic Review Logic
# ---------------------------------------------------------------------------

def _classify_affected_domains(entity: dict) -> list[str]:
    """Determine which domains a change touches."""
    domains = set()
    for f in entity.get("files", []):
        if f:
            classification = classify_file(f)
            domains.add(classification["domain"])

    # Special domain inference for non-file entities
    if entity["type"] == "model_upgrade":
        domains.add("model_governance")
        module = entity.get("target_module", "")
        if "orchestrator" in module:
            domains.add("orchestrator")
        if "bugfix" in module:
            domains.add("autofix")
    elif entity["type"] == "action_approval":
        action = entity.get("action_type", "")
        if "restart" in action:
            domains.add("workers")
        if "cache" in action:
            domains.add("infra")
        if "migration" in action:
            domains.add("migrations")
    elif entity["type"] == "scaling_recommendation":
        domains.add("infra")

    return sorted(domains) if domains else ["other"]


def _compute_risk_level(entity: dict, domains: list[str]) -> str:
    """Compute risk based on domain criticality and entity properties."""
    # Highest criticality among affected domains
    max_crit = "low"
    crit_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    for d in domains:
        c = _DOMAIN_CRITICALITY.get(d, "low")
        if crit_order.get(c, 0) > crit_order.get(max_crit, 0):
            max_crit = c

    # Entity-specific risk amplifiers
    if entity["type"] == "bugfix_candidate":
        tier = entity.get("risk_tier")
        if tier == 2:
            return "critical"
        if tier == 1 and max_crit in ("high", "critical"):
            return "critical"
        patch_lines = entity.get("patch_lines", 0)
        if patch_lines > 200:
            return "high" if max_crit == "low" else max_crit

    if entity["type"] == "evolution_proposal":
        if entity.get("risk_level") == "LEVEL_3":
            return "high" if max_crit == "low" else max_crit

    return max_crit


def _check_constitution(entity: dict, domains: list[str], risk: str) -> list[str]:
    """Check entity against constitution principles. Return relevant concerns."""
    constitution = get_constitution()
    concerns = []

    touches_sensitive = any(d in SENSITIVE_DOMAINS for d in domains)

    for p in constitution["principles"]:
        pid = p["id"]

        if pid == "protect_core" and touches_sensitive:
            if entity["type"] == "bugfix_candidate" and entity.get("risk_tier") == 0:
                concerns.append(f"TIER_0 patch touches sensitive domain ({', '.join(d for d in domains if d in SENSITIVE_DOMAINS)}) — violates 'protect_core'")
            if entity["type"] == "evolution_proposal" and entity.get("auto_applicable"):
                concerns.append(f"Auto-applicable proposal targets sensitive domain — violates 'protect_core'")

        if pid == "tier0_safety" and entity["type"] == "bugfix_candidate":
            tier = entity.get("risk_tier")
            if tier == 0 and risk in ("high", "critical"):
                concerns.append(f"TIER_0 with {risk} risk level — violates 'tier0_safety'")

        if pid == "no_regressions" and touches_sensitive:
            if entity["type"] == "bugfix_candidate" and not entity.get("patch_diff"):
                concerns.append("No patch diff available for review on sensitive domain — violates 'no_regressions'")

        if pid == "no_overengineering" and entity["type"] == "evolution_proposal":
            if entity.get("risk_level") == "LEVEL_3" and entity.get("proposal_type") == "refactor":
                concerns.append("LEVEL_3 refactor — review for overengineering risk")

    return concerns


def _compute_alignment(entity: dict, domains: list[str], concerns: list[str]) -> str:
    """Compute strategic alignment score."""
    if len(concerns) >= 3:
        return "weak"
    if len(concerns) >= 1:
        return "medium"

    # Positive signals
    if entity["type"] == "bugfix_candidate" and entity.get("source_type") == "evolution":
        return "strong"  # evolution-driven fixes align with self-management
    if entity["type"] == "evolution_proposal" and entity.get("proposal_type") == "reliability":
        return "strong"

    return "strong" if not concerns else "medium"


def _compute_verdict(
    entity: dict, domains: list[str], risk: str, alignment: str,
    concerns: list[str], blocking: list[str],
) -> str:
    """Compute the final verdict."""
    if blocking:
        return "reject"

    if risk == "critical" and any(d in SENSITIVE_DOMAINS for d in domains):
        return "reject"

    if concerns:
        if risk in ("high", "critical"):
            return "refine"
        return "approve_with_notes"

    if risk == "low" and alignment == "strong":
        return "approve"

    if risk == "medium":
        return "approve_with_notes"

    if risk in ("high", "critical"):
        return "refine"

    return "approve"


def _compute_confidence(entity: dict, snapshot) -> str:
    """How confident the reviewer is in its assessment."""
    if not snapshot:
        return "low"  # no brain data
    if snapshot.created_at:
        age_hours = (_now() - snapshot.created_at).total_seconds() / 3600
        if age_hours > 72:
            return "low"  # stale brain
    # No patch to review for bugfixes
    if entity["type"] == "bugfix_candidate" and not entity.get("patch_diff"):
        return "low"
    return "high"


def _is_auto_approvable(entity: dict, domains: list[str], risk: str, verdict: str, concerns: list[str]) -> bool:
    """Determine if this can be auto-approved without human gating."""
    if verdict in ("reject", "refine"):
        return False
    if any(d in SENSITIVE_DOMAINS for d in domains):
        return False
    if risk in ("high", "critical"):
        return False
    if concerns:
        return False

    # Entity-specific
    if entity["type"] == "bugfix_candidate":
        if entity.get("risk_tier") != 0 or risk != "low":
            return False
        # Enforce via tier_check: verify files are actually TIER_0
        files = entity.get("files", [])
        if files:
            try:
                from app.core.tier_check import check_tier, TIER_0
                tier_result = check_tier(files)
                if tier_result.tier != TIER_0:
                    return False
            except ImportError:
                pass
        return True
    if entity["type"] == "evolution_proposal":
        return entity.get("risk_level") == "LEVEL_1" and entity.get("auto_applicable", False)

    return False


def _build_summary(entity: dict, verdict: str, risk: str, domains: list[str]) -> str:
    """Build a human-readable summary."""
    entity_desc = {
        "bugfix_candidate": f"Bugfix #{entity['id']}: {entity.get('title', 'untitled')[:100]}",
        "evolution_proposal": f"Evolution #{entity['id']}: {entity.get('reason', '')[:100]}",
        "action_approval": f"Action approval #{entity['id']}: {entity.get('action_type', 'unknown')}",
        "model_upgrade": f"Model upgrade #{entity['id']}: {entity.get('candidate_model', '?')} for {entity.get('target_module', '?')}",
        "scaling_recommendation": f"Scaling #{entity['id']}: {entity.get('title', '')[:100]}",
    }
    desc = entity_desc.get(entity["type"], f"{entity['type']} #{entity['id']}")
    domain_str = ", ".join(domains)
    return f"{verdict.upper()} [{risk}] — {desc}. Domains: {domain_str}."


# ---------------------------------------------------------------------------
# Lesson-aware review — institutional memory integration
# ---------------------------------------------------------------------------

def _lookup_domain_lessons(db: Session, domains: list[str]) -> dict:
    """
    Look up active lessons for the affected domains.
    Returns structured summary for use in risk scoring, notes, and auto-approval.

    Returns:
        {
            "negative_count": int,      # ineffective_pattern lessons
            "positive_count": int,      # effective_pattern lessons
            "high_confidence_negatives": list[str],  # summaries of strong warnings
            "domain_risk_boost": str | None,  # "high" if domain is lesson-flagged
            "auto_approve_blocked": bool,  # True if strong negative lessons exist
            "notes": list[str],
        }
    """
    result = {
        "negative_count": 0,
        "positive_count": 0,
        "high_confidence_negatives": [],
        "domain_risk_boost": None,
        "auto_approve_blocked": False,
        "notes": [],
    }

    try:
        from app.models.system_lesson import SystemLesson

        lessons = (
            db.query(SystemLesson)
            .filter(
                SystemLesson.status == "active",
                SystemLesson.domain.in_(domains),
                SystemLesson.confidence >= 0.3,
            )
            .order_by(SystemLesson.confidence.desc())
            .limit(10)
            .all()
        )

        if not lessons:
            return result

        for l in lessons:
            if l.lesson_type in ("ineffective_pattern", "regression_warning"):
                result["negative_count"] += 1
                if l.confidence >= 0.7 and l.evidence_count >= 2:
                    result["high_confidence_negatives"].append(l.summary[:150])
                # Only CONFIRMED regression warnings are hard blockers
                if l.lesson_type == "regression_warning" and getattr(l, "promotion_status", None) in ("promoted", None):
                    result["auto_approve_blocked"] = True
                    result["notes"].append(
                        f"REGRESSION WARNING (confirmed lesson #{l.id}): {l.summary[:120]}"
                    )
                # Pending promotions are advisory — notes but not hard blocks
                elif getattr(l, "promotion_status", None) == "pending_promotion":
                    result["notes"].append(
                        f"PENDING REVIEW: lesson #{l.id} may become a regression warning: {l.summary[:100]}"
                    )
            elif l.lesson_type == "effective_pattern":
                result["positive_count"] += 1

        # Risk boost: if domain has 3+ negative lessons or 2+ high-confidence negatives
        if result["negative_count"] >= 3 or len(result["high_confidence_negatives"]) >= 2:
            result["domain_risk_boost"] = "high"
            result["notes"].append(
                f"LESSON WARNING: domain has {result['negative_count']} ineffective fix pattern(s) "
                f"({len(result['high_confidence_negatives'])} high-confidence)"
            )

        # Block auto-approve if high-confidence negatives exist for this domain
        if result["high_confidence_negatives"]:
            result["auto_approve_blocked"] = True
            result["notes"].append("Auto-approve blocked: domain has high-confidence negative lessons")

        # Positive reinforcement note (informational only — never relaxes scrutiny)
        if result["positive_count"] >= 2 and result["negative_count"] == 0:
            result["notes"].append(
                f"Lesson context: domain has {result['positive_count']} effective pattern(s) on record"
            )

    except Exception as exc:
        log.warning("reviewer_lessons: lookup failed (non-fatal): %s", exc)

    return result


# ---------------------------------------------------------------------------
# Main Review Function
# ---------------------------------------------------------------------------

def review_entity(
    db: Session,
    entity_type: str,
    entity_id: int,
    mode: str = "deterministic",
) -> ReviewerAssessment | None:
    """
    Review an entity and produce a structured assessment.

    Args:
        db: Database session
        entity_type: bugfix_candidate | evolution_proposal | action_approval | model_upgrade | scaling_recommendation
        entity_id: Primary key of the entity
        mode: "deterministic" (default). "llm_assisted" is accepted for
              forward-compat but currently routes through the same
              deterministic path — the mode string is stored on the
              ReviewerAssessment row but no LLM is invoked yet.

    Returns:
        ReviewerAssessment row (persisted to DB), or None if entity not found.
    """
    entity = _load_entity(db, entity_type, entity_id)
    if not entity:
        return None

    snapshot = get_latest_snapshot(db)

    # Step 1: Classify affected domains
    domains = _classify_affected_domains(entity)

    # Step 1b: Lookup domain lessons (institutional memory)
    lesson_context = _lookup_domain_lessons(db, domains)

    # Step 2: Compute risk (lesson-aware)
    risk = _compute_risk_level(entity, domains)
    # Escalate risk if domain has strong negative lesson history
    if lesson_context["domain_risk_boost"] and risk == "low":
        risk = "medium"
    elif lesson_context["domain_risk_boost"] and risk == "medium":
        risk = "high"

    # Step 3: Check constitution
    concerns = _check_constitution(entity, domains, risk)
    # Add lesson-based concerns
    for note in lesson_context["notes"]:
        if "WARNING" in note:
            concerns.append(note)

    # Step 4: Compute blocking concerns (hard blockers)
    # 2026-04-11 elite sprint: added deterministic blockers that exercise
    # the reviewer's gate. Audit showed 50 approve / 0 reject in 90d — the
    # old blocking list was too narrow (only TIER_2 + terminal status).
    # These new blockers make the reviewer actually reject risky candidates.
    blocking = []
    if entity["type"] == "bugfix_candidate":
        if entity.get("risk_tier") == 2:
            blocking.append("TIER_2 patch — never auto-approvable by policy")
        if entity.get("status") in ("rejected", "apply_failed", "rolled_back"):
            blocking.append(f"Entity is in terminal status: {entity['status']}")

        # Elite block 1: large-diff protection — diffs over 200 changed
        # lines have a much higher regression risk and deserve eyes.
        patch_diff = entity.get("patch_diff") or ""
        if patch_diff:
            changed_lines = sum(
                1 for line in patch_diff.split("\n")
                if line.startswith("+") or line.startswith("-")
            )
            if changed_lines > 200:
                blocking.append(
                    f"Diff too large for auto-approval: {changed_lines} changed lines (>200)"
                )

        # Elite block 2: self-modification guard — the reviewer joins the
        # bugfix_pipeline.touches_self_healing_pipeline check. Defense in
        # depth: if classify_patch_risk missed it or a future refactor
        # weakens that check, the reviewer still catches it.
        #
        # FAIL-CLOSED: if the guard itself fails to run (import error,
        # refactor broke it, etc.), treat as a blocking safety concern.
        # A self-healing-pipeline check that silently skipped would let a
        # patch bypass the protection. 2026-04-23 reviewer_layer audit.
        try:
            from app.services.bugfix_pipeline import touches_self_healing_pipeline
            files_list = entity.get("files") or []
            if files_list:
                self_mod, self_files = touches_self_healing_pipeline(json.dumps(files_list))
                if self_mod:
                    blocking.append(
                        "Patch touches self-healing pipeline files — human review required"
                    )
        except Exception as exc:
            log.warning(
                "reviewer_layer: self-mod guard failed — forcing block: %s",
                exc,
            )
            blocking.append(
                "reviewer safety check failed (self-modification guard) — "
                "cannot verify patch does not touch pipeline; manual review required"
            )

        # Elite block 3: prior-failure fingerprint match — if 3+ prior
        # candidates in the same (domain, source_type) have apply_failed
        # or rolled_back, this one is likely to fail too. Downgrade to
        # human review instead of burning budget.
        #
        # FAIL-CLOSED: if the fingerprint query itself errors, we can't
        # prove the patch is NOT in a failing pattern, so we must treat
        # it as blocked rather than silently skip. 2026-04-23 audit.
        try:
            from app.models.patch_fingerprint import PatchFingerprint
            from datetime import timedelta
            cutoff = _now() - timedelta(days=90)
            source_type = entity.get("source_type") or ""
            candidate_domain = domains[0] if domains else None
            if candidate_domain and source_type:
                prior_failures = (
                    db.query(PatchFingerprint)
                    .filter(
                        PatchFingerprint.affected_domain == candidate_domain,
                        PatchFingerprint.outcome.in_(["apply_failed", "rolled_back"]),
                        PatchFingerprint.created_at >= cutoff,
                    )
                    .count()
                )
                if prior_failures >= 3:
                    blocking.append(
                        f"Prior failure pattern: {prior_failures} candidates in "
                        f"domain '{candidate_domain}' have failed in 90d — "
                        "new approach needed, not auto-approvable"
                    )
        except Exception as exc:
            log.warning(
                "reviewer_layer: prior-failure check failed — forcing block: %s",
                exc,
            )
            blocking.append(
                "reviewer safety check failed (prior-failure fingerprint) — "
                "cannot verify absence of recurring failure pattern; manual review required"
            )

    if entity["type"] == "model_upgrade":
        if entity.get("eval_result") == "fail":
            blocking.append("Model evaluation FAILED — do not activate")

    # Step 5: Alignment
    alignment = _compute_alignment(entity, domains, concerns)

    # Step 6: Verdict
    verdict = _compute_verdict(entity, domains, risk, alignment, concerns, blocking)

    # Step 7: Confidence
    confidence = _compute_confidence(entity, snapshot)

    # Step 8: Auto-approvable (lesson-aware)
    auto_ok = _is_auto_approvable(entity, domains, risk, verdict, concerns)
    # Lessons can block auto-approval but never grant it
    if auto_ok and lesson_context["auto_approve_blocked"]:
        auto_ok = False

    # Step 9: Summary + notes
    summary = _build_summary(entity, verdict, risk, domains)

    notes = []
    if entity["type"] == "bugfix_candidate" and entity.get("patch_lines", 0) > 100:
        notes.append(f"Large patch ({entity['patch_lines']} lines) — review carefully")
    if entity["type"] == "evolution_proposal" and entity.get("risk_level") == "LEVEL_3":
        notes.append("LEVEL_3: architecture-level change — requires deep review")
    for c in concerns:
        notes.append(c)
    # Add informational lesson notes (non-warning ones)
    for note in lesson_context["notes"]:
        if note not in notes:  # avoid duplicates from concerns
            notes.append(note)

    # Persist assessment
    assessment = ReviewerAssessment(
        entity_type=entity_type,
        entity_id=entity_id,
        verdict=verdict,
        risk_level=risk,
        strategic_alignment=alignment,
        confidence=confidence,
        auto_approvable=auto_ok,
        summary=summary,
        notes_json=json.dumps(notes) if notes else None,
        blocking_concerns_json=json.dumps(blocking) if blocking else None,
        affected_domains_json=json.dumps(domains),
        reviewer_mode=mode,
        brain_snapshot_id=snapshot.id if snapshot else None,
    )
    db.add(assessment)
    db.flush()

    log.info(
        "reviewer: %s #%d → %s [%s] align=%s conf=%s auto=%s domains=%s",
        entity_type, entity_id, verdict, risk, alignment, confidence, auto_ok,
        ",".join(domains),
    )

    return assessment


# ---------------------------------------------------------------------------
# Operator Output Formatter
# ---------------------------------------------------------------------------

def format_for_operator(assessment: ReviewerAssessment) -> str:
    """
    Format a reviewer assessment as a compact, operator-readable string.
    Suitable for Telegram notifications or operator dashboard display.
    """
    lines = []
    lines.append(f"Reviewer verdict: {assessment.verdict.upper().replace('_', ' ')}")
    lines.append(f"Risk: {assessment.risk_level}")
    lines.append(f"Strategic alignment: {assessment.strategic_alignment}")
    lines.append(f"Confidence: {assessment.confidence}")
    lines.append(f"Auto-approvable: {'yes' if assessment.auto_approvable else 'no'}")
    lines.append("")
    lines.append(assessment.summary)

    if assessment.blocking_concerns_json:
        blocking = json.loads(assessment.blocking_concerns_json)
        if blocking:
            lines.append("")
            lines.append("BLOCKING:")
            for b in blocking:
                lines.append(f"  • {b}")

    if assessment.notes_json:
        notes = json.loads(assessment.notes_json)
        if notes:
            lines.append("")
            lines.append("Notes:")
            for n in notes:
                lines.append(f"  • {n}")

    if assessment.affected_domains_json:
        domains = json.loads(assessment.affected_domains_json)
        if domains:
            lines.append(f"\nDomains: {', '.join(domains)}")

    return "\n".join(lines)
