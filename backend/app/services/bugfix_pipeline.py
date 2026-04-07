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


# ---------------------------------------------------------------------------
# Patch fingerprinting — prevents retrying identical failed approaches
# ---------------------------------------------------------------------------

import hashlib


def _compute_patch_fingerprint(title: str, files_json: str | None, patch_diff: str | None = None) -> str:
    """
    Compute a SHA-256 fingerprint for a patch based on its identity.
    Normalized: sorted file list + lowercased title keywords.
    """
    parts = []

    # Normalize title into sorted keywords
    if title:
        words = sorted(set(title.lower().split()))
        parts.append(" ".join(words))

    # Normalize file list
    if files_json:
        try:
            files = json.loads(files_json)
            if isinstance(files, list):
                parts.append("|".join(sorted(files)))
        except (json.JSONDecodeError, ValueError):
            parts.append(files_json[:200])

    # Include first 500 chars of diff for diff-level dedup
    if patch_diff:
        parts.append(patch_diff[:500])

    raw = "\n".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()


import re

# Patterns stripped during diff normalization
_DIFF_STRIP_PATTERNS = [
    re.compile(r"^@@\s.*@@.*$", re.MULTILINE),     # hunk headers
    re.compile(r"^---\s.*$", re.MULTILINE),          # file headers
    re.compile(r"^\+\+\+\s.*$", re.MULTILINE),      # file headers
    re.compile(r"^diff\s--git.*$", re.MULTILINE),    # diff command line
    re.compile(r"^index\s[0-9a-f]+\.\.[0-9a-f]+.*$", re.MULTILINE),  # index line
]


def _compute_diff_fingerprint(patch_diff: str | None) -> str | None:
    """
    Compute a normalized diff fingerprint that catches semantically identical patches
    even when cosmetic details differ (whitespace, comments, context lines, hunk headers).

    Normalization rules:
    1. Strip all hunk headers (@@...@@), file headers (---/+++), diff/index lines
    2. Keep only +/- lines (actual changes), strip leading +/-
    3. Collapse all whitespace to single spaces
    4. Strip inline comments (# ... at end of line)
    5. Sort remaining lines (order-independent — same changes in different order = same hash)
    6. Lowercase everything
    """
    if not patch_diff or not patch_diff.strip():
        return None

    normalized = patch_diff

    # Step 1: Strip headers and metadata
    for pattern in _DIFF_STRIP_PATTERNS:
        normalized = pattern.sub("", normalized)

    # Step 2: Keep only change lines (+ or -), strip the prefix
    change_lines = []
    for line in normalized.split("\n"):
        stripped = line.strip()
        if stripped.startswith("+") or stripped.startswith("-"):
            # Remove the +/- prefix
            content = stripped[1:].strip()
            if not content:
                continue  # skip empty change lines

            # Step 3: Collapse whitespace
            content = re.sub(r"\s+", " ", content)

            # Step 4: Strip trailing comments
            content = re.sub(r"\s*#\s*.*$", "", content)

            if content:
                change_lines.append(content.lower())

    if not change_lines:
        return None

    # Step 5: Sort for order-independence
    change_lines.sort()

    raw = "\n".join(change_lines)
    return hashlib.sha256(raw.encode()).hexdigest()


def _check_patch_fingerprint(
    db: Session, fingerprint: str, diff_fp: str | None = None, lookback_days: int = 30,
) -> dict | None:
    """
    Check if a patch with this fingerprint (or diff fingerprint) recently failed.
    Checks both identity fingerprint and normalized diff fingerprint.
    Returns dict with candidate_id and outcome if found, None otherwise.
    """
    try:
        from app.models.patch_fingerprint import PatchFingerprint
        from sqlalchemy import or_

        cutoff = _now() - timedelta(days=lookback_days)

        # Build filter: match identity fingerprint OR diff fingerprint
        fp_conditions = [PatchFingerprint.fingerprint == fingerprint]
        if diff_fp:
            fp_conditions.append(PatchFingerprint.diff_fingerprint == diff_fp)

        fp = (
            db.query(PatchFingerprint)
            .filter(
                or_(*fp_conditions),
                PatchFingerprint.outcome.in_(["rolled_back", "apply_failed", "tests_failed", "test_timeout"]),
                PatchFingerprint.created_at >= cutoff,
            )
            .order_by(PatchFingerprint.created_at.desc())
            .first()
        )
        if fp:
            match_type = "diff" if (diff_fp and fp.diff_fingerprint == diff_fp) else "identity"
            return {
                "candidate_id": fp.bugfix_candidate_id,
                "outcome": fp.outcome,
                "failure_reason": fp.failure_reason,
                "created_at": fp.created_at,
                "match_type": match_type,
            }
    except Exception as exc:
        log.debug("patch_fingerprint: check failed (non-fatal): %s", exc)
    return None


def _record_patch_fingerprint(
    db: Session, candidate: BugFixCandidate, outcome: str,
    failure_reason: str | None = None,
) -> None:
    """Record a patch fingerprint (identity + normalized diff) for future dedup."""
    try:
        from app.models.patch_fingerprint import PatchFingerprint
        fp_hash = _compute_patch_fingerprint(
            candidate.title, candidate.patch_files, candidate.patch_diff,
        )
        diff_fp = _compute_diff_fingerprint(candidate.patch_diff)
        domain = _classify_candidate_domain(candidate)
        from app.services.learning_isolation import label_fingerprint
        fp = PatchFingerprint(
            fingerprint=fp_hash,
            diff_fingerprint=diff_fp,
            bugfix_candidate_id=candidate.id,
            outcome=outcome,
            failure_reason=failure_reason[:500] if failure_reason else None,
            source_type=candidate.source_type,
            source_ref=candidate.source_ref,
            affected_domain=domain,
            patch_files=candidate.patch_files,
        )
        label_fingerprint(db, fp, candidate)
        db.add(fp)
        db.flush()
    except Exception as exc:
        log.debug("patch_fingerprint: record failed (non-fatal): %s", exc)


