"""
telegram_webhook.py — Telegram Bot webhook endpoint.

Receives updates from Telegram Bot API and dispatches commands.
Operator control commands require authorized TELEGRAM_CHAT_ID.

Performance strategy:
  Fast commands (read-only) return the reply directly in the webhook
  response body.  Telegram delivers these inline without an extra API
  round-trip — saving 2-4s of Telegram infrastructure latency.

  Slow commands (writes, apply, merge) run in a background thread and
  reply via a separate sendMessage call, so the webhook never times out.

Session safety:
  Each thread creates its OWN SQLAlchemy session.  The request-scoped
  session from FastAPI's Depends(get_db) is NEVER passed across threads.

Markdown safety:
  All messages go through _escape_markdown.  If the escaped text still
  has unmatched Markdown entities, we degrade to plain text rather than
  sending a message Telegram will reject with "can't parse entities".

Setup: Set the Telegram webhook URL to POST /telegram/webhook
"""
from __future__ import annotations

import asyncio
import hmac as _hmac
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("telegram_webhook")

router = APIRouter(prefix="/telegram", tags=["telegram"])


# ---------------------------------------------------------------------------
# Webhook signature verification (2026-04-11 security audit).
#
# Telegram lets you set a secret_token when registering the webhook; every
# incoming request then carries `X-Telegram-Bot-Api-Secret-Token`. If we
# never set the header-based secret, the endpoint is reachable by anyone
# who finds the URL and can spoof operator commands (approve, reject,
# apply bugfix, deploy, …).
#
# Behaviour:
#   * `TELEGRAM_WEBHOOK_SECRET` unset → log CRITICAL + refuse traffic.
#     Previously this was a silent pass-through.
#   * Header present AND matches → allow.
#   * Header missing or mismatch → 401.
#
# The comparison uses `hmac.compare_digest` so we don't leak the secret
# via timing. The check runs BEFORE any body parsing, authorization, or
# command routing.
# ---------------------------------------------------------------------------
_WEBHOOK_SECRET_ENV = "TELEGRAM_WEBHOOK_SECRET"


def _load_webhook_secret() -> str:
    return os.getenv(_WEBHOOK_SECRET_ENV, "").strip()


def _verify_telegram_signature(request: Request) -> None:
    """Raise HTTPException on anything other than a valid signature."""
    secret = _load_webhook_secret()
    if not secret:
        log.error(
            "telegram_webhook: %s not set — refusing traffic. Set the "
            "secret_token when registering the webhook via setWebhook "
            "and mirror it in the env.",
            _WEBHOOK_SECRET_ENV,
        )
        raise HTTPException(
            status_code=503,
            detail="Telegram webhook signing not configured",
        )
    provided = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not provided or not _hmac.compare_digest(provided, secret):
        log.warning(
            "telegram_webhook: rejected request with bad/missing signature"
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram webhook signature",
        )

# Commands that are read-only and fast enough to return inline.
# These skip the separate sendMessage API call entirely.
_INLINE_COMMANDS = frozenset({
    "/help", "/status", "/costs", "/scaling",
    "/merchants", "/evolution",
    "/approvals", "/bugfixes", "/promotions",
    "/review",
    "/incidents", "/meta_review", "/digest", "/webhooks",
})

# Time budget for inline commands.  If the handler exceeds this,
# we still return the result inline (it's already computed) — the
# budget is just a safety threshold for logging.
_INLINE_BUDGET_MS = 500

# Thread pool shared across webhook requests.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tg")


def _safe_html(text: str) -> tuple[str, str]:
    """
    Convert text to Telegram HTML format.
    Returns (formatted_text, "HTML").
    Falls back to plain text if HTML conversion fails.
    """
    try:
        from app.services.telegram_agent import _to_html
        return _to_html(text), "HTML"
    except Exception:
        from app.services.telegram_agent import _strip_markdown
        return _strip_markdown(text), ""


