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
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("telegram_webhook")

router = APIRouter(prefix="/telegram", tags=["telegram"])

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


def _safe_markdown(text: str) -> tuple[str, str]:
    """
    Escape text for Telegram Markdown V1 and validate.
    Returns (formatted_text, parse_mode).
    If the escaped text is still likely to break Telegram parsing,
    returns (plain_text, "") — no parse_mode = plain text.
    """
    from app.services.telegram_agent import _escape_markdown, _strip_markdown

    escaped = _escape_markdown(text)

    # Validate: count unescaped asterisks — odd = will break
    unescaped_stars = len(re.findall(r'(?<!\\)\*', escaped))
    unescaped_backticks = len(re.findall(r'(?<!\\)`', escaped))

    if unescaped_stars % 2 != 0 or unescaped_backticks % 2 != 0:
        log.warning("telegram_webhook: Markdown still has unmatched entities after escaping — using plain text")
        return _strip_markdown(text), ""

    return escaped, "Markdown"


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
    try:
        body = await request.json()
    except Exception:
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

    formatted_text, parse_mode = _safe_markdown(response_text)

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