def _lookup_lessons_for_proposal(db: Session, domain: str) -> tuple[str | None, list[int]]:
    """
    Look up active lessons for a domain to inject into LLM context.
    Returns (formatted text block, list of lesson IDs used) or (None, []).

    ISOLATION: All evidence sources contribute to TECHNICAL context (patch
    formatting, failure patterns). But lessons are clearly labeled so the
    LLM understands the evidence weight. Only real_merchant lessons are
    marked as high-trust; pre-merchant lessons are labeled as low-trust
    technical reference.
    """
    try:
        from app.models.system_lesson import SystemLesson
        from app.services.learning_isolation import is_product_learning_eligible
        lessons = (
            db.query(SystemLesson)
            .filter(
                SystemLesson.status == "active",
                SystemLesson.confidence >= 0.3,
                SystemLesson.domain.in_([domain, "unknown"]),
            )
            .order_by(SystemLesson.confidence.desc())
            .limit(5)
            .all()
        )
        if not lessons:
            return None, []

        lesson_ids = [l.id for l in lessons]
        lines = [f"## Institutional Memory — Lessons for domain '{domain}'"]
        for l in lessons:
            marker = "✓" if l.lesson_type == "effective_pattern" else "✗"
            source = getattr(l, "evidence_source", None) or "pre_merchant"
            trust_tag = "HIGH-TRUST" if is_product_learning_eligible(source) else "TECHNICAL-ONLY"
            lines.append(
                f"- {marker} [{l.lesson_type}] [{trust_tag}] {l.summary} "
                f"(confidence: {l.confidence:.1f}, evidence: {l.evidence_count})"
            )
        return "\n".join(lines), lesson_ids
    except Exception:
        return None, []


def _classify_candidate_domain(candidate: BugFixCandidate) -> str | None:
    """Classify a candidate into a domain using project_brain."""
    if candidate.affected_domain:
        return candidate.affected_domain
    if not candidate.patch_files:
        return None
    try:
        from app.services.project_brain import classify_file
        files = json.loads(candidate.patch_files)
        if files:
            result = classify_file(files[0])
            domain = result.get("domain")
            candidate.affected_domain = domain
            return domain
    except Exception:
        pass
    return None


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Triage: scan for actionable bugs → create candidates
# ---------------------------------------------------------------------------

def run_bug_triage(db: Session) -> dict:
    """
    Scan ops_alerts and action_outcomes for patterns that indicate bugs.
    Create BugFixCandidate rows for new findings. Dedup by source_type+source_ref.
    Suppresses sources that are thrashing (3+ failed attempts in 30 days).
    """
    summary = {"scanned": 0, "created": 0, "deduped": 0, "suppressed": 0}
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
        if _should_skip_source(db, "ops_alert", ref, summary):
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
        if _should_skip_source(db, "ops_alert", ref, summary):
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
        if _should_skip_source(db, "outcome", ref, summary):
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
        if _should_skip_source(db, "support_incident", ref, summary):
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
        .order_by(
            BugFixCandidate.priority_score.desc().nullslast(),
            BugFixCandidate.created_at,
        )
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


def _should_skip_source(db: Session, source_type: str, source_ref: str, summary: dict) -> bool:
    """Check dedup + thrash suppression + escalation. Returns True if source should be skipped."""
    if _has_open_candidate(db, source_type, source_ref):
        summary["deduped"] += 1
        return True
    try:
        from app.services.loop_health import is_source_thrashing
        if is_source_thrashing(db, source_type, source_ref):
            summary["suppressed"] = summary.get("suppressed", 0) + 1
            log.info("triage: suppressed thrashing source=%s ref=%s", source_type, source_ref)
            _escalate_thrashing(db, source_type, source_ref)
            return True
    except ImportError:
        pass
    return False


def _escalate_thrashing(db: Session, source_type: str, source_ref: str) -> None:
    """
    Create a dedup-safe operator alert for a chronically thrashing source.
    Only creates one unresolved alert per source — won't spam.
    """
    try:
        from app.models.ops_alert import OpsAlert
        existing = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.alert_type == "chronic_thrashing",
                OpsAlert.source == f"{source_type}:{source_ref}",
                OpsAlert.resolved == False,
            )
            .first()
        )
        if existing:
            return  # already escalated, not yet resolved

        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="warning",
            source=f"{source_type}:{source_ref}",
            alert_type="chronic_thrashing",
            summary=(
                f"Bug source '{source_ref}' ({source_type}) has failed 3+ times in 30 days. "
                f"Auto-fix attempts are now suppressed. Manual investigation required."
            ),
            detail={
                "source_type": source_type,
                "source_ref": source_ref,
                "action": "Manual investigation needed — auto-fix pipeline cannot resolve this.",
            },
        )
        log.warning("triage: ESCALATED thrashing source=%s ref=%s → ops_alert created", source_type, source_ref)
    except Exception as exc:
        log.debug("triage: thrash escalation failed (non-fatal): %s", exc)


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
    When a bugfix candidate is applied, mark linked support incidents as
    fix_applied — but do NOT set resolution_summary yet.

    The merchant message is withheld until the fix is verified effective
    (48h outcome measurement) or an operator manually confirms.

    Status flow: investigating → fix_applied → resolved (after verification)
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
        inc.status = "fix_applied"
        inc.resolved_by = "auto_bugfix"
        inc.resolved_at = _now()
        # resolution_summary is deliberately NOT set here.
        # It will be set when the outcome is measured as effective,
        # or when an operator manually verifies.
        inc.resolution_verified = False
        log.info("bugfix_pipeline: fix applied for incident=%d via candidate=%d (awaiting verification)", inc.id, candidate.id)
    if incidents:
        db.flush()


# ---------------------------------------------------------------------------
# Domain effectiveness context for LLM
# ---------------------------------------------------------------------------

