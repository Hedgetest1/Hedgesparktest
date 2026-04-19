"""on_alert_responder.py — LIVE (2026-04-19).

Closes the last capability gap in the autonomous-CTO rubric: when a
critical ops_alert lands, Claude triages it immediately rather than
waiting for the 08:00 Rome daily brief.

**Current state: LIVE.** Framework-mode fallback kept for unit tests
and for operating with `ON_ALERT_RESPONDER_ENABLED=0`.

Flow when enabled:
    1. Poll unresolved critical ops_alerts from the last 24h with no
       triage row yet (5-alert cap per cycle, idempotent via
       anti-join on audit_log action_type='alert_triage').
    2. For each, build a context packet (alert + recent related alerts
       + recent commits + recent worker_log).
    3. Call `on_alert_triage_llm.triage` — budget gate + PII guard +
       anthropic-primary openai-fallback + 429 backoff all handled
       in the LLM service.
    4. Write triage row to audit_log with action_type='alert_triage'
       and status in {'triaged', 'triage_failed', 'framework_mode'}.
    5. If verdict.requires_human_now → ping founder via Telegram with
       the triage summary (P0 policy).

Scope restrictions (§10 TIER_1 — propose only; NEVER relax without
founder review):
    - TRIAGE ONLY. No bugfix proposals — those go through the
      existing bugfix_pipeline (governed TIER_1 auto-apply).
    - Read-only on system state. No writes except audit_log row.
    - Never modifies alert.resolved — triage is analysis, not closure.

Kill switch: env `ON_ALERT_RESPONDER_ENABLED=0` (default). Budget
approved 2026-04-19 after founder sign-off; the env var is now the
only gate. Flipping it to 1 enables live LLM calls under the
`on_alert_responder` module in llm_budget (30 calls/day cap).

Budget math (current dev phase):
    - ~3k input + ~500 output tokens per call
    - Claude Sonnet 4: ~€0.016 / call
    - Observed volume: 5-10 critical alerts/day
    - Expected monthly spend: €2.50-5, worst case (30/day cap hit)
      €14/mo. Below the €10/mo dev floor in practice.
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
            "llm_calls_made": int,
            "triaged": int,
            "triage_failed": int,
            "p0_pings": int,
            "mode": "framework" | "live",
        }
    """
    report: dict = {
        "enabled": is_enabled(),
        "alerts_found": 0,
        "contexts_built": 0,
        "llm_calls_made": 0,
        "triaged": 0,
        "triage_failed": 0,
        "p0_pings": 0,
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
        except Exception as exc:
            log.warning(
                "on_alert_responder: context build failed for alert id=%s: %s",
                a.get("id"), exc,
            )
            continue

        try:
            from app.services.on_alert_triage_llm import triage
            verdict = triage(pkt)
        except Exception as exc:
            log.warning(
                "on_alert_responder: triage raised for alert id=%s: %s",
                a.get("id"), exc,
            )
            verdict = None

        if verdict is None:
            # Budget-blocked / PII-blocked / no-provider / parse-failed —
            # write a receipt so the same alert isn't retriaged next cycle
            # (idempotency anti-join on audit_log). The caller can see
            # the failure count in the report.
            _write_triage_receipt(
                db, a, status="triage_failed",
                verdict=None, reason="llm_unavailable_or_parse_failed",
            )
            report["triage_failed"] += 1
            continue

        report["llm_calls_made"] += 1
        _write_triage_receipt(
            db, a, status="triaged", verdict=verdict, reason=None,
        )
        report["triaged"] += 1

        if verdict.requires_human_now:
            pinged = _ping_founder_p0(a, verdict)
            if pinged:
                report["p0_pings"] += 1

    return report


def _write_triage_receipt(
    db: Session, alert: dict, *,
    status: str, verdict, reason: str | None,
) -> None:
    """Append an audit_log row for this (alert, triage) pair. The
    `target_id = str(alert.id)` lets `_find_untrimmed_criticals`
    anti-join skip already-triaged alerts on the next cycle."""
    try:
        from app.services.audit import write_audit_log
        metadata: dict = {
            "alert_type": alert.get("alert_type"),
            "alert_summary": (alert.get("summary") or "")[:400],
        }
        if verdict is not None:
            metadata.update({
                "severity": verdict.severity,
                "probable_cause": verdict.probable_cause,
                "suggested_owner": verdict.suggested_owner,
                "triage_steps": verdict.triage_steps,
                "related_commits": verdict.related_commits,
                "requires_human_now": verdict.requires_human_now,
                "model_used": verdict.model_used,
            })
        if reason:
            metadata["failure_reason"] = reason
        write_audit_log(
            db,
            actor_type="worker",
            actor_name="on_alert_responder",
            action_type="alert_triage",
            target_type="ops_alert",
            target_id=str(alert.get("id")),
            status=status,
            metadata=metadata,
        )
        db.commit()
    except Exception as exc:
        log.warning(
            "on_alert_responder: audit write failed for alert id=%s: %s",
            alert.get("id"), exc,
        )


def _ping_founder_p0(alert: dict, verdict) -> bool:
    """Forward a P0 triage verdict to the operator Telegram channel.
    Returns True on successful dispatch. Never raises — Telegram
    outages must not break the triage loop."""
    try:
        from app.services.telegram_agent import (
            is_configured,
            send_message,
        )
    except Exception as exc:
        log.warning(
            "on_alert_responder: telegram_agent import failed: %s", exc,
        )
        return False
    if not is_configured():
        return False

    steps = "\n".join(f"• {s}" for s in (verdict.triage_steps or [])[:5])
    body = (
        f"🚨 P0 triage: {alert.get('alert_type')}\n"
        f"Alert id: {alert.get('id')} · "
        f"severity={alert.get('severity')}\n"
        f"Summary: {(alert.get('summary') or '')[:300]}\n"
        f"Probable cause: {verdict.probable_cause}\n"
        f"Suggested owner: {verdict.suggested_owner}\n"
        f"Triage steps:\n{steps}"
    )
    try:
        result = send_message(body)
        # send_message returns False / message_id(int) / True. Treat
        # any non-False value as success.
        return bool(result)
    except Exception as exc:
        log.warning(
            "on_alert_responder: telegram send failed: %s", exc,
        )
        return False
