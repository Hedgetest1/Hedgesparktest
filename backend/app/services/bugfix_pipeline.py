"""
bugfix_pipeline.py — Bug triage, patch proposal, and human-gated fix pipeline.

Triage: reads ops_alerts + outcome data → creates BugFixCandidate rows
Proposal: builds context → calls LLM → stores patch on candidate row
Apply: safe apply with test verification and rollback

Public interface:
    run_bug_triage(db) -> dict          — scan for new bugs, create candidates
    propose_patch(db, candidate_id) -> bool  — LLM proposes patch for a candidate
    run_auto_propose(db) -> dict        — auto-propose for open candidates
    run_auto_apply(db) -> dict          — auto-apply TIER_0 candidates
    apply_bugfix_candidate(db, id) -> ApplyResult

Unified pipeline integration:
    - Rule 4 in triage scans merchant_reported_bug alerts (from chatbot)
    - Back-links support incidents when candidates are created from chatbot alerts
    - Propagates resolution to linked support incidents after successful apply
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.bugfix_candidate import BugFixCandidate

log = logging.getLogger("bugfix_pipeline")

_TRIAGE_LOOKBACK_HOURS = 24


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Triage: scan for actionable bugs → create candidates
# ---------------------------------------------------------------------------

def run_bug_triage(db: Session) -> dict:
    """
    Scan ops_alerts and action_outcomes for patterns that indicate bugs.
    Create BugFixCandidate rows for new findings. Dedup by source_type+source_ref.
    """
    summary = {"scanned": 0, "created": 0, "deduped": 0}
    cutoff = _now() - timedelta(hours=_TRIAGE_LOOKBACK_HOURS)

    # Rule 1: GDPR failures → likely code bug
    gdpr_alerts = db.execute(text("""
        SELECT id, shop_domain, summary, detail
        FROM ops_alerts
        WHERE alert_type = 'gdpr_failure' AND created_at >= :cutoff
        ORDER BY created_at DESC LIMIT 5
    """), {"cutoff": cutoff}).fetchall()

    for alert in gdpr_alerts:
        summary["scanned"] += 1
        ref = f"alert_{alert[0]}"
        if _has_open_candidate(db, "ops_alert", ref):
            summary["deduped"] += 1
            continue
        _create_candidate(
            db,
            source_type="ops_alert",
            source_ref=ref,
            title=f"GDPR processing failure (alert {alert[0]})",
            summary_text=alert[2],
            context={"alert_id": alert[0], "shop": alert[1], "detail": alert[3]},
        )
        summary["created"] += 1

    # Rule 2: Repeated worker failures → likely code or config bug
    worker_alerts = db.execute(text("""
        SELECT id, source, summary, detail
        FROM ops_alerts
        WHERE alert_type = 'worker_repeated_failure' AND created_at >= :cutoff
        ORDER BY created_at DESC LIMIT 5
    """), {"cutoff": cutoff}).fetchall()

    for alert in worker_alerts:
        summary["scanned"] += 1
        ref = f"worker_{alert[1]}"
        if _has_open_candidate(db, "ops_alert", ref):
            summary["deduped"] += 1
            continue
        _create_candidate(
            db,
            source_type="ops_alert",
            source_ref=ref,
            title=f"Worker {alert[1]} repeated failures",
            summary_text=alert[2],
            context={"alert_id": alert[0], "worker": alert[1], "detail": alert[3]},
        )
        summary["created"] += 1

    # Rule 3: Repeated no_effect outcomes → action implementation may be broken
    no_effect = db.execute(text("""
        SELECT action_type, target_id, COUNT(*) AS cnt
        FROM action_outcomes
        WHERE outcome_status = 'no_effect' AND executed_at >= :cutoff
        GROUP BY action_type, target_id
        HAVING COUNT(*) >= 3
        LIMIT 5
    """), {"cutoff": cutoff}).fetchall()

    for row in no_effect:
        summary["scanned"] += 1
        ref = f"outcome_{row[0]}_{row[1]}"
        if _has_open_candidate(db, "outcome", ref):
            summary["deduped"] += 1
            continue
        _create_candidate(
            db,
            source_type="outcome",
            source_ref=ref,
            title=f"Action {row[0]} repeatedly ineffective on {row[1]}",
            summary_text=f"{row[2]} consecutive no_effect outcomes for {row[0]} targeting {row[1]}",
            context={"action_type": row[0], "target": row[1], "count": row[2]},
        )
        summary["created"] += 1

    # Rule 4: Merchant-reported bugs → chatbot-originated alerts
    # Consumes alerts created by merchant_chatbot._route_to_pipeline()
    merchant_bugs = db.execute(text("""
        SELECT id, shop_domain, summary, detail
        FROM ops_alerts
        WHERE alert_type = 'merchant_reported_bug'
          AND resolved = false
          AND created_at >= :cutoff
        ORDER BY created_at DESC LIMIT 5
    """), {"cutoff": cutoff}).fetchall()

    for alert in merchant_bugs:
        summary["scanned"] += 1
        ref = f"merchant_bug_alert_{alert[0]}"
        if _has_open_candidate(db, "support_incident", ref):
            summary["deduped"] += 1
            continue
        candidate = _create_candidate(
            db,
            source_type="support_incident",
            source_ref=ref,
            title=f"Merchant reported: {(alert[2] or '')[:150]}",
            summary_text=alert[2],
            context={"alert_id": alert[0], "shop": alert[1], "detail": alert[3]},
        )
        summary["created"] += 1

        # Back-link: update any support incidents linked to this alert
        _backlink_incidents_to_candidate(db, alert_id=alert[0], candidate_id=candidate.id)

    # Recover stuck candidates (applying for >10 min)
    _recover_stuck_candidates(db)

    if summary["created"] > 0:
        db.flush()
        log.info("bugfix_triage: scanned=%d created=%d deduped=%d", summary["scanned"], summary["created"], summary["deduped"])

    return summary


def run_auto_propose(db: Session, max_per_cycle: int = 2) -> dict:
    """
    Auto-propose patches for open/analyzed candidates that have not yet
    been attempted. Max 2 per cycle to control LLM cost.
    """
    summary = {"attempted": 0, "proposed": 0, "failed": 0}

    candidates = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.status.in_(["open", "analyzed"]),
            BugFixCandidate.proposal_attempted_at.is_(None),
        )
        .order_by(BugFixCandidate.created_at)
        .limit(max_per_cycle)
        .all()
    )

    for c in candidates:
        summary["attempted"] += 1
        c.proposal_attempted_at = _now()
        try:
            success = propose_patch(db, c.id)
            if success:
                summary["proposed"] += 1
                # Determine provider from env
                c.proposal_provider = "anthropic" if os.getenv("ANTHROPIC_API_KEY", "").strip() else (
                    "openai" if os.getenv("OPENAI_API_KEY", "").strip() else "none"
                )
            else:
                summary["failed"] += 1
                c.proposal_error = c.failure_reason or "proposal_returned_false"
        except Exception as exc:
            summary["failed"] += 1
            c.proposal_error = str(exc)[:500]
            log.warning("auto_propose: failed id=%d: %s", c.id, exc)
        db.flush()

    if summary["attempted"] > 0:
        log.info(
            "auto_propose: attempted=%d proposed=%d failed=%d",
            summary["attempted"], summary["proposed"], summary["failed"],
        )

    return summary


def _has_open_candidate(db: Session, source_type: str, source_ref: str) -> bool:
    """Check if an active candidate already exists for this source (any non-terminal status)."""
    return db.query(BugFixCandidate).filter(
        BugFixCandidate.source_type == source_type,
        BugFixCandidate.source_ref == source_ref,
        BugFixCandidate.status.in_(["open", "analyzed", "patch_proposed", "approved", "applying"]),
    ).first() is not None


def _create_candidate(
    db: Session, *, source_type: str, source_ref: str,
    title: str, summary_text: str, context: dict,
) -> BugFixCandidate:
    c = BugFixCandidate(
        source_type=source_type,
        source_ref=source_ref,
        title=title,
        summary=summary_text,
        context_json=json.dumps(context, default=str),
        status="open",
    )
    db.add(c)
    db.flush()
    return c


def _backlink_incidents_to_candidate(db: Session, alert_id: int, candidate_id: int):
    """
    Find support incidents linked to this ops_alert and set their
    linked_bugfix_candidate_id + transition status to 'investigating'.
    """
    from app.models.support_incident import SupportIncident
    incidents = (
        db.query(SupportIncident)
        .filter(
            SupportIncident.linked_ops_alert_id == alert_id,
            SupportIncident.status.in_(["open", "triaged"]),
        )
        .all()
    )
    for inc in incidents:
        inc.linked_bugfix_candidate_id = candidate_id
        inc.status = "investigating"
        log.info("bugfix_triage: linked incident=%d → candidate=%d, status→investigating",
                 inc.id, candidate_id)
    if incidents:
        db.flush()


def _recover_stuck_candidates(db: Session):
    """
    Reset candidates stuck in 'applying' for >10 minutes.
    Prevents permanent stuck state if worker crashes during apply.
    """
    stuck_cutoff = _now() - timedelta(minutes=10)
    stuck = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.status == "applying",
            BugFixCandidate.decided_at.isnot(None),
            BugFixCandidate.decided_at <= stuck_cutoff,
        )
        .all()
    )
    for c in stuck:
        c.status = "patch_proposed"
        c.failure_reason = "stuck_in_applying_recovered"
        log.warning("bugfix_triage: recovered stuck candidate id=%d", c.id)
    if stuck:
        db.flush()


def _propagate_resolution(db: Session, candidate: BugFixCandidate):
    """
    When a bugfix candidate is applied, resolve all linked support incidents.
    Transitions: investigating/triaged/open → resolved (by auto_bugfix).
    """
    from app.models.support_incident import SupportIncident
    incidents = (
        db.query(SupportIncident)
        .filter(
            SupportIncident.linked_bugfix_candidate_id == candidate.id,
            SupportIncident.status.in_(["open", "triaged", "investigating"]),
        )
        .all()
    )
    for inc in incidents:
        inc.status = "resolved"
        inc.resolved_by = "auto_bugfix"
        inc.resolved_at = _now()
        inc.resolution_summary = f"Resolved by auto-fix #{candidate.id}: {candidate.title}"
        log.info("bugfix_pipeline: auto-resolved incident=%d via candidate=%d", inc.id, candidate.id)
    if incidents:
        db.flush()


# ---------------------------------------------------------------------------
# Patch proposal: LLM generates a fix suggestion
# ---------------------------------------------------------------------------

_PATCH_SYSTEM_PROMPT = """You are a senior backend engineer fixing a bug in the Hedge Spark SaaS platform.