def _get_domain_effectiveness_context(db: Session) -> str | None:
    """
    Build a short context block showing per-domain patch effectiveness
    over the last 90 days. Helps the LLM calibrate its approach.

    Groups by affected_domain (actual domain intelligence, not source_type).
    Falls back to source_type grouping if no domain data available.
    """
    # Try per-domain grouping first (actual domain intelligence)
    domain_rows = db.execute(text("""
        SELECT
            COALESCE(bc.affected_domain, 'unknown') AS domain,
            bc.outcome_status,
            COUNT(*) as cnt
        FROM bugfix_candidates bc
        WHERE bc.outcome_status IS NOT NULL
          AND bc.outcome_measured_at >= NOW() - INTERVAL '90 days'
        GROUP BY COALESCE(bc.affected_domain, 'unknown'), bc.outcome_status
        ORDER BY domain
    """)).fetchall()

    if not domain_rows:
        return None

    # Aggregate by domain
    stats: dict[str, dict[str, int]] = {}
    for domain, outcome, cnt in domain_rows:
        if domain not in stats:
            stats[domain] = {}
        stats[domain][outcome] = cnt

    lines = ["## System Learning Context (90-day effectiveness by domain)"]
    for domain, outcomes in sorted(stats.items()):
        total = sum(outcomes.values())
        effective = outcomes.get("effective", 0)
        pct = round(effective / total * 100) if total > 0 else 0
        lines.append(f"- {domain}: {effective}/{total} effective ({pct}%)")

    # Add overall
    all_effective = sum(o.get("effective", 0) for o in stats.values())
    all_total = sum(sum(o.values()) for o in stats.values())
    if all_total > 0:
        lines.append(f"- OVERALL: {all_effective}/{all_total} effective ({round(all_effective/all_total*100)}%)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Diff normalization + structural + semantic validation
# ---------------------------------------------------------------------------

def _extract_json(raw: str) -> dict | None:
    """
    Extract a JSON object from LLM output. Handles:
    - Markdown code fences wrapping the JSON
    - Trailing text after the JSON
    - Leading text before the JSON
    - Truncated output (partial JSON)

    Uses brace-matching to find the outermost { ... } object.
    Returns the parsed dict, or None if extraction fails.
    """
    if not raw or not raw.strip():
        return None

    text = raw.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    # Fast path: try direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Brace-matching: find the outermost { ... } handling string escaping
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == '\\' and in_string:
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except (json.JSONDecodeError, ValueError):
                    return None

    # Unbalanced braces — truncated output
    return None


def _normalize_diff(raw_diff: str) -> str:
    """Normalize LLM-generated diff text into a format git apply will accept."""
    if not raw_diff:
        return raw_diff
    diff = raw_diff.replace("\r\n", "\n").replace("\r", "\n")
    lines = diff.split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    diff = "\n".join(lines).strip()

    # Fix content lines in new-file patches
    if "--- /dev/null" in diff:
        fixed_lines = []
        in_hunk = False
        for line in diff.split("\n"):
            if line.startswith("@@"):
                in_hunk = True
                fixed_lines.append(line)
            elif not in_hunk:
                fixed_lines.append(line)
            elif line.startswith(("+", "-", "\\")):
                fixed_lines.append(line)
            elif line == "":
                fixed_lines.append("+")
            elif line.startswith(" "):
                fixed_lines.append("+" + line)
            else:
                fixed_lines.append("+" + line)
        diff = "\n".join(fixed_lines)

    # Fix hunk line counts
    _hunk_re = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$')
    result_lines = diff.split("\n")
    for i, line in enumerate(result_lines):
        m = _hunk_re.match(line)
        if m:
            old_count = 0
            new_count = 0
            for j in range(i + 1, len(result_lines)):
                cl = result_lines[j]
                if cl.startswith("@@") or cl.startswith("--- ") or cl.startswith("+++ "):
                    break
                if cl.startswith("+"):
                    new_count += 1
                elif cl.startswith("-"):
                    old_count += 1
                elif cl.startswith(" "):
                    old_count += 1
                    new_count += 1
            result_lines[i] = f"@@ -{m.group(1)},{old_count} +{m.group(3)},{new_count} @@{m.group(5) or ''}"
    diff = "\n".join(result_lines)

    if diff and not diff.endswith("\n"):
        diff += "\n"
    return diff


def _validate_diff_structure(diff: str) -> tuple[bool, str]:
    """Structural validation of a unified diff."""
    if not diff or not diff.strip():
        return False, "empty_diff"
    lines = diff.strip().split("\n")
    if len(lines) < 4:
        return False, f"too_short: {len(lines)} lines"
    if not any(l.startswith("--- ") for l in lines):
        return False, "missing_minus_header"
    if not any(l.startswith("+++ ") for l in lines):
        return False, "missing_plus_header"
    if not any(l.startswith("@@ ") for l in lines):
        return False, "missing_hunk_marker"
    for i, line in enumerate(lines):
        if not line:
            continue
        if line[0] in ('+', '-', ' ', '@', '\\'):
            continue
        if line.startswith(("diff --git", "index ", "new file mode", "old mode", "new mode")):
            continue
        return False, f"text_contamination: line {i+1}: {line[:60]}"
    _hunk_pat = re.compile(r'^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@')
    for line in lines:
        if line.startswith("@@ ") and not _hunk_pat.match(line):
            return False, f"malformed_hunk: {line[:60]}"
    return True, "valid"


def _validate_patch_semantics(patch_diff: str, patch_files_json: str | None) -> tuple[bool, str]:
    """Semantic validation: check imports resolve, files exist, symbols are real."""
    if not patch_diff:
        return False, "empty_diff"
    try:
        files = json.loads(patch_files_json) if patch_files_json else []
    except (json.JSONDecodeError, ValueError):
        files = []
    for f in files:
        target_path = os.path.join(_BACKEND_DIR, f)
        is_new_file = "--- /dev/null" in patch_diff and f"+++ b/{f}" in patch_diff
        if not is_new_file and not os.path.isfile(target_path):
            return False, f"file_not_found: {f}"

    added_lines = [l[1:] for l in patch_diff.split("\n") if l.startswith("+") and not l.startswith("+++")]
    joined_source = "\n".join(added_lines)
    import_pattern = re.compile(
        r'from (app\.\S+) import \(([^)]+)\)'
        r'|from (app\.\S+) import ([^\n(]+)',
        re.DOTALL,
    )
    for m in import_pattern.finditer(joined_source):
        module = m.group(1) or m.group(3)
        names_str = m.group(2) or m.group(4)
        if not module or not names_str:
            continue
        module_path = module.replace(".", "/") + ".py"
        full_path = os.path.join(_BACKEND_DIR, module_path)
        if not os.path.isfile(full_path):
            return False, f"import_not_found: module {module}"
        imported_names = [
            n.strip().split(" as ")[0].strip()
            for n in names_str.replace("\n", ",").split(",")
            if n.strip()
        ]
        try:
            with open(full_path, "r") as fh:
                source = fh.read()
            for name in imported_names:
                if not name:
                    continue
                if (f"def {name}" not in source
                        and f"class {name}" not in source
                        and f"{name} =" not in source
                        and f"{name}:" not in source):
                    return False, f"symbol_not_found: {name} not in {module}"
        except Exception:
            pass
    return True, "valid"


# ---------------------------------------------------------------------------
# Patch proposal: LLM generates a fix suggestion
# ---------------------------------------------------------------------------

_PATCH_SYSTEM_PROMPT = """You are a senior backend engineer fixing a bug in the Hedge Spark SaaS platform.

Given the bug context (alert details, error info, affected subsystem), propose a minimal, safe fix.

RULES:
- Output a JSON object with these fields:
  - patch_summary: one paragraph explaining the fix
  - files: list of file paths relative to the backend directory (e.g. "tests/test_foo.py")
  - diff: the proposed changes as a unified diff
  - test_command: pytest command to verify the fix (e.g. "python -m pytest tests/test_foo.py -v")
- Be conservative — propose the smallest change that fixes the root cause
- Never propose changes to encryption, auth, or billing logic
- If you cannot determine the fix, return {"patch_summary": "Unable to determine fix", "files": [], "diff": "", "test_command": ""}

DIFF FORMAT (critical — malformed diffs will be rejected):
- For new files use `--- /dev/null` and `+++ b/path/to/file.py`
- Every added line MUST start with `+`
- Include proper hunk headers: `@@ -start,count +start,count @@`
- Do NOT wrap the diff in markdown code fences
- The diff MUST end with a newline character

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

    # Classify domain early (for fingerprinting and context)
    _classify_candidate_domain(candidate)

    # Fingerprint pre-check: reject if identical patch recently failed
    pre_fp = _compute_patch_fingerprint(candidate.title, candidate.patch_files)
    failed_match = _check_patch_fingerprint(db, pre_fp)
    if failed_match:
        log.info(
            "propose_patch: FINGERPRINT REJECT id=%d — matches failed candidate #%d (%s)",
            candidate.id, failed_match["candidate_id"], failed_match["outcome"],
        )
        candidate.failure_reason = (
            f"fingerprint_dedup: identical approach failed in candidate #{failed_match['candidate_id']} "
            f"({failed_match['outcome']})"
        )
        db.flush()
        return False

    # Build context
    context_parts = [f"## Bug: {candidate.title}", f"Summary: {candidate.summary}"]

    # Inject source file API for evolution proposals
    if candidate.context_json:
        try:
            _ctx = json.loads(candidate.context_json)
            target = _ctx.get("target_file")
            if target:
                _target_path = os.path.join(_BACKEND_DIR, target)
                if os.path.isfile(_target_path):
                    with open(_target_path, "r") as _f:
                        _lines = _f.readlines()
                    _api_lines: list[str] = []
                    for i, line in enumerate(_lines):
                        stripped = line.rstrip()
                        if stripped.startswith(("import ", "from ")):
                            _api_lines.append(stripped)
                        elif stripped.startswith(("def ", "async def ", "class ")):
                            _api_lines.append(stripped)
                            if i + 1 < len(_lines) and _lines[i + 1].strip().startswith(('"""', "'''")):
                                _api_lines.append("    " + _lines[i + 1].strip())
                    _module_path = target.replace("/", ".").replace(".py", "")
                    context_parts.append(
                        f"## Source File API: {target}\n"
                        f"REAL function/class signatures. Import from `{_module_path}`. "
                        f"Do NOT invent classes or functions.\n"
                        f"```python\n" + "\n".join(_api_lines) + "\n```\n"
                        f"Write 3-5 SHORT test functions (not a class). "
                        f"Use unittest.mock.Mock() for db Session parameters."
                    )
        except Exception:
            pass

    # Recurrence-aware context: if this is a follow-up from an ineffective fix,
    # tell the LLM explicitly so it tries a different approach.
    if candidate.source_type == "recurrence" and candidate.context_json:
        try:
            ctx = json.loads(candidate.context_json)
            context_parts.append(
                "## IMPORTANT — Previous Fix Was Ineffective\n"
                "A prior attempt to fix this bug did NOT resolve it. "
                "You MUST try a fundamentally different approach.\n\n"
                f"Previous patch summary: {ctx.get('previous_patch_summary', 'unknown')}\n"
                f"Previous files changed: {ctx.get('previous_files', 'unknown')}\n"
                f"Alerts before previous fix: {ctx.get('alerts_before', '?')}\n"
                f"Alerts after previous fix: {ctx.get('alerts_after', '?')} (should have decreased)\n"
                f"Original bug: {ctx.get('original_title', 'unknown')}\n"
                f"Original source: {ctx.get('original_source_type', '?')}/{ctx.get('original_source_ref', '?')}\n\n"
                "Do NOT repeat the same fix. Investigate the root cause more deeply."
            )
            # Still include remaining context
            remaining = {k: v for k, v in ctx.items()
                         if k not in ("previous_patch_summary", "previous_files",
                                      "alerts_before", "alerts_after",
                                      "original_title", "original_source_type",
                                      "original_source_ref", "previous_candidate_id",
                                      "previous_outcome")}
            if remaining:
                context_parts.append(f"Additional context: {json.dumps(remaining, indent=2)}")
        except Exception:
            context_parts.append(f"Context: {candidate.context_json[:500]}")

    elif candidate.source_type == "sentry_incident" and candidate.context_json:
        # Sentry triage packet — structured production error with parsed evidence
        try:
            pkt = json.loads(candidate.context_json)
            sentry_parts: list[str] = ["## Sentry Production Error"]

            # Error identity
            if pkt.get("error_type"):
                sentry_parts.append(f"Error type: {pkt['error_type']}")
            if pkt.get("error_title"):
                sentry_parts.append(f"Error: {pkt['error_title']}")

            # Location
            if pkt.get("culprit"):
                sentry_parts.append(f"File: {pkt['culprit']}")
            if pkt.get("subsystem") and pkt["subsystem"] != "unknown":
                sentry_parts.append(f"Subsystem: {pkt['subsystem']} (criticality: {pkt.get('criticality', '?')})")

            # Environment
            if pkt.get("environment"):
                sentry_parts.append(f"Environment: {pkt['environment']}")

            # Recurrence
            recurrence = pkt.get("recurrence_count", 1)
            if recurrence > 1:
                sentry_parts.append(f"Recurrences: {recurrence} (this error keeps happening)")
                if pkt.get("first_seen"):
                    sentry_parts.append(f"First seen: {pkt['first_seen']}")
                if pkt.get("last_seen"):
                    sentry_parts.append(f"Last seen: {pkt['last_seen']}")

            # Stack trace — the most critical evidence
            if pkt.get("stack_trace"):
                trace = pkt["stack_trace"]
                # Cap at 2000 chars to leave room for other context
                if len(trace) > 2000:
                    trace = trace[-2000:]
                sentry_parts.append(f"\n## Stack Trace\n```\n{trace}\n```")

            # Root-cause hints from parser
            hints = pkt.get("probable_root_cause_hints", [])
            if hints:
                sentry_parts.append("\n## Root-Cause Hints")
                for h in hints:
                    sentry_parts.append(f"- {h}")

            # Related lessons from system memory
            lessons = pkt.get("related_lessons", [])
            if lessons:
                sentry_parts.append("\n## Related Lessons from System Memory")
                for lesson in lessons[:3]:
                    sentry_parts.append(
                        f"- [{lesson.get('type', '?')}] {lesson.get('summary', '?')} "
                        f"(confidence: {lesson.get('confidence', '?')})"
                    )

            # Related past fix attempts
            past_candidates = pkt.get("related_bugfix_candidates", [])
            if past_candidates:
                sentry_parts.append("\n## Previous Fix Attempts")
                for pc in past_candidates[:3]:
                    sentry_parts.append(
                        f"- #{pc.get('id', '?')}: {pc.get('title', '?')} "
                        f"(status: {pc.get('status', '?')}, outcome: {pc.get('outcome', 'unknown')})"
                    )

            # Sentry link for reference
            if pkt.get("sentry_issue_url"):
                sentry_parts.append(f"\nSentry issue: {pkt['sentry_issue_url']}")

            context_parts.append("\n".join(sentry_parts))
        except Exception:
            context_parts.append(f"Context: {candidate.context_json[:500]}")

    elif candidate.context_json:
        try:
            ctx = json.loads(candidate.context_json)
            context_parts.append(f"Context: {json.dumps(ctx, indent=2)}")
        except Exception:
            context_parts.append(f"Context: {candidate.context_json[:500]}")

    # Inject domain effectiveness context — helps LLM understand what works
    try:
        domain_context = _get_domain_effectiveness_context(db)
        if domain_context:
            context_parts.append(domain_context)
    except Exception:
        pass  # non-critical enhancement

    # Inject relevant lessons from persistent memory + track which were used
    _lesson_ids_used = []
    try:
        domain = candidate.affected_domain or "unknown"
        lesson_context, _lesson_ids_used = _lookup_lessons_for_proposal(db, domain)
        if lesson_context:
            context_parts.append(lesson_context)
        if _lesson_ids_used:
            candidate.lesson_ids_used = json.dumps(_lesson_ids_used)
    except Exception:
        pass  # non-critical enhancement

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

    # Parse response — robust JSON extraction
    data = _extract_json(raw)
    if data is None:
        candidate.failure_reason = f"json_parse_error: could not extract valid JSON ({len(raw)} chars)"
        db.flush()
        return False

    candidate.patch_summary = data.get("patch_summary", "")
    raw_diff = data.get("diff", "")
    candidate.patch_files = json.dumps(data.get("files", []))
    # Normalize test_command: always use venv python, never bare "python"
    _raw_test_cmd = data.get("test_command", "")
    _venv_py = f"{_BACKEND_DIR}/venv/bin/python"
    if _raw_test_cmd.startswith("python3 "):
        candidate.test_command = f"{_venv_py} {_raw_test_cmd[8:]}"
    elif _raw_test_cmd.startswith("python "):
        candidate.test_command = f"{_venv_py} {_raw_test_cmd[7:]}"
    elif _raw_test_cmd.startswith("pytest "):
        candidate.test_command = f"{_venv_py} -m pytest {_raw_test_cmd[7:]}"
    else:
        candidate.test_command = _raw_test_cmd

    # Reject empty/whitespace-only diffs
    if not raw_diff or not raw_diff.strip():
        candidate.failure_reason = "llm_returned_empty_diff"
        db.flush()
        return False

    # Normalize + validate diff
    candidate.patch_diff = _normalize_diff(raw_diff)
    valid, reason = _validate_diff_structure(candidate.patch_diff)
    if not valid:
        candidate.failure_reason = f"diff_validation_failed: {reason}"
        db.flush()
        return False
    sem_valid, sem_reason = _validate_patch_semantics(candidate.patch_diff, candidate.patch_files)
    if not sem_valid:
        candidate.failure_reason = f"semantic_validation_failed: {sem_reason}"
        db.flush()
        return False

    # POST-LLM diff fingerprint check: now that we have the actual diff,
    # check if a semantically equivalent diff recently failed
    post_diff_fp = _compute_diff_fingerprint(candidate.patch_diff)
    if post_diff_fp:
        post_fp_hash = _compute_patch_fingerprint(candidate.title, candidate.patch_files, candidate.patch_diff)
        diff_match = _check_patch_fingerprint(db, post_fp_hash, diff_fp=post_diff_fp)
        if diff_match:
            log.info(
                "propose_patch: DIFF FINGERPRINT REJECT id=%d — semantically matches "
                "failed candidate #%d (%s, match_type=%s)",
                candidate.id, diff_match["candidate_id"], diff_match["outcome"],
                diff_match.get("match_type", "unknown"),
            )
            candidate.failure_reason = (
                f"diff_fingerprint_dedup: LLM proposed semantically identical patch to "
                f"failed candidate #{diff_match['candidate_id']} ({diff_match['outcome']})"
            )
            db.flush()
            return False

    candidate.status = "patch_proposed"

    # Classify risk tier
    tier, tier_reasons = classify_patch_risk(candidate.patch_files, candidate.patch_diff)
    candidate.patch_risk_tier = tier

    # Classify remediation type from patch metadata
    try:
        from app.services.scoring_calibration import classify_remediation
        candidate.remediation_class = classify_remediation(
            candidate.patch_files, candidate.patch_summary, candidate.patch_diff,
        )
    except Exception:
        candidate.remediation_class = "unknown"

    # Compute fix confidence — gates auto-apply at TIER_0 (with adaptive calibration)
    try:
        from app.services.candidate_scoring import compute_fix_confidence
        calibration = None
        try:
            from app.services.scoring_calibration import get_scoring_calibration
            calibration = get_scoring_calibration(db)
        except Exception:
            pass
        conf_score, conf_detail = compute_fix_confidence(
            db, candidate, calibration=calibration,
        )
        candidate.fix_confidence = conf_score
        candidate.confidence_detail = json.dumps(conf_detail, default=str)
        log.info(
            "bugfix_pipeline: confidence id=%d score=%d remediation=%s",
            candidate.id, conf_score, candidate.remediation_class,
        )
    except Exception as exc:
        log.warning("bugfix_pipeline: confidence scoring failed id=%d: %s", candidate.id, exc)

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
    """
    Make the actual API call based on model selection. Handles 429 with backoff.

    Returns raw response text, or empty string on failure.
    Rejects truncated output (max_tokens reached) before returning —
    truncated JSON is unparseable and should not propagate.
    """
    import httpx
    from app.core.llm_budget import is_provider_backed_off, record_429

    anthropic_failed = False
    if sel.provider == "anthropic" and anthropic_key:
        if is_provider_backed_off("anthropic"):
            log.info("bugfix_pipeline: Anthropic backed off (429 cooldown)")
            anthropic_failed = True
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
                    timeout=60.0,
                )
                if resp.status_code == 200:
                    body = resp.json()
                    # Reject truncated output — truncated JSON is unparseable
                    stop = body.get("stop_reason", "")
                    if stop == "max_tokens":
                        log.warning("bugfix_pipeline: Anthropic output TRUNCATED (max_tokens=%d)", sel.max_tokens)
                        anthropic_failed = True
                    else:
                        return body.get("content", [{}])[0].get("text", "")
                elif resp.status_code == 429:
                    record_429("anthropic")
                    anthropic_failed = True
                else:
                    log.warning("bugfix_pipeline: Anthropic %s returned %d", sel.model, resp.status_code)
                    anthropic_failed = True
            except Exception as exc:
                log.warning("bugfix_pipeline: Anthropic %s failed: %s", sel.model, type(exc).__name__)
                anthropic_failed = True

    if openai_key:
        if is_provider_backed_off("openai"):
            log.info("bugfix_pipeline: OpenAI backed off (429 cooldown)")
            return ""
        model = sel.model if sel.provider == "openai" else "gpt-4o-mini"
        if anthropic_failed:
            log.info("bugfix_pipeline: anthropic unavailable → fallback=openai model=%s", model)
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
                timeout=60.0,
            )
            if resp.status_code == 200:
                body = resp.json()
                choice = body.get("choices", [{}])[0]
                # Reject truncated output
                finish = choice.get("finish_reason", "")
                if finish == "length":
                    log.warning("bugfix_pipeline: OpenAI output TRUNCATED (max_tokens=%d)", sel.max_tokens)
                    return ""
                return choice.get("message", {}).get("content", "")
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


