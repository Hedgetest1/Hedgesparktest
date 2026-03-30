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
    if not _BOT_TOKEN:
        return
    try:
        client = _get_http_client()
        resp = client.get(
            f"https://api.telegram.org/bot{_BOT_TOKEN}/getMe",
        )
        log.info("telegram_agent: connection warmed up (status=%d)", resp.status_code)
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


# ---------------------------------------------------------------------------
# Send message
# ---------------------------------------------------------------------------

def _escape_markdown(text: str) -> str:
    """
    Escape characters that break Telegram Markdown V1 parsing.

    Telegram Markdown V1 special characters: _ * ` [
    Strategy:
      - Underscores: always escape (we never use _italic_)
      - Backticks: always escape unmatched ones
      - Square brackets: always escape unmatched ones
      - Asterisks: preserve MATCHED pairs (our intentional *bold*),
        escape any unmatched trailing asterisk
    """
    import re

    # Step 1: Escape all underscores (we never use italic)
    text = text.replace("\\_", "\x00").replace("_", "\\_").replace("\x00", "\\_")

    # Step 2: Escape unmatched backticks
    # If odd number of backticks, escape the last one
    if text.count("`") % 2 != 0:
        # Find and escape the last backtick
        idx = text.rfind("`")
        text = text[:idx] + "\\`" + text[idx + 1:]

    # Step 3: Escape unmatched square brackets
    # Telegram V1 Markdown uses [text](url) — unmatched [ breaks parsing
    # Since we don't use link syntax, escape all bare brackets
    text = text.replace("[", "\\[").replace("]", "\\]")

    # Step 4: Check for unmatched asterisks
    # Count asterisks that are not escaped
    asterisks = [m.start() for m in re.finditer(r'(?<!\\)\*', text)]
    if len(asterisks) % 2 != 0:
        # Odd number — escape the last one to avoid "can't find end of entity"
        last_idx = asterisks[-1]
        text = text[:last_idx] + "\\*" + text[last_idx + 1:]

    return text


def _strip_markdown(text: str) -> str:
    """Remove all Markdown V1 formatting for plain-text fallback."""
    import re
    # Remove bold markers
    text = re.sub(r'\*([^*]*)\*', r'\1', text)
    # Remove backtick code spans
    text = re.sub(r'`([^`]*)`', r'\1', text)
    # Remove link syntax [text](url)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    # Remove remaining escape backslashes
    text = text.replace("\\_", "_").replace("\\*", "*").replace("\\`", "`").replace("\\[", "[").replace("\\]", "]")
    return text


def send_message(text: str, chat_id: str | None = None, parse_mode: str = "Markdown") -> bool:
    """
    Send a message via Telegram Bot API.
    Returns True if sent. Returns False (never raises) on any failure.
    In dry_run mode, prefixes message with [DRY RUN].

    Safety: if Telegram rejects the message due to Markdown parse errors
    (HTTP 400 + "can't parse entities"), automatically retries as plain text.
    """
    from app.core.execution_mode import is_dry_run

    if not _BOT_TOKEN:
        log.debug("telegram_agent: not configured — skipping send")
        return False

    target = chat_id or _CHAT_ID
    if not target:
        log.debug("telegram_agent: no chat_id — skipping send")
        return False

    if is_dry_run():
        text = f"[DRY RUN] {text}"

    formatted_text = _escape_markdown(text) if parse_mode == "Markdown" else text

    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"

    try:
        client = _get_http_client()
        resp = client.post(url, json={
            "chat_id": target,
            "text": formatted_text,
            "parse_mode": parse_mode,
        })

        if resp.status_code == 200:
            log.info("telegram_agent: message sent to %s", target)
            return True

        # Markdown parse failure → retry as plain text
        if resp.status_code == 400 and "parse entities" in (resp.text or "").lower():
            log.warning("telegram_agent: Markdown parse failed — retrying as plain text")
            plain = _strip_markdown(text)
            resp2 = client.post(url, json={
                "chat_id": target,
                "text": plain,
            })
            if resp2.status_code == 200:
                log.info("telegram_agent: message sent (plain text fallback) to %s", target)
                return True
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
    "/merge",
}

# All known commands
_ALL_COMMANDS = {
    "/status", "/evolution", "/costs", "/merchants", "/scaling",
    "/approvals", "/approve", "/reject",
    "/bugfixes", "/bugfix_approve", "/bugfix_apply",
    "/promotions", "/merge",
    "/review",
    "/incidents", "/meta_review", "/digest",
    "/help",
}


