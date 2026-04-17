"""
loop_health.py — Autonomous loop health diagnostics and circuit breakers.

Monitors the full autonomous maintenance wheel:
    detection → diagnosis → proposal → review → apply → verify → promote → learn

Detects:
    - Bug recurrence (same source_ref producing repeated candidates)
    - Thrashing (repeated apply_failed / ineffective for same source)
    - Queue starvation (stages with growing backlogs and no throughput)
    - Stuck items (candidates/proposals in non-terminal status too long)

Public interface:
    get_loop_health(db) -> dict              — full pipeline health snapshot (includes weakness ranking)
    score_subsystem_weakness(db) -> list     — ranked subsystem weakness scores
    check_recurrence(db) -> list[dict]       — bugs that keep coming back
    check_thrashing(db) -> list[dict]        — sources that keep failing
    is_source_thrashing(db, ...) -> bool     — check single source thrash status
    reopen_from_ineffective(db) -> dict      — create new candidates from ineffective outcomes
    auto_resolve_thrash_alerts(db) -> dict   — resolve chronic_thrashing alerts for stabilized sources
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text, func
from sqlalchemy.orm import Session

from app.models.bugfix_candidate import BugFixCandidate

log = logging.getLogger("loop_health")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# A source that has produced this many failed/ineffective candidates is "thrashing"
_THRASH_THRESHOLD = 3

# Lookback window for thrash detection
_THRASH_LOOKBACK_DAYS = 30

# Max age for "stuck" items (hours)
_STUCK_HOURS = {
    "open": 72,           # open for 3 days with no proposal attempt
    "analyzed": 48,       # analyzed but no patch proposed in 2 days
    "patch_proposed": 168, # proposed but not approved/rejected in 7 days
    "approved": 24,       # approved but not applied in 1 day
    "applying": 0.17,     # applying for > 10 min (covered by _recover_stuck but worth flagging)
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Bug recurrence detection
# ---------------------------------------------------------------------------

def check_recurrence(db: Session, lookback_days: int = 60) -> list[dict]:
    """
    Find source_type+source_ref combinations that have produced 2+ candidates,
    where the latest outcome was ineffective or inconclusive.

    These are bugs that the system tried to fix but the fix didn't work,
    and the same alert pattern reappeared.
    """
    cutoff = _now() - timedelta(days=lookback_days)

    rows = db.execute(text("""
        SELECT source_type, source_ref,
               COUNT(*) AS total_candidates,
               COUNT(CASE WHEN outcome_status = 'ineffective' THEN 1 END) AS ineffective_count,
               COUNT(CASE WHEN outcome_status = 'effective' THEN 1 END) AS effective_count,
               COUNT(CASE WHEN status IN ('apply_failed', 'rolled_back') THEN 1 END) AS failed_count,
               MAX(created_at) AS latest_created
        FROM bugfix_candidates
        WHERE created_at >= :cutoff
          AND source_ref IS NOT NULL
          AND outcome_status IS NOT NULL
        GROUP BY source_type, source_ref
        HAVING COUNT(*) >= 2
          AND COUNT(CASE WHEN outcome_status = 'ineffective' THEN 1 END) > 0
        ORDER BY COUNT(*) DESC
        LIMIT 20
    """), {"cutoff": cutoff}).fetchall()

    recurrences = []
    for r in rows:
        # Skip if the latest fix was effective (bug is resolved)
        if r[4] > 0 and r[4] >= r[3]:
            continue
        recurrences.append({
            "source_type": r[0],
            "source_ref": r[1],
            "total_candidates": r[2],
            "ineffective_fixes": r[3],
            "effective_fixes": r[4],
            "failed_applies": r[5],
            "latest_created": r[6].isoformat() if r[6] else None,
            "status": "thrashing" if (r[3] + r[5]) >= _THRASH_THRESHOLD else "recurring",
        })

    return recurrences


# ---------------------------------------------------------------------------
# Thrash detection and suppression
# ---------------------------------------------------------------------------

def check_thrashing(db: Session) -> list[dict]:
    """
    Find sources that have hit the thrash threshold:
    3+ candidates that ended in apply_failed, rolled_back, or ineffective.
    These sources should be suppressed from auto-triage.
    """
    cutoff = _now() - timedelta(days=_THRASH_LOOKBACK_DAYS)

    rows = db.execute(text("""
        SELECT source_type, source_ref,
               COUNT(*) AS failure_count,
               MAX(created_at) AS latest
        FROM bugfix_candidates
        WHERE created_at >= :cutoff
          AND source_ref IS NOT NULL
          AND (status IN ('apply_failed', 'rolled_back')
               OR outcome_status = 'ineffective')
        GROUP BY source_type, source_ref
        HAVING COUNT(*) >= :threshold
        ORDER BY COUNT(*) DESC
        LIMIT 20
    """), {"cutoff": cutoff, "threshold": _THRASH_THRESHOLD}).fetchall()

    return [
        {
            "source_type": r[0],
            "source_ref": r[1],
            "failure_count": r[2],
            "latest": r[3].isoformat() if r[3] else None,
        }
        for r in rows
    ]


def is_source_thrashing(db: Session, source_type: str, source_ref: str) -> bool:
    """
    Check if a specific source has hit the thrash threshold.
    Called by bugfix_pipeline.run_bug_triage to skip thrashing sources.

    Binary gate kept for backwards compatibility — new code should prefer
    thrash_score() which returns a graduated [0.0, 1.0] signal.
    """
    cutoff = _now() - timedelta(days=_THRASH_LOOKBACK_DAYS)

    row = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE source_type = :stype AND source_ref = :sref
          AND created_at >= :cutoff
          AND (status IN ('apply_failed', 'rolled_back')
               OR outcome_status = 'ineffective')
    """), {"stype": source_type, "sref": source_ref, "cutoff": cutoff}).fetchone()

    return (row[0] if row else 0) >= _THRASH_THRESHOLD


