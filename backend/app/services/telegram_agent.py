"""
telegram_agent.py — Telegram operator agent.

Sends structured messages to a Telegram chat via Bot API.
Supports read-only commands and operator control commands via webhook.

Configuration (env):
    TELEGRAM_BOT_TOKEN  — Bot API token (from @BotFather)
    TELEGRAM_CHAT_ID    — Authorized operator chat ID (for push + auth)

Safety:
    - Write commands (approve/reject/merge/apply) require TELEGRAM_CHAT_ID match
    - Read-only commands available to authorized chat only
    - No secrets in responses, no raw stack traces
    - All actions go through existing service layer (no business logic here)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.database import _ailab_dsn

log = logging.getLogger("telegram_agent")

_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
_TIMEOUT = 8.0

# ---------------------------------------------------------------------------
# Persistent HTTP client — avoids 5-10s TLS handshake on every message.
# The first call pays the connection cost; subsequent calls reuse it (<100ms).
# ---------------------------------------------------------------------------
_http_client: httpx.Client | None = None


def _get_http_client() -> httpx.Client:
    """Return a module-level persistent httpx.Client. Lazy-initialized."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.Client(
            http2=False,
            timeout=httpx.Timeout(_TIMEOUT, connect=_TIMEOUT),
        )
    return _http_client


def warmup_connection() -> None:
    """
    Pre-establish the TLS connection to Telegram API.
    Call once at startup (in a background thread) so the first operator
    command doesn't pay the 5-10s TLS handshake cost.

    Uses the shared client and sends a lightweight getMe request.
    Since the webhook now returns 200 immediately (fire-and-forget),
    there is no race — warmup and the first command simply queue on
    the connection pool and both complete without blocking the webhook.
    """
    from app.core.notifier_guard import is_real_send_allowed

    if not is_real_send_allowed():
        return

    if not _BOT_TOKEN:
        return
    try:
        client = _get_http_client()
        resp = client.get(
            f"https://api.telegram.org/bot{_BOT_TOKEN}/getMe",
        )
        log.info("telegram_agent: connection warmed up (status=%d)", resp.status_code)
        # Register bot menu commands on startup
        register_bot_commands()
    except Exception as exc:
        log.warning("telegram_agent: warmup failed (non-fatal): %s", type(exc).__name__)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def is_configured() -> bool:
    """Check if Telegram is configured."""
    return bool(_BOT_TOKEN) and bool(_CHAT_ID)


def is_authorized_chat(chat_id: str) -> bool:
    """Check if the chat ID matches the authorized operator chat."""
    if not _CHAT_ID:
        return False
    return str(chat_id).strip() == _CHAT_ID


# Rate limiting for commands runs through
# `app.core.telegram_safety.check_criticality_rate` (Redis-backed,
# criticality-aware). The legacy per-process `_check_rate_limit`/
# `_cmd_rate` helpers that used to live here were removed 2026-04-23
# during the telegram_agent audit — they were zero-caller dead code
# and created a false sense of rate-limiting while never being invoked.


