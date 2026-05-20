"""
orchestrator_context.py — Builds a structured, token-efficient context
for the AI orchestrator decision layer.

Aggregates real operational state from:
    - ops_alerts (unresolved, last 48h)
    - worker_log (recent cycle health per worker)
    - system health summary

Output is a plain-text block designed for LLM consumption —
grouped, summarized, never raw log dumps.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("orchestrator.context")


_MAX_OUTCOME_ACTION_TYPES = 6  # max action types shown in outcome summary


def build_orchestrator_context(db: Session) -> str:
    """
    Build a structured text context for the orchestrator's LLM decision layer.

    Returns a compact string (typically 500-2000 tokens) with:
        - Alert summary (counts by severity + type, key examples)
        - Worker health summary (per-worker recent error rates)
        - Action outcomes (success rates per action type, last 24h)
        - System vitals

    This is the ONLY input the LLM receives about current system state.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    sections: list[str] = []

    sections.append(_build_alerts_section(db, now))
    sections.append(_build_workers_section(db, now))
    sections.append(_build_outcomes_section(db, now))
    sections.append(_build_vitals_section(db, now))

    context = "\n\n".join(sections)
    log.debug("orchestrator_context: built %d chars, %d sections", len(context), len(sections))
    return context


def _build_alerts_section(db: Session, now: datetime) -> str:
    """Summarize unresolved ops_alerts."""
    cutoff = now - timedelta(hours=48)
    # Use UNION to ensure each severity level is represented — prevents
    # one severity (e.g. 50+ critical alerts) from drowning out others.
    rows = db.execute(text("""
        (SELECT severity, alert_type, shop_domain, summary, created_at
         FROM ops_alerts
         WHERE resolved = false AND created_at >= :cutoff AND severity = 'critical'
         ORDER BY created_at DESC LIMIT 20)
        UNION ALL
        (SELECT severity, alert_type, shop_domain, summary, created_at
         FROM ops_alerts
         WHERE resolved = false AND created_at >= :cutoff AND severity = 'warning'
         ORDER BY created_at DESC LIMIT 15)
        UNION ALL
        (SELECT severity, alert_type, shop_domain, summary, created_at
         FROM ops_alerts
         WHERE resolved = false AND created_at >= :cutoff AND severity NOT IN ('critical', 'warning')
         ORDER BY created_at DESC LIMIT 15)
    """), {"cutoff": cutoff}).fetchall()

    if not rows:
        return "## Alerts\nNo unresolved alerts."

    # Count by severity
    counts: dict[str, int] = {}
    for r in rows:
        counts[r[0]] = counts.get(r[0], 0) + 1

    lines = ["## Alerts"]
    lines.append(f"Unresolved (last 48h): {len(rows)} total — " +
                 ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))

    # Show top alerts with detail — ensure each severity gets representation.
    # First, collect up to 3 per severity to avoid one severity drowning others.
    shown: list = []
    by_sev: dict[str, list] = {}
    for r in rows:
        by_sev.setdefault(r[0], []).append(r)
    for sev in ["critical", "warning", "info"]:
        shown.extend(by_sev.get(sev, [])[:3])
    # If fewer than 5 total, pad from remaining rows
    shown_set = set(id(r) for r in shown)
    for r in rows:
        if len(shown) >= 10:
            break
        if id(r) not in shown_set:
            shown.append(r)
            shown_set.add(id(r))

    for r in shown:
        age_h = max(1, int((now - r[4]).total_seconds() / 3600)) if r[4] else 0
        shop = f" shop={r[2]}" if r[2] else ""
        lines.append(f"  [{r[0].upper()}] {r[1]}{shop} — {r[3]} ({age_h}h ago)")

    if len(rows) > len(shown):
        lines.append(f"  ... and {len(rows) - len(shown)} more")

    return "\n".join(lines)


def _build_workers_section(db: Session, now: datetime) -> str:
    """Summarize recent worker_log health per worker."""
    cutoff = now - timedelta(hours=6)
    rows = db.execute(text("""
        SELECT worker_name,
               COUNT(*) AS cycles,
               SUM(errors) AS total_errors,
               MAX(started_at) AS last_run
        FROM worker_log
        WHERE started_at >= :cutoff
        GROUP BY worker_name
        ORDER BY total_errors DESC, worker_name
    """), {"cutoff": cutoff}).fetchall()

    if not rows:
        return "## Workers\nNo worker activity in the last 6 hours."

    lines = ["## Workers (last 6h)"]
    for r in rows:
        age_min = max(1, int((now - r[3]).total_seconds() / 60)) if r[3] else 0
        status = "OK" if r[2] == 0 else f"ERRORS({r[2]})"
        lines.append(f"  {r[0]:30s} cycles={r[1]:3d}  {status:12s}  last={age_min}min ago")

    return "\n".join(lines)


def _build_vitals_section(db: Session, now: datetime) -> str:
    """Basic system vitals from queryable state."""
    lines = ["## System Vitals"]

    # Event ingestion rate (last hour)
    cutoff_ms = int((now - timedelta(hours=1)).timestamp() * 1000)
    # sql-ms-type: ok — `:cutoff` bound to cutoff_ms (int epoch ms).
    event_count = db.execute(text(
        "SELECT COUNT(*) FROM events WHERE timestamp >= :cutoff"
    ), {"cutoff": cutoff_ms}).scalar() or 0
    lines.append(f"  Events (last 1h): {event_count}")

    # Active merchants
    merchant_count = db.execute(text(
        "SELECT COUNT(*) FROM merchants WHERE install_status = 'active'"
    )).scalar() or 0
    lines.append(f"  Active merchants: {merchant_count}")

    # Active signals
    signal_count = db.execute(text(
        "SELECT COUNT(*) FROM opportunity_signals WHERE expires_at >= NOW()"
    )).scalar() or 0
    lines.append(f"  Active signals: {signal_count}")

    lines.append(f"  Time: {now.isoformat()}Z")

    return "\n".join(lines)


def _build_outcomes_section(db: Session, now: datetime) -> str:
    """Aggregate recent action outcomes into per-action-type success rates."""
    cutoff = now - timedelta(hours=24)
    rows = db.execute(text("""
        SELECT action_type,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE outcome_status = 'success') AS successes,
               COUNT(*) FILTER (WHERE outcome_status = 'no_effect') AS no_effects,
               COUNT(*) FILTER (WHERE outcome_status = 'unknown') AS unknowns
        FROM action_outcomes
        WHERE executed_at >= :cutoff AND outcome_status != 'pending'
        GROUP BY action_type
        ORDER BY COUNT(*) DESC
        LIMIT :max_types
    """), {"cutoff": cutoff, "max_types": _MAX_OUTCOME_ACTION_TYPES}).fetchall()

    if not rows:
        return "## Action Outcomes (last 24h)\nNo evaluated outcomes yet."

    lines = ["## Action Outcomes (last 24h)"]
    for r in rows:
        action_type = r[0]
        total = r[1]
        successes = r[2]
        no_effects = r[3]
        unknowns = r[4]
        rate = round(100 * successes / total) if total > 0 else 0
        # Clean up action_type prefix for readability
        display = action_type.replace("orch_", "").replace("llm_exec_", "")
        lines.append(
            f"  {display}: executions={total} success={successes} "
            f"no_effect={no_effects} unknown={unknowns} success_rate={rate}%"
        )

    return "\n".join(lines)


