"""
notifier_guard.py — Hard environment gate for all outbound notifications.

Purpose: guarantee that tests, local dev, and non-production simulations
can NEVER send real Telegram or Slack messages, even if a caller forgets
to mock the sender.

Gate logic:
    APP_ENV=production  → real sends allowed
    any other value     → real sends BLOCKED (logged, never hit the wire)

Escape hatch for staging / on-call drills:
    NOTIFICATIONS_ALLOW_REAL=1  → override the block (use sparingly)

This module is imported by every real-send path:
    - app/services/telegram_agent.py  (send_message, send_message_with_buttons,
      warmup_connection, register_bot_commands)
    - app/core/alert_delivery.py      (post_slack_alert, deliver_incident)
    - any other direct httpx.post(slack_url / telegram) callsite

Fail-safe: if APP_ENV is unset, the guard defaults to BLOCKING (safe by
construction). Production deploys MUST set APP_ENV=production explicitly.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("notifier_guard")

_PRODUCTION_ENVS = {"production", "prod"}


def _app_env() -> str:
    return os.getenv("APP_ENV", "").strip().lower()


def _allow_real_override() -> bool:
    val = os.getenv("NOTIFICATIONS_ALLOW_REAL", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def is_real_send_allowed() -> bool:
    """
    Return True iff this process is permitted to send real notifications.

    Checks APP_ENV at call time (not import time) so tests that manipulate
    environment variables at runtime are respected immediately.
    """
    if _app_env() in _PRODUCTION_ENVS:
        return True
    if _allow_real_override():
        return True
    return False


def block_send(channel: str, reason_preview: str = "") -> None:
    """
    Log a blocked send at INFO level. Callers should invoke this and then
    return a falsy result — the guard itself does not raise.
    """
    env = _app_env() or "unset"
    log.info(
        "notifier_guard: BLOCKED %s send — APP_ENV=%s (not production). preview=%r",
        channel, env, reason_preview[:80],
    )


def require_production(channel: str, reason_preview: str = "") -> bool:
    """
    Convenience wrapper: if not allowed, emit block log and return False.
    Callers should short-circuit on False.

    Example:
        if not require_production("telegram", text):
            return False
        # ... proceed to real HTTP call ...
    """
    if is_real_send_allowed():
        return True
    block_send(channel, reason_preview)
    return False
