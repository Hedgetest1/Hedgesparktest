"""
email.py — Email sending via Resend (transactional/digest only).

send_email(to, subject, html, text) -> bool

Returns True on success, False on failure.
Fails gracefully when RESEND_API_KEY is not set.

NOTE: This module is used ONLY by the weekly digest scripts
(send_digest.py, send_all_digests.py). All execution and
lifecycle email flows have been migrated to Klaviyo.
"""
from __future__ import annotations

import hashlib
import logging
import os

log = logging.getLogger(__name__)

def _get_api_key() -> str:
    """Read RESEND_API_KEY lazily — ensures dotenv has loaded before first use."""
    return os.getenv("RESEND_API_KEY", "")


def _get_from_address() -> str:
    return os.getenv("EMAIL_FROM_ADDRESS", "Hedge Spark <digest@hedgesparkhq.com>")


def send_email(
    to: str,
    subject: str,
    html: str,
    text: str | None = None,
) -> bool:
    """
    Send an email via Resend.

    Returns True on success, False on any failure.
    Never raises — all errors are logged and swallowed.
    """
    api_key = _get_api_key()
    if not api_key:
        log.warning(
            "email: RESEND_API_KEY not set — email not sent (to=%s subject=%r)",
            to, subject,
        )
        return False

    try:
        import resend

        resend.api_key = api_key

        params: dict = {
            "from": _get_from_address(),
            "to": [to],
            "subject": subject,
            "html": html,
        }

        if text:
            params["text"] = text

        result = resend.Emails.send(params)

        email_id = (
            result.get("id")
            if isinstance(result, dict)
            else getattr(result, "id", None)
        )

        content_hash = hashlib.sha256(html.encode()).hexdigest()[:12]
        log.info(
            "email: sent to=%s subject=%r resend_id=%s content_hash=%s",
            to, subject, email_id, content_hash,
        )
        return True

    except Exception as exc:
        log.error(
            "email: send failed to=%s subject=%r: %s",
            to, subject, exc
        )
        return False