def register_bot_commands() -> bool:
    """
    Register commands with Telegram BotFather so they appear in the / autocomplete menu.
    Call once at startup.
    """
    from app.core.notifier_guard import require_production

    if not require_production("telegram", "register_bot_commands"):
        return False

    if not _BOT_TOKEN:
        return False

    commands = [
        {"command": "status", "description": "System health status"},
        {"command": "bugfixes", "description": "List pending patches"},
        {"command": "incidents", "description": "Active merchant issues"},
        {"command": "costs", "description": "LLM budget breakdown"},
        {"command": "cleanup", "description": "Resolve all alerts + incidents"},
        {"command": "rollback", "description": "Revert an applied bugfix"},
        {"command": "help", "description": "All commands"},
    ]

    try:
        client = _get_http_client()
        resp = client.post(
            f"https://api.telegram.org/bot{_BOT_TOKEN}/setMyCommands",
            json={"commands": commands},
        )
        if resp.status_code == 200:
            log.info("telegram_agent: bot commands registered (%d commands)", len(commands))
            return True
        log.warning("telegram_agent: setMyCommands failed: %d", resp.status_code)
    except Exception as exc:
        log.warning("telegram_agent: setMyCommands error: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Send message
# ---------------------------------------------------------------------------

def _escape_markdown(text: str) -> str:
    """LEGACY — converts *bold* to HTML <b> and escapes for HTML mode."""
    return _to_html(text)


def _strip_markdown(text: str) -> str:
    """LEGACY — strips formatting markers, returns plain text."""
    import re
    text = re.sub(r'\*([^*]*)\*', r'\1', text)
    text = re.sub(r'`([^`]*)`', r'\1', text)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = text.replace("\\_", "_").replace("\\*", "*").replace("\\`", "`")
    text = text.replace("\\[", "[").replace("\\]", "]")
    return text


def _to_html(text: str) -> str:
    """
    Convert Markdown-style text to Telegram HTML.

    Converts: *bold* → <b>bold</b>
    Escapes all other HTML entities via html.escape().
    This eliminates ALL Markdown V1 escaping bugs permanently.
    """
    import html as _html
    import re

    # First: extract *bold* markers, protect them
    bold_parts = []
    def _bold_replace(m):
        bold_parts.append(m.group(1))
        return f"\x00BOLD{len(bold_parts) - 1}\x00"

    text = re.sub(r'\*([^*]+)\*', _bold_replace, text)

    # Escape ALL HTML entities in the remaining text
    text = _html.escape(text, quote=False)

    # Restore bold markers as HTML
    for i, part in enumerate(bold_parts):
        text = text.replace(f"\x00BOLD{i}\x00", f"<b>{_html.escape(part, quote=False)}</b>")

    return text


def _safe_html(text: str) -> str:
    """Escape dynamic content for safe inclusion in HTML messages."""
    import html as _html
    return _html.escape(str(text), quote=False)


def send_message(
    text: str,
    chat_id: str | None = None,
    parse_mode: str = "HTML",
    reply_to: int | None = None,
) -> bool | int:
    """
    Send a message via Telegram Bot API using HTML parse mode.

    Returns True/message_id if sent. Returns False on any failure.
    If reply_to is set, the message replies to that message ID (threading).

    Safety: if HTML parse fails (HTTP 400), automatically retries as plain text.
    """
    from app.core.execution_mode import is_dry_run
    from app.core.notifier_guard import require_production

    if not require_production("telegram", text):
        return False

    if not _BOT_TOKEN:
        log.debug("telegram_agent: not configured — skipping send")
        return False

    target = chat_id or _CHAT_ID
    if not target:
        log.debug("telegram_agent: no chat_id — skipping send")
        return False

    if is_dry_run():
        text = f"[DRY RUN] {text}"

    # Convert *bold* to <b>bold</b> and escape HTML entities
    formatted_text = _to_html(text)

    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"

    payload: dict = {
        "chat_id": target,
        "text": formatted_text,
        "parse_mode": "HTML",
    }
    if reply_to:
        payload["reply_to_message_id"] = reply_to

    try:
        client = _get_http_client()
        resp = client.post(url, json=payload)

        if resp.status_code == 200:
            msg_id = resp.json().get("result", {}).get("message_id")
            log.info("telegram_agent: message sent to %s (msg_id=%s)", target, msg_id)
            return msg_id or True

        # HTML parse failure → retry as plain text
        if resp.status_code == 400 and "parse entities" in (resp.text or "").lower():
            log.warning("telegram_agent: HTML parse failed — retrying as plain text")
            plain = _strip_markdown(text)
            payload_plain = {"chat_id": target, "text": plain}
            if reply_to:
                payload_plain["reply_to_message_id"] = reply_to
            resp2 = client.post(url, json=payload_plain)
            if resp2.status_code == 200:
                msg_id = resp2.json().get("result", {}).get("message_id")
                log.info("telegram_agent: message sent (plain fallback) to %s", target)
                return msg_id or True
            log.warning("telegram_agent: plain text fallback also failed: %d", resp2.status_code)
            return False

        log.warning("telegram_agent: API returned %d: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as exc:
        log.warning("telegram_agent: send failed: %s", type(exc).__name__)
        # Reset client on connection errors so next call retries fresh
        global _http_client
        _http_client = None
        return False


def send_message_with_buttons(
    text: str,
    buttons: list[list[dict]],
    chat_id: str | None = None,
    reply_to: int | None = None,
) -> bool | int:
    """
    Send a Telegram message with inline keyboard buttons (HTML mode).

    buttons format: [[{"text": "Approve", "callback_data": "/bugfix_approve 19917"}]]
    Returns message_id on success, False on failure.
    """
    from app.core.notifier_guard import require_production

    if not require_production("telegram", text):
        return False

    if not _BOT_TOKEN:
        return False

    target = chat_id or _CHAT_ID
    if not target:
        return False

    formatted_text = _to_html(text)
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"

    payload: dict = {
        "chat_id": target,
        "text": formatted_text,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": buttons},
    }
    if reply_to:
        payload["reply_to_message_id"] = reply_to

    try:
        client = _get_http_client()
        resp = client.post(url, json=payload)
        if resp.status_code == 200:
            msg_id = resp.json().get("result", {}).get("message_id")
            log.info("telegram_agent: message with buttons sent to %s (msg_id=%s)", target, msg_id)
            return msg_id or True

        # HTML fallback → plain text with buttons
        if resp.status_code == 400 and "parse entities" in (resp.text or "").lower():
            plain = _strip_markdown(text)
            payload["text"] = plain
            del payload["parse_mode"]
            resp2 = client.post(url, json=payload)
            if resp2.status_code == 200:
                return resp2.json().get("result", {}).get("message_id") or True

        log.warning("telegram_agent: button message failed: %d %s", resp.status_code, (resp.text or "")[:100])
        return False
    except Exception as exc:
        log.warning("telegram_agent: button send failed: %s", type(exc).__name__)
        return False


# ---------------------------------------------------------------------------
# Reviewer context — decision-first formatting
# ---------------------------------------------------------------------------

# Decision levels: what the operator sees first
_DECISION_GREEN = "\U0001f7e2 *You can proceed*"
_DECISION_YELLOW_CAUTION = "\U0001f7e1 *Proceed with caution*"
_DECISION_YELLOW_IMPROVE = "\U0001f7e1 *Needs improvement before proceeding*"
_DECISION_RED = "\U0001f534 *Do NOT proceed*"


def _classify_decision(assessment) -> str:
    """
    Map a reviewer assessment to a human decision level.

    Returns one of the _DECISION_* constants.
    """
    verdict = assessment.verdict
    risk = assessment.risk_level
    alignment = getattr(assessment, "strategic_alignment", "medium")

    # Red: reject, or high/critical risk
    if verdict == "reject" or risk in ("high", "critical"):
        return _DECISION_RED

    # Yellow-improve: refine, or weak alignment
    if verdict == "refine" or alignment == "weak":
        return _DECISION_YELLOW_IMPROVE

    # Yellow-caution: approve_with_notes, or medium risk
    if verdict == "approve_with_notes" or risk == "medium":
        return _DECISION_YELLOW_CAUTION

    # Green: approve + low risk + strong alignment
    return _DECISION_GREEN


def _build_explanation(assessment) -> list[str]:
    """
    Build max 3 human-readable reason bullets from assessment.
    Prefers blocking concerns, then notes, then a domain hint.
    """
    bullets: list[str] = []

    # Blocking concerns are highest priority
    if assessment.blocking_concerns_json:
        blocking = json.loads(assessment.blocking_concerns_json)
        for b in blocking[:2]:
            bullets.append(b[:100])

    # Notes fill remaining slots
    if assessment.notes_json and len(bullets) < 3:
        notes = json.loads(assessment.notes_json)
        for n in notes[: 3 - len(bullets)]:
            bullets.append(n[:100])

    # If still room, mention affected domains
    if len(bullets) < 3 and getattr(assessment, "affected_domains_json", None):
        domains = json.loads(assessment.affected_domains_json)
        if domains:
            sensitive = [d for d in domains if d in ("core", "billing", "auth", "migrations")]
            if sensitive:
                bullets.append(f"Touches critical area: {', '.join(sensitive)}")
            elif domains:
                bullets.append(f"Affects: {', '.join(domains[:3])}")

    return bullets[:3]


def _format_reviewer_decision(assessment, action_hint: str | None = None) -> str:
    """
    Format a reviewer assessment as a decision-first operator message.

    action_hint: e.g. "/approve 42" or "/bugfix_apply 8"
    """
    decision = _classify_decision(assessment)
    bullets = _build_explanation(assessment)

    lines = [decision]

    if bullets:
        lines.append("")
        for b in bullets:
            lines.append(f"\u2022 {b}")

    if action_hint:
        lines.extend(["", f"\U0001f449 {action_hint}"])

    return "\n".join(lines)


def _format_reviewer_inline(assessment) -> str:
    """
    One-line reviewer summary for list items.

    Example: "🟢 Safe to proceed" or "🔴 Blocked — touches billing"
    """
    verdict = assessment.verdict
    risk = assessment.risk_level

    if verdict == "reject" or risk in ("high", "critical"):
        reason = ""
        if assessment.blocking_concerns_json:
            blocking = json.loads(assessment.blocking_concerns_json)
            if blocking:
                reason = f" \u2014 {blocking[0][:50]}"
        return f"\U0001f534 Blocked{reason}"

    if verdict == "refine" or getattr(assessment, "strategic_alignment", "") == "weak":
        return "\U0001f7e1 Needs improvement"

    if verdict == "approve_with_notes" or risk == "medium":
        return "\U0001f7e1 Caution"

    return "\U0001f7e2 Safe to proceed"


def _get_reviewer_assessment(db, entity_type: str, entity_id: int):
    """Fetch latest reviewer assessment for an entity. Returns assessment or None."""
    try:
        from app.models.reviewer_assessment import ReviewerAssessment
        return (
            db.query(ReviewerAssessment)
            .filter(
                ReviewerAssessment.entity_type == entity_type,
                ReviewerAssessment.entity_id == entity_id,
            )
            .order_by(ReviewerAssessment.created_at.desc())
            .first()
        )
    except Exception:
        return None


def _get_reviewer_context(db, entity_type: str, entity_id: int) -> str | None:
    """Fetch decision-first reviewer summary for an entity. Returns None if no assessment."""
    assessment = _get_reviewer_assessment(db, entity_type, entity_id)
    if not assessment:
        return None
    return _format_reviewer_decision(assessment)


def _get_reviewer_for_display(db, entity_type: str, entity_id: int) -> str:
    """Get reviewer context block for post-action confirmations, or empty string."""
    assessment = _get_reviewer_assessment(db, entity_type, entity_id)
    if not assessment:
        return ""
    decision = _classify_decision(assessment)
    return f"\n{decision}"


# ---------------------------------------------------------------------------
# Time formatting helper
# ---------------------------------------------------------------------------

def _time_remaining(expires_at) -> str:
    """Format time remaining until expiry."""
    if not expires_at:
        return "?"
    now = _now()
    delta = expires_at - now
    minutes = int(delta.total_seconds() / 60)
    if minutes <= 0:
        return "expired"
    if minutes >= 60:
        return f"{minutes // 60}h {minutes % 60}m"
    return f"{minutes}m"


# ---------------------------------------------------------------------------
# Command router
# ---------------------------------------------------------------------------

# Commands that modify state — require authorized chat
_WRITE_COMMANDS = {
    "/approve", "/reject", "/bugfix_approve", "/bugfix_apply",
    "/merge", "/cleanup", "/rollback",
}

# All known commands
_ALL_COMMANDS = {
    "/status", "/evolution", "/costs", "/merchants", "/scaling",
    "/approvals", "/approve", "/reject",
    "/bugfixes", "/bugfix_approve", "/bugfix_apply",
    "/promotions", "/merge",
    "/review",
    "/incidents", "/meta_review", "/digest", "/webhooks",
    "/dashboard_restart",
    "/help",
}


def handle_command(command: str, db=None, chat_id: str | None = None) -> str:
    """
    Handle a Telegram command. Returns response text.

    Read-only commands work for authorized chat.
    Write commands require chat_id == TELEGRAM_CHAT_ID.
    """
    # Normalize: strip Markdown escape backslashes that Telegram may send
    # when user taps a command from a formatted message (e.g. /bugfix\_approve → /bugfix_approve)
    cleaned = command.strip().replace("\\_", "_").replace("\\*", "*")
    parts = cleaned.split()
    cmd = parts[0].lower() if parts else ""
    args = parts[1:] if len(parts) > 1 else []

    # Strip @botname suffix from command (e.g. /help@HedgeSparkBot)
    if "@" in cmd:
        cmd = cmd.split("@")[0]

    # Auth check: all commands require authorized chat
    if chat_id and not is_authorized_chat(chat_id):
        log.warning("telegram_agent: unauthorized chat %s attempted command %s", chat_id, cmd)
        return "Unauthorized. This bot only responds to the authorized operator chat."

    # Route to handlers
    handlers = {
        "/status": lambda: _cmd_status(db),
        "/evolution": lambda: _cmd_evolution(db),
        "/costs": lambda: _cmd_costs(db),
        "/merchants": lambda: _cmd_merchants(db),
        "/scaling": lambda: _cmd_scaling(db),
        "/approvals": lambda: _cmd_approvals(db),
        "/approve": lambda: _cmd_approve(db, args),
        "/reject": lambda: _cmd_reject(db, args),
        "/bugfixes": lambda: _cmd_bugfixes(db),
        "/bugfix_approve": lambda: _cmd_bugfix_approve(db, args),
        "/bugfix_apply": lambda: _cmd_bugfix_apply(db, args),
        "/promotions": lambda: _cmd_promotions(db),
        "/merge": lambda: _cmd_merge(db, args),
        "/review": lambda: _cmd_review(db, args),
        "/incidents": lambda: _cmd_incidents(db),
        "/meta_review": lambda: _cmd_meta_review(db),
        "/digest": lambda: _cmd_digest(db),
        "/webhooks": lambda: _cmd_webhooks(db),
        "/loop_health": lambda: _cmd_loop_health(db),
        "/weakness": lambda: _cmd_weakness(db),
        "/cleanup": lambda: _cmd_cleanup(db, chat_id=chat_id),
        "/cleanup_confirm": lambda: _cmd_cleanup_confirm(db, chat_id=chat_id),
        "/cleanup_cancel": lambda: _cmd_cleanup_cancel(db, chat_id=chat_id),
        "/cleanup_safe": lambda: _cmd_cleanup_safe(db, chat_id=chat_id),
        "/rollback": lambda: _cmd_rollback(db, args),
        "/dashboard_restart": lambda: _cmd_dashboard_restart(db, chat_id=chat_id),
        "/help": lambda: _cmd_help(db),
    }

    handler = handlers.get(cmd)
    if not handler:
        return _cmd_unknown(db)

    # Criticality-based rate limit
    from app.core.telegram_safety import check_criticality_rate
    rate_ok, rate_limit = check_criticality_rate(cmd)
    if not rate_ok:
        return f"Rate limited — max {rate_limit}/min for this command."

    try:
        return handler()
    except Exception as exc:
        log.warning("telegram_agent: command %s failed: %s", cmd, exc, exc_info=True)
        return f"Error processing {cmd}: {type(exc).__name__}"


# ---------------------------------------------------------------------------
# Read-only command handlers
# ---------------------------------------------------------------------------

def _cmd_status(db) -> str:
    """Return system status using CTO health model — same as daily digest."""
    if db is None:
        return "System status unavailable (no DB session)"

    # CTO health — single source of truth
    try:
        from app.core.redis_client import cache_get
        health = cache_get("hs:system_health")
        if not health:
            from app.services.system_health_synthesizer import synthesize_health
            h = synthesize_health(db)
            health = h.to_dict()
    except Exception:
        health = None

    lines = ["*System Status* — HedgeSpark", ""]

    if health:
        status = health.get("overall_status", "unknown").upper()
        icon = {"HEALTHY": "🟢", "DEGRADED": "🟡", "CRITICAL": "🔴"}.get(status, "⚪")
        lines.append(f"{icon} *{status}*")
        lines.append("")

        for d in health.get("dimensions", []):
            d_icon = {"healthy": "🟢", "degraded": "🟡", "critical": "🔴"}[d["status"]]
            trend = {"worsening": "↑", "improving": "↓", "stable": "→"}.get(d["trend"], "·")
            lines.append(f"  {d_icon} {d['name']}: {d['detail']} {trend}")

        if health.get("top_issues"):
            lines.append("")
            for issue in health["top_issues"][:3]:
                lines.append(f"  ⚠️ {issue}")
    else:
        lines.append("Health data unavailable")

    # LLM budget (always useful)
    try:
        from app.core.llm_budget import get_usage_summary
        s = get_usage_summary()
        lines.append("")
        lines.append(f"LLM: €{s['monthly_cost_eur']:.3f} / €{s['monthly_cap_eur']} "
                      f"({s['global_calls_today']} calls today)")
    except Exception as exc:
        log.warning("telegram_agent: LLM budget fetch failed: %s", exc)

    # Infra basics
    try:
        from app.services.system_summary import build_system_summary
        s = build_system_summary(db)
        ram = s["infra"]["ram"]
        lines.append(f"RAM: {ram.get('usage_pct', '?')}% | CPU: {s['infra']['cpu'].get('load_5m', '?')}")
    except Exception as exc:
        log.warning("telegram_agent: system summary fetch failed: %s", exc)

    return "\n".join(lines)


def _cmd_evolution(db) -> str:
    """Return last monthly Opus audit summary."""
    if db is None:
        return "Evolution data unavailable (no DB session)"

    from app.models.evolution_proposal import EvolutionProposal
    from sqlalchemy import desc

    proposals = (
        db.query(EvolutionProposal)
        .filter(EvolutionProposal.audit_cycle.like("%-M%"))  # monthly audits use YYYY-MM format
        .order_by(desc(EvolutionProposal.created_at))
        .limit(10)
        .all()
    )

    if not proposals:
        return "No monthly evolution proposals found yet."

    cycle = proposals[0].audit_cycle
    lines = [f"*Monthly Evolution Audit* \u2014 {cycle}", ""]

    for i, p in enumerate(proposals, 1):
        if p.audit_cycle != cycle:
            break
        status_icon = {"open": "\U0001f4cb", "accepted": "\u2705", "rejected": "\u274c"}.get(p.status, "\U0001f4cb")
        lines.append(f"{i}. {status_icon} [{p.risk_level}] {p.reason[:80]}")
        if p.expected_impact:
            lines.append(f"   Impact: {p.expected_impact[:60]}")

    return "\n".join(lines)


def _cmd_costs(db) -> str:
    """Return cost estimation breakdown with live LLM budget state."""
    if db is None:
        return "Cost data unavailable (no DB session)"

    from app.services.system_summary import build_system_summary
    s = build_system_summary(db)
    cost = s["cost_estimate"]
    fixed = cost["fixed_monthly_eur"]

    lines = [
        "*Monthly Cost Estimate* \u2014 HedgeSpark",
        "",
        "*Fixed costs:*",
    ]
    for name, amount in fixed.items():
        lines.append(f"  {name}: \u20ac{amount:.2f}")
    lines.append(f"  *Subtotal:* \u20ac{cost['fixed_total_eur']:.2f}")
    lines.append("")
    lines.append(f"*LLM (projected):* \u20ac{cost['llm_monthly_eur']:.2f}")
    lines.append(f"*Total:* \u20ac{cost['total_monthly_eur']:.2f}")

    # Live LLM budget state
    try:
        from app.core.llm_budget import get_usage_summary
        budget = get_usage_summary()
        lines.append("")
        lines.append("*LLM Budget (live):*")
        lines.append(f"  Month: {budget['month']}")
        lines.append(f"  Spent: \u20ac{budget['monthly_cost_eur']:.4f}")
        lines.append(f"  Cap: \u20ac{budget['monthly_cap_eur']:.2f}")
        lines.append(f"  Remaining: \u20ac{budget['monthly_remaining_eur']:.4f}")
        if budget["monthly_cap_reached"]:
            lines.append("  \u26a0\ufe0f *CAP REACHED* \u2014 LLM calls blocked")
        lines.append(f"  Calls today: {budget['global_calls_today']}/{budget['global_max_per_day']}")
        lines.append(f"  Blocked today: {budget['blocked_today']}")

        # Provider 429 backoff state
        for provider, state in budget.get("provider_429_state", {}).items():
            if state.get("backed_off"):
                lines.append(f"  \u26a0\ufe0f {provider}: backed off ({state['backoff_secs']}s)")
            elif state.get("total_429s", 0) > 0:
                lines.append(f"  {provider}: {state['total_429s']} 429s today (recovered)")
    except Exception:
        lines.append("")
        lines.append("*LLM Budget:* unavailable")

    return "\n".join(lines)


def _cmd_merchants(db) -> str:
    """Placeholder for merchant summary."""
    if db is None:
        return "Merchant data unavailable"

    try:
        from app.models.merchant import Merchant
        total = db.query(Merchant).count()
        active = db.query(Merchant).filter(Merchant.billing_active == True).count()
        return f"*Merchants:* {total} total, {active} billing active"
    except Exception:
        return "Merchant summary not yet available."


def _cmd_scaling(db) -> str:
    """Return active scaling recommendations + forecast."""
    if db is None:
        return "Scaling data unavailable (no DB session)"

    from app.services.scaling_intelligence import get_active_recommendations, build_forecast

    recs = get_active_recommendations(db)
    forecast = build_forecast(db)

    lines = ["*Scaling Intelligence* \u2014 HedgeSpark", ""]

    if forecast.get("status") == "not_enough_data":
        lines.append(f"Forecast: not enough data ({forecast.get('snapshots_available', 0)}/{forecast.get('minimum_required', 5)} days)")
    elif forecast.get("status") == "ok":
        m = forecast["merchants"]
        r = forecast["ram_pct"]
        llm = forecast["llm_daily_cost_eur"]
        lines.append(f"*Forecast ({forecast['horizon_days']}d, {forecast['confidence']} confidence):*")
        lines.append(f"  Merchants: {m['current']} \u2192 {m['projected']}")
        lines.append(f"  RAM: {r['current']}% \u2192 {r['projected']}%")
        lines.append(f"  LLM cost: \u20ac{llm['monthly_projected']:.2f}/mo projected")

    if recs:
        lines.append("")
        lines.append("*Active recommendations:*")
        for r in recs[:5]:
            icon = {"critical": "\U0001f534", "warning": "\U0001f7e1", "info": "\U0001f535"}.get(r["severity"], "\U0001f535")
            cost = f" (+\u20ac{r['cost_increase_eur']:.0f}/mo)" if r.get("cost_increase_eur") else ""
            lines.append(f"{icon} {r['title']}{cost}")
    else:
        lines.append("")
        lines.append("No active recommendations.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Operator command handlers (write operations)
# ---------------------------------------------------------------------------

def _cmd_approvals(db) -> str:
    """List pending TIER_1 action approvals.

    audit-log: read-only — the UPDATE below expires rows whose
    expires_at has already passed. This is a lazy-expiry hygiene
    pattern, not an operator-initiated destructive action: the
    expiration is time-driven, the operator is just the trigger
    for the cleanup to happen at this moment. No compliance audit
    needed per CLAUDE.md §9.3 (operator accountability applies to
    DECISIONS, not time-driven hygiene). If the lazy-expiry is ever
    moved to a scheduled task, remove this annotation.
    """
    if db is None:
        return "No DB session available."

    from app.models.action_approval import ActionApproval
    from sqlalchemy import text

    now = _now()

    # Expire old approvals (lazy-expiry hygiene — see docstring)
    db.execute(text(
        "UPDATE action_approvals SET status = 'expired' "
        "WHERE status = 'pending' AND expires_at < :now"
    ), {"now": now})
    db.commit()

    approvals = (
        db.query(ActionApproval)
        .filter(ActionApproval.status == "pending")
        .order_by(ActionApproval.created_at.desc())
        .limit(10)
        .all()
    )

    if not approvals:
        return "No pending approvals."

    lines = [f"\U0001f7e1 *Pending approvals:* {len(approvals)}", ""]

    for a in approvals:
        remaining = _time_remaining(a.expires_at)
        assessment = _get_reviewer_assessment(db, "action_approval", a.id)
        r_inline = _format_reviewer_inline(assessment) if assessment else ""

        lines.append(f"*#{a.id}* {a.action_type} `{a.target_id or ''}`")
        if r_inline:
            lines.append(f"  {r_inline}")
        lines.append(f"  Expires: {remaining}")
        if a.reason:
            lines.append(f"  {a.reason[:80]}")
        lines.append(f"  \U0001f449 /approve {a.id}  or  /reject {a.id}")
        lines.append("")

    return "\n".join(lines)


def _cmd_approve(db, args: list[str]) -> str:
    """Approve a pending ActionApproval."""
    if db is None:
        return "No DB session available."
    if not args:
        return "Usage: /approve <id>"

    try:
        approval_id = int(args[0])
    except ValueError:
        return "Invalid approval ID. Usage: /approve <id>"

    from app.models.action_approval import ActionApproval
    from app.services.orchestrator import ACTION_REGISTRY, _is_on_cooldown, _set_cooldown
    from app.services.audit import write_audit_log
    from app.services.outcome_evaluator import record_pending_outcome

    now = _now()

    approval = db.get(ActionApproval, approval_id)
    if not approval:
        return f"Approval #{approval_id} not found."
    if approval.status != "pending":
        return f"Approval #{approval_id} is already {approval.status}."
    if approval.expires_at and approval.expires_at < now:
        approval.status = "expired"
        db.commit()
        return f"Approval #{approval_id} has expired."

    # Validate action exists
    entry = ACTION_REGISTRY.get(approval.action_type)
    if not entry:
        return f"Unknown action type: {approval.action_type}"
    action_fn = entry[0]

    # Cooldown warning
    cooldown_warn = ""
    if _is_on_cooldown(approval.action_type, approval.target_id or ""):
        cooldown_warn = "\n\u26a0 Action was on cooldown but executed per operator override."

    # Execute
    try:
        exec_result = action_fn(db, approval.target_id or "")
    except Exception as exc:
        approval.status = "approved"
        approval.decided_at = now
        approval.decided_by = "telegram_operator"
        approval.reason = f"approved but execution failed: {str(exc)[:200]}"
        db.commit()
        return f"\u274c Approved #{approval_id} but execution failed: {type(exc).__name__}"

    # Update approval
    approval.status = "approved"
    approval.decided_at = now
    approval.decided_by = "telegram_operator"

    # Audit log
    audit_entry = write_audit_log(
        db,
        actor_type="human",
        actor_name="telegram_operator",
        action_type=f"approved_{approval.action_type}",
        target_type="system",
        target_id=approval.target_id,
        shop_domain=approval.shop_domain,
        after_state={"result": exec_result, "approval_id": approval_id},
        status="completed",
        approval_mode="human_approved",
        metadata={"channel": "telegram"},
    )

    # Outcome tracking
    record_pending_outcome(
        db,
        audit_log_id=audit_entry.id,
        action_type=f"approved_{approval.action_type}",
        target_id=approval.target_id,
        shop_domain=approval.shop_domain,
    )

    _set_cooldown(approval.action_type, approval.target_id or "")
    db.commit()

    reviewer_ctx = _get_reviewer_for_display(db, "action_approval", approval_id)

    return (
        f"\u2705 *Approved and executed* #{approval_id}\n"
        f"Action: {approval.action_type}\n"
        f"Target: {approval.target_id or 'n/a'}\n"
        f"Status: completed{cooldown_warn}"
        f"{reviewer_ctx}"
    )


def _cmd_reject(db, args: list[str]) -> str:
    """Reject a pending ActionApproval."""
    if db is None:
        return "No DB session available."
    if not args:
        return "Usage: /reject <id> [reason]"

    try:
        approval_id = int(args[0])
    except ValueError:
        return "Invalid approval ID. Usage: /reject <id> [reason]"

    reason = " ".join(args[1:]) if len(args) > 1 else None

    from app.models.action_approval import ActionApproval
    from app.services.audit import write_audit_log

    now = _now()

    approval = db.get(ActionApproval, approval_id)
    if not approval:
        return f"Approval #{approval_id} not found."
    if approval.status != "pending":
        return f"Approval #{approval_id} is already {approval.status}."

    approval.status = "rejected"
    approval.decided_at = now
    approval.decided_by = "telegram_operator"
    if reason:
        approval.reason = reason

    write_audit_log(
        db,
        actor_type="human",
        actor_name="telegram_operator",
        action_type=f"rejected_{approval.action_type}",
        target_type="system",
        target_id=approval.target_id,
        shop_domain=approval.shop_domain,
        status="rejected",
        approval_mode="human_approved",
        metadata={"channel": "telegram", "reason": reason},
    )

    db.commit()

    return (
        f"\u274c *Rejected* #{approval_id}\n"
        f"Action: {approval.action_type}\n"
        f"Target: {approval.target_id or 'n/a'}"
        + (f"\nReason: {reason}" if reason else "")
    )


def _cmd_bugfixes(db) -> str:
    """List bugfix candidates needing operator action."""
    if db is None:
        return "No DB session available."

    from app.models.bugfix_candidate import BugFixCandidate

    candidates = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.status.in_(["patch_proposed", "approved"]))
        .order_by(BugFixCandidate.created_at.desc())
        .limit(10)
        .all()
    )

    if not candidates:
        return "No bugfix candidates needing action."

    lines = [f"\U0001f41b *Bugfix candidates:* {len(candidates)}", ""]

    for c in candidates:
        assessment = _get_reviewer_assessment(db, "bugfix_candidate", c.id)
        r_inline = _format_reviewer_inline(assessment) if assessment else ""

        lines.append(f"*#{c.id}* {c.title[:60]}")
        lines.append(f"  Status: {c.status}")
        if r_inline:
            lines.append(f"  {r_inline}")
        if c.status == "patch_proposed":
            lines.append(f"  \U0001f449 /bugfix_approve {c.id}")
        elif c.status == "approved":
            lines.append(f"  \U0001f449 /bugfix_apply {c.id}")
        lines.append("")

    return "\n".join(lines)


def _cmd_bugfix_approve(db, args: list[str]) -> str:
    """Approve a proposed bugfix candidate. Idempotent + state-guarded."""
    if db is None:
        return "No DB session available."
    if not args:
        return "Usage: /bugfix_approve <id>"

    try:
        candidate_id = int(args[0])
    except ValueError:
        return "Invalid bugfix ID."

    from app.core.telegram_safety import check_idempotency, validate_transition
    from app.models.bugfix_candidate import BugFixCandidate
    from app.services.audit import write_audit_log

    # Idempotency check — blocks double-taps
    if not check_idempotency("bugfix_approve", str(candidate_id)):
        return f"Already processed — bugfix #{candidate_id} approve is in progress."

    now = _now()

    c = db.get(BugFixCandidate, candidate_id)
    if not c:
        return f"Bugfix #{candidate_id} not found."

    # State machine validation
    allowed, err = validate_transition(c.status, "approved")
    if not allowed:
        return f"❌ {err}"

    c.status = "approved"
    c.decided_by = "telegram_operator"
    c.decided_at = now

    write_audit_log(
        db,
        actor_type="human",
        actor_name="telegram_operator",
        action_type="bugfix_approved",
        target_type="bugfix",
        target_id=str(c.id),
        status="completed",
        approval_mode="human_approved",
        metadata={
            "channel": "telegram",
            "title": c.title,
            "patch_risk_tier": c.patch_risk_tier,
            "reviewer_assessment_id": getattr(c, "reviewer_assessment_id", None),
        },
    )
    db.commit()

    reviewer_ctx = _get_reviewer_for_display(db, "bugfix_candidate", candidate_id)

    return (
        f"\u2705 *Bugfix approved* #{candidate_id}\n"
        f"Title: {c.title[:80]}\n"
        f"Next: /bugfix_apply {candidate_id}"
        f"{reviewer_ctx}"
    )


def _cmd_bugfix_apply(db, args: list[str]) -> str:
    """Apply an approved bugfix. Idempotent + locked + state-guarded + traced."""
    if db is None:
        return "No DB session available."
    if not args:
        return "Usage: /bugfix_apply <id>"

    try:
        candidate_id = int(args[0])
    except ValueError:
        return "Invalid bugfix ID."

    from app.core.telegram_safety import (
        check_idempotency, validate_transition,
        acquire_execution_lock, release_execution_lock,
        send_progress,
    )
    from app.models.bugfix_candidate import BugFixCandidate

    # 1. Idempotency — FAIL-CLOSED: blocks if Redis down (critical operation)
    if not check_idempotency("bugfix_apply", str(candidate_id), critical=True):
        return f"⏳ Already processing — bugfix #{candidate_id} apply is in progress. (If Redis is down, critical actions are blocked for safety.)"

    c = db.get(BugFixCandidate, candidate_id)
    if not c:
        return f"Bugfix #{candidate_id} not found."

    # 2. State machine — only approved → applying is allowed
    allowed, err = validate_transition(c.status, "applying")
    if not allowed:
        return f"❌ {err}"

    # 3. Concurrency lock — prevents apply + rollback overlap
    if not acquire_execution_lock("bugfix", str(candidate_id)):
        return f"🔒 Another operation on #{candidate_id} is in progress. Wait and retry."

    try:
        # 4. Progress trace — immediate feedback
        progress_id = send_progress(
            f"⏳ *Applying bugfix #{candidate_id}...*\n\n"
            f"→ git apply...\n"
            f"→ pytest (120s timeout)...\n"
            f"→ PM2 restart + health check\n\n"
            f"Files: {_safe_html(c.patch_files or '[]')}\n"
            f"Risk: TIER_{c.patch_risk_tier or '?'}"
        )

        # 5. Run pipeline
        from app.services.bugfix_pipeline import apply_bugfix_candidate
        from app.services.audit import write_audit_log

        result = apply_bugfix_candidate(db, candidate_id)

        write_audit_log(
            db,
            actor_type="human",
            actor_name="telegram_operator",
            action_type="bugfix_apply_triggered",
            target_type="bugfix",
            target_id=str(candidate_id),
            after_state={
                "status": result.status,
                "test_passed": result.test_passed,
                "health_ok": result.health_ok,
            },
            status="completed" if result.status == "applied" else "failed",
            approval_mode="human_approved",
            metadata={"channel": "telegram"},
        )
        db.flush()

        # 6. Result — threaded reply to progress message
        reply_to = progress_id

        if result.status == "applied":
            # Refresh to get commit SHA
            db.refresh(c)
            msg = (
                f"✅ *Bugfix applied* #{candidate_id}\n\n"
                f"Tests: {('passed' if result.test_passed else 'failed')}\n"
                f"Health: {'ok' if result.health_ok else 'degraded'}\n"
                f"Commit: {c.git_commit_sha or 'n/a'}\n\n"
                f"Outcome measurement starts in 48h."
            )
            send_message(msg, reply_to=reply_to)
            # Add rollback button
            send_message_with_buttons(
                f"Rollback available:",
                [[{"text": f"🔄 Rollback #{candidate_id}", "callback_data": f"/rollback {candidate_id}"}]],
                reply_to=reply_to,
            )
            return msg
        else:
            msg = (
                f"❌ *Bugfix #{candidate_id} failed*\n\n"
                f"Status: {result.status}\n"
                f"Reason: {result.failure_reason or 'unknown'}\n\n"
                f"Patch was auto-reverted. No code changed."
            )
            send_message(msg, reply_to=reply_to)
            return msg

    finally:
        release_execution_lock("bugfix", str(candidate_id))


def _cmd_rollback(db, args: list[str]) -> str:
    """Rollback an applied bugfix. Requires confirmation + locked + idempotent."""
    if db is None:
        return "No DB session available."
    if not args:
        return "Usage: /rollback <candidate_id>"

    try:
        candidate_id = int(args[0])
    except ValueError:
        return "Invalid candidate ID."

    from app.core.telegram_safety import (
        check_idempotency, validate_transition,
        acquire_execution_lock, release_execution_lock,
        request_confirmation, check_confirmation,
        send_progress,
    )
    from app.models.bugfix_candidate import BugFixCandidate
    import subprocess

    c = db.get(BugFixCandidate, candidate_id)
    if not c:
        return f"Candidate #{candidate_id} not found."

    # State machine
    allowed, err = validate_transition(c.status, "rolled_back")
    if not allowed:
        return f"❌ {err}"

    if not c.git_commit_sha:
        return f"❌ #{candidate_id} has no recorded commit SHA."

    # Confirmation flow — first tap asks, second tap executes
    if not check_confirmation("rollback", str(candidate_id)):
        request_confirmation("rollback", str(candidate_id))
        send_message_with_buttons(
            f"⚠️ *Confirm rollback #{candidate_id}?*\n\n"
            f"This will revert commit {c.git_commit_sha[:8]} and restart the backend.\n\n"
            f"Tap again within 2 minutes to confirm:",
            [[{"text": f"🔄 CONFIRM Rollback #{candidate_id}", "callback_data": f"/rollback {candidate_id}"}]],
        )
        return ""  # sent via button message

    # Idempotency
    if not check_idempotency("rollback", str(candidate_id)):
        return f"⏳ Rollback #{candidate_id} already in progress."

    # Concurrency lock
    if not acquire_execution_lock("bugfix", str(candidate_id)):
        return f"🔒 Another operation on #{candidate_id} in progress."

    # best-effort: this function is invoked from the Telegram webhook
    # handler. If db.commit() raises mid-flow, the session is implicitly
    # rolled back when the FastAPI request dependency teardown closes
    # it after this function returns. The handler returns an error
    # string; the caller (handle_command) does not re-use the session.
    try:
        progress_id = send_progress(
            f"⏳ *Rolling back #{candidate_id}...*\n→ Reverting commit {c.git_commit_sha[:8]}..."
        )

        revert = subprocess.run(
            ["git", "revert", "--no-edit", c.git_commit_sha],
            capture_output=True, text=True, timeout=30,
            cwd="/opt/wishspark",
        )
        if revert.returncode != 0:
            send_message(f"❌ Git revert failed:\n{_safe_html(revert.stderr[:200])}", reply_to=progress_id)
            return ""

        send_progress("→ Restarted backend...", reply_to=progress_id)
        subprocess.run(["pm2", "restart", "wishspark-backend"], capture_output=True, timeout=15)
        import time as _time
        _time.sleep(4)

        try:
            import httpx
            health = httpx.get("http://127.0.0.1:8000/system/health", timeout=8.0)
            health_ok = health.status_code == 200
        except Exception:
            health_ok = False

        c.status = "rolled_back"
        c.failure_reason = "operator_rollback via Telegram"

        from app.services.audit import write_audit_log
        write_audit_log(
            db,
            actor_type="human",
            actor_name="telegram_operator",
            action_type="bugfix_rollback",
            target_type="bugfix",
            target_id=str(candidate_id),
            after_state={"reverted_sha": c.git_commit_sha, "health_ok": health_ok},
            status="completed",
            metadata={"channel": "telegram"},
        )
        db.commit()

        send_message(
            f"✅ *Bugfix #{candidate_id} rolled back*\n\n"
            f"Reverted: {c.git_commit_sha[:8]}\n"
            f"Health: {'ok ✓' if health_ok else '⚠️ degraded — /status'}\n"
            f"Backend restarted.",
            reply_to=progress_id,
        )
        return ""

    # best-effort error reporting: session cleanup is handled by the
    # FastAPI dependency teardown after this handler returns.
    except subprocess.TimeoutExpired:
        return f"❌ Rollback timed out. Manual intervention needed."
    except Exception as exc:
        return f"❌ Rollback failed: {_safe_html(str(exc)[:200])}"
    finally:
        release_execution_lock("bugfix", str(candidate_id))


def _cmd_dashboard_restart(db, *, chat_id: str | None = None) -> str:
    """Operator-initiated `pm2 restart wishspark-dashboard` for the stale
    Next.js manifest bug class. Honors the same hourly rate limit the
    autonomous remediation uses. Writes an audit_log entry with
    actor_type=operator."""
    if db is None:
        return "No DB session available."
    # Write command — require authorized chat
    if chat_id is not None and chat_id != _CHAT_ID:
        return "❌ Write commands require authorized operator chat."

    from app.services import dashboard_auto_remediation as remed
    report = remed.manual_restart(db, actor_name=f"telegram:{chat_id or 'console'}")

    if report["action"] == "rate_limited":
        return (
            "⏳ *Dashboard restart rate-limited*\n\n"
            f"Max {remed._RATE_LIMIT_PER_HOUR} restarts/hour. If the dashboard "
            "is still broken after the hour window, the cause is not a "
            "manifest drift — investigate pm2 logs + backend."
        )
    if report["action"] == "restart_failed":
        return (
            "❌ *Dashboard restart failed*\n\n"
            f"`{_safe_html((report.get('restart_error') or '')[:160])}`\n\n"
            "Manual intervention needed — SSH + `pm2 logs wishspark-dashboard`."
        )
    # restarted
    if report["ok"]:
        return (
            "✅ *Dashboard restarted — all assets resolve 200*\n\n"
            f"`pm2 restart {remed._PM2_PROCESS} --update-env`\n"
            "Audit row written."
        )
    failures = report.get("post_probe_failures") or []
    first = _safe_html(failures[0][:140]) if failures else ""
    return (
        "⚠️ *Dashboard restarted but probe still red*\n\n"
        f"Residual failures: {len(failures)}\n"
        f"First: `{first}`\n\n"
        "Not a manifest-drift bug — investigate build output + "
        "pm2 logs."
    )


def _cmd_promotions(db) -> str:
    """List promotions needing operator action."""
    if db is None:
        return "No DB session available."

    from app.models.autofix_promotion import AutoFixPromotion
    from app.models.bugfix_candidate import BugFixCandidate

    # Only show promotions whose candidate is still in an actionable state.
    # Exclude candidates that have been rejected, rolled back, or discarded —
    # their promotions are no longer relevant. Use outerjoin so promotions
    # with missing candidates still appear (edge case / data cleanup).
    from sqlalchemy import or_
    promotions = (
        db.query(AutoFixPromotion)
        .outerjoin(BugFixCandidate, BugFixCandidate.id == AutoFixPromotion.bugfix_candidate_id)
        .filter(
            AutoFixPromotion.status.notin_(["merged", "rejected", "failed"]),
            or_(
                BugFixCandidate.id.is_(None),  # no candidate linked
                BugFixCandidate.status.notin_(["rejected", "rolled_back", "discarded"]),
            ),
        )
        .order_by(AutoFixPromotion.created_at.desc())
        .limit(10)
        .all()
    )

    if not promotions:
        return "No active promotions."

    lines = [f"\U0001f680 *Active promotions:* {len(promotions)}", ""]

    for p in promotions:
        pr_info = ""
        if getattr(p, "pr_url", None):
            pr_info = f" | PR #{getattr(p, 'pr_number', '?')}"
        ci_info = ""
        if getattr(p, "remote_ci_status", None):
            ci_info = f" | CI: {p.remote_ci_status}"

        assessment = _get_reviewer_assessment(db, "bugfix_candidate", p.bugfix_candidate_id)
        r_inline = _format_reviewer_inline(assessment) if assessment else ""

        lines.append(f"*#{p.id}* candidate #{p.bugfix_candidate_id}")
        lines.append(f"  Status: {p.status}{pr_info}{ci_info}")
        if r_inline:
            lines.append(f"  {r_inline}")
        lines.append(f"  \U0001f449 /merge {p.id}")
        lines.append("")

    return "\n".join(lines)


def _cmd_merge(db, args: list[str]) -> str:
    """Merge a promotion PR through the existing gated merge path."""
    if db is None:
        return "No DB session available."
    if not args:
        return "Usage: /merge <id>"

    try:
        promo_id = int(args[0])
    except ValueError:
        return "Invalid promotion ID."

    from app.models.autofix_promotion import AutoFixPromotion
    from app.services.audit import write_audit_log

    p = db.get(AutoFixPromotion, promo_id)
    if not p:
        return f"Promotion #{promo_id} not found."

    # Check merge recommendation first
    merge_rec = None
    try:
        from app.services.merge_intelligence import compute_merge_recommendation
        merge_rec = compute_merge_recommendation(db, promo_id)
    except Exception as exc:
        log.warning("telegram_agent: merge recommendation failed: %s", exc)

    if merge_rec and not merge_rec.recommend:
        reasons = "\n".join(f"  - {r}" for r in merge_rec.reasons[:5])
        return (
            f"\U0001f6ab *Cannot merge* #{promo_id}\n"
            f"Merge recommendation: NO\n"
            f"Reasons:\n{reasons}"
        )

    # Use existing merge pipeline
    from app.services.promotion_pipeline import merge_promotion

    result = merge_promotion(db, promo_id)

    write_audit_log(
        db,
        actor_type="human",
        actor_name="telegram_operator",
        action_type="promotion_merge_triggered",
        target_type="promotion",
        target_id=str(promo_id),
        after_state={"result": result, "bugfix_candidate_id": p.bugfix_candidate_id},
        status="completed" if result == "merged" else "failed",
        approval_mode="human_approved",
        metadata={"channel": "telegram"},
    )
    db.commit()

    reviewer_ctx = _get_reviewer_for_display(db, "bugfix_candidate", p.bugfix_candidate_id)

    if result == "merged":
        rec_info = ""
        if merge_rec:
            rec_info = "\nMerge recommendation: YES"
        return (
            f"\u2705 *Merged* promotion #{promo_id}\n"
            f"Candidate: #{p.bugfix_candidate_id}{rec_info}"
            f"{reviewer_ctx}"
        )
    else:
        return (
            f"\u274c *Merge failed* #{promo_id}\n"
            f"Reason: {result}"
        )


def _cmd_review(db, args: list[str]) -> str:
    """Fetch compact reviewer verdict for any supported entity."""
    if db is None:
        return "No DB session available."
    if len(args) < 2:
        return (
            "Usage: /review <entity\\_type> <id>\n\n"
            "Types: bugfix, approval, promotion, evolution, model\\_upgrade, scaling"
        )

    entity_type_map = {
        "bugfix": "bugfix_candidate",
        "approval": "action_approval",
        "promotion": "bugfix_candidate",  # promotions reviewed via their linked bugfix
        "evolution": "evolution_proposal",
        "model_upgrade": "model_upgrade",
        "scaling": "scaling_recommendation",
    }

    raw_type = args[0].lower()
    entity_type = entity_type_map.get(raw_type)
    if not entity_type:
        return f"Unknown entity type: {raw_type}. Use: bugfix, approval, promotion, evolution, model\\_upgrade, scaling"

    try:
        entity_id = int(args[1])
    except ValueError:
        return "Invalid entity ID."

    # For promotions, resolve the bugfix_candidate_id
    if raw_type == "promotion":
        from app.models.autofix_promotion import AutoFixPromotion
        p = db.get(AutoFixPromotion, entity_id)
        if not p:
            return f"Promotion #{entity_id} not found."
        entity_id = p.bugfix_candidate_id

    ctx = _get_reviewer_context(db, entity_type, entity_id)
    if not ctx:
        return f"No reviewer assessment found for {raw_type} #{args[1]}."

    return f"*Reviewer Assessment* \u2014 {raw_type} #{args[1]}\n\n{ctx}"


def _cmd_incidents(db) -> str:
    """List recent open/triaged/investigating support incidents."""
    try:
        from app.models.support_incident import SupportIncident
        from sqlalchemy import desc

        incidents = (
            db.query(SupportIncident)
            .filter(SupportIncident.status.in_(["open", "triaged", "investigating"]))
            .order_by(desc(SupportIncident.created_at))
            .limit(10)
            .all()
        )

        if not incidents:
            return "No active support incidents."

        lines = [f"*Active Support Incidents* ({len(incidents)}):\n"]
        for inc in incidents:
            linked = ""
            if inc.linked_bugfix_candidate_id:
                linked = f" \u2192 bugfix #{inc.linked_bugfix_candidate_id}"
            lines.append(
                f"#{inc.id} *{inc.severity}* {inc.classification} "
                f"({inc.affected_area or 'unknown'}) \u2014 {inc.status}{linked}\n"
                f"  {inc.shop_domain} \u2014 {(inc.original_message or '')[:80]}"
            )

        return "\n".join(lines)
    except Exception as exc:
        return f"Error loading incidents: {exc}"


def _cmd_meta_review(db) -> str:
    """Show the latest meta-review summary."""
    try:
        from app.services.meta_reviewer import get_latest_meta_review

        review = get_latest_meta_review(db)
        if not review:
            return "No meta-review available yet. Runs weekly."

        meta = review.get("_meta", {})
        focus = review.get("weekly_focus_area", "unknown")
        priorities = review.get("priorities", [])
        conflicts = review.get("conflicts", [])
        summary = review.get("summary", "")
        budget = review.get("budget_guidance", "")

        lines = [
            f"*Meta-Review* ({meta.get('review_window', '?')})",
            f"Focus: *{focus}*",
            f"Proposals ranked: {len(priorities)}",
            f"Conflicts: {len(conflicts)}",
        ]

        if summary:
            lines.append(f"\n{summary[:300]}")
        if budget:
            lines.append(f"\nBudget: {budget[:200]}")

        # Top 5 priorities
        if priorities:
            lines.append("\n*Top priorities:*")
            for p in priorities[:5]:
                lines.append(
                    f"  #{p['proposal_id']} score={p['priority_score']} "
                    f"\u2192 {p['recommendation']}"
                )

        model = meta.get("model_used") or "deterministic"
        lines.append(f"\nModel: {model}")

        return "\n".join(lines)
    except Exception as exc:
        return f"Error loading meta-review: {exc}"


def _cmd_digest(db) -> str:
    """Build and return the daily health digest (manual trigger)."""
    return build_daily_digest(db)


def _cmd_webhooks(db) -> str:
    """Fleet-wide webhook status summary."""
    try:
        from app.services.webhook_monitor import get_fleet_webhook_summary
        summary = get_fleet_webhook_summary(db)

        sev = summary.get("by_severity", {})
        healthy = sev.get("healthy", 0)
        broken = sev.get("broken", 0)
        unreachable = sev.get("unreachable", 0)
        drifted = sev.get("drifted", 0)
        total = summary.get("total_merchants", 0)
        checked = summary.get("checked_merchants", 0)

        emoji = "\u2705" if broken == 0 and unreachable == 0 else ("\u26a0\ufe0f" if broken > 0 else "\U0001f534")
        lines = [
            f"{emoji} *Webhook Fleet Status*",
            f"Merchants: {total} total, {checked} checked",
            f"Healthy: {healthy} | Drifted: {drifted} | Broken: {broken} | Unreachable: {unreachable}",
        ]

        for shop_info in summary.get("broken_shops", [])[:5]:
            lines.append(f"  \U0001f534 {shop_info['shop']} — missing: {shop_info.get('missing', [])}")
        for shop_info in summary.get("unreachable_shops", [])[:3]:
            lines.append(f"  \u26a0\ufe0f {shop_info['shop']} — {shop_info.get('error', '?')[:60]}")

        if not summary.get("broken_shops") and not summary.get("unreachable_shops"):
            lines.append("All checked merchants are healthy.")

        return "\n".join(lines)
    except Exception as exc:
        return f"Webhook status unavailable: {exc}"


def _cmd_cleanup(db, chat_id: str | None = None) -> str:
    """
    Two-step cleanup: first call stages a confirmation in Redis;
    second call (/cleanup_confirm) actually executes.

    This prevents accidental one-tap board wipes.
    """
    if db is None:
        return "No DB session available."

    from app.core.redis_client import _client as get_redis

    redis = get_redis()
    key = f"hs:cleanup_pending:{chat_id or 'unknown'}"

    # Stage a pending cleanup (120s TTL)
    redis.set(key, "full", ex=120)

    from sqlalchemy import text
    alert_count = db.execute(text(
        "SELECT COUNT(*) FROM ops_alerts WHERE resolved = false"
    )).scalar() or 0

    send_message_with_buttons(
        f"\u26a0\ufe0f *Cleanup confirmation required*\n\n"
        f"This will resolve {alert_count} alert(s) and dismiss open incidents.\n\n"
        f"Send /cleanup\\_confirm to proceed or /cleanup\\_cancel to abort.\n"
        f"Expires in 2 minutes.",
        [],
    )
    return ""


def _cmd_cleanup_confirm(db, chat_id: str | None = None) -> str:
    """Execute a previously staged cleanup. Requires /cleanup first."""
    if db is None:
        return "No DB session available."

    from app.core.redis_client import _client as get_redis

    redis = get_redis()
    key = f"hs:cleanup_pending:{chat_id or 'unknown'}"
    scope = redis.get(key)

    if not scope:
        return "No pending cleanup — run /cleanup first (may have expired)."

    # Clear pending state
    redis.delete(key)

    from sqlalchemy import text as _text

    # Resolve all alerts
    alert_result = db.execute(_text("""
        UPDATE ops_alerts SET resolved = true, resolved_at = now()
        WHERE resolved = false
        RETURNING id
    """))
    alerts_resolved = len(alert_result.fetchall())

    # Dismiss all active incidents
    incident_result = db.execute(_text("""
        UPDATE support_incidents SET status = 'dismissed',
        resolved_at = now(), resolved_by = 'telegram_operator'
        WHERE status IN ('open', 'triaged', 'investigating')
        RETURNING id
    """))
    incidents_dismissed = len(incident_result.fetchall())

    # Discard stuck bugfix candidates (failed, not applied)
    candidate_result = db.execute(_text("""
        UPDATE bugfix_candidates SET status = 'discarded',
        failure_reason = 'operator_cleanup'
        WHERE status IN ('open', 'analyzed', 'apply_failed')
        RETURNING id
    """))
    candidates_discarded = len(candidate_result.fetchall())

    db.commit()

    # Decode scope for audit log
    scope_str = scope if isinstance(scope, str) else scope.decode() if isinstance(scope, bytes) else "full"

    # Hash-chained audit row — canonical operator-accountability per
    # CLAUDE.md §9.3. The log.warning below is a human-readable backup;
    # the chained row is the compliance-queryable proof of destructive
    # cleanup. Added 2026-04-23 during telegram_agent audit.
    try:
        from app.services.audit import write_audit_log
        write_audit_log(
            db,
            actor_type="telegram_operator",
            actor_name=str(chat_id or "unknown"),
            action_type="telegram_cleanup_confirm",
            target_type="ops_cleanup_batch",
            after_state={
                "scope": scope_str,
                "alerts_resolved": alerts_resolved,
                "incidents_dismissed": incidents_dismissed,
                "candidates_discarded": candidates_discarded,
            },
            status="completed",
            metadata={"command": "/cleanup_confirm", "scope": scope_str},
        )
        db.commit()
    except Exception as exc:
        # Destructive cleanup at line 1742 is ALREADY committed — the
        # audit row is best-effort. Rollback to keep the session usable
        # for any subsequent operation in the same handler. The
        # log.warning below remains as the human-readable backup.
        try:
            db.rollback()
        except Exception:
            pass  # SILENT-EXCEPT-OK: rollback-of-rollback in best-effort audit path
        log.error("cleanup_confirm: audit_log write failed (proceeding): %s", exc)

    log.warning(
        "AUDIT cleanup scope=%s actor_chat=%s alerts=%d incidents=%d candidates=%d",
        scope_str, chat_id or "unknown",
        alerts_resolved, incidents_dismissed, candidates_discarded,
    )

    total = alerts_resolved + incidents_dismissed + candidates_discarded
    if total == 0:
        return "\u2705 Cleanup complete (scope=full) — already clean."

    return (
        f"\u2705 *Cleanup complete* (scope=full)\n\n"
        f"Alerts resolved: {alerts_resolved}\n"
        f"Incidents dismissed: {incidents_dismissed}\n"
        f"Candidates discarded: {candidates_discarded}\n\n"
        f"Board is clear."
    )


def _cmd_cleanup_cancel(db, chat_id: str | None = None) -> str:
    """Cancel a pending cleanup."""
    from app.core.redis_client import _client as get_redis

    redis = get_redis()
    key = f"hs:cleanup_pending:{chat_id or 'unknown'}"
    redis.delete(key)
    return "Cleanup cancelled."


def _cmd_cleanup_safe(db, chat_id: str | None = None) -> str:
    """
    Safe cleanup: resolve only non-critical, old alerts.
    Never touches critical alerts or fresh incidents.
    """
    if db is None:
        return "No DB session available."

    from sqlalchemy import text as _text

    # Only resolve non-critical alerts older than 24h
    alert_result = db.execute(_text("""
        UPDATE ops_alerts SET resolved = true, resolved_at = now()
        WHERE resolved = false
          AND severity != 'critical'
          AND created_at < now() - interval '24 hours'
        RETURNING id
    """))
    alerts_resolved = len(alert_result.fetchall())

    db.commit()

    # Hash-chained audit row \u2014 mirrors _cmd_cleanup_confirm. Safe cleanup
    # touches only non-critical old alerts, but operator-action still
    # deserves a queryable chain entry for compliance.
    try:
        from app.services.audit import write_audit_log
        write_audit_log(
            db,
            actor_type="telegram_operator",
            actor_name=str(chat_id or "unknown"),
            action_type="telegram_cleanup_safe",
            target_type="ops_cleanup_batch",
            after_state={
                "scope": "safe",
                "alerts_resolved": alerts_resolved,
            },
            status="completed",
            metadata={"command": "/cleanup_safe", "scope": "safe"},
        )
        db.commit()
    except Exception as exc:
        # Mirror of _cmd_cleanup_confirm: destructive cleanup at line
        # 1821 is ALREADY committed; the audit row is best-effort.
        # Rollback to leave the session in a usable state.
        try:
            db.rollback()
        except Exception:
            pass  # SILENT-EXCEPT-OK: rollback-of-rollback in best-effort audit path
        log.error("cleanup_safe: audit_log write failed (proceeding): %s", exc)

    log.warning(
        "AUDIT cleanup scope=%s actor_chat=%s alerts=%d",
        "safe", chat_id or "unknown", alerts_resolved,
    )

    if alerts_resolved == 0:
        return "\u2705 Safe cleanup: nothing to resolve (all alerts are critical or fresh)."

    return (
        f"\u2705 *Safe cleanup complete* (scope=safe)\n\n"
        f"Non-critical alerts resolved: {alerts_resolved}\n"
        f"Critical alerts preserved."
    )


def _cmd_loop_health(db) -> str:
    """Autonomous loop health snapshot."""
    from app.services.loop_health import get_loop_health
    h = get_loop_health(db)

    healthy = "healthy" if h["is_healthy"] else "NEEDS ATTENTION"
    lines = [
        f"*Loop Health* — {healthy}",
        "",
        f"*Throughput (7d):*",
        f"  Applied: {h['throughput_7d'].get('bugfixes_applied_7d', 0)}",
        f"  Proposed: {h['throughput_7d'].get('patches_proposed_7d', 0)}",
        f"  Evolutions: {h['throughput_7d'].get('evolutions_converted_7d', 0)}",
        f"  Failure rate: {h['failure_rate_30d_pct']}%",
    ]

    outcomes = h.get("outcomes_30d", {})
    if outcomes:
        lines.append(f"\n*Outcomes (30d):*")
        for k, v in outcomes.items():
            lines.append(f"  {k}: {v}")

    stuck = h.get("stuck_items", [])
    if stuck:
        lines.append(f"\n*Stuck ({len(stuck)}):*")
        for s in stuck[:5]:
            lines.append(f"  {s['count']}x {s['status']} (>{s['threshold_hours']}h)")

    thrashing = h.get("thrashing_sources", [])
    if thrashing:
        lines.append(f"\n*Thrashing ({len(thrashing)}):*")
        for t in thrashing[:3]:
            lines.append(f"  {t['source_ref']} ({t['failure_count']}x fails)")

    weak = h.get("weakest_subsystems", [])
    if weak:
        lines.append(f"\n*Weakest:*")
        for w in weak[:3]:
            lines.append(f"  {w['domain']} — score {w['score']} [{w['criticality']}]")

    return "\n".join(lines)


def _cmd_weakness(db) -> str:
    """Subsystem weakness ranking."""
    from app.services.loop_health import score_subsystem_weakness
    ranking = score_subsystem_weakness(db, lookback_days=30)

    if not ranking:
        return "*Subsystem Weakness* — No weakness signals detected. All systems healthy."

    lines = ["*Subsystem Weakness* (30d, weakest first)", ""]
    for i, w in enumerate(ranking[:10], 1):
        reasons = ", ".join(w["reasons"][:3])
        lines.append(f"{i}. *{w['domain']}* — score {w['score']} [{w['criticality']}]")
        lines.append(f"   {reasons}")

    return "\n".join(lines)


def _cmd_help(db) -> str:
    """Full command list."""
    return (
        "*HedgeSpark Operator Bot*\n\n"
        "*Status & Info:*\n"
        "/status \u2014 system health summary\n"
        "/evolution \u2014 last monthly audit proposals\n"
        "/costs \u2014 cost estimation breakdown\n"
        "/merchants \u2014 merchant summary\n"
        "/scaling \u2014 scaling forecast + recommendations\n"
        "/incidents \u2014 active support incidents\n"
        "/meta_review \u2014 latest strategic meta-review\n"
        "/digest \u2014 daily health digest\n"
        "/webhooks \u2014 webhook fleet status\n\n"
        "*Approvals:*\n"
        "/approvals \u2014 list pending action approvals\n"
        "/approve <id> \u2014 approve and execute\n"
        "/reject <id> [reason] \u2014 reject with optional reason\n\n"
        "*Bugfixes:*\n"
        "/bugfixes \u2014 list bugfixes needing action\n"
        "/bugfix_approve <id> \u2014 approve a proposed patch\n"
        "/bugfix_apply <id> \u2014 apply approved patch\n\n"
        "*Promotions:*\n"
        "/promotions \u2014 list active promotions\n"
        "/merge <id> \u2014 merge eligible promotion PR\n\n"
        "*Review:*\n"
        "/review <type> <id> \u2014 reviewer verdict\n"
        "  types: bugfix, approval, promotion, evolution, model\\_upgrade, scaling\n\n"
        "*Loop Intelligence:*\n"
        "/loop_health \u2014 autonomous loop health snapshot\n"
        "/weakness \u2014 subsystem weakness ranking\n\n"
        "/cleanup \u2014 resolve all alerts + dismiss all incidents\n"
        "/rollback <id> \u2014 revert an applied bugfix\n"
        "/dashboard_restart \u2014 force pm2 restart wishspark-dashboard + asset probe\n"
        "/help \u2014 this message"
    )


def _cmd_unknown(db) -> str:
    return "Unknown command. Type /help for available commands."


# ---------------------------------------------------------------------------
# Monthly report message
# ---------------------------------------------------------------------------

def send_monthly_report(proposals: list[dict], system_summary: dict) -> bool:
    """
    Send the monthly evolution report to Telegram.
    Called after monthly Opus audit completes.
    """
    ram = system_summary["infra"]["ram"]
    workers = system_summary["infra"]["workers"]
    llm = system_summary["llm_usage"]
    cost = system_summary["cost_estimate"]

    lines = [
        "*Monthly Evolution Report \u2014 HedgeSpark*",
        "",
        "Opus audit completed.",
        "",
    ]

    if proposals:
        lines.append("*Top improvements proposed:*")
        for i, p in enumerate(proposals[:5], 1):
            lines.append(f"{i}. [{p.get('type', '?')}] {p.get('title', 'Untitled')}")
        lines.append("")

    lines.extend([
        "*System status:*",
        f"\u2022 RAM: {ram.get('usage_pct', '?')}% ({ram.get('used_mb', '?')}MB / {ram.get('total_mb', '?')}MB)",
        f"\u2022 Worker health: {workers.get('error_rate_pct', 0)}% error rate ({workers.get('cycles_24h', 0)} cycles/24h)",
        f"\u2022 LLM usage: {llm.get('global_calls_today', 0)} calls today",
        "",
        "*Estimated monthly cost:*",
        f"\u2022 LLM: \u20ac{cost.get('llm_monthly_eur', 0):.2f}",
        f"\u2022 Server: \u20ac{cost['fixed_monthly_eur'].get('server_vps', 0):.2f}",
        f"\u2022 Total: \u20ac{cost.get('total_monthly_eur', 0):.2f}",
    ])

    warnings = system_summary.get("warnings", [])
    if warnings:
        lines.append("")
        lines.append("*Recommendations:*")
        for w in warnings[:3]:
            lines.append(f"\u2022 {w}")

    # Market intelligence from AI Lab (fail-soft)
    try:
        import psycopg2
        import psycopg2.extras
        ailab_conn = psycopg2.connect(_ailab_dsn(), connect_timeout=3)
        try:
            cur = ailab_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT title, hedgespark_action, relevance_score
                FROM market_ideas
                WHERE status = 'new'
                ORDER BY relevance_score DESC, evidence_count DESC
                LIMIT 5
            """)
            ideas = cur.fetchall()

            cur.execute("""
                SELECT pain_category, COUNT(*) as cnt
                FROM pain_points
                WHERE hedgespark_relevant
                  AND created_at > NOW() - INTERVAL '30 days'
                GROUP BY pain_category
                ORDER BY cnt DESC LIMIT 5
            """)
            pains = cur.fetchall()

            cur.close()
        finally:
            ailab_conn.close()

        if ideas or pains:
            lines.append("")
            lines.append("*Market Intelligence (AI Lab):*")
            if pains:
                pain_str = ", ".join(f"{p['pain_category']}({p['cnt']})" for p in pains[:3])
                lines.append(f"\u2022 Top pain areas: {pain_str}")
            if ideas:
                lines.append("\u2022 Top opportunities:")
                for idea in ideas[:3]:
                    lines.append(f"  [{idea['relevance_score']}/100] {idea['title']}")

            # Mark reported
            try:
                mark_conn = psycopg2.connect(_ailab_dsn(), connect_timeout=3)
                try:
                    mark_cur = mark_conn.cursor()
                    idea_titles = [i["title"] for i in ideas]
                    if idea_titles:
                        mark_cur.execute(
                            "UPDATE market_ideas SET status = 'reported', reported_at = NOW() "
                            "WHERE title = ANY(%s) AND status = 'new'",
                            (idea_titles,),
                        )
                        mark_conn.commit()
                    mark_cur.close()
                finally:
                    mark_conn.close()
            except Exception as exc:
                log.warning("telegram_agent: ailab idea mark failed: %s", exc)

    except Exception as exc:
        log.warning("telegram_agent: ailab market intel unavailable: %s", exc)

    lines.extend([
        "",
        "Reply with:",
        "/evolution \u2014 full proposal list",
        "/status \u2014 live system status",
        "/costs \u2014 cost breakdown",
    ])

    return send_message("\n".join(lines))


def send_reviewer_verdict(assessment, entity_title: str | None = None) -> bool:
    """
    Send a decision-first reviewer verdict to Telegram WITH action buttons.

    Buttons solve the Telegram limitation where tapping a /command in text
    only sends the command without arguments. Inline keyboard buttons carry
    the full command as callback_data.
    """
    title = entity_title or f"{assessment.entity_type} #{assessment.entity_id}"
    etype = assessment.entity_type
    eid = assessment.entity_id

    # Build text
    action_hint = None
    buttons = []

    if assessment.verdict == "reject" or assessment.risk_level in ("high", "critical"):
        action_hint = "Do not apply this change."
    elif etype == "bugfix_candidate":
        action_hint = f"Step 1: Approve | Step 2: Apply"
        buttons = [
            [
                {"text": f"Approve #{eid}", "callback_data": f"/bugfix_approve {eid}"},
                {"text": f"Apply #{eid}", "callback_data": f"/bugfix_apply {eid}"},
            ],
        ]
    elif etype == "action_approval":
        action_hint = f"Tap to approve:"
        buttons = [
            [{"text": f"Approve #{eid}", "callback_data": f"/approve {eid}"}],
        ]

    decision_block = _format_reviewer_decision(assessment, action_hint=action_hint)

    lines = [
        f"*{title[:120]}*",
        "",
        decision_block,
    ]

    if buttons:
        return send_message_with_buttons("\n".join(lines), buttons)
    return send_message("\n".join(lines))


def send_scaling_alert(recommendation: dict, forecast: dict) -> bool:
    """
    Send a Telegram notification for a new scaling recommendation.
    Called after recommendation engine creates significant entries.
    """
    merch = forecast.get("merchants", {})
    ram = forecast.get("ram_pct", {})

    lines = [
        "*Scaling Recommendation \u2014 HedgeSpark*",
        "",
        "*Current:*",
        f"\u2022 Active merchants: {merch.get('current', '?')}",
        f"\u2022 RAM usage: {ram.get('current', '?')}%",
        "",
        f"*Projected in {forecast.get('horizon_days', 30)} days:*",
        f"\u2022 Active merchants: {merch.get('projected', '?')}",
        f"\u2022 RAM usage: {ram.get('projected', '?')}%",
        "",
        f"*Recommendation:*",
        f"\u2192 {recommendation.get('title', 'Review scaling')}",
    ]

    cost = recommendation.get("estimated_cost_increase_eur")
    if cost:
        lines.extend(["", f"*Estimated additional cost:* +\u20ac{cost:.0f}/mo"])

    lines.extend([
        "",
        f"*Reason:*",
        recommendation.get("reason", "")[:200],
        "",
        "Commands:",
        "/scaling \u2014 full forecast",
        "/status \u2014 system status",
        "/costs \u2014 cost breakdown",
    ])

    return send_message("\n".join(lines))


# ---------------------------------------------------------------------------
# Daily health digest
# ---------------------------------------------------------------------------

def is_digest_quiet(db) -> bool:
    """
    Decide whether today's digest state has nothing that needs founder
    attention. Used by the scheduler to implement silence policy
    (Option B — send only when ATTENTION or higher).

    Returns True when ALL of:
      - system_health overall_status == 'healthy'
      - No TIER_2 candidates awaiting review
      - No rollbacks in last 24h
      - No unresolved critical ops_alerts in last 24h
      - No single alert_type exceeding 20 rows in last 24h (spike)

    Uses the same truth sources as build_daily_digest.attention_lines —
    if you add a new attention source there, mirror it here, or the
    scheduler will silence a message that should have fired. Fails open
    (returns False → send) on any query error rather than suppress.
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text as sql_text

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff_24h = now_utc - timedelta(hours=24)

    try:
        from app.core.redis_client import cache_get
        health = cache_get("hs:system_health")
        if not health:
            from app.services.system_health_synthesizer import synthesize_health
            health = synthesize_health(db).to_dict()
        if (health or {}).get("overall_status") != "healthy":
            return False
    except Exception as exc:
        log.warning("telegram_agent: is_digest_quiet health probe failed: %s", exc)
        return False

    try:
        if (db.execute(sql_text(
            "SELECT COUNT(*) FROM bugfix_candidates "
            "WHERE status='patch_proposed' AND patch_risk_tier=2"
        )).scalar() or 0) > 0:
            return False
        if (db.execute(sql_text(
            "SELECT COUNT(*) FROM bugfix_candidates "
            "WHERE status='rolled_back' AND applied_at >= :c"
        ), {"c": cutoff_24h}).scalar() or 0) > 0:
            return False
        if (db.execute(sql_text(
            "SELECT COUNT(*) FROM ops_alerts "
            "WHERE severity='critical' AND resolved=false AND created_at >= :c"
        ), {"c": cutoff_24h}).scalar() or 0) > 0:
            return False
        # Same filter rule as build_daily_digest attention B3b — only
        # unresolved warning/critical counts as a "spike" for silence
        # purposes. Self-resolving probes (heartbeat_synthetic_test) and
        # info-level telemetry do not silence the silence.
        spike = db.execute(sql_text(
            "SELECT alert_type FROM ops_alerts "
            "WHERE created_at >= :c "
            "  AND severity IN ('warning', 'critical') "
            "  AND resolved = false "
            "GROUP BY alert_type HAVING COUNT(*) > 20 LIMIT 1"
        ), {"c": cutoff_24h}).scalar()
        if spike:
            return False
    except Exception as exc:
        log.warning("telegram_agent: is_digest_quiet attention-probe failed: %s", exc)
        return False

    return True


def build_daily_digest(db) -> str:
    """
    Founder morning newspaper. Scannable in 3 seconds.

    Structure:
      1. HEADLINE — one emoji + status, date
      2. REVENUE — the money line (this week vs last, trend)
      3. MERCHANTS — count + churn alert if any
      4. SHIELD LINE — compliance grade + proven savings
      5. PIPELINE — one-liner: fixes shipped / rollbacks
      6. ATTENTION — only if something truly needs the founder
      7. FOOTER — drill-down commands

    Everything else lives in /status, /costs, /bugfixes, /incidents.
    """
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    from sqlalchemy import text as sql_text

    now_rome = datetime.now(ZoneInfo("Europe/Rome"))
    # Keep naive-UTC to match the rest of this file's comparisons against
    # TIMESTAMP WITHOUT TIME ZONE columns. utcnow() is deprecated so we
    # materialize the same value via now(timezone.utc).replace(tzinfo=None).
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff_24h = now_utc - timedelta(hours=24)
    cutoff_7d = now_utc - timedelta(days=7)
    cutoff_14d = now_utc - timedelta(days=14)

    # ── Determine overall status ──
    overall_status = "OK"
    attention_lines: list[str] = []
    rolled_24h = 0  # set early so attention section can reference it

    try:
        from app.core.redis_client import cache_get
        health = cache_get("hs:system_health")
        if not health:
            from app.services.system_health_synthesizer import synthesize_health
            h = synthesize_health(db)
            health = h.to_dict()
        cto_status = health.get("overall_status", "unknown")
        if cto_status == "critical":
            overall_status = "CRITICAL"
        elif cto_status == "degraded":
            overall_status = "WARNING"
        # Surface only actionable critical dimensions — skip alert
        # accumulation noise (that's an ops metric, not a founder action)
        _SKIP_DIMENSIONS = {"alerts", "fix_rate"}
        for d in health.get("dimensions", []):
            if d["status"] == "critical" and d["name"] not in _SKIP_DIMENSIONS:
                attention_lines.append(f"\U0001f534 {d['name']}: {d['detail']}")
    except Exception:
        overall_status = "WARNING"

    # ── 1. HEADLINE (placeholder — finalized after attention section) ──
    day_name = now_rome.strftime("%A")
    date_str = now_rome.strftime("%-d %B")

    lines: list[str] = [
        f"\U0001f4ca *Daily Digest* \u2014 {day_name} {date_str}",
        "",
        "__STATUS_PLACEHOLDER__",  # replaced at the end
    ]

    # ── 2. (REVENUE removed 2026-05-07) ──
    # Founder digest is OPERATOR/CTO scope only — no merchant-aggregate
    # revenue framing.
    #
    # Rationale: founder explicit feedback 2026-05-07 verbatim "Mi
    # prendi per il culo? Io Founder che ricevo reveneu at risk come
    # fossi un merchant?!". Pre-merchant, the only "revenue" in
    # shop_orders comes from dev/test shops — surfacing that as
    # "Revenue €3,090 this week" / "€20,674 at risk" / "AOV €X" /
    # "€Y prevented (holdout)" reads as a MERCHANT digest (your-store
    # framing) rather than an operator one, which is misleading and
    # CLAUDE.md §0 forbids ("no false claims").
    #
    # Network-revenue aggregates belong on:
    #   - merchant_digest.py (per-merchant, operator-filtered)
    #   - admin /status command (explicit query, framed network-scope)
    #   - public_roi_counter.py (operator-filtered, public marketing)
    # NOT on the founder's daily Telegram digest.
    #
    # NOTE: legacy revenue/AOV/RARS/proven-savings + merchants/churn
    # blocks were physically REMOVED in this commit. The audit
    # `audit_telegram_founder_digest_scope.py` (born same commit) blocks
    # any re-introduction of merchant-aggregate `shop_orders` /
    # `total_price` / `rars_history` / `compute_churn_report` calls in
    # `build_daily_digest`.

    # ── 3. NETWORK STATE — operator scope, not merchant scope ──
    # Show installed-merchants COUNT only (operator metadata). NEVER
    # per-shop revenue/AOV/at-risk/churn — those belong on per-merchant
    # digests. Pre-merchant: expect "0 paying" — the truth, no framing.
    try:
        merch_row = db.execute(sql_text(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE billing_active = true) "
            "FROM merchants WHERE install_status = 'active'"
        )).fetchone()
        if merch_row:
            active, paying = merch_row[0], merch_row[1]
            lines.append("")
            lines.append(
                f"\U0001f465 *Network:* {active} merchant"
                f"{'s' if active != 1 else ''} installed · "
                f"{paying} paying"
            )
    except Exception as exc:
        log.warning("telegram_agent: network count failed: %s", exc)

    # ── DEAD-CODE BLOCK INTENTIONALLY EXCISED ──
    # The block previously starting here built per-currency revenue
    # aggregates from `shop_orders`, computed AOV, summed rars_history,
    # called `get_weekly_proven_savings`, queried `merchants` for churn,
    # and pasted the 5-line merchant-style summary into the founder
    # digest. Excised 2026-05-07 per founder feedback. The audit
    # `audit_telegram_founder_digest_scope` is the structural preventer.

    # ── 4. SHIELD LINE — compliance + security in one line ──
    try:
        from app.services.compliance_score import (
            compute_compliance_score,
            get_cached_compliance_score,
        )
        compliance = get_cached_compliance_score() or compute_compliance_score(db)
        score_val = compliance.get("score", 0)
        grade = compliance.get("grade", "?")
        grade_emoji = "\U0001f7e2" if score_val >= 90 else ("\U0001f7e1" if score_val >= 70 else "\U0001f534")
        lines.append("")
        lines.append(f"\U0001f6e1 *Compliance:* {grade_emoji} {grade} ({score_val}/100)")
        if score_val < 70:
            if overall_status == "OK":
                overall_status = "WARNING"
    except Exception as exc:
        log.warning("telegram_agent: compliance score fetch failed: %s", exc)

    # ── 5. PIPELINE — one compact line ──
    try:
        applied_24h = db.execute(sql_text(
            "SELECT COUNT(*) FROM bugfix_candidates "
            "WHERE status = 'applied' AND applied_at >= :c"
        ), {"c": cutoff_24h}).scalar() or 0
        rolled_24h = db.execute(sql_text(
            "SELECT COUNT(*) FROM bugfix_candidates "
            "WHERE status = 'rolled_back' AND applied_at >= :c"
        ), {"c": cutoff_24h}).scalar() or 0

        # Phase B retro-grep alerts in last 24h — counts cases where
        # the pipeline applied a "fix" that did NOT strictly decrease
        # an UPPER_SNAKE pattern signature. Surfaces silent fix-
        # incomplete to the founder so multidim discipline stays
        # visible day to day.
        retro_alerts_24h = db.execute(sql_text(
            "SELECT COUNT(*) FROM ops_alerts "
            "WHERE alert_type = 'fix_incomplete' AND created_at >= :c"
        ), {"c": cutoff_24h}).scalar() or 0

        # Triage activity in 24h — open/in-flight candidates created
        # during the window. Triaged > applied = pipeline producing
        # without converging; useful health signal.
        triaged_24h = db.execute(sql_text(
            "SELECT COUNT(*) FROM bugfix_candidates "
            "WHERE created_at >= :c"
        ), {"c": cutoff_24h}).scalar() or 0

        # Dormancy-aware framing: when parked by design (enrichers off
        # pre-merchant), "in flight" misleads — nothing is moving. Show
        # "queued (parked)" so founder knows count is by-design.
        from app.services.pipeline_state import is_pipeline_dormant
        _dormant = is_pipeline_dormant()

        lines.append("")
        pipe_parts = [f"{applied_24h} fixes shipped"]
        if triaged_24h > applied_24h:
            queued = triaged_24h - applied_24h
            label = "queued (parked)" if _dormant else "in flight"
            pipe_parts.append(f"{queued} {label}")
        if rolled_24h > 0:
            pipe_parts.append(f"\U0001f534 {rolled_24h} rolled back")
            if overall_status == "OK":
                overall_status = "WARNING"
        if retro_alerts_24h > 0:
            pipe_parts.append(f"\U0001f7e0 {retro_alerts_24h} fix\\_incomplete")
            if overall_status == "OK":
                overall_status = "WARNING"

        # LLM spend — compact
        try:
            from app.core.llm_budget import MONTHLY_EUR_CAP, get_usage_summary
            budget = get_usage_summary()
            spent = budget.get("monthly_cost_eur", 0)
            cap = budget.get("monthly_cap_eur", MONTHLY_EUR_CAP)
            # Founder feedback 2026-05-07: "0.00/10 EUR" was ambiguous
            # (this-month metered vs lifetime/console-credits). Explicit
            # "this month" frames it correctly.
            pipe_parts.append(f"LLM \u20ac{spent:.2f}/\u20ac{cap:.0f} this month")
            if budget.get("monthly_cap_reached"):
                pipe_parts.append("\u26a0\ufe0f CAP HIT")
                if overall_status == "OK":
                    overall_status = "WARNING"
        except Exception as exc:
            log.warning("telegram_agent: digest LLM budget fetch failed: %s", exc)

        lines.append(f"\U0001f916 *Pipeline:* {' \u00b7 '.join(pipe_parts)}")
    except Exception as exc:
        log.warning("telegram_agent: digest pipeline section failed: %s", exc)

    # ── 6. ATTENTION — only things that truly need the founder ──
    # TIER_2 review
    try:
        tier2_count = db.execute(sql_text(
            "SELECT COUNT(*) FROM bugfix_candidates "
            "WHERE status = 'patch_proposed' AND patch_risk_tier = 2"
        )).scalar() or 0
        if tier2_count > 0:
            attention_lines.append(
                f"\U0001f512 {tier2_count} TIER\\_2 fix{'es' if tier2_count > 1 else ''} "
                f"awaiting your review"
            )
    except Exception as exc:
        log.warning("telegram_agent: tier2 review count failed: %s", exc)

    # Rollbacks
    if rolled_24h > 0:
        attention_lines.append(
            f"\u21a9\ufe0f {rolled_24h} rollback{'s' if rolled_24h > 1 else ''} in the last 24h"
        )

    # B3a — Critical unresolved ops_alerts (24h). Max 3 distinct types.
    try:
        crit_rows = db.execute(sql_text(
            "SELECT alert_type, COUNT(*) AS n FROM ops_alerts "
            "WHERE severity='critical' AND resolved=false "
            "  AND created_at >= :c "
            "GROUP BY alert_type ORDER BY n DESC LIMIT 3"
        ), {"c": cutoff_24h}).fetchall()
        for _row in crit_rows:
            _name = (_row[0] or "?").replace("_", "\\_")
            _n = int(_row[1] or 0)
            _suffix = f" \u00d7{_n}" if _n > 1 else ""
            attention_lines.append(f"\U0001f534 critical: {_name}{_suffix}")
    except Exception as exc:
        log.warning("telegram_agent: critical-alerts attention failed: %s", exc)

    # B3b — Sustained unresolved warning/critical spike in 24h.
    # Filters: (a) severity is real (not info/debug), (b) still unresolved —
    # skips self-healing probes like heartbeat_synthetic_test (300/day but
    # all self-resolve) and info-level telemetry like regulatory_update.
    try:
        spike_rows = db.execute(sql_text(
            "SELECT alert_type, COUNT(*) AS n FROM ops_alerts "
            "WHERE created_at >= :c "
            "  AND severity IN ('warning', 'critical') "
            "  AND resolved = false "
            "GROUP BY alert_type HAVING COUNT(*) > 20 "
            "ORDER BY n DESC LIMIT 2"
        ), {"c": cutoff_24h}).fetchall()
        for _row in spike_rows:
            _name = (_row[0] or "?").replace("_", "\\_")
            _n = int(_row[1] or 0)
            attention_lines.append(f"\u26a0\ufe0f spike: {_name} \u00d7{_n} in 24h")
    except Exception as exc:
        log.warning("telegram_agent: spike attention failed: %s", exc)

    # B3c — Pipeline liveness stall (from cached health dimensions).
    # Suppressed when pipeline is dormant by design (enrichers off pre-
    # merchant) — surfacing "pipeline stalled: 88 candidates, 0 LLM
    # calls, awaiting Anthropic top-up" every cycle is noise; the
    # founder already knows the pipeline is parked. Reasserts when
    # dormancy ends.
    try:
        from app.services.pipeline_state import is_pipeline_dormant
        if health and not is_pipeline_dormant():
            for _d in health.get("dimensions", []):
                if _d.get("name") == "liveness" and _d.get("status") == "degraded":
                    _det = _d.get("detail") or "check /status"
                    attention_lines.append(f"\U0001f504 pipeline stalled: {_det}")
                    break
    except Exception as exc:
        log.warning("telegram_agent: liveness attention failed: %s", exc)

    if attention_lines:
        lines.append("")
        lines.append("\u261d\ufe0f *Needs you:*")
        for al in attention_lines:
            lines.append(f"  {al}")

    # ── 7. FINALIZE HEADLINE ──
    # B4 — If CTO says WARNING/CRITICAL but nothing actually needs the
    # founder, downgrade to keep the headline honest. An orange/red
    # headline without a "Needs you:" line was misleading (shows state
    # without teaching action). Raw ops state remains visible via /status.
    if overall_status in ("WARNING", "CRITICAL") and not attention_lines:
        overall_status = "OK"

    status_emoji = {
        "OK": "\u2705", "WARNING": "\u26a0\ufe0f", "CRITICAL": "\U0001f534",
    }[overall_status]
    status_suffix = {
        "OK": " \u2014 all systems running.",
        "WARNING": "",
        "CRITICAL": " \u2014 see below.",
    }[overall_status]
    status_line = f"{status_emoji} *{overall_status}*{status_suffix}"

    # Replace placeholder
    lines = [status_line if l == "__STATUS_PLACEHOLDER__" else l for l in lines]

    # ── 8. FOOTER ──
    lines.append("")
    lines.append("_/status /costs /bugfixes /merchants /incidents_")

    _digest_buttons_cache.clear()
    return "\n".join(lines)


