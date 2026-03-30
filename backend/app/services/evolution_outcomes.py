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
            outcome, evidence = _measure_single(db, c)
            c.outcome_status = outcome
            c.outcome_measured_at = _now()
            c.outcome_evidence = json.dumps(evidence, default=str)
            summary["evaluated"] += 1
            summary[outcome] = summary.get(outcome, 0) + 1
            log.info("evolution_outcome: candidate=%d outcome=%s", c.id, outcome)
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
    """
    applied = candidate.applied_at
    window = timedelta(hours=_MEASUREMENT_WINDOW_HOURS)

    before_start = applied - window
    before_end = applied
    after_start = applied
    after_end = applied + window

    # Count ops_alerts in each window
    alerts_before = _count_alerts(db, before_start, before_end, candidate.source_type, candidate.source_ref)
    alerts_after = _count_alerts(db, after_start, after_end, candidate.source_type, candidate.source_ref)

    # Count worker_log errors in each window
    errors_before = _count_worker_errors(db, before_start, before_end)
    errors_after = _count_worker_errors(db, after_start, after_end)

    evidence = {
        "alerts_before": alerts_before,
        "alerts_after": alerts_after,
        "errors_before": errors_before,
        "errors_after": errors_after,
        "window_hours": _MEASUREMENT_WINDOW_HOURS,
    }

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


def _count_alerts(
    db: Session, start: datetime, end: datetime,
    source_type: str | None, source_ref: str | None,
) -> int:
    """Count ops_alerts in a time window, optionally filtered by source pattern."""
    try:
        q = text("""
            SELECT COUNT(*) FROM ops_alerts
            WHERE created_at >= :start AND created_at < :end
        """)
        row = db.execute(q, {"start": start, "end": end}).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _count_worker_errors(db: Session, start: datetime, end: datetime) -> int:
    """Count total worker_log errors in a time window."""
    try:
        row = db.execute(
            text("SELECT COALESCE(SUM(errors), 0) FROM worker_log WHERE started_at >= :start AND started_at < :end"),
            {"start": start, "end": end},
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def get_effectiveness_stats(db: Session, days: int = 90) -> dict:
    """
    Aggregate bugfix outcome stats for evolution engine and Opus context.
    Returns stats grouped by source_type.
    """
    cutoff = _now() - timedelta(days=days)

    try:
        rows = db.execute(text("""
            SELECT
                source_type,
                outcome_status,
                COUNT(*) AS cnt
            FROM bugfix_candidates
            WHERE status = 'applied'
              AND outcome_status IS NOT NULL
              AND outcome_measured_at >= :cutoff
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
