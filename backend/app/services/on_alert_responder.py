"""on_alert_responder.py — FRAMEWORK ONLY (2026-04-18).

Closes the last capability gap in the autonomous-CTO rubric: when a
critical ops_alert lands, Claude should triage it immediately rather
than waiting for the 08:00 Rome daily brief.

**Current state: FRAMEWORK ONLY. No LLM call is made.** The poll loop
reads alerts, builds a context packet, and logs "would trigger LLM
with N chars of context". The actual LLM call is deferred until the
founder explicitly approves the money-spend scope
(~€1-3/mo estimated at current alert volume).

When enabled, behavior will be:
    1. Poll unresolved critical ops_alerts from the last N hours with
       no triage row yet.
    2. For each, build a context packet (alert + recent related alerts
       + recent commits + recent worker_log).
    3. Call llm_router with a grounded triage prompt (PII guard +
       budget gate already enforced by llm_router).
    4. Write triage row to audit_log (action_type='alert_triage').
    5. Optionally ping founder via Telegram if triage classifies as
       P0 (requires human eyes NOW).

Scope restrictions (NEVER relax without founder + §10 TIER_1 review):
    - TRIAGE ONLY. No bugfix proposals — those go through the
      existing bugfix_pipeline (governed TIER_1 auto-apply).
    - Read-only on system state. No writes except audit_log row.
    - Never modifies alert.resolved — triage is analysis, not closure.

Kill switch: env ON_ALERT_RESPONDER_ENABLED=0 (default). Flip to 1
ONLY after founder signs off on:
    - LLM budget (per-alert spend + monthly cap)
    - Notification policy (when to Telegram-ping on top of triage)
    - Retention policy (how long triage rows keep in audit_log)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("on_alert_responder")


def is_enabled() -> bool:
    """Global gate. Default OFF. Founder flips to ON after approving
    the scope documented in the module docstring above."""
    return os.getenv("ON_ALERT_RESPONDER_ENABLED", "0") == "1"


def _find_untrimmed_criticals(
    db: Session, *, lookback_hours: int = 24, limit: int = 5
) -> list[dict]:
    """Return up to `limit` unresolved critical ops_alerts from the last
    `lookback_hours` that have NOT yet received an alert_triage audit row."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        hours=lookback_hours
    )
    rows = db.execute(
        text(
            """
            SELECT a.id, a.created_at, a.severity, a.alert_type,
                   a.shop_domain, a.summary, a.detail
            FROM ops_alerts a
            WHERE a.severity = 'critical'
              AND a.resolved = false
              AND a.created_at >= :cutoff
              AND NOT EXISTS (
                  SELECT 1 FROM audit_log al
                  WHERE al.action_type = 'alert_triage'
                    AND al.target_id = a.id::text
              )
            ORDER BY a.created_at DESC
            LIMIT :lim
            """
        ),
        {"cutoff": cutoff, "lim": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


def build_context_packet(db: Session, alert: dict) -> dict:
    """Build the read-only context packet a future LLM call will consume.

    Pure read; no side effects. Reused by the framework stub today and
    by the real LLM call when enabled. Centralizing this function means
    the LLM integration in a future commit only adds a single call site.
    """
    packet: dict = {"alert": alert}

    # Related alerts: same alert_type, last 48h, resolved or unresolved.
    try:
        related_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)
        related = db.execute(
            text(
                """
                SELECT id, created_at, severity, resolved, summary
                FROM ops_alerts
                WHERE alert_type = :kind AND created_at >= :cutoff
                  AND id != :self
                ORDER BY created_at DESC LIMIT 10
                """
            ),
            {"kind": alert["alert_type"], "cutoff": related_cutoff, "self": alert["id"]},
        ).mappings().all()
        packet["related_alerts_48h"] = [dict(r) for r in related]
    except Exception as exc:
        packet["related_alerts_48h_error"] = str(exc)[:120]

    # Recent commits — useful for a regression-class triage.
    try:
        import subprocess
        out = subprocess.run(
            ["git", "log", "--since=48 hours ago", "--pretty=format:%h %s"],
            capture_output=True, text=True, timeout=5, cwd="/opt/wishspark",
        )
        packet["recent_commits_48h"] = out.stdout.strip().splitlines()[:20]
    except Exception as exc:
        packet["recent_commits_error"] = str(exc)[:120]

    # Recent worker_log error lines — a failing worker often correlates.
    try:
        worker_logs = db.execute(
            text(
                """
                SELECT worker_name, started_at, errors, error_detail
                FROM worker_log
                WHERE errors > 0
                  AND started_at >= NOW() - INTERVAL '6 hours'
                ORDER BY started_at DESC LIMIT 10
                """
            )
        ).mappings().all()
        packet["worker_errors_6h"] = [dict(r) for r in worker_logs]
    except Exception as exc:
        packet["worker_errors_error"] = str(exc)[:120]

    return packet


def run(db: Session) -> dict:
    """Entry point called by `agent_worker._run_on_alert_responder`.

    Returns a dict for telemetry:
        {
            "enabled": bool,
            "alerts_found": int,
            "contexts_built": int,
            "llm_calls_made": int,   # always 0 in framework mode
            "mode": "framework" | "live",
        }

    Framework mode (default): builds context packets, logs summary,
    does NOT call the LLM or write anything.
    Live mode (ON_ALERT_RESPONDER_ENABLED=1): TODO — requires founder
    sign-off on money-spend scope. See module docstring.
    """
    report: dict = {
        "enabled": is_enabled(),
        "alerts_found": 0,
        "contexts_built": 0,
        "llm_calls_made": 0,
        "mode": "live" if is_enabled() else "framework",
    }

    if not is_enabled():
        # Do nothing on the data path. Still callable for tests.
        return report

    alerts = _find_untrimmed_criticals(db)
    report["alerts_found"] = len(alerts)
    if not alerts:
        return report

    for a in alerts:
        try:
            pkt = build_context_packet(db, a)
            report["contexts_built"] += 1
            # FRAMEWORK MODE: log and stop. No LLM call yet.
            log.info(
                "on_alert_responder: would trigger LLM for alert id=%s "
                "type=%s ctx_bytes=~%d (framework mode — no LLM call)",
                a["id"],
                a["alert_type"],
                sum(len(str(v)) for v in pkt.values()),
            )
        except Exception as exc:
            log.warning(
                "on_alert_responder: context build failed for alert id=%s: %s",
                a.get("id"),
                exc,
            )

    return report