def handle_command(command: str, db=None, chat_id: str | None = None) -> str:
    """
    Handle a Telegram command. Returns response text.

    Read-only commands work for authorized chat.
    Write commands require chat_id == TELEGRAM_CHAT_ID.
    """
    parts = command.strip().split()
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
        "/help": lambda: _cmd_help(db),
    }

    handler = handlers.get(cmd)
    if not handler:
        return _cmd_unknown(db)

    try:
        return handler()
    except Exception as exc:
        log.warning("telegram_agent: command %s failed: %s", cmd, exc, exc_info=True)
        return f"Error processing {cmd}: {type(exc).__name__}"


# ---------------------------------------------------------------------------
# Read-only command handlers
# ---------------------------------------------------------------------------

def _cmd_status(db) -> str:
    """Return system status summary."""
    if db is None:
        return "System status unavailable (no DB session)"

    from app.services.system_summary import build_system_summary
    s = build_system_summary(db)

    ram = s["infra"]["ram"]
    cpu = s["infra"]["cpu"]
    workers = s["infra"]["workers"]
    llm = s["llm_usage"]

    lines = [
        "*System Status* \u2014 Hedge Spark",
        "",
        f"RAM: {ram.get('used_mb', '?')}MB / {ram.get('total_mb', '?')}MB ({ram.get('usage_pct', '?')}%)",
        f"CPU: {cpu.get('load_5m', '?')} (5m avg, {cpu.get('cpu_count', '?')} cores)",
        f"Workers: {workers.get('cycles_24h', 0)} cycles, {workers.get('error_rate_pct', 0)}% errors",
        f"LLM: {llm.get('global_calls_today', 0)}/{llm.get('global_max_per_day', 150)} calls today",
    ]

    warnings = s.get("warnings", [])
    if warnings:
        lines.append("")
        lines.append("*Warnings:*")
        for w in warnings:
            lines.append(f"\u26a0 {w}")

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
        "*Monthly Cost Estimate* \u2014 Hedge Spark",
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

    lines = ["*Scaling Intelligence* \u2014 Hedge Spark", ""]

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
    """List pending TIER_1 action approvals."""
    if db is None:
        return "No DB session available."

    from app.models.action_approval import ActionApproval
    from sqlalchemy import text

    now = _now()

    # Expire old approvals
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

    approval = db.query(ActionApproval).get(approval_id)
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

    approval = db.query(ActionApproval).get(approval_id)
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
            lines.append(f"  \U0001f449 /bugfix\\_approve {c.id}")
        elif c.status == "approved":
            lines.append(f"  \U0001f449 /bugfix\\_apply {c.id}")
        lines.append("")

    return "\n".join(lines)


def _cmd_bugfix_approve(db, args: list[str]) -> str:
    """Approve a proposed bugfix candidate."""
    if db is None:
        return "No DB session available."
    if not args:
        return "Usage: /bugfix\\_approve <id>"

    try:
        candidate_id = int(args[0])
    except ValueError:
        return "Invalid bugfix ID."

    from app.models.bugfix_candidate import BugFixCandidate
    from app.services.audit import write_audit_log

    now = _now()

    c = db.query(BugFixCandidate).get(candidate_id)
    if not c:
        return f"Bugfix #{candidate_id} not found."
    if c.status != "patch_proposed":
        return f"Cannot approve \u2014 bugfix #{candidate_id} status is {c.status}."

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
        f"Next: /bugfix\\_apply {candidate_id}"
        f"{reviewer_ctx}"
    )


def _cmd_bugfix_apply(db, args: list[str]) -> str:
    """Apply an approved bugfix through the guarded apply pipeline."""
    if db is None:
        return "No DB session available."
    if not args:
        return "Usage: /bugfix\\_apply <id>"

    try:
        candidate_id = int(args[0])
    except ValueError:
        return "Invalid bugfix ID."

    from app.models.bugfix_candidate import BugFixCandidate

    c = db.query(BugFixCandidate).get(candidate_id)
    if not c:
        return f"Bugfix #{candidate_id} not found."
    if c.status != "approved":
        return f"Cannot apply \u2014 bugfix #{candidate_id} status is {c.status}. Must be approved first."

    # Use the existing guarded apply path
    from app.services.bugfix_pipeline import apply_bugfix_candidate
    from app.services.audit import write_audit_log

    result = apply_bugfix_candidate(db, candidate_id)

    # Additional audit log for telegram channel
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
        metadata={
            "channel": "telegram",
            "reviewer_assessment_id": getattr(c, "reviewer_assessment_id", None),
        },
    )
    db.commit()

    reviewer_ctx = _get_reviewer_for_display(db, "bugfix_candidate", candidate_id)

    if result.status == "applied":
        return (
            f"\u2705 *Bugfix applied* #{candidate_id}\n"
            f"Tests: {'passed' if result.test_passed else 'failed'}\n"
            f"Health: {'ok' if result.health_ok else 'failed'}"
            f"{reviewer_ctx}"
        )
    else:
        return (
            f"\u274c *Bugfix apply failed* #{candidate_id}\n"
            f"Status: {result.status}\n"
            f"Reason: {result.failure_reason or 'unknown'}"
            f"{reviewer_ctx}"
        )