# Maximum auto-applies per calendar day. Hard safety limit.
_MAX_AUTO_APPLIES_PER_DAY = 5


def _get_adaptive_daily_cap(db: Session) -> int:
    """Get the adaptive daily auto-apply cap (bounded, evidence-aware)."""
    try:
        from app.services.adaptive_governance import get_adaptive_thresholds
        return get_adaptive_thresholds(db).max_auto_applies_per_day
    except Exception:
        return _MAX_AUTO_APPLIES_PER_DAY  # fallback to static default


def _check_daily_apply_cap(db: Session) -> bool:
    """
    Check if daily auto-apply cap has been reached.
    Uses adaptive cap when evidence is available, falls back to static default.
    Returns True if cap reached (no more applies allowed today).
    """
    cap = _get_adaptive_daily_cap(db)
    today_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    count = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE decided_by = 'auto_tier_0'
          AND applied_at >= :today
          AND status = 'applied'
    """), {"today": today_start}).fetchone()
    applied_today = count[0] if count else 0

    if applied_today >= cap:
        log.info("auto_apply: DAILY CAP reached (%d/%d adaptive) — skipping", applied_today, cap)
        return True
    return False


# ---------------------------------------------------------------------------
# Domain-level autonomy budgets
# ---------------------------------------------------------------------------

# Per-domain daily caps based on domain stability
_DOMAIN_BUDGET_DEFAULT = 2       # healthy domains: 2 auto-applies per day
_DOMAIN_BUDGET_UNSTABLE = 1      # unstable domains: 1 per day
_DOMAIN_BUDGET_QUARANTINE = 0    # quarantined domains: 0 (human-only)

# Weakness score thresholds (from loop_health.score_subsystem_weakness)
_WEAKNESS_UNSTABLE_THRESHOLD = 15
_WEAKNESS_QUARANTINE_THRESHOLD = 30


def _get_domain_budget(db: Session, domain: str) -> int:
    """
    Get the daily auto-apply budget for a domain.

    Uses per-domain adaptive profiles when available:
    - Per-domain effectiveness history
    - Per-domain operator feedback (approval/rejection rates)
    - Weakness score
    Falls back to global adaptive defaults, then to static defaults.

    Returns max allowed auto-applies per day for this domain.
    0 = quarantined (no auto-apply allowed).
    """
    if not domain or domain == "unknown":
        return _DOMAIN_BUDGET_DEFAULT

    try:
        # Try per-domain profile first (highest intelligence)
        try:
            from app.services.adaptive_governance import get_domain_profiles
            profiles = get_domain_profiles(db)
            if domain in profiles:
                return profiles[domain].budget
        except Exception:
            pass

        # Fallback: global adaptive thresholds + weakness score
        try:
            from app.services.adaptive_governance import get_adaptive_thresholds
            thresholds = get_adaptive_thresholds(db)
            budget_default = thresholds.domain_budget_default
            unstable_threshold = thresholds.weakness_unstable_threshold
            quarantine_threshold = thresholds.weakness_quarantine_threshold
        except Exception:
            budget_default = _DOMAIN_BUDGET_DEFAULT
            unstable_threshold = _WEAKNESS_UNSTABLE_THRESHOLD
            quarantine_threshold = _WEAKNESS_QUARANTINE_THRESHOLD

        from app.services.loop_health import score_subsystem_weakness
        weakness_ranking = score_subsystem_weakness(db, lookback_days=30)
        weakness_map = {w["domain"]: w["score"] for w in weakness_ranking}
        score = weakness_map.get(domain, 0)

        if score >= quarantine_threshold:
            return _DOMAIN_BUDGET_QUARANTINE
        if score >= unstable_threshold:
            return _DOMAIN_BUDGET_UNSTABLE
        return budget_default
    except Exception:
        return _DOMAIN_BUDGET_DEFAULT


def _check_domain_budget(db: Session, domain: str) -> bool:
    """
    Check if a domain's daily auto-apply budget is exhausted.
    Returns True if budget exhausted (no more applies allowed for this domain today).
    """
    if not domain or domain == "unknown":
        return False  # unknown domains fall under global cap only

    budget = _get_domain_budget(db, domain)

    if budget == 0:
        log.info("auto_apply: DOMAIN QUARANTINED domain=%s — no auto-apply allowed", domain)
        return True

    today_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    count = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE decided_by = 'auto_tier_0'
          AND applied_at >= :today
          AND status = 'applied'
          AND affected_domain = :domain
    """), {"today": today_start, "domain": domain}).fetchone()
    applied_today = count[0] if count else 0

    if applied_today >= budget:
        log.info(
            "auto_apply: DOMAIN BUDGET exhausted domain=%s (%d/%d) — skipping",
            domain, applied_today, budget,
        )
        return True
    return False


