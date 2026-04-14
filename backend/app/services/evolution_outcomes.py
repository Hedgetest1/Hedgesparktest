"""
evolution_outcomes.py — Closed-loop outcome measurement for bugfixes.

Measures whether applied bugfixes actually resolved the issues they targeted.
Called from agent_worker on every cycle. Only evaluates candidates that are
48+ hours past applied_at with outcome_status still NULL.

Public interface:
    evaluate_bugfix_outcomes(db) -> dict   — measure pending outcomes
    get_effectiveness_stats(db) -> dict    — aggregated stats for Opus context
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text, func
from sqlalchemy.orm import Session

from app.models.bugfix_candidate import BugFixCandidate

log = logging.getLogger("evolution_outcomes")

_MEASUREMENT_DELAY_HOURS = 48
_MEASUREMENT_WINDOW_HOURS = 48  # compare 48h before vs 48h after


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def evaluate_bugfix_outcomes(db: Session) -> dict:
    """
    Find applied candidates that are 48+ hours old and haven't been measured.
    For each, count ops_alerts in the 48h before apply vs 48h after.
    """
    summary = {"evaluated": 0, "effective": 0, "ineffective": 0, "inconclusive": 0}

    cutoff = _now() - timedelta(hours=_MEASUREMENT_DELAY_HOURS)

    candidates = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.status == "applied",
            BugFixCandidate.applied_at.isnot(None),
            BugFixCandidate.applied_at <= cutoff,
            BugFixCandidate.outcome_status.is_(None),
        )
        .limit(10)
        .all()
    )

    for c in candidates:
        try:
            # Label evidence source on candidate before generating lessons
            from app.services.learning_isolation import label_candidate
            label_candidate(db, c)

            outcome, evidence = _measure_single(db, c)
            c.outcome_status = outcome
            c.outcome_measured_at = _now()
            c.outcome_evidence = json.dumps(evidence, default=str)
            summary["evaluated"] += 1
            summary[outcome] = summary.get(outcome, 0) + 1
            log.info("evolution_outcome: candidate=%d outcome=%s evidence_source=%s", c.id, outcome, c.evidence_source)

            # Generate persistent lesson from outcome
            _generate_lesson(db, c, outcome, evidence)

            # Update patch fingerprint with measured outcome
            _update_fingerprint_outcome(db, c.id, outcome)

            # Update lesson effectiveness based on outcome
            _update_lesson_effectiveness(db, c, outcome)

            # Propagate outcome to linked support incidents
            _propagate_outcome_to_incidents(db, c, outcome)
        except Exception as exc:
            log.warning("evolution_outcome: failed candidate=%d: %s", c.id, exc)

    if summary["evaluated"] > 0:
        db.flush()
        log.info(
            "evolution_outcomes: evaluated=%d effective=%d ineffective=%d inconclusive=%d",
            summary["evaluated"], summary["effective"], summary["ineffective"], summary["inconclusive"],
        )

    return summary


def _measure_single(db: Session, candidate: BugFixCandidate) -> tuple[str, dict]:
    """
    Measure whether a bugfix was effective by comparing alert/error counts
    before and after the apply.

    Returns (outcome_status, evidence_dict).

    2026-04-11 fix for the "missing_tests false negative" bug:
    Evolution-sourced candidates (e.g. adding a test file, reducing a
    large file, clearing TODOs) are NOT measurable via system-wide
    alert counts. Audit showed two applied test-coverage fixes being
    marked 'ineffective' only because the system was noisy in the 48h
    measurement window — the test file WAS successfully created, but
    alerts_before=192 < alerts_after=409 (unrelated noise). We now
    use a goal-scoped evaluator for evolution candidates, and only
    fall back to the alert-counting heuristic when a clear source
    alert_type exists.
    """
    applied = candidate.applied_at
    window = timedelta(hours=_MEASUREMENT_WINDOW_HOURS)

    before_start = applied - window
    before_end = applied
    after_start = applied
    after_end = applied + window

    # Determine scoping: filter measurements to RELATED alerts/errors only,
    # not the entire system. This prevents unrelated improvements from
    # contaminating effectiveness measurement.
    alert_type_filter = _extract_alert_type(db, candidate)
    worker_filter = _extract_worker_name(candidate)

    # --- Evolution-sourced candidates: goal-scoped evaluation ---
    # A candidate like "add missing test file" cannot be judged by
    # counting system-wide ops_alerts. Judge it by whether the goal
    # was achieved: the patch_files now exist on disk and the test
    # file is valid. If we cannot make a direct goal observation,
    # we return "inconclusive" rather than a false negative.
    if candidate.source_type == "evolution":
        goal_outcome, goal_evidence = _measure_evolution_goal(db, candidate)
        if goal_outcome is not None:
            return goal_outcome, goal_evidence

    # Count SCOPED ops_alerts in each window
    alerts_before = _count_alerts(db, before_start, before_end, alert_type_filter)
    alerts_after = _count_alerts(db, after_start, after_end, alert_type_filter)

    # Count SCOPED worker_log errors in each window
    errors_before = _count_worker_errors(db, before_start, before_end, worker_filter)
    errors_after = _count_worker_errors(db, after_start, after_end, worker_filter)

    evidence = {
        "alerts_before": alerts_before,
        "alerts_after": alerts_after,
        "errors_before": errors_before,
        "errors_after": errors_after,
        "window_hours": _MEASUREMENT_WINDOW_HOURS,
        "scoped_to_alert_type": alert_type_filter,
        "scoped_to_worker": worker_filter,
    }

    # Honesty gate: if the candidate has NO alert_type and NO worker
    # scoping, comparing system-wide counts is meaningless. Return
    # 'inconclusive' explicitly instead of false-negative 'ineffective'.
    if not alert_type_filter and not worker_filter:
        evidence["note"] = "unscoped_measurement_returns_inconclusive"
        return "inconclusive", evidence

    # Classification logic:
    # - If alerts dropped >50% AND no increase in errors → effective
    # - If alerts increased or errors increased significantly → ineffective
    # - Otherwise → inconclusive
    if alerts_before == 0 and alerts_after == 0:
        # No alerts in either window — check errors as secondary signal
        if errors_before > 0 and errors_after < errors_before * 0.5:
            return "effective", evidence
        return "inconclusive", evidence

    if alerts_before > 0 and alerts_after <= alerts_before * 0.5:
        return "effective", evidence

    if alerts_after > alerts_before:
        return "ineffective", evidence

    return "inconclusive", evidence


def _measure_evolution_goal(
    db: Session, candidate: BugFixCandidate,
) -> tuple[str | None, dict]:
    """
    Goal-scoped evaluator for evolution-sourced candidates.

    Returns (outcome, evidence) where outcome is one of:
      - "effective": the proposal's explicit goal was achieved
      - "inconclusive": we can observe partial signal but not decisive
      - None: no goal-scoped measurement available, caller should fall back

    Why this exists: evolution proposals target specific, observable
    code-level goals (add test file, reduce line count, remove TODOs).
    System-wide alert counts are the wrong yardstick. Judge the actual
    goal.
    """
    try:
        ctx = json.loads(candidate.context_json or "{}")
    except (ValueError, TypeError):
        ctx = {}

    proposal_type = (ctx.get("proposal_type") or "").lower()
    target_file = ctx.get("target_file") or ""
    patch_files_raw = candidate.patch_files or "[]"
    try:
        patch_files = json.loads(patch_files_raw)
    except (ValueError, TypeError):
        patch_files = []

    evidence = {
        "method": "goal_scoped",
        "proposal_type": proposal_type,
        "target_file": target_file,
        "patch_files": patch_files,
    }

    import os
    backend_dir = "/opt/wishspark/backend"

    # Case 1: missing_tests — effective if the test file now exists
    if "missing_test" in proposal_type or "test" in (candidate.title or "").lower():
        if patch_files:
            test_path = patch_files[0]
            full_path = os.path.join(backend_dir, test_path)
            if os.path.isfile(full_path):
                try:
                    size = os.path.getsize(full_path)
                except Exception:
                    size = 0
                evidence["goal"] = "test_file_exists"
                evidence["file_size_bytes"] = size
                if size > 100:  # non-empty test file
                    return "effective", evidence
                evidence["note"] = "test_file_exists_but_empty"
                return "inconclusive", evidence
            evidence["goal"] = "test_file_missing_post_apply"
            return "inconclusive", evidence
        return None, evidence

    # Case 2: large_file refactor — check if target_file shrunk
    if "large_file" in proposal_type or "refactor" in proposal_type:
        if target_file:
            full_path = os.path.join(backend_dir, target_file)
            if os.path.isfile(full_path):
                try:
                    with open(full_path) as f:
                        lines = sum(1 for _ in f)
                    evidence["goal"] = "file_line_count"
                    evidence["current_lines"] = lines
                    # Without a baseline we can't be sure, so inconclusive
                    return "inconclusive", evidence
                except Exception:
                    pass
        return None, evidence

    # Case 3: TODO/FIXME cleanup — check remaining markers
    if "todo" in proposal_type or "fixme" in proposal_type:
        if target_file:
            full_path = os.path.join(backend_dir, target_file)
            if os.path.isfile(full_path):
                try:
                    with open(full_path) as f:
                        content = f.read()
                    remaining = content.upper().count("TODO") + content.upper().count("FIXME")
                    evidence["goal"] = "todo_fixme_count"
                    evidence["remaining_markers"] = remaining
                    if remaining == 0:
                        return "effective", evidence
                    return "inconclusive", evidence
                except Exception:
                    pass
        return None, evidence

    # Unknown evolution type — let the caller fall back to alert-counting
    return None, evidence


def _extract_alert_type(db: Session, candidate: BugFixCandidate) -> str | None:
    """
    Extract the original alert_type from a bugfix candidate's source context.
    Used to scope outcome measurement to related alerts only.
    """
    if candidate.source_type == "ops_alert" and candidate.source_ref:
        # source_ref is "alert_{id}" or "worker_{name}"
        try:
            if candidate.source_ref.startswith("alert_"):
                alert_id = int(candidate.source_ref.split("_", 1)[1])
                row = db.execute(
                    text("SELECT alert_type FROM ops_alerts WHERE id = :id"),
                    {"id": alert_id},
                ).fetchone()
                if row:
                    return row[0]
            elif candidate.source_ref.startswith("worker_"):
                return "worker_repeated_failure"
        except Exception:
            pass
    elif candidate.source_type == "recurrence" and candidate.context_json:
        try:
            ctx = json.loads(candidate.context_json)
            original_ref = ctx.get("original_source_ref", "")
            if original_ref.startswith("alert_"):
                alert_id = int(original_ref.split("_", 1)[1])
                row = db.execute(
                    text("SELECT alert_type FROM ops_alerts WHERE id = :id"),
                    {"id": alert_id},
                ).fetchone()
                if row:
                    return row[0]
        except Exception:
            pass
    return None  # unscoped fallback


def _extract_worker_name(candidate: BugFixCandidate) -> str | None:
    """Extract worker name from candidate context for scoped error counting."""
    if candidate.source_ref and candidate.source_ref.startswith("worker_"):
        return candidate.source_ref.replace("worker_", "", 1)
    if candidate.context_json:
        try:
            ctx = json.loads(candidate.context_json)
            return ctx.get("worker")
        except Exception:
            pass
    return None


def _count_alerts(
    db: Session, start: datetime, end: datetime,
    alert_type_filter: str | None,
) -> int:
    """
    Count ops_alerts in a time window, scoped to a specific alert_type when available.
    Falls back to total count when no filter is provided (preserves behavior for
    candidates without clear alert_type lineage).
    """
    try:
        if alert_type_filter:
            q = text("""
                SELECT COUNT(*) FROM ops_alerts
                WHERE created_at >= :start AND created_at < :end
                  AND alert_type = :alert_type
            """)
            row = db.execute(q, {"start": start, "end": end, "alert_type": alert_type_filter}).fetchone()
        else:
            q = text("""
                SELECT COUNT(*) FROM ops_alerts
                WHERE created_at >= :start AND created_at < :end
            """)
            row = db.execute(q, {"start": start, "end": end}).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _count_worker_errors(
    db: Session, start: datetime, end: datetime,
    worker_filter: str | None = None,
) -> int:
    """
    Count worker_log errors in a time window, scoped to a specific worker when available.
    """
    try:
        if worker_filter:
            row = db.execute(
                text("""
                    SELECT COALESCE(SUM(errors), 0) FROM worker_log
                    WHERE started_at >= :start AND started_at < :end
                      AND worker_name = :worker
                """),
                {"start": start, "end": end, "worker": worker_filter},
            ).fetchone()
        else:
            row = db.execute(
                text("""
                    SELECT COALESCE(SUM(errors), 0) FROM worker_log
                    WHERE started_at >= :start AND started_at < :end
                """),
                {"start": start, "end": end},
            ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _generate_lesson(db: Session, candidate: BugFixCandidate, outcome: str, evidence: dict) -> None:
    """
    Generate a persistent lesson from a measured bugfix outcome.
    Only generates for definitive outcomes (effective/ineffective), not inconclusive.
    """
    if outcome not in ("effective", "ineffective"):
        return

    try:
        from app.models.system_lesson import SystemLesson

        # Determine domain
        domain = getattr(candidate, "affected_domain", None) or "unknown"
        if domain == "unknown" and candidate.patch_files:
            try:
                from app.services.project_brain import classify_file
                files = json.loads(candidate.patch_files)
                if files:
                    domain = classify_file(files[0]).get("domain", "unknown")
                    candidate.affected_domain = domain
            except Exception:
                pass

        # Build lesson
        if outcome == "effective":
            lesson_type = "effective_pattern"
            summary = (
                f"Fix for '{candidate.title[:100]}' was effective in domain '{domain}'. "
                f"Alerts dropped from {evidence.get('alerts_before', '?')} to {evidence.get('alerts_after', '?')}."
            )
        else:
            lesson_type = "ineffective_pattern"
            summary = (
                f"Fix for '{candidate.title[:100]}' was INEFFECTIVE in domain '{domain}'. "
                f"Alerts: {evidence.get('alerts_before', '?')} → {evidence.get('alerts_after', '?')}. "
                f"Avoid similar approach."
            )

        dedup_key = f"{domain}:{candidate.source_type}:{candidate.source_ref}:{outcome}"

        # Check dedup
        existing = db.query(SystemLesson).filter(SystemLesson.dedup_key == dedup_key).first()
        if existing:
            # Reinforce existing lesson
            existing.evidence_count += 1
            existing.last_reinforced_at = _now()
            existing.confidence = min(1.0, existing.confidence + 0.1)
            return

        # Propagate evidence source from candidate to lesson
        from app.services.learning_isolation import label_lesson
        lesson = SystemLesson(
            domain=domain,
            lesson_type=lesson_type,
            summary=summary,
            detail_json=json.dumps({
                "candidate_id": candidate.id,
                "title": candidate.title,
                "files": candidate.patch_files,
                "patch_summary": (candidate.patch_summary or "")[:300],
                "evidence": evidence,
            }, default=str),
            source_candidate_id=candidate.id,
            source_type=candidate.source_type,
            dedup_key=dedup_key,
        )
        label_lesson(db, lesson, candidate)
        db.add(lesson)
        log.info("lesson: generated %s lesson for domain=%s candidate=%d evidence_source=%s", lesson_type, domain, candidate.id, lesson.evidence_source)
    except Exception as exc:
        log.debug("lesson: generation failed (non-fatal): %s", exc)


def _update_fingerprint_outcome(db: Session, candidate_id: int, measured_outcome: str) -> None:
    """Update patch fingerprint with measured outcome (effective/ineffective/inconclusive)."""
    try:
        from app.models.patch_fingerprint import PatchFingerprint
        fps = (
            db.query(PatchFingerprint)
            .filter(PatchFingerprint.bugfix_candidate_id == candidate_id)
            .all()
        )
        for fp in fps:
            fp.measured_outcome = measured_outcome
            if measured_outcome == "ineffective":
                fp.confidence = max(0.0, fp.confidence - 0.3)
    except Exception as exc:
        log.debug("fingerprint: outcome update failed (non-fatal): %s", exc)


def _update_lesson_effectiveness(db: Session, candidate: BugFixCandidate, outcome: str) -> None:
    """
    Update lesson confidence based on whether lesson-assisted proposals succeed.

    If a candidate used lessons and succeeded → reinforce those lessons (+0.05 confidence).
    If a candidate used lessons and failed → decay those lessons (-0.05 confidence).
    This provides observational evidence that lessons actually improve outcomes.
    """
    lesson_ids_json = getattr(candidate, "lesson_ids_used", None)
    if not lesson_ids_json:
        return

    try:
        lesson_ids = json.loads(lesson_ids_json)
        if not isinstance(lesson_ids, list) or not lesson_ids:
            return

        from app.models.system_lesson import SystemLesson

        lessons = (
            db.query(SystemLesson)
            .filter(
                SystemLesson.id.in_(lesson_ids),
                SystemLesson.status == "active",
            )
            .all()
        )

        for lesson in lessons:
            if outcome == "effective":
                # Lesson-assisted proposal succeeded → reinforce
                lesson.confidence = min(1.0, lesson.confidence + 0.05)
                lesson.evidence_count += 1
                lesson.last_reinforced_at = _now()
                log.debug(
                    "lesson_effectiveness: REINFORCED lesson #%d (outcome=effective, conf=%.2f)",
                    lesson.id, lesson.confidence,
                )
            elif outcome == "ineffective":
                # Lesson-assisted proposal failed → decay (lesson didn't help)
                lesson.confidence = max(0.0, lesson.confidence - 0.05)
                log.debug(
                    "lesson_effectiveness: DECAYED lesson #%d (outcome=ineffective, conf=%.2f)",
                    lesson.id, lesson.confidence,
                )
            # inconclusive: no change — insufficient evidence either way

    except Exception as exc:
        log.debug("lesson_effectiveness: update failed (non-fatal): %s", exc)


def detect_self_caused_regressions(db: Session) -> dict:
    """
    Detect when an auto-applied fix may have CAUSED new problems.

    Checks: for each recently applied auto-fix, are there NEW alert types
    that appeared within 2 hours of apply that didn't exist in the 48h before?
    These are candidate self-caused regressions.

    Returns summary dict.
    """
    summary = {"checked": 0, "flagged": 0}

    # Check auto-applied fixes from the last 72 hours
    cutoff = _now() - timedelta(hours=72)
    recent_applies = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.status == "applied",
            BugFixCandidate.decided_by == "auto_tier_0",
            BugFixCandidate.applied_at >= cutoff,
            BugFixCandidate.applied_at.isnot(None),
        )
        .all()
    )

    for c in recent_applies:
        summary["checked"] += 1

        # Combined window query — fetches every alert_type touching the
        # 48h-before / 2h-after window in ONE round-trip, then buckets
        # them in Python. Previously this was two separate fetchall()
        # calls per candidate (2x N queries on a hot loop). With N=50
        # auto_tier_0 applies that's 100 round-trips → 50.
        rows = db.execute(text("""
            SELECT alert_type, created_at
            FROM ops_alerts
            WHERE created_at >= :before_start
              AND created_at <  :after_end
        """), {
            "before_start": c.applied_at - timedelta(hours=48),
            "after_end":    c.applied_at + timedelta(hours=2),
        }).fetchall()
        before_set: set[str] = set()
        after_set: set[str] = set()
        for at, created in rows:
            if created < c.applied_at:
                before_set.add(at)
            else:
                after_set.add(at)

        # NEW alert types = types that appeared after but not before
        new_types = after_set - before_set
        if new_types:
            summary["flagged"] += 1
            log.warning(
                "self_regression: candidate=%d may have caused new alert types: %s "
                "(applied_at=%s, files=%s)",
                c.id, list(new_types), c.applied_at, c.patch_files,
            )
            # Write ops_alert for operator visibility
            try:
                from app.services.alerting import write_alert
                write_alert(
                    db, severity="warning", source="evolution_outcomes",
                    alert_type="possible_self_regression",
                    summary=f"Auto-fix #{c.id} may have caused new alert types: {', '.join(new_types)}",
                    detail={
                        "candidate_id": c.id,
                        "candidate_title": c.title,
                        "new_alert_types": list(new_types),
                        "files_changed": c.patch_files,
                        "applied_at": str(c.applied_at),
                    },
                )
            except Exception:
                pass

    if summary["flagged"] > 0:
        db.flush()
        log.info("self_regression: checked=%d flagged=%d", summary["checked"], summary["flagged"])

    return summary


def get_effectiveness_stats(db: Session, days: int = 90, *, product_only: bool = False) -> dict:
    """
    Aggregate bugfix outcome stats for evolution engine and Opus context.
    Returns stats grouped by source_type.

    If product_only=True, only includes real_merchant evidence. Used by
    monthly Opus audit to prevent pre-merchant data from influencing
    strategic reasoning.
    """
    cutoff = _now() - timedelta(days=days)

    try:
        source_filter = ""
        if product_only:
            source_filter = "AND evidence_source = 'real_merchant'"
        rows = db.execute(text(f"""
            SELECT
                source_type,
                outcome_status,
                COUNT(*) AS cnt
            FROM bugfix_candidates
            WHERE status = 'applied'
              AND outcome_status IS NOT NULL
              AND outcome_measured_at >= :cutoff
              {source_filter}
            GROUP BY source_type, outcome_status
            ORDER BY source_type, outcome_status
        """), {"cutoff": cutoff}).fetchall()
    except Exception:
        return {"total_measured": 0, "by_source": {}}

    by_source: dict[str, dict] = {}
    total = 0
    for r in rows:
        src = r[0]
        status = r[1]
        cnt = r[2]
        if src not in by_source:
            by_source[src] = {"effective": 0, "ineffective": 0, "inconclusive": 0, "total": 0}
        by_source[src][status] = by_source[src].get(status, 0) + cnt
        by_source[src]["total"] += cnt
        total += cnt

    return {"total_measured": total, "by_source": by_source}


# ---------------------------------------------------------------------------
# Propagate outcome to linked support incidents
# ---------------------------------------------------------------------------

def _propagate_outcome_to_incidents(db: Session, candidate: BugFixCandidate, outcome: str):
    """
    After bugfix outcome is measured, update linked support incidents.

    If effective:
        → status = "resolved", resolution_verified = True, fix_outcome = "effective"
        → set resolution_summary (this is what the merchant sees in chat)
    If ineffective:
        → status = "fix_failed", resolution_verified = False, fix_outcome = "ineffective"
        → do NOT set resolution_summary (merchant should not be told it's fixed)
    If inconclusive:
        → fix_outcome = "inconclusive", no status change, no message
    """
    from app.models.support_incident import SupportIncident

    incidents = (
        db.query(SupportIncident)
        .filter(
            SupportIncident.linked_bugfix_candidate_id == candidate.id,
            SupportIncident.status.in_(["fix_applied", "investigating"]),
        )
        .all()
    )

    if incidents:
        for inc in incidents:
            inc.fix_outcome = outcome

            if outcome == "effective":
                inc.status = "resolved"
                inc.resolution_verified = True
                inc.resolution_summary = _build_specific_resolution(candidate, inc)
                log.info(
                    "outcome_propagation: incident=%d RESOLVED (fix verified effective, candidate=%d)",
                    inc.id, candidate.id,
                )
            elif outcome == "ineffective":
                inc.status = "fix_failed"
                inc.resolution_verified = False
                # Do NOT set resolution_summary — merchant should not receive a "fixed" message
                log.info(
                    "outcome_propagation: incident=%d FIX FAILED (candidate=%d ineffective)",
                    inc.id, candidate.id,
                )
            else:
                # inconclusive — leave status as-is, record outcome only
                log.info(
                    "outcome_propagation: incident=%d inconclusive (candidate=%d)",
                    inc.id, candidate.id,
                )

        db.flush()

    # --- Merchant visibility bridge for Sentry-originated fixes ---
    # If this is a Sentry-originated fix that was effective AND affected a specific shop,
    # create a proactive support incident so the merchant sees the fix notification.
    if outcome == "effective" and candidate.source_type == "sentry_incident":
        _bridge_sentry_fix_to_merchant(db, candidate)


def _build_specific_resolution(candidate: BugFixCandidate, incident=None) -> str:
    """
    Build a specific, merchant-friendly resolution message from candidate data.
    Uses title, affected_domain, and patch_summary to tell the merchant
    WHAT was broken and WHAT was fixed — not a generic "we applied a fix".
    """
    title = candidate.title or ""
    domain = candidate.affected_domain or ""
    summary = (candidate.patch_summary or "")[:200]

    # Map affected domains to merchant-friendly descriptions
    _DOMAIN_DESCRIPTIONS = {
        "tracking": "visitor tracking",
        "webhooks": "Shopify data sync",
        "shopify_auth": "Shopify connection",
        "billing": "billing",
        "nudges": "on-site nudges",
        "intelligence": "store intelligence",
        "merchant_api": "dashboard data",
        "support": "support system",
        "frontend": "dashboard display",
    }

    area_desc = _DOMAIN_DESCRIPTIONS.get(domain, domain or "your store")

    # Build specific message
    parts = []

    # What was broken
    if "tracker" in title.lower() or domain == "tracking":
        parts.append("An issue with your visitor tracking has been identified and fixed.")
    elif "webhook" in title.lower() or domain == "webhooks":
        parts.append("An issue with your Shopify data sync (webhooks) has been fixed.")
    elif "auth" in title.lower() or domain == "shopify_auth":
        parts.append("A connection issue between your store and HedgeSpark has been resolved.")
    elif title:
        # Use the candidate title directly (it's already descriptive)
        clean_title = title.split("]")[-1].strip() if "]" in title else title
        parts.append(f"An issue was found and fixed: {clean_title}.")
    else:
        parts.append(f"An issue affecting {area_desc} has been identified and fixed.")

    # What to expect
    if domain in ("tracking", "webhooks"):
        parts.append("Data should start flowing correctly within the next hour.")
    elif domain == "frontend":
        parts.append("Refresh the page to see the fix.")
    else:
        parts.append("The fix has been verified and is now live.")

    # Follow-up invitation
    parts.append("If anything still feels off, reply here and I\u2019ll dig deeper.")

    return " ".join(parts)


def _bridge_sentry_fix_to_merchant(db: Session, candidate: BugFixCandidate):
    """
    Merchant visibility bridge — close the loop between Sentry-detected
    errors and merchant experience.

    When a Sentry-originated fix is verified effective:
      1. Find the affected shop(s) from linked SentryIncidents
      2. For each shop with no existing support incident for this fix:
         → Create a SupportIncident with resolution message
         → Merchant sees the fix notification in chat

    Guards:
      - Dedup: one notification per shop per candidate (prevents spam)
      - Only for merchant-impacting errors (high/medium)
      - Only for effective fixes (already checked by caller)
    """
    from app.models.support_incident import SupportIncident

    try:
        from app.models.sentry_incident import SentryIncident

        # Find all unique shop domains from linked sentry incidents
        linked_incidents = (
            db.query(SentryIncident.affected_shop, SentryIncident.merchant_impact, SentryIncident.error_title)
            .filter(
                SentryIncident.linked_bugfix_candidate_id == candidate.id,
                SentryIncident.affected_shop.isnot(None),
                SentryIncident.affected_shop != "",
            )
            .distinct(SentryIncident.affected_shop)
            .all()
        )

        if not linked_incidents:
            return

        for inc in linked_incidents:
            shop = inc.affected_shop
            impact = inc.merchant_impact
            error_title = inc.error_title

            # Only notify for merchant-impacting errors
            if impact not in ("high", "medium"):
                continue

            # Dedup: check if we already created a support incident for this shop + candidate
            existing = (
                db.query(SupportIncident.id)
                .filter(
                    SupportIncident.shop_domain == shop,
                    SupportIncident.linked_bugfix_candidate_id == candidate.id,
                )
                .first()
            )
            if existing:
                continue

            # Create proactive support incident
            support_inc = SupportIncident(
                shop_domain=shop,
                source="sentry_bridge",
                original_message=f"[Auto-detected] {error_title or candidate.title}",
                classification="bug_report",
                severity="low",  # already fixed, informational
                affected_area=candidate.affected_domain,
                status="resolved",
                linked_bugfix_candidate_id=candidate.id,
                resolution_verified=True,
                fix_outcome="effective",
                resolution_summary=_build_specific_resolution(candidate),
            )
            db.add(support_inc)
            log.info(
                "merchant_bridge: created proactive support incident for shop=%s candidate=%d",
                shop, candidate.id,
            )

        db.flush()

    except Exception as exc:
        log.warning("merchant_bridge: failed for candidate=%d: %s", candidate.id, exc)