def thrash_score(db: Session, source_type: str, source_ref: str) -> float:
    """
    Return a graduated thrash score in [0.0, 1.0] for the given source.

    The previous binary `is_source_thrashing` was overly aggressive: one hit
    to the threshold and a source was locked out forever, hiding real new
    bugs on historically-flaky services. The graduated score lets the caller
    make smarter decisions:

        score = 0.0  → clean. Proceed normally.
        0.0–0.5      → mild history. Proceed but the triage pipeline should
                       annotate new proposals with prior-failure context so
                       the LLM attempts a different approach.
        0.5–1.0      → heavy thrash. Skip triage, escalate to operator.
        1.0          → same as binary is_source_thrashing == True.

    The underlying mapping is linear: score = min(1.0, failures / (2 * threshold)).
    At `threshold` failures (the old binary cutoff) score = 0.5 — meaning
    we *start* suppressing only after **double** the previous threshold,
    while still conveying warning signals below that.
    """
    cutoff = _now() - timedelta(days=_THRASH_LOOKBACK_DAYS)

    row = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE source_type = :stype AND source_ref = :sref
          AND created_at >= :cutoff
          AND (status IN ('apply_failed', 'rolled_back')
               OR outcome_status = 'ineffective')
    """), {"stype": source_type, "sref": source_ref, "cutoff": cutoff}).fetchone()

    failures = int(row[0]) if row else 0
    if failures <= 0:
        return 0.0
    return min(1.0, failures / float(2 * _THRASH_THRESHOLD))


# ---------------------------------------------------------------------------
# Ineffective outcome → new candidate (closed-loop re-triage)
# ---------------------------------------------------------------------------

def reopen_from_ineffective(db: Session, max_per_cycle: int = 2) -> dict:
    """
    Find candidates marked ineffective that don't already have a follow-up candidate.
    Create a new triage candidate with context about the previous failed fix.

    This closes the loop: detect → fix → verify → fix didn't work → re-detect.
    """
    summary = {"scanned": 0, "reopened": 0, "suppressed": 0}

    # Find ineffective candidates that are 48+ hours old
    cutoff = _now() - timedelta(hours=48)
    ineffective = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.outcome_status == "ineffective",
            BugFixCandidate.outcome_measured_at.isnot(None),
            BugFixCandidate.outcome_measured_at <= cutoff,
        )
        .order_by(BugFixCandidate.outcome_measured_at)
        .limit(max_per_cycle * 3)
        .all()
    )

    reopened = 0
    for c in ineffective:
        summary["scanned"] += 1
        if reopened >= max_per_cycle:
            break

        # Check if a follow-up already exists
        followup_ref = f"reopen_{c.source_type}_{c.source_ref}_{c.id}"
        existing = (
            db.query(BugFixCandidate)
            .filter(
                BugFixCandidate.source_ref == followup_ref,
                BugFixCandidate.status.in_(["open", "analyzed", "patch_proposed", "approved", "applying", "applied"]),
            )
            .first()
        )
        if existing:
            continue

        # Graduated thrash gate — use the same [0,1] score that triage uses.
        # Score >= 0.5 suppresses (equivalent to the legacy binary cutoff at
        # the threshold). Score 0.0–0.5 still allows reopening but marks the
        # followup with enrichment so the LLM knows this is a repeat attempt.
        score = thrash_score(db, c.source_type, c.source_ref or "")
        if score >= 0.5:
            summary["suppressed"] += 1
            log.info(
                "loop_health: suppressed reopen for high-thrash source=%s ref=%s score=%.2f",
                c.source_type, c.source_ref, score,
            )
            continue

        # Create follow-up candidate with richer context
        evidence = {}
        if c.outcome_evidence:
            try:
                evidence = json.loads(c.outcome_evidence)
            except (json.JSONDecodeError, ValueError):
                pass

        context = {
            "previous_candidate_id": c.id,
            "previous_outcome": "ineffective",
            "previous_patch_summary": (c.patch_summary or "")[:300],
            "previous_files": c.patch_files,
            "alerts_before": evidence.get("alerts_before", "?"),
            "alerts_after": evidence.get("alerts_after", "?"),
            "original_source_type": c.source_type,
            "original_source_ref": c.source_ref,
            "original_title": c.title,
            # Graduated thrash score at the moment of reopen. Passed through
            # to the LLM prompt so it can adjust its aggressiveness: a source
            # that has failed twice before (score ~0.33) needs a fundamentally
            # different approach, not a cosmetic variant of the previous patch.
            "thrash_score_at_reopen": round(score, 3),
        }

        new_candidate = BugFixCandidate(
            source_type="recurrence",
            source_ref=followup_ref,
            title=f"[Recurrence] {c.title[:200]}",
            summary=(
                f"Previous fix (candidate #{c.id}) was ineffective. "
                f"Alerts before: {evidence.get('alerts_before', '?')}, "
                f"after: {evidence.get('alerts_after', '?')}. "
                f"A different approach is needed."
            ),
            context_json=json.dumps(context, default=str),
            status="open",
        )
        db.add(new_candidate)
        reopened += 1
        summary["reopened"] += 1

        log.info(
            "loop_health: reopened candidate=%d as %s (previous fix ineffective)",
            c.id, followup_ref,
        )

    if summary["reopened"] > 0:
        db.flush()

    return summary


# ---------------------------------------------------------------------------
# Auto-resolve stabilized thrash alerts
# ---------------------------------------------------------------------------

_STABILIZATION_DAYS = 7


def auto_resolve_thrash_alerts(db: Session) -> dict:
    """
    Find unresolved chronic_thrashing alerts where the source has stabilized.

    A source is stabilized when it has had NO new failures (apply_failed,
    rolled_back, or ineffective) in the last 7 days.

    Returns: {"checked": N, "resolved": N}
    """
    summary = {"checked": 0, "resolved": 0}

    try:
        from app.models.ops_alert import OpsAlert

        # Find all unresolved chronic_thrashing alerts
        alerts = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.alert_type == "chronic_thrashing",
                OpsAlert.resolved == False,
            )
            .all()
        )

        if not alerts:
            return summary

        stabilization_cutoff = _now() - timedelta(days=_STABILIZATION_DAYS)

        for alert in alerts:
            summary["checked"] += 1

            # Extract source_type:source_ref from alert.source
            # Format: "source_type:source_ref" (set by _escalate_thrashing)
            parts = (alert.source or "").split(":", 1)
            if len(parts) != 2:
                continue
            source_type, source_ref = parts

            # Check for recent failures
            recent_failures = db.execute(text("""
                SELECT COUNT(*) FROM bugfix_candidates
                WHERE source_type = :stype AND source_ref = :sref
                  AND created_at >= :cutoff
                  AND (status IN ('apply_failed', 'rolled_back')
                       OR outcome_status = 'ineffective')
            """), {
                "stype": source_type,
                "sref": source_ref,
                "cutoff": stabilization_cutoff,
            }).fetchone()

            if (recent_failures[0] if recent_failures else 0) == 0:
                # Source has stabilized — resolve the alert
                from app.services.alerting import resolve_alert
                resolve_alert(db, alert.id)
                summary["resolved"] += 1
                log.info(
                    "loop_health: auto-resolved chronic_thrashing alert=%d source=%s (stable %dd)",
                    alert.id, alert.source, _STABILIZATION_DAYS,
                )

        if summary["resolved"] > 0:
            db.flush()

    except Exception as exc:
        log.warning("loop_health: auto-resolve thrash alerts failed: %s", exc)

    return summary


# ---------------------------------------------------------------------------
# Pipeline health snapshot
# ---------------------------------------------------------------------------

def get_loop_health(db: Session) -> dict:
    """
    Full health snapshot of the autonomous maintenance loop.
    Returns queue depths, stuck items, throughput, and failure rates.
    """
    now = _now()

    # Queue depths by status
    queue_rows = db.execute(text("""
        SELECT status, COUNT(*) FROM bugfix_candidates
        GROUP BY status
    """)).fetchall()
    bugfix_queues = {r[0]: r[1] for r in queue_rows}

    evo_rows = db.execute(text("""
        SELECT status, COUNT(*) FROM evolution_proposals
        GROUP BY status
    """)).fetchall()
    evolution_queues = {r[0]: r[1] for r in evo_rows}

    # Throughput (last 7 days)
    week_ago = now - timedelta(days=7)
    throughput = {}

    applied_7d = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE status = 'applied' AND applied_at >= :cutoff
    """), {"cutoff": week_ago}).fetchone()
    throughput["bugfixes_applied_7d"] = applied_7d[0] if applied_7d else 0

    proposed_7d = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE status IN ('patch_proposed', 'approved', 'applied', 'rejected')
          AND proposal_attempted_at >= :cutoff
    """), {"cutoff": week_ago}).fetchone()
    throughput["patches_proposed_7d"] = proposed_7d[0] if proposed_7d else 0

    evo_converted_7d = db.execute(text("""
        SELECT COUNT(*) FROM evolution_proposals
        WHERE status = 'accepted' AND decided_at >= :cutoff
    """), {"cutoff": week_ago}).fetchone()
    throughput["evolutions_converted_7d"] = evo_converted_7d[0] if evo_converted_7d else 0

    # Outcome stats (last 30 days)
    month_ago = now - timedelta(days=30)
    outcome_rows = db.execute(text("""
        SELECT outcome_status, COUNT(*) FROM bugfix_candidates
        WHERE outcome_status IS NOT NULL AND outcome_measured_at >= :cutoff
        GROUP BY outcome_status
    """), {"cutoff": month_ago}).fetchall()
    outcomes_30d = {r[0]: r[1] for r in outcome_rows}

    # Stuck items — single aggregation instead of 5 per-status queries.
    # Each status has its own threshold hour cutoff; the CASE expression
    # encodes that mapping in SQL so we walk the table once.
    stuck_rows = db.execute(text("""
        SELECT status, COUNT(*) AS cnt
        FROM bugfix_candidates
        WHERE (status = 'open'            AND created_at <= :cut_open)
           OR (status = 'analyzed'        AND created_at <= :cut_analyzed)
           OR (status = 'patch_proposed'  AND created_at <= :cut_proposed)
           OR (status = 'approved'        AND created_at <= :cut_approved)
           OR (status = 'applying'        AND created_at <= :cut_applying)
        GROUP BY status
    """), {
        "cut_open":     now - timedelta(hours=_STUCK_HOURS["open"]),
        "cut_analyzed": now - timedelta(hours=_STUCK_HOURS["analyzed"]),
        "cut_proposed": now - timedelta(hours=_STUCK_HOURS["patch_proposed"]),
        "cut_approved": now - timedelta(hours=_STUCK_HOURS["approved"]),
        "cut_applying": now - timedelta(hours=_STUCK_HOURS["applying"]),
    }).fetchall()
    stuck_map = {r[0]: r[1] for r in stuck_rows}
    stuck_items = [
        {"status": status, "count": stuck_map[status], "threshold_hours": max_hours}
        for status, max_hours in _STUCK_HOURS.items()
        if stuck_map.get(status, 0) > 0
    ]

    # Thrashing sources
    thrashing = check_thrashing(db)

    # Recurrences
    recurrences = check_recurrence(db)

    # Failure rate
    total_attempted_30d = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE created_at >= :cutoff
          AND status NOT IN ('open', 'analyzed')
    """), {"cutoff": month_ago}).fetchone()
    total_failed_30d = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE created_at >= :cutoff
          AND status IN ('apply_failed', 'rolled_back', 'rejected')
    """), {"cutoff": month_ago}).fetchone()

    attempted = total_attempted_30d[0] if total_attempted_30d else 0
    failed = total_failed_30d[0] if total_failed_30d else 0
    failure_rate = round(failed / attempted * 100, 1) if attempted > 0 else 0.0

    # ----- Organic repair honesty metrics (post-2026-04-11 audit) -------
    # The old `failure_rate` counts raw apply_failed/rolled_back/rejected
    # over everything past-open. That number is MISLEADING because:
    #   (a) bulk operator cleanups show up as "discarded" rows — they
    #       aren't failures, they're noise removal
    #   (b) orchestrator cosmetic actions (resolve_alert) inflate the
    #       denominator when they recur as no_effect outcomes
    #   (c) "applied" with outcome="ineffective" isn't counted as failed,
    #       but from a merchant standpoint it IS a failed repair.
    #
    # `organic_*` metrics expose the honest signal:
    #   - only real propose-patch attempts (excluding operator_cleanup)
    #   - success = applied AND outcome='effective'
    #   - failed = apply_failed OR rolled_back OR outcome='ineffective'
    organic_attempted_30d = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE created_at >= :cutoff
          AND status IN ('applied','apply_failed','rolled_back')
          AND COALESCE(failure_reason,'') NOT LIKE 'pre_activation_cleanup%%'
          AND COALESCE(failure_reason,'') NOT LIKE 'operator_cleanup%%'
    """), {"cutoff": month_ago}).fetchone()
    organic_effective_30d = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE created_at >= :cutoff
          AND status = 'applied'
          AND outcome_status = 'effective'
    """), {"cutoff": month_ago}).fetchone()
    organic_ineffective_30d = db.execute(text("""
        SELECT COUNT(*) FROM bugfix_candidates
        WHERE created_at >= :cutoff
          AND status = 'applied'
          AND outcome_status = 'ineffective'
    """), {"cutoff": month_ago}).fetchone()

    org_attempted = organic_attempted_30d[0] if organic_attempted_30d else 0
    org_effective = organic_effective_30d[0] if organic_effective_30d else 0
    org_ineffective = organic_ineffective_30d[0] if organic_ineffective_30d else 0
    organic_success_rate = (
        round(org_effective / org_attempted * 100, 1) if org_attempted > 0 else 0.0
    )

    # Orchestrator: distinguish cosmetic vs repair
    try:
        from app.services.orchestrator import _COSMETIC_ACTIONS
        cosmetic_list = list(_COSMETIC_ACTIONS)
    except Exception:
        cosmetic_list = ["resolve_alert"]
    orch_repair = db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE outcome_status = 'success') AS successes,
            COUNT(*) FILTER (WHERE outcome_status = 'no_effect') AS no_effects,
            COUNT(*) AS total
        FROM action_outcomes
        WHERE executed_at >= :cutoff
          AND action_type LIKE 'orch_%%'
          AND action_type NOT IN :cosmetic
    """), {"cutoff": month_ago, "cosmetic": tuple(f"orch_{c}" for c in cosmetic_list) or ("__none__",)}).fetchone()
    orch_cosmetic = db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE outcome_status = 'success') AS successes,
            COUNT(*) FILTER (WHERE outcome_status = 'no_effect') AS no_effects,
            COUNT(*) AS total
        FROM action_outcomes
        WHERE executed_at >= :cutoff
          AND action_type IN :cosmetic
    """), {"cutoff": month_ago, "cosmetic": tuple(f"orch_{c}" for c in cosmetic_list) or ("__none__",)}).fetchone()

    orch_repair_success_rate = (
        round((orch_repair[0] or 0) / (orch_repair[2] or 1) * 100, 1)
        if orch_repair and orch_repair[2] else 0.0
    )

    # Subsystem weakness ranking
    weakness = score_subsystem_weakness(db)

    # Trend tracking — compare recent (7d) vs baseline (30d) performance
    trend = _compute_trend(db, now)

    # Honesty gate: the system claims health only when ORGANIC repairs
    # actually produce effective outcomes. Pure activity (throughput)
    # is not enough — we want measurable recovery.
    organic_health_healthy = (
        org_attempted == 0  # nothing to judge yet — don't flag as unhealthy
        or organic_success_rate >= 40.0  # at least 40% effective when we do try
    )

    return {
        "timestamp": now.isoformat() + "Z",
        "bugfix_queues": bugfix_queues,
        "evolution_queues": evolution_queues,
        "throughput_7d": throughput,
        "outcomes_30d": outcomes_30d,
        "failure_rate_30d_pct": failure_rate,
        "organic_repairs_30d": {
            "attempted": org_attempted,
            "effective": org_effective,
            "ineffective": org_ineffective,
            "success_rate_pct": organic_success_rate,
        },
        "orchestrator_30d": {
            "repair_actions_total": orch_repair[2] if orch_repair else 0,
            "repair_actions_success": orch_repair[0] if orch_repair else 0,
            "repair_success_rate_pct": orch_repair_success_rate,
            "cosmetic_actions_total": orch_cosmetic[2] if orch_cosmetic else 0,
            "cosmetic_note": (
                "cosmetic actions (e.g. resolve_alert) clean up noise "
                "but are not expected to fix root causes — excluded "
                "from repair_success_rate"
            ),
        },
        "stuck_items": stuck_items,
        "thrashing_sources": thrashing,
        "recurrences": recurrences,
        "weakest_subsystems": weakness[:5],
        "trend": trend,
        "is_healthy": (
            len(stuck_items) == 0
            and len(thrashing) == 0
            and failure_rate < 50
            and organic_health_healthy
        ),
    }


def _compute_trend(db: Session, now) -> dict:
    """
    Compare recent (7d) vs baseline (8-30d) performance to detect direction.

    Returns:
        {
            "direction": "improving" | "stable" | "degrading",
            "effectiveness_7d_pct": float,
            "effectiveness_baseline_pct": float,
            "alert_rate_7d": float,  # alerts per day
            "alert_rate_baseline": float,
            "detail": str
        }
    """
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    # Effectiveness: recent 7d
    recent_outcomes = db.execute(text("""
        SELECT outcome_status, COUNT(*) FROM bugfix_candidates
        WHERE outcome_measured_at >= :recent AND outcome_status IS NOT NULL
        GROUP BY outcome_status
    """), {"recent": week_ago}).fetchall()
    recent_map = {r[0]: r[1] for r in recent_outcomes}
    recent_total = sum(recent_map.values())
    recent_effective = recent_map.get("effective", 0)
    eff_7d = round(recent_effective / recent_total * 100, 1) if recent_total > 0 else None

    # Effectiveness: baseline 8-30d
    baseline_outcomes = db.execute(text("""
        SELECT outcome_status, COUNT(*) FROM bugfix_candidates
        WHERE outcome_measured_at >= :baseline AND outcome_measured_at < :recent
          AND outcome_status IS NOT NULL
        GROUP BY outcome_status
    """), {"baseline": month_ago, "recent": week_ago}).fetchall()
    baseline_map = {r[0]: r[1] for r in baseline_outcomes}
    baseline_total = sum(baseline_map.values())
    baseline_effective = baseline_map.get("effective", 0)
    eff_baseline = round(baseline_effective / baseline_total * 100, 1) if baseline_total > 0 else None

    # Alert rate: recent 7d vs baseline 8-30d
    recent_alerts = db.execute(text("""
        SELECT COUNT(*) FROM ops_alerts WHERE created_at >= :recent
    """), {"recent": week_ago}).fetchone()
    baseline_alerts = db.execute(text("""
        SELECT COUNT(*) FROM ops_alerts
        WHERE created_at >= :baseline AND created_at < :recent
    """), {"baseline": month_ago, "recent": week_ago}).fetchall()

    alert_rate_7d = round((recent_alerts[0] if recent_alerts else 0) / 7, 1)
    baseline_alert_count = baseline_alerts[0][0] if baseline_alerts and baseline_alerts[0] else 0
    alert_rate_baseline = round(baseline_alert_count / 23, 1)  # 30-7 = 23 days

    # Determine direction
    direction = "stable"
    detail_parts = []

    if eff_7d is not None and eff_baseline is not None:
        delta = eff_7d - eff_baseline
        if delta > 10:
            direction = "improving"
            detail_parts.append(f"effectiveness up {delta:+.0f}pp ({eff_baseline}% → {eff_7d}%)")
        elif delta < -10:
            direction = "degrading"
            detail_parts.append(f"effectiveness down {delta:+.0f}pp ({eff_baseline}% → {eff_7d}%)")
        else:
            detail_parts.append(f"effectiveness stable ({eff_7d}%)")
    elif eff_7d is not None:
        detail_parts.append(f"effectiveness {eff_7d}% (no baseline yet)")

    if alert_rate_baseline > 0:
        alert_change = alert_rate_7d / alert_rate_baseline if alert_rate_baseline > 0 else 1
        if alert_change > 1.5:
            if direction != "degrading":
                direction = "degrading"
            detail_parts.append(f"alert rate up {alert_rate_baseline}/d → {alert_rate_7d}/d")
        elif alert_change < 0.5:
            if direction == "stable":
                direction = "improving"
            detail_parts.append(f"alert rate down {alert_rate_baseline}/d → {alert_rate_7d}/d")

    return {
        "direction": direction,
        "effectiveness_7d_pct": eff_7d,
        "effectiveness_baseline_pct": eff_baseline,
        "alert_rate_7d": alert_rate_7d,
        "alert_rate_baseline": alert_rate_baseline,
        "detail": "; ".join(detail_parts) if detail_parts else "insufficient data",
    }


# ---------------------------------------------------------------------------
# Subsystem weakness scoring
# ---------------------------------------------------------------------------

# Criticality multiplier — failures in critical domains weigh more
_CRITICALITY_WEIGHT = {"critical": 4, "high": 2, "medium": 1, "low": 0.5}

# Signal weights — each type of failure contributes differently to weakness
_SIGNAL_WEIGHT = {
    "apply_failed": 3,     # broken fix attempt — high signal
    "rolled_back": 4,      # fix deployed then reverted — very high signal
    "ineffective": 5,      # fix applied but didn't help — strongest signal
    "recurrence": 6,       # same bug came back — strongest signal
    "open_candidate": 1,   # pending work — low signal but shows attention needed
    "stuck": 2,            # candidate stuck in pipeline — moderate signal
}


def _extract_domains_from_files(files_json: str | None) -> list[str]:
    """Extract unique domains from a JSON file list using project_brain."""
    if not files_json:
        return []
    try:
        from app.services.project_brain import classify_file
        files = json.loads(files_json)
        if not isinstance(files, list):
            return []
        domains = set()
        for f in files:
            result = classify_file(str(f))
            domains.add(result["domain"])
        return list(domains)
    except (json.JSONDecodeError, ValueError, ImportError):
        return []


def _extract_domain_from_target(target_file: str | None) -> str | None:
    """Extract domain from a single target file path."""
    if not target_file:
        return None
    try:
        from app.services.project_brain import classify_file
        path = target_file.split(":")[0]  # strip line number suffix
        return classify_file(path)["domain"]
    except ImportError:
        return None


def score_subsystem_weakness(db: Session, lookback_days: int = 30) -> list[dict]:
    """
    Score each subsystem/domain by aggregating failure signals from
    bugfix candidates and evolution proposals.

    Returns a ranked list (weakest first) of:
        {domain, score, criticality, reasons[], signals{}}

    Score formula:
        score = sum(signal_count * signal_weight) * criticality_weight

    Higher score = weaker subsystem = needs more attention.
    """
    cutoff = _now() - timedelta(days=lookback_days)

    # Accumulator: domain → {signal_type → count}
    domain_signals: dict[str, dict[str, int]] = {}

    def _add_signal(domain: str, signal_type: str, count: int = 1):
        if domain == "other" or not domain:
            return
        if domain not in domain_signals:
            domain_signals[domain] = {}
        domain_signals[domain][signal_type] = domain_signals[domain].get(signal_type, 0) + count

    # --- Signal source 1: Bugfix candidates with patch_files ---
    candidates = db.execute(text("""
        SELECT status, outcome_status, patch_files, source_type
        FROM bugfix_candidates
        WHERE created_at >= :cutoff AND patch_files IS NOT NULL
    """), {"cutoff": cutoff}).fetchall()

    for row in candidates:
        status, outcome, patch_files, source_type = row[0], row[1], row[2], row[3]
        domains = _extract_domains_from_files(patch_files)
        for d in domains:
            if status in ("apply_failed", "rolled_back"):
                _add_signal(d, status)
            if outcome == "ineffective":
                _add_signal(d, "ineffective")
            if source_type == "recurrence":
                _add_signal(d, "recurrence")
            if status in ("open", "analyzed", "patch_proposed"):
                _add_signal(d, "open_candidate")

    # --- Signal source 2: Bugfix candidates without patch_files but with context_json ---
    # These are candidates that never got a patch proposed (LLM failed or budget blocked)
    no_patch = db.execute(text("""
        SELECT status, context_json, source_type
        FROM bugfix_candidates
        WHERE created_at >= :cutoff
          AND patch_files IS NULL
          AND context_json IS NOT NULL
          AND status IN ('open', 'analyzed')
    """), {"cutoff": cutoff}).fetchall()

    for row in no_patch:
        status, ctx_json, source_type = row[0], row[1], row[2]
        try:
            ctx = json.loads(ctx_json)
            # Try to get target file from evolution context
            target = ctx.get("target_file")
            if target:
                d = _extract_domain_from_target(target)
                if d:
                    _add_signal(d, "open_candidate")
        except (json.JSONDecodeError, ValueError):
            pass

    # --- Signal source 3: Evolution proposals (open = attention needed) ---
    evo_rows = db.execute(text("""
        SELECT target_file, status
        FROM evolution_proposals
        WHERE created_at >= :cutoff
          AND target_file IS NOT NULL
          AND status IN ('open', 'needs_revalidation')
    """), {"cutoff": cutoff}).fetchall()

    for row in evo_rows:
        target_file, status = row[0], row[1]
        d = _extract_domain_from_target(target_file)
        if d:
            _add_signal(d, "open_candidate")

    # --- Signal source 4: Stuck candidates ---
    # Single query instead of 5 per-status scans. The per-status time
    # cutoff is encoded in a disjunction so we touch the bugfix_candidates
    # table once for all stuck detection.
    now = _now()
    stuck_rows = db.execute(text("""
        SELECT patch_files FROM bugfix_candidates
        WHERE patch_files IS NOT NULL
          AND (
               (status = 'open'           AND created_at <= :cut_open)
            OR (status = 'analyzed'       AND created_at <= :cut_analyzed)
            OR (status = 'patch_proposed' AND created_at <= :cut_proposed)
            OR (status = 'approved'       AND created_at <= :cut_approved)
            OR (status = 'applying'       AND created_at <= :cut_applying)
          )
    """), {
        "cut_open":     now - timedelta(hours=_STUCK_HOURS["open"]),
        "cut_analyzed": now - timedelta(hours=_STUCK_HOURS["analyzed"]),
        "cut_proposed": now - timedelta(hours=_STUCK_HOURS["patch_proposed"]),
        "cut_approved": now - timedelta(hours=_STUCK_HOURS["approved"]),
        "cut_applying": now - timedelta(hours=_STUCK_HOURS["applying"]),
    }).fetchall()
    for row in stuck_rows:
        for d in _extract_domains_from_files(row[0]):
            _add_signal(d, "stuck")

    # --- Compute scores ---
    try:
        from app.services.project_brain import _DOMAIN_CRITICALITY
    except ImportError:
        _DOMAIN_CRITICALITY = {}

    results = []
    for domain, signals in domain_signals.items():
        criticality = _DOMAIN_CRITICALITY.get(domain, "low")
        crit_weight = _CRITICALITY_WEIGHT.get(criticality, 0.5)

        raw_score = sum(
            count * _SIGNAL_WEIGHT.get(sig_type, 1)
            for sig_type, count in signals.items()
        )
        score = round(raw_score * crit_weight, 1)

        # Build human-readable reasons
        reasons = []
        for sig_type, count in sorted(signals.items(), key=lambda x: -x[1]):
            if count > 0:
                reasons.append(f"{count}x {sig_type}")

        results.append({
            "domain": domain,
            "score": score,
            "criticality": criticality,
            "signals": signals,
            "reasons": reasons,
        })

    # Sort by score descending (weakest first)
    results.sort(key=lambda x: -x["score"])
    return results