# Cache for digest buttons (consumed by send_daily_digest)
# multi-worker: accept-degrade — telegram_agent runs only in singleton agent_worker, never in uvicorn-API fleet
_digest_buttons_cache: list[list[dict]] = []


def send_daily_digest(db) -> bool:
    """Build and send the daily digest. Includes action buttons if items need attention."""
    try:
        message = build_daily_digest(db)
        if _digest_buttons_cache:
            return send_message_with_buttons(message, _digest_buttons_cache)
        return send_message(message)
    except Exception as exc:
        log.warning("telegram_agent: daily digest failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# B4 — Weekly TIER_2 batch review (Monday morning, single message)
# ---------------------------------------------------------------------------

def build_tier2_weekly_review(db) -> tuple[str, list[list[dict]]]:
    """Compose the Monday-morning TIER_2 batch review.

    Lists every BugFixCandidate(patch_risk_tier=2, status='patch_proposed')
    of the past 7 days with a one-line summary + the reviewer assessment
    risk_level. Provides ONE batch-approve and ONE batch-reject button
    for the entire group, plus dashboard links for individual review.
    """
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    from sqlalchemy import text as sql_text
    from zoneinfo import ZoneInfo

    rome_now = _dt.now(ZoneInfo("Europe/Rome"))
    # Naive-UTC so it matches TIMESTAMP WITHOUT TIME ZONE columns.
    cutoff = _dt.now(_tz.utc).replace(tzinfo=None) - _td(days=7)

    try:
        rows = db.execute(sql_text("""
            SELECT bc.id, bc.title, bc.affected_domain, bc.created_at,
                   bc.fix_confidence, ra.risk_level, ra.verdict
            FROM bugfix_candidates bc
            LEFT JOIN reviewer_assessments ra ON ra.id = bc.reviewer_assessment_id
            WHERE bc.patch_risk_tier = 2
              AND bc.status = 'patch_proposed'
              AND bc.created_at >= :cutoff
            ORDER BY bc.created_at DESC
            LIMIT 20
        """), {"cutoff": cutoff}).fetchall()
    except Exception as exc:
        log.warning("tier2_weekly_review: query failed: %s", exc)
        return "", []

    if not rows:
        return "", []

    lines = [
        "*TIER\\_2 Weekly Review* \u2014 HedgeSpark",
        f"{rome_now.strftime('%A %d %B, %H:%M')} (Rome)",
        f"\U0001f512 *{len(rows)} TIER\\_2 candidate{'s' if len(rows) != 1 else ''} pending* (last 7d)",
        "",
        "These touch sensitive paths (auth, billing, encryption, webhooks, migrations).",
        "Review each one, then batch-approve or batch-reject.",
        "",
    ]

    candidate_ids: list[int] = []
    try:
        from app.services.operator_prediction import predict_decision_for_candidate
        from app.models.bugfix_candidate import BugFixCandidate
    except Exception:
        predict_decision_for_candidate = None  # type: ignore[assignment]
        BugFixCandidate = None  # type: ignore[assignment]

    for r in rows:
        cid = r[0]
        title = (r[1] or "")[:80]
        domain = r[2] or "?"
        confidence = r[4] or 0
        risk = (r[5] or "?")
        verdict = (r[6] or "?")
        candidate_ids.append(cid)

        risk_emoji = {
            "low": "\U0001f7e2",
            "medium": "\U0001f7e1",
            "high": "\U0001f534",
        }.get(risk, "\u26aa")

        # D6 — operator answer prediction. Query the historical audit-log
        # distribution for this file-pattern / domain and surface the
        # suggested action inline so the founder can read down the list
        # and tap batch-approve with confidence.
        prediction_badge = ""
        if predict_decision_for_candidate and BugFixCandidate is not None:
            try:
                bc = db.get(BugFixCandidate, cid)
                if bc is not None:
                    pred = predict_decision_for_candidate(db, bc)
                    rec = pred.get("recommendation", "unknown")
                    if rec == "approve":
                        prediction_badge = (
                            f" \u2192 \u2705 likely approve "
                            f"({int(pred['posterior_mean'] * 100)}%, n={pred['sample_size']})"
                        )
                    elif rec == "reject":
                        prediction_badge = (
                            f" \u2192 \u274c likely reject "
                            f"({int((1 - pred['posterior_mean']) * 100)}%, n={pred['sample_size']})"
                        )
            except Exception as exc:
                log.warning("telegram_agent: decision prediction failed: %s", exc)

        lines.append(
            f"  {risk_emoji} *#{cid}* [{domain}] conf={confidence}% \u00b7 reviewer={verdict}{prediction_badge}"
        )
        lines.append(f"     {title}")

    lines.append("")
    lines.append("_Tap a button to act on the batch, or open dashboard for individual review._")
    lines.append("[Open dashboard \u2192](https://app.hedgesparkhq.com/ops/bugfixes?filter=tier2)")

    # ONE batch-approve + ONE batch-reject button covering all listed ids.
    # Individual review still happens via the dashboard link.
    ids_csv = ",".join(str(c) for c in candidate_ids)
    buttons = [[
        {
            "text": f"\u2705 Batch approve all ({len(candidate_ids)})",
            "callback_data": f"/tier2_batch_approve {ids_csv}",
        },
        {
            "text": f"\u274c Batch reject all",
            "callback_data": f"/tier2_batch_reject {ids_csv}",
        },
    ]]

    return "\n".join(lines), buttons


def send_tier2_weekly_review(db) -> bool:
    """Build and send the TIER_2 weekly batch. Returns True if a message
    was sent, False if there was nothing pending or send failed."""
    try:
        message, buttons = build_tier2_weekly_review(db)
        if not message:
            log.info("tier2_weekly_review: nothing to send")
            return False
        if buttons:
            return send_message_with_buttons(message, buttons)
        return send_message(message)
    except Exception as exc:
        log.warning("tier2_weekly_review: failed: %s", exc)
        return False