def _cmd_promotions(db) -> str:
    """List promotions needing operator action."""
    if db is None:
        return "No DB session available."

    from app.models.autofix_promotion import AutoFixPromotion

    promotions = (
        db.query(AutoFixPromotion)
        .filter(AutoFixPromotion.status.notin_(["merged", "rejected", "failed"]))
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

    p = db.query(AutoFixPromotion).get(promo_id)
    if not p:
        return f"Promotion #{promo_id} not found."

    # Check merge recommendation first
    merge_rec = None
    try:
        from app.services.merge_intelligence import compute_merge_recommendation
        merge_rec = compute_merge_recommendation(db, promo_id)
    except Exception:
        pass

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
        p = db.query(AutoFixPromotion).get(entity_id)
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


def _cmd_help(db) -> str:
    """Full command list."""
    return (
        "*Hedge Spark Operator Bot*\n\n"
        "*Status & Info:*\n"
        "/status \u2014 system health summary\n"
        "/evolution \u2014 last monthly audit proposals\n"
        "/costs \u2014 cost estimation breakdown\n"
        "/merchants \u2014 merchant summary\n"
        "/scaling \u2014 scaling forecast + recommendations\n"
        "/incidents \u2014 active support incidents\n"
        "/meta\\_review \u2014 latest strategic meta-review\n"
        "/digest \u2014 daily health digest\n\n"
        "*Approvals:*\n"
        "/approvals \u2014 list pending action approvals\n"
        "/approve <id> \u2014 approve and execute\n"
        "/reject <id> [reason] \u2014 reject with optional reason\n\n"
        "*Bugfixes:*\n"
        "/bugfixes \u2014 list bugfixes needing action\n"
        "/bugfix\\_approve <id> \u2014 approve a proposed patch\n"
        "/bugfix\\_apply <id> \u2014 apply approved patch\n\n"
        "*Promotions:*\n"
        "/promotions \u2014 list active promotions\n"
        "/merge <id> \u2014 merge eligible promotion PR\n\n"
        "*Review:*\n"
        "/review <type> <id> \u2014 reviewer verdict\n"
        "  types: bugfix, approval, promotion, evolution, model\\_upgrade, scaling\n\n"
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
        "*Monthly Evolution Report \u2014 Hedge Spark*",
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
    Send a decision-first reviewer verdict to Telegram.
    Called when the reviewer blocks or gates an action.
    """
    title = entity_title or f"{assessment.entity_type} #{assessment.entity_id}"

    # Build action hint based on entity type
    action_hint = None
    etype = assessment.entity_type
    eid = assessment.entity_id
    if assessment.verdict == "reject" or assessment.risk_level in ("high", "critical"):
        action_hint = "Do not apply this change."
    elif etype == "bugfix_candidate":
        action_hint = f"/bugfix\\_approve {eid}"
    elif etype == "action_approval":
        action_hint = f"/approve {eid}"
    elif etype == "scaling_recommendation":
        action_hint = f"/review scaling {eid}"

    decision_block = _format_reviewer_decision(assessment, action_hint=action_hint)

    lines = [
        f"*{title[:120]}*",
        "",
        decision_block,
    ]

    return send_message("\n".join(lines))


def send_scaling_alert(recommendation: dict, forecast: dict) -> bool:
    """
    Send a Telegram notification for a new scaling recommendation.
    Called after recommendation engine creates significant entries.
    """
    merch = forecast.get("merchants", {})
    ram = forecast.get("ram_pct", {})

    lines = [
        "*Scaling Recommendation \u2014 Hedge Spark*",
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

def build_daily_digest(db) -> str:
    """
    Build a concise daily health digest message.
    Uses existing data sources. Resilient to individual source failures.
    Returns the formatted message string (not sent — caller decides).
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    lines = [
        f"*Daily Health Digest* \u2014 Hedge Spark",
        f"{now.strftime('%Y-%m-%d %H:%M')} UTC",
        "",
    ]

    # 1. System health
    overall_status = "OK"
    try:
        from app.services.system_summary import build_system_summary
        s = build_system_summary(db)
        ram = s.get("infra", {}).get("ram", {})
        workers = s.get("infra", {}).get("workers", {})
        warnings = s.get("warnings", [])

        ram_pct = ram.get("usage_pct", 0)
        error_rate = workers.get("error_rate_pct", 0)
        cycles = workers.get("cycles_24h", 0)

        lines.append(f"*System:*")
        lines.append(f"  RAM: {ram_pct}% | Workers: {cycles} cycles/24h, {error_rate}% errors")

        if error_rate > 20:
            overall_status = "CRITICAL"
        elif error_rate > 10 or ram_pct > 85:
            overall_status = "WARNING"

        if warnings:
            for w in warnings[:3]:
                lines.append(f"  \u26a0\ufe0f {w}")
    except Exception:
        lines.append("*System:* unavailable")
        overall_status = "WARNING"

    # 2. Alerts + incidents
    try:
        from sqlalchemy import text
        alert_row = db.execute(text(
            "SELECT COUNT(*) FROM ops_alerts WHERE resolved = false"
        )).fetchone()
        active_alerts = alert_row[0] if alert_row else 0

        incident_row = db.execute(text(
            "SELECT COUNT(*) FROM support_incidents WHERE status IN ('open', 'triaged', 'investigating')"
        )).fetchone()
        active_incidents = incident_row[0] if incident_row else 0

        lines.append("")
        lines.append(f"*Alerts:* {active_alerts} unresolved")
        lines.append(f"*Incidents:* {active_incidents} active")

        if active_alerts > 5 or active_incidents > 3:
            if overall_status == "OK":
                overall_status = "WARNING"
    except Exception:
        lines.append("")
        lines.append("*Alerts/Incidents:* unavailable")

    # 3. LLM budget
    try:
        from app.core.llm_budget import get_usage_summary
        budget = get_usage_summary()
        spent = budget.get("monthly_cost_eur", 0)
        cap = budget.get("monthly_cap_eur", 5.0)
        remaining = budget.get("monthly_remaining_eur", cap)
        cap_reached = budget.get("monthly_cap_reached", False)
        blocked = budget.get("blocked_today", 0)

        lines.append("")
        lines.append(f"*LLM Budget:*")
        lines.append(f"  Spent: \u20ac{spent:.3f} / \u20ac{cap:.2f} ({remaining:.3f} remaining)")
        if cap_reached:
            lines.append(f"  \u26a0\ufe0f *CAP REACHED* \u2014 LLM calls blocked")
            if overall_status == "OK":
                overall_status = "WARNING"
        if blocked > 0:
            lines.append(f"  Blocked today: {blocked}")

        # 429 state
        for provider, state in budget.get("provider_429_state", {}).items():
            if state.get("total_429s", 0) > 0:
                lines.append(f"  {provider}: {state['total_429s']} rate limits today")
    except Exception:
        lines.append("")
        lines.append("*LLM Budget:* unavailable")

    # 4. Bugfix pipeline
    try:
        from sqlalchemy import text as sql_text
        pipeline = db.execute(sql_text("""
            SELECT status, COUNT(*) FROM bugfix_candidates
            WHERE created_at >= NOW() - INTERVAL '7 days'
            GROUP BY status
        """)).fetchall()
        if pipeline:
            parts = [f"{r[0]}={r[1]}" for r in pipeline]
            lines.append("")
            lines.append(f"*Bugfixes (7d):* {', '.join(parts)}")
    except Exception:
        pass

    # 5. Merchants
    try:
        from sqlalchemy import text as sql_text2
        merch_row = db.execute(sql_text2(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE billing_active = true) FROM merchants"
        )).fetchone()
        if merch_row:
            lines.append("")
            lines.append(f"*Merchants:* {merch_row[0]} total, {merch_row[1]} billing active")
    except Exception:
        pass

    # Status badge
    emoji = "\u2705" if overall_status == "OK" else ("\u26a0\ufe0f" if overall_status == "WARNING" else "\U0001f534")
    lines.insert(2, f"{emoji} *Status: {overall_status}*")

    lines.append("")
    lines.append("Commands: /status /costs /incidents /bugfixes")

    return "\n".join(lines)


def send_daily_digest(db) -> bool:
    """Build and send the daily health digest via Telegram."""
    try:
        message = build_daily_digest(db)
        return send_message(message)
    except Exception as exc:
        log.warning("telegram_agent: daily digest failed: %s", exc)
        return False