def reclassify_proposed_candidates(db: Session) -> dict:
    """Re-evaluate tier and confidence for patch_proposed candidates."""
    summary = {"reclassified": 0, "confidence_updated": 0}
    candidates = db.query(BugFixCandidate).filter(BugFixCandidate.status == "patch_proposed").all()
    for c in candidates:
        if not c.patch_files or not c.patch_diff:
            continue
        new_tier, reasons = classify_patch_risk(c.patch_files, c.patch_diff)
        if new_tier != c.patch_risk_tier:
            c.patch_risk_tier = new_tier
            summary["reclassified"] += 1
        try:
            from app.services.candidate_scoring import compute_fix_confidence
            new_conf, _ = compute_fix_confidence(db, c)
            if new_conf != c.fix_confidence:
                c.fix_confidence = new_conf
                summary["confidence_updated"] += 1
        except Exception:
            pass
    if summary["reclassified"] > 0 or summary["confidence_updated"] > 0:
        db.flush()
    return summary


def run_auto_apply(db: Session, max_per_cycle: int = 1) -> dict:
    """
    Auto-approve + auto-apply PATCH_TIER_0 candidates.
    Max 1 per cycle, max 5 per day. Stops on any failure.
    """
    import time as _time

    summary = {"attempted": 0, "applied": 0, "failed": 0, "skipped": 0}

    # Daily aggregate safety cap
    if _check_daily_apply_cap(db):
        summary["skipped"] = 1
        return summary

    candidates = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.status == "patch_proposed",
            BugFixCandidate.patch_risk_tier == PATCH_TIER_0,
        )
        .order_by(
            BugFixCandidate.priority_score.desc().nullslast(),
            BugFixCandidate.created_at,
        )
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

        # Confidence gate — TIER_0 auto-apply only if confidence >= 40
        _MIN_AUTO_APPLY_CONFIDENCE = 40
        if c.fix_confidence is not None and c.fix_confidence < _MIN_AUTO_APPLY_CONFIDENCE:
            log.info(
                "auto_apply: CONFIDENCE GATE blocked id=%d confidence=%d (min=%d)",
                c.id, c.fix_confidence, _MIN_AUTO_APPLY_CONFIDENCE,
            )
            c.patch_risk_tier = 1  # escalate to human-approve
            summary["skipped"] += 1
            db.flush()
            continue

        # Domain-level autonomy budget check
        _classify_candidate_domain(c)
        if c.affected_domain and _check_domain_budget(db, c.affected_domain):
            summary["skipped"] += 1
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