Given the bug context (alert details, error info, affected subsystem), propose a minimal, safe fix.

RULES:
- Output a JSON object with these fields:
  - patch_summary: one paragraph explaining the fix
  - files: list of file paths that need changes
  - diff: unified diff text of the proposed changes
  - test_command: pytest command to verify the fix (e.g. "python -m pytest tests/test_gdpr.py -v")
- Be conservative — propose the smallest change that fixes the root cause
- Never propose changes to encryption, auth, or billing logic
- If you cannot determine the fix, return {"patch_summary": "Unable to determine fix", "files": [], "diff": "", "test_command": ""}

Respond with strict JSON only."""


def propose_patch(db: Session, candidate_id: int) -> bool:
    """
    Call LLM to propose a patch for a BugFixCandidate.
    Stores result on the candidate row. Does NOT apply anything.
    Returns True if proposal was generated.
    """
    candidate = db.query(BugFixCandidate).get(candidate_id)
    if not candidate or candidate.status not in ("open", "analyzed"):
        return False

    candidate.status = "analyzed"

    # Build context
    context_parts = [f"## Bug: {candidate.title}", f"Summary: {candidate.summary}"]
    if candidate.context_json:
        try:
            ctx = json.loads(candidate.context_json)
            context_parts.append(f"Context: {json.dumps(ctx, indent=2)}")
        except Exception:
            context_parts.append(f"Context: {candidate.context_json[:500]}")
    user_message = "\n\n".join(context_parts)

    # Call LLM with routing context
    file_count = 1
    if candidate.context_json:
        try:
            ctx = json.loads(candidate.context_json)
            file_count = len(ctx.get("files", [])) or 1
        except Exception:
            pass
    raw = _call_llm(user_message, patch_risk_tier=None, file_count=file_count)
    if not raw:
        candidate.failure_reason = "llm_call_failed"
        db.flush()
        return False

    # Parse response
    try:
        text_clean = raw.strip()
        if text_clean.startswith("```"):
            lines = text_clean.split("\n")
            text_clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        data = json.loads(text_clean)
    except (json.JSONDecodeError, ValueError) as exc:
        candidate.failure_reason = f"json_parse_error: {exc}"
        db.flush()
        return False

    candidate.patch_summary = data.get("patch_summary", "")
    candidate.patch_diff = data.get("diff", "")
    candidate.patch_files = json.dumps(data.get("files", []))
    candidate.test_command = data.get("test_command", "")

    # Reject empty/whitespace-only diffs — LLM sometimes returns valid JSON with no actual patch
    if not candidate.patch_diff or not candidate.patch_diff.strip():
        candidate.failure_reason = "llm_returned_empty_diff"
        db.flush()
        return False

    candidate.status = "patch_proposed"

    # Classify risk tier
    tier, tier_reasons = classify_patch_risk(candidate.patch_files, candidate.patch_diff)
    candidate.patch_risk_tier = tier
    db.flush()
    log.info("bugfix_pipeline: classified id=%d tier=%d reasons=%s", candidate.id, tier, tier_reasons)

    # Notify via Slack if configured
    try:
        from app.core.alert_delivery import _SLACK_URL
        if _SLACK_URL:
            import httpx
            httpx.post(_SLACK_URL, json={
                "text": (
                    f":wrench: *PATCH PROPOSED* — `{candidate.title}`\n"
                    f"*Files:* {', '.join(data.get('files', []))}\n"
                    f"*Summary:* {(candidate.patch_summary or '')[:200]}\n"
                    f"*ID:* {candidate.id}\n"
                    f"_Review via: GET /ops/bugfixes/{candidate.id}_"
                ),
            }, timeout=5.0)
            candidate.notified_at = _now()
    except Exception:
        pass

    # Telegram: send reviewer pre-assessment for non-TIER_0 patches
    if tier != PATCH_TIER_0:
        try:
            from app.services.reviewer_layer import review_entity
            from app.services.telegram_agent import send_reviewer_verdict, is_configured
            assessment = review_entity(db, "bugfix_candidate", candidate.id)
            if assessment:
                candidate.reviewer_assessment_id = assessment.id
                db.flush()
                if is_configured():
                    send_reviewer_verdict(assessment, entity_title=candidate.title)
        except Exception:
            pass

    log.info("bugfix_pipeline: patch proposed id=%d title=%s", candidate.id, candidate.title)
    return True


def _call_llm(
    user_message: str,
    patch_risk_tier: int | None = None,
    file_count: int = 1,
    previous_failed: bool = False,
) -> str:
    """
    Call LLM for patch proposal. Budget-guarded + model-routed.
    Returns raw response text or empty string.
    If Sonnet fails, retries once with Opus (escalation).
    """
    from app.core.llm_budget import check_budget, record_usage, record_blocked
    allowed, reason = check_budget("bugfix_proposal")
    if not allowed:
        record_blocked("bugfix_proposal", reason)
        log.info("bugfix_pipeline: LLM call blocked by budget: %s", reason)
        return ""

    from app.core.llm_router import select_model
    import httpx

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()

    sel = select_model(
        module="bugfix_proposal",
        patch_risk_tier=patch_risk_tier,
        file_count=file_count,
        previous_failed=previous_failed,
        anthropic_available=bool(anthropic_key),
        openai_available=bool(openai_key),
    )

    text = _call_provider(sel, user_message, anthropic_key, openai_key)

    if text:
        record_usage("bugfix_proposal", tokens_used=len(text) // 4, provider=sel.provider, model=sel.model)
        return text

    # Escalation: if Sonnet failed and not already escalated, try Opus once
    if not previous_failed and not sel.escalation:
        log.info("bugfix_pipeline: Sonnet failed, escalating to Opus")
        return _call_llm(user_message, patch_risk_tier=patch_risk_tier, file_count=file_count, previous_failed=True)

    return ""


def _call_provider(sel, user_message: str, anthropic_key: str, openai_key: str) -> str:
    """Make the actual API call based on model selection. Handles 429 with backoff."""
    import httpx
    from app.core.llm_budget import is_provider_backed_off, record_429

    if sel.provider == "anthropic" and anthropic_key:
        if is_provider_backed_off("anthropic"):
            log.info("bugfix_pipeline: Anthropic backed off (429 cooldown)")
        else:
            try:
                resp = httpx.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": anthropic_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={
                        "model": sel.model,
                        "max_tokens": sel.max_tokens,
                        "temperature": 0.1,
                        "system": _PATCH_SYSTEM_PROMPT,
                        "messages": [{"role": "user", "content": user_message}],
                    },
                    timeout=30.0,
                )
                if resp.status_code == 200:
                    return resp.json().get("content", [{}])[0].get("text", "")
                if resp.status_code == 429:
                    record_429("anthropic")
                else:
                    log.warning("bugfix_pipeline: Anthropic %s returned %d", sel.model, resp.status_code)
            except Exception as exc:
                log.warning("bugfix_pipeline: Anthropic %s failed: %s", sel.model, type(exc).__name__)

    if openai_key:
        if is_provider_backed_off("openai"):
            log.info("bugfix_pipeline: OpenAI backed off (429 cooldown)")
            return ""
        model = sel.model if sel.provider == "openai" else "gpt-4o-mini"
        try:
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "max_tokens": sel.max_tokens,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _PATCH_SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                },
                timeout=30.0,
            )
            if resp.status_code == 200:
                return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            if resp.status_code == 429:
                record_429("openai")
            else:
                log.warning("bugfix_pipeline: OpenAI %s returned %d", model, resp.status_code)
        except Exception as exc:
            log.warning("bugfix_pipeline: OpenAI %s failed: %s", model, type(exc).__name__)

    return ""


# ---------------------------------------------------------------------------
# Patch risk tiering — deterministic classifier
# ---------------------------------------------------------------------------

PATCH_TIER_0 = 0  # Ultra-safe: auto-apply
PATCH_TIER_1 = 1  # Human-approve required (default)
PATCH_TIER_2 = 2  # Never auto-apply (forbidden paths)

_MAX_SAFE_DIFF_LINES = 120

# Paths that are explicitly safe for TIER_0 auto-apply
_SAFE_PATH_PREFIXES = [
    "app/services/signal_text",
    "app/services/digest_formatter",
    "app/services/nudge_rank",
    "app/services/revenue_metrics",
    "app/services/utm_attribution",
    "app/services/conversion_metrics",
    "tests/",
]

# Diff patterns that indicate dangerous content (force TIER_2)
_DANGEROUS_DIFF_PATTERNS = [
    "subprocess",
    "os.system",
    "eval(",
    "exec(",
    "__import__",
    "MERCHANT_TOKEN_ENCRYPTION_KEY",
    "SHOPIFY_API_SECRET",
    "DASHBOARD_API_KEY",
]


def classify_patch_risk(patch_files_json: str | None, patch_diff: str | None) -> tuple[int, list[str]]:
    """
    Classify patch risk tier. Returns (tier, reasons).

    TIER_0 only if ALL:
      - all files in safe path prefixes
      - no forbidden paths
      - diff <= 120 lines
      - no dangerous patterns in diff
    TIER_2 if any forbidden path or dangerous pattern found.
    Else TIER_1.
    """
    reasons: list[str] = []

    if not patch_files_json or not patch_diff:
        return PATCH_TIER_1, ["no_patch_data"]

    try:
        files = json.loads(patch_files_json)
    except (json.JSONDecodeError, ValueError):
        return PATCH_TIER_1, ["invalid_files_json"]

    if not files:
        return PATCH_TIER_1, ["empty_file_list"]

    # Check forbidden paths → TIER_2
    for f in files:
        for pattern in _FORBIDDEN_PATH_PATTERNS:
            if pattern in str(f):
                return PATCH_TIER_2, [f"forbidden: {f}"]

    # Check dangerous diff patterns → TIER_2
    diff_lower = (patch_diff or "").lower()
    for pattern in _DANGEROUS_DIFF_PATTERNS:
        if pattern.lower() in diff_lower:
            return PATCH_TIER_2, [f"dangerous_pattern: {pattern}"]

    # Check diff size
    diff_lines = len([l for l in (patch_diff or "").split("\n") if l.startswith("+") or l.startswith("-")])
    if diff_lines > _MAX_SAFE_DIFF_LINES:
        reasons.append(f"large_diff: {diff_lines} lines")
        return PATCH_TIER_1, reasons

    # Check all files are in safe prefixes → TIER_0
    all_safe = True
    for f in files:
        if not any(str(f).startswith(prefix) or prefix in str(f) for prefix in _SAFE_PATH_PREFIXES):
            all_safe = False
            reasons.append(f"non_safe_path: {f}")
            break

    if all_safe:
        return PATCH_TIER_0, ["all_files_safe", f"diff_lines={diff_lines}"]

    return PATCH_TIER_1, reasons or ["default_tier_1"]


def _notify_reviewer_block(candidate, assessment):
    """Send Telegram notification when reviewer blocks auto-apply."""
    try:
        from app.services.telegram_agent import send_reviewer_verdict, is_configured
        if is_configured():
            send_reviewer_verdict(assessment, entity_title=candidate.title)
    except Exception:
        pass


def run_auto_apply(db: Session, max_per_cycle: int = 1) -> dict:
    """
    Auto-approve + auto-apply PATCH_TIER_0 candidates.
    Max 1 per cycle. Stops on any failure.
    """
    import time as _time

    summary = {"attempted": 0, "applied": 0, "failed": 0, "skipped": 0}

    candidates = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.status == "patch_proposed",
            BugFixCandidate.patch_risk_tier == PATCH_TIER_0,
        )
        .order_by(BugFixCandidate.created_at)
        .limit(max_per_cycle)
        .all()
    )

    for c in candidates:
        # Re-verify tier (defensive)
        tier, reasons = classify_patch_risk(c.patch_files, c.patch_diff)
        if tier != PATCH_TIER_0:
            c.patch_risk_tier = tier
            summary["skipped"] += 1
            db.flush()
            continue

        # Reviewer gate — deterministic assessment before auto-apply
        try:
            from app.services.reviewer_layer import review_entity
            assessment = review_entity(db, "bugfix_candidate", c.id)
            if assessment:
                c.reviewer_assessment_id = assessment.id
                db.flush()
                if assessment.verdict == "reject":
                    log.info("auto_apply: REVIEWER BLOCKED id=%d verdict=reject", c.id)
                    summary["skipped"] += 1
                    _notify_reviewer_block(c, assessment)
                    continue
                if assessment.verdict == "refine":
                    log.info("auto_apply: REVIEWER HELD id=%d verdict=refine", c.id)
                    summary["skipped"] += 1
                    _notify_reviewer_block(c, assessment)
                    continue
                if not assessment.auto_approvable:
                    log.info("auto_apply: REVIEWER NOT AUTO-APPROVABLE id=%d", c.id)
                    summary["skipped"] += 1
                    _notify_reviewer_block(c, assessment)
                    continue
        except Exception as exc:
            log.warning("auto_apply: reviewer error (non-fatal, proceeding): %s", exc)

        summary["attempted"] += 1

        # Auto-approve
        c.status = "approved"
        c.decided_by = "auto_tier_0"
        c.decided_at = _now()
        db.flush()

        from app.services.audit import write_audit_log
        write_audit_log(
            db, actor_type="system", actor_name="auto_apply",
            action_type="bugfix_auto_approved", target_type="bugfix",
            target_id=str(c.id), status="completed", approval_mode="autonomous",
            metadata={"tier": 0, "reasons": reasons,
                      "reviewer_assessment_id": c.reviewer_assessment_id},
        )
        db.flush()

        # Apply
        result = apply_bugfix_candidate(db, c.id)
        db.flush()

        if result.status == "applied":
            summary["applied"] += 1
            # Slack notify success
            try:
                from app.core.alert_delivery import _SLACK_URL
                if _SLACK_URL:
                    import httpx
                    httpx.post(_SLACK_URL, json={
                        "text": (
                            f":white_check_mark: *AUTO-APPLIED* — `{c.title}`\n"
                            f"*SHA:* `{c.git_commit_sha or 'N/A'}`\n"
                            f"*Tests:* passed\n*ID:* {c.id}"
                        ),
                    }, timeout=5.0)
            except Exception:
                pass
            log.info("auto_apply: SUCCESS id=%d title=%s", c.id, c.title)
        else:
            summary["failed"] += 1
            log.warning("auto_apply: FAILED id=%d status=%s reason=%s", c.id, result.status, result.failure_reason)
            break  # Stop further auto-applies this cycle

    if summary["attempted"] > 0:
        log.info("auto_apply: attempted=%d applied=%d failed=%d", summary["attempted"], summary["applied"], summary["failed"])

    return summary


# ---------------------------------------------------------------------------
# Safety blocklist — file paths that must NEVER be auto-patched
# ---------------------------------------------------------------------------

_FORBIDDEN_PATH_PATTERNS = [
    "app/core/token_crypto",
    "app/core/merchant_session",
    "app/core/deps.py",
    "app/api/billing",
    "app/api/shopify_oauth",
    "app/services/orchestrator.py",
    "app/models/action_approval",
    "migrations/",
]

_REPO_DIR = "/opt/wishspark"
_BACKEND_DIR = "/opt/wishspark/backend"


def _check_forbidden_paths(patch_files_json: str | None) -> str | None:
    """Return rejection reason if any file is in the forbidden list."""
    if not patch_files_json:
        return None
    try:
        files = json.loads(patch_files_json)
    except (json.JSONDecodeError, ValueError):
        return None
    for f in files:
        for pattern in _FORBIDDEN_PATH_PATTERNS:
            if pattern in str(f):
                return f"forbidden_path: {f} matches {pattern}"
    return None


# ---------------------------------------------------------------------------
# Safe apply pipeline — human-gated, test-verified, reversible
# ---------------------------------------------------------------------------

@dataclass
class ApplyResult:
    status: str = "pending"
    test_passed: bool = False
    test_output: str = ""
    health_ok: bool = False
    failure_reason: str | None = None


def apply_bugfix_candidate(db: Session, candidate_id: int) -> ApplyResult:
    """
    Apply an approved patch with verification and rollback.

    Sequence: preconditions → clean check → apply --check → apply →
    tests → restart → health → success or rollback.

    On success: propagates resolution to linked support incidents.
    """
    import subprocess
    import tempfile

    result = ApplyResult()
    candidate = db.query(BugFixCandidate).get(candidate_id)

    if not candidate:
        result.status = "apply_failed"
        result.failure_reason = "candidate_not_found"
        return result

    if candidate.status != "approved":
        result.status = "apply_failed"
        result.failure_reason = f"wrong_status: {candidate.status}"
        return result

    if not candidate.patch_diff or not candidate.patch_diff.strip():
        result.status = "apply_failed"
        result.failure_reason = "empty_patch_diff"
        candidate.status = "apply_failed"
        candidate.failure_reason = result.failure_reason
        db.flush()
        return result

    # Forbidden path check
    forbidden = _check_forbidden_paths(candidate.patch_files)
    if forbidden:
        result.status = "apply_failed"
        result.failure_reason = forbidden
        candidate.status = "apply_failed"
        candidate.failure_reason = forbidden
        db.flush()
        _write_apply_alert(db, candidate, result)
        return result

    patch_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False, dir="/tmp") as f:
            f.write(candidate.patch_diff)
            patch_path = f.name

        candidate.status = "applying"
        db.flush()

        # Git tree clean check
        git_status = subprocess.run(
            ["git", "diff", "--quiet"], cwd=_BACKEND_DIR,
            capture_output=True, timeout=10,
        )
        if git_status.returncode != 0:
            return _fail_apply(db, candidate, result, "git_tree_dirty")

        # git apply --check
        check = subprocess.run(
            ["git", "apply", "--check", patch_path], cwd=_BACKEND_DIR,
            capture_output=True, text=True, timeout=10,
        )
        if check.returncode != 0:
            return _fail_apply(db, candidate, result, f"apply_check_failed: {check.stderr[:300]}")

        # Apply
        apply_cmd = subprocess.run(
            ["git", "apply", patch_path], cwd=_BACKEND_DIR,
            capture_output=True, text=True, timeout=10,
        )
        if apply_cmd.returncode != 0:
            return _fail_apply(db, candidate, result, f"apply_failed: {apply_cmd.stderr[:300]}")

        # Run tests
        test_cmd = candidate.test_command or f"{_BACKEND_DIR}/venv/bin/python -m pytest tests/ -q"
        test_run = subprocess.run(
            test_cmd.split(), cwd=_BACKEND_DIR,
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "PYTHONPATH": _BACKEND_DIR},
        )
        result.test_output = (test_run.stdout[-500:] + "\n" + test_run.stderr[-500:]).strip()
        result.test_passed = test_run.returncode == 0
        candidate.test_result = result.test_output[:2000]

        if not result.test_passed:
            _rollback_patch(patch_path)
            return _fail_apply(db, candidate, result, "tests_failed", rolled_back=True)

        # Restart + health
        subprocess.run(["pm2", "restart", "wishspark-backend"], capture_output=True, timeout=15)
        import time
        time.sleep(4)

        try:
            import httpx
            health = httpx.get("http://127.0.0.1:8000/system/health", timeout=8.0)
            result.health_ok = health.status_code == 200
        except Exception:
            result.health_ok = False

        if not result.health_ok:
            _rollback_patch(patch_path)
            subprocess.run(["pm2", "restart", "wishspark-backend"], capture_output=True, timeout=15)
            return _fail_apply(db, candidate, result, "health_check_failed", rolled_back=True)

        # Git commit (local only, no push)
        commit_sha = _git_commit_patch(candidate)
        if commit_sha is None:
            # Commit failed — rollback
            _rollback_patch(patch_path)
            subprocess.run(["pm2", "restart", "wishspark-backend"], capture_output=True, timeout=15)
            return _fail_apply(db, candidate, result, "git_commit_failed", rolled_back=True)

        # Success
        result.status = "applied"
        candidate.status = "applied"
        candidate.applied_at = _now()
        candidate.git_commit_sha = commit_sha
        candidate.failure_reason = None
        candidate.outcome_status = None  # will be measured 48h later by evolution_outcomes
        db.flush()

        from app.services.audit import write_audit_log
        write_audit_log(
            db, actor_type="system", actor_name="bugfix_apply",
            action_type="bugfix_applied", target_type="bugfix",
            target_id=str(candidate.id),
            after_state={"title": candidate.title, "tests_passed": True, "commit": commit_sha},
            status="completed", approval_mode="human_approved",
        )
        db.flush()

        # Propagate resolution to linked support incidents
        _propagate_resolution(db, candidate)

        # Create promotion for remote push
        try:
            from app.services.promotion_pipeline import create_promotion
            create_promotion(db, bugfix_candidate_id=candidate.id, git_commit_sha=commit_sha)
            db.flush()
        except Exception as exc:
            log.warning("bugfix_apply: promotion creation failed (non-fatal): %s", exc)

        log.info("bugfix_apply: SUCCESS id=%d sha=%s title=%s", candidate.id, commit_sha, candidate.title)
        return result

    except Exception as exc:
        if patch_path:
            try:
                _rollback_patch(patch_path)
            except Exception:
                pass
        result.status = "apply_failed"
        result.failure_reason = f"unexpected: {str(exc)[:300]}"
        candidate.status = "apply_failed"
        candidate.failure_reason = result.failure_reason
        db.flush()
        _write_apply_alert(db, candidate, result)
        return result

    finally:
        if patch_path:
            try:
                os.unlink(patch_path)
            except Exception:
                pass


def _git_commit_patch(candidate: BugFixCandidate) -> str | None:
    """Create a local git commit for the applied patch. Returns SHA or None."""
    import subprocess
    try:
        # Stage all changes
        subprocess.run(["git", "add", "-A"], cwd=_BACKEND_DIR, capture_output=True, timeout=10)
        # Commit
        msg = f"chore(autofix): apply bugfix candidate #{candidate.id}\n\n{candidate.title}"
        result = subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            log.warning("bugfix_apply: git commit failed: %s", result.stderr[:200])
            return None
        # Get SHA
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=5,
        )
        return sha_result.stdout.strip()[:40] if sha_result.returncode == 0 else None
    except Exception as exc:
        log.warning("bugfix_apply: git commit error: %s", exc)
        return None


def _fail_apply(
    db: Session, candidate: BugFixCandidate, result: ApplyResult,
    reason: str, rolled_back: bool = False,
) -> ApplyResult:
    result.status = "rolled_back" if rolled_back else "apply_failed"
    result.failure_reason = reason
    candidate.status = result.status
    candidate.failure_reason = reason
    db.flush()
    _write_apply_alert(db, candidate, result)
    return result


def _rollback_patch(patch_path: str) -> None:
    import subprocess
    subprocess.run(
        ["git", "apply", "-R", patch_path], cwd=_BACKEND_DIR,
        capture_output=True, timeout=10,
    )


def _write_apply_alert(db: Session, candidate: BugFixCandidate, result: ApplyResult) -> None:
    from app.services.alerting import write_alert
    severity = "critical" if result.status == "rolled_back" else "warning"
    write_alert(
        db, severity=severity, source="bugfix_apply",
        alert_type="bugfix_rolled_back" if result.status == "rolled_back" else "bugfix_apply_failed",
        summary=f"Bug fix #{candidate.id} {result.status}: {result.failure_reason}",
        detail={"candidate_id": candidate.id, "title": candidate.title, "reason": result.failure_reason},
    )
    db.flush()
