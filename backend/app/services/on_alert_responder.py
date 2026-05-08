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
    - TRIAGE ONLY. Produces an explanation paragraph for the operator,
      never a code patch.
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
        # Best-effort audit row — restore session so the caller's loop
        # over remaining alerts can continue to query the DB. Without
        # rollback, the next ORM op on the same session raises
        # PendingRollbackError.
        try:
            db.rollback()
        except Exception:
            pass  # SILENT-EXCEPT-OK: rollback-of-rollback in best-effort audit path
        log.warning(
            "on_alert_responder: audit write failed for alert id=%s: %s",
            alert.get("id"), exc,
        )


_TELEGRAM_STRATEGIC_ALLOWLIST = frozenset({
    # Founder direttiva 2026-05-05: Telegram surfaces ONLY strategic
    # signals — memory state, merchant counts, RAM, LLM usage, capacity,
    # cost, financial, breach (legal). Operational/technical alerts
    # (invariant_regression, sentry_regression, slo_*, p95_slow_trend,
    # circuit_breaker_tripped, pipeline_stall_*, session_anomaly,
    # llm_safety_*, frontend_error*, onboarding_*, etc.) are handled
    # autonomously by the brain — they never page the founder.
    #
    # The brain still SEES every alert via /ops/system-health and
    # internal observability; Telegram is the strategic channel only.
    #
    # GROUND-TRUTH POLICY (G2 close 2026-05-06):
    #     Every entry MUST have a real emitter — `audit_telegram_
    #     allowlist_ground_truth.py` blocks phantom entries at preflight.
    #     When a NEW class of strategic signal becomes real
    #     (e.g. billing_payment_failure_critical when Stripe webhooks
    #     ship), that PR lands the allowlist entry AND the emitter
    #     atomically. Aspirational/reserved entries are kept in
    #     `_FUTURE_STRATEGIC_RESERVED` (informational, not enforced).
    #
    # When adding a new alert_type that should reach the founder,
    # land it here in the SAME commit as the emitter, AND document
    # the rationale in feedback_telegram_strategic_only_doctrine.md.
    # `audit_telegram_strategic_only.py` blocks new operational types
    # from reaching the Telegram path at preflight.
    "breach_response_required",       # GDPR Art. 33/34 — legal duty 72h clock
    # Emitter: app/services/breach_notification.py
})

# Reserved future strategic alert_types — NOT in the active allowlist
# until their emitter ships. Tracking here keeps the founder-channel
# vocabulary consistent without lying about coverage. Each entry must
# pair with a project_*.md / sprint memo identifying the trigger that
# activates the wiring.
_FUTURE_STRATEGIC_RESERVED = frozenset({
    # Pricing & billing — wires when first paying merchant lands
    # (billing_failure detection in app/api/billing.py + Stripe webhooks).
    "billing_payment_failure_critical",
    # Merchant churn detection — wires when retention surface ships.
    "merchant_churn_critical",
    # LLM exhaustion — currently uses direct send_message() in
    # llm_budget._send_exhaustion_alert (bypasses on_alert_responder).
    # heal-detection: responder forwards/routes alerts; does not own a recurring condition of its own
    # Wires when llm_budget is refactored to write_alert(); TIER_1.
    "llm_budget_exhaustion_strategic",
    # Infra cost — wires when cost-tracking emitter ships
    # (system_health_synthesizer._assess_cost is in place; not yet
    # firing as discrete write_alert).
    "infrastructure_cost_breach",
    # Security — wires when external attack detector ships.
    "security_critical_external",
    # Shopify app review — wires when app review portal monitor ships.
    "shopify_app_review_action_required",
    # Celebrate first paying merchant — wires alongside
    # billing_payment_failure_critical (Stripe activation event).
    "merchant_paying_first_install",
})


def _is_strategic_alert(alert: dict) -> bool:
    """Founder-doctrine strategic-only Telegram gate.
    Operational alerts handled autonomously must NOT reach the founder."""
    atype = (alert or {}).get("alert_type") or ""
    return atype in _TELEGRAM_STRATEGIC_ALLOWLIST


def _ping_founder_p0(alert: dict, verdict) -> bool:
    """Forward a P0 triage verdict to the operator Telegram channel.
    Returns True on successful dispatch. Never raises — Telegram
    outages must not break the triage loop.

    Strategic-only gate (founder direttiva 2026-05-05): operational
    alerts return False without sending. The autonomous brain handles
    those; the founder sees them only in /ops/system-health when
    actively investigating.
    """
    if not _is_strategic_alert(alert):
        log.info(
            "on_alert_responder: strategic-only gate suppressed Telegram "
            "ping for non-strategic alert_type=%s id=%s",
            alert.get("alert_type"), alert.get("id"),
        )
        return False
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