# Imported from tier_check — single source of truth for protected paths.
# Used by legacy _check_forbidden_paths (defense-in-depth behind pre_apply_guard).
try:
    from app.core.tier_check import _TIER_2_PATTERNS as _FORBIDDEN_PATH_PATTERNS
except ImportError:
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


def _tracker_version_bumped(patch_diff: str | None) -> bool:
    """Check if a patch that touches tracker JS also bumps TRACKER_VERSION."""
    if not patch_diff:
        return False
    # Look for a change to tracker_version.py in the diff
    return "tracker_version" in patch_diff.lower() and (
        "+TRACKER_VERSION" in patch_diff or "+tracker_version" in patch_diff.lower()
    )


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

    # === EXECUTION POLICY ENFORCEMENT (tier_check + file_lock) ===
    _MAX_PATCH_FILES = 8  # hard cap — patches touching >8 files are too risky for auto-apply
    _apply_files = []
    if candidate.patch_files:
        try:
            _apply_files = json.loads(candidate.patch_files)
        except (json.JSONDecodeError, ValueError):
            pass

    if len(_apply_files) > _MAX_PATCH_FILES:
        result.status = "apply_failed"
        result.failure_reason = f"hard_file_cap: patch touches {len(_apply_files)} files (max {_MAX_PATCH_FILES})"
        candidate.status = "apply_failed"
        candidate.failure_reason = result.failure_reason
        db.flush()
        log.warning("apply: BLOCKED — %d files exceeds hard cap of %d", len(_apply_files), _MAX_PATCH_FILES)
        return result

    try:
        from app.core.pre_apply_guard import guard_pre_apply, release_guard
        guard = guard_pre_apply(
            files=_apply_files,
            patch_diff=candidate.patch_diff,
            owner="bugfix_pipeline",
        )
        if guard.blocked:
            result.status = "apply_failed"
            result.failure_reason = f"guard_blocked: {guard.block_reason}"
            candidate.status = "apply_failed"
            candidate.failure_reason = result.failure_reason
            db.flush()
            _write_apply_alert(db, candidate, result)
            return result
        if not guard.allowed and candidate.decided_by == "auto_tier_0":
            # Auto-apply attempted on a patch that the guard escalated beyond TIER_0
            release_guard(_apply_files, "bugfix_pipeline")
            result.status = "apply_failed"
            result.failure_reason = f"tier_escalated: guard returned {guard.label} — auto-apply requires TIER_0"
            candidate.status = "apply_failed"
            candidate.failure_reason = result.failure_reason
            db.flush()
            _write_apply_alert(db, candidate, result)
            return result
    except ImportError:
        pass  # Fallback to legacy forbidden path check below

    # Legacy forbidden path check (retained as defense-in-depth)
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
        normalized_diff = _normalize_diff(candidate.patch_diff)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False, dir="/tmp") as f:
            f.write(normalized_diff)
            patch_path = f.name

        candidate.status = "applying"
        db.flush()

        # Git tree clean check — skip for new-file patches.
        # New files (--- /dev/null) never conflict with existing dirty files,
        # so git apply works fine on a dirty tree.
        _is_new_file_patch = "--- /dev/null" in (candidate.patch_diff or "")
        if not _is_new_file_patch:
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

        # Run tests — for new test-only files, just verify the new file can be imported
        # (doesn't break any existing code). Full regression suite is too heavyweight
        # for adding a new file that can't affect production.
        _venv_python = f"{_BACKEND_DIR}/venv/bin/python"
        _new_test_files = []
        if _is_new_file_patch:
            try:
                _flist = json.loads(candidate.patch_files) if candidate.patch_files else []
                _new_test_files = [f for f in _flist if f.startswith("tests/")]
            except (json.JSONDecodeError, ValueError):
                pass

        if _new_test_files:
            # For new test files: just verify they can be collected by pytest (syntax check)
            test_cmd = f"{_venv_python} -m pytest {' '.join(_new_test_files)} --collect-only -q"
        else:
            test_cmd = f"{_venv_python} -m pytest tests/ --ignore=tests/test_scaling_intelligence.py --ignore=tests/test_merge_intelligence.py -q"
        log.info("bugfix_apply: running tests: %s", test_cmd)
        try:
            test_run = subprocess.run(
                test_cmd.split(), cwd=_BACKEND_DIR,
                capture_output=True, text=True, timeout=300,
                env={**os.environ, "PYTHONPATH": _BACKEND_DIR},
            )
        except subprocess.TimeoutExpired:
            _rollback_patch(patch_path)
            return _fail_apply(db, candidate, result, "test_timeout: tests exceeded 300s", rolled_back=True)
        result.test_output = (test_run.stdout[-500:] + "\n" + test_run.stderr[-500:]).strip()
        result.test_passed = test_run.returncode == 0
        candidate.test_result = result.test_output[:2000]
        log.info("bugfix_apply: tests completed rc=%d passed=%s", test_run.returncode, result.test_passed)

        if not result.test_passed:
            _rollback_patch(patch_path)
            return _fail_apply(db, candidate, result, "tests_failed", rolled_back=True)

        # Frontend build verification (if guard flagged it)
        _needs_frontend = False
        _needs_tracker_bump = False
        try:
            _needs_frontend = guard.requires_frontend_build
            _needs_tracker_bump = guard.requires_tracker_bump
        except (NameError, AttributeError):
            # guard may not exist if ImportError path was taken above
            from app.core.tier_check import require_frontend_build, require_tracker_bump
            _needs_frontend = require_frontend_build(_apply_files)
            _needs_tracker_bump = require_tracker_bump(_apply_files)

        if _needs_frontend:
            log.info("bugfix_apply: running frontend build verification (dashboard files touched)")
            try:
                from app.core.pre_apply_guard import verify_frontend_build
                build_ok, build_output = verify_frontend_build()
                if not build_ok:
                    _rollback_patch(patch_path)
                    return _fail_apply(
                        db, candidate, result,
                        f"frontend_build_failed: {build_output[:300]}",
                        rolled_back=True,
                    )
            except Exception as exc:
                _rollback_patch(patch_path)
                return _fail_apply(
                    db, candidate, result,
                    f"frontend_build_error: {str(exc)[:200]}",
                    rolled_back=True,
                )

        if _needs_tracker_bump:
            # Verify TRACKER_VERSION was bumped in the patch
            if not _tracker_version_bumped(candidate.patch_diff):
                _rollback_patch(patch_path)
                return _fail_apply(
                    db, candidate, result,
                    "tracker_version_not_bumped: patch modifies tracker JS but does not bump TRACKER_VERSION",
                    rolled_back=True,
                )

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
        _classify_candidate_domain(candidate)
        db.flush()

        # Record successful patch fingerprint (outcome will be updated after 48h measurement)
        _record_patch_fingerprint(db, candidate, outcome="applied")

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
        # Release file locks acquired by pre_apply_guard
        if _apply_files:
            try:
                from app.core.pre_apply_guard import release_guard
                release_guard(_apply_files, "bugfix_pipeline")
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

    # Record failed patch fingerprint for future dedup
    _record_patch_fingerprint(db, candidate, outcome=result.status, failure_reason=reason)

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