@router.post("/webhook")
async def telegram_webhook(request: Request):
    """
    Receive Telegram updates and respond.

    Fast read-only commands return the reply in the webhook response body
    (inline response — no extra API call, ~2-4s faster delivery).

    Write commands fire-and-forget in a background thread.

    NOTE: No Depends(get_db) here.  Each command handler creates its own
    session inside its thread.
    """
    # Security gate — always first, always fail-closed.
    _verify_telegram_signature(request)

    try:
        body = await request.json()
    except Exception:
        return {"ok": True}

    # Handle inline keyboard button taps (callback_query)
    callback = body.get("callback_query")
    if callback:
        cb_data = callback.get("data", "")
        cb_chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
        cb_id = callback.get("id", "")
        if cb_data and cb_chat_id and cb_data.startswith("/"):
            # Contextual instant feedback based on command
            feedback = "Processing..."
            cmd_lower = cb_data.split()[0].lower()
            if "approve" in cmd_lower and "apply" not in cmd_lower:
                feedback = "✅ Approved!"
            elif "apply" in cmd_lower:
                feedback = "⏳ Applying patch... running tests"
            elif "rollback" in cmd_lower:
                feedback = "⏳ Rolling back..."
            elif "cleanup" in cmd_lower:
                feedback = "🧹 Cleaning up..."

            try:
                from app.services.telegram_agent import _get_http_client, _BOT_TOKEN
                _get_http_client().post(
                    f"https://api.telegram.org/bot{_BOT_TOKEN}/answerCallbackQuery",
                    json={"callback_query_id": cb_id, "text": feedback, "show_alert": False},
                )
            except Exception:
                pass
            _background_response(cb_data, cb_chat_id)
            return {"ok": True}

    message = body.get("message", {})
    text = message.get("text", "")
    chat_id = str(message.get("chat", {}).get("id", ""))

    if not text or not chat_id:
        return {"ok": True}

    if not text.startswith("/"):
        return {"ok": True}

    # Extract the base command (strip @botname suffix and args)
    base_cmd = text.strip().split()[0].lower()
    if "@" in base_cmd:
        base_cmd = base_cmd.split("@")[0]

    if base_cmd in _INLINE_COMMANDS:
        return await _inline_response(text, chat_id)
    else:
        _background_response(text, chat_id)
        return {"ok": True}


async def _inline_response(text: str, chat_id: str) -> JSONResponse:
    """
    Run the command handler in a thread, then return the reply directly
    in the webhook response body.  Telegram delivers this without an
    extra sendMessage API call.

    A fresh DB session is created INSIDE the thread — never shared with
    the async request context.

    Markdown safety: if the formatted text has unmatched entities, we
    degrade to plain text (no parse_mode) to avoid Telegram 400 errors.
    """
    from app.core.execution_mode import is_dry_run

    loop = asyncio.get_running_loop()

    t0 = time.monotonic()
    try:
        response_text = await loop.run_in_executor(
            _executor, partial(_handle_sync, text, chat_id),
        )
    except Exception as exc:
        log.warning("telegram_webhook: inline command error: %s", exc)
        response_text = "Error processing command."

    elapsed_ms = (time.monotonic() - t0) * 1000
    if elapsed_ms > _INLINE_BUDGET_MS:
        log.warning("telegram_webhook: inline command slow: %.0fms %s", elapsed_ms, text[:30])

    if is_dry_run():
        response_text = f"[DRY RUN] {response_text}"

    formatted_text, parse_mode = _safe_html(response_text)

    # Telegram inline webhook response: return a sendMessage payload
    # directly.  Telegram processes this without a separate API call.
    payload = {
        "method": "sendMessage",
        "chat_id": chat_id,
        "text": formatted_text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    return JSONResponse(content=payload)


def _background_response(text: str, chat_id: str) -> None:
    """
    Fire-and-forget: submit to thread pool.  The HTTP response has already
    been sent by the caller.  A fresh DB session is created inside the thread.
    """
    _executor.submit(_process_and_reply, text, chat_id)


def _handle_sync(text: str, chat_id: str) -> str:
    """
    Run handle_command in a thread with its OWN DB session.
    Session is always closed — even on exception.
    """
    from app.core.database import SessionLocal
    from app.services.telegram_agent import handle_command

    db = SessionLocal()
    try:
        return handle_command(text, db=db, chat_id=chat_id)
    finally:
        db.close()


def _process_and_reply(text: str, chat_id: str) -> None:
    """
    Handle the command and send the reply — runs in a background thread.
    Creates and closes its own DB session.
    send_message() has its own Markdown fallback (retries as plain text on 400).
    """
    from app.core.database import SessionLocal
    from app.services.telegram_agent import handle_command, send_message

    db = SessionLocal()
    try:
        response_text = handle_command(text, db=db, chat_id=chat_id)
    except Exception as exc:
        log.warning("telegram_webhook: command error: %s", exc)
        response_text = "Error processing command."
    finally:
        db.close()

    try:
        send_message(response_text, chat_id=chat_id)
    except Exception as exc:
        log.warning("telegram_webhook: send failed: %s", exc)
