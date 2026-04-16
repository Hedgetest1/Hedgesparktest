"""
email.py — Email sending via Resend (transactional/digest only).

send_email(to, subject, html, text) -> str | None

Returns the Resend email ID on success, None on failure.
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
    return os.getenv("EMAIL_FROM_ADDRESS", "HedgeSpark <dev@hedgesparkhq.com>")


def send_email(
    to: str,
    subject: str,
    html: str,
    text: str | None = None,
    from_address: str | None = None,
) -> str | None:
    """
    Send an email via Resend.

    Returns the Resend email ID string on success, None on failure.
    Never raises — all errors are logged and swallowed.

    CALLER ENFORCEMENT: Only the email orchestrator and operator scripts
    are allowed to call this function. All services must route through
    the orchestrator (submit_intent or send_immediate).
    """
    # ── Caller enforcement: only orchestrator + scripts allowed ──
    import inspect
    caller = inspect.stack()[1]
    caller_file = caller.filename
    _ALLOWED_CALLERS = (
        "email_orchestrator.py",  # production path
        "/scripts/",              # operator scripts
        "test_",                  # test files
        "conftest.py",            # test fixtures
    )
    if not any(allowed in caller_file for allowed in _ALLOWED_CALLERS):
        log.error(
            "email: UNAUTHORIZED CALLER blocked — %s:%s:%d attempted to send to=%s subject=%r",
            caller_file, caller.function, caller.lineno, to, subject,
        )
        return None

    # ── Last-line governance: brand voice check ──
    try:
        from app.services.brand_voice import validate_email_text, validate_subject_line
        plain = text or ""
        if plain:
            check = validate_email_text(plain, check_structure=False)
            if not check.passed:
                log.warning(
                    "email: BRAND VIOLATION to=%s subject=%r: %s",
                    to, subject, check.violations,
                )
        subj_check = validate_subject_line(subject)
        if not subj_check.passed:
            log.warning(
                "email: SUBJECT VIOLATION to=%s: %s",
                to, subj_check.violations,
            )
    except Exception as exc:
        log.warning("email: brand check failed (non-fatal): %s", exc)

    api_key = _get_api_key()
    if not api_key:
        log.warning(
            "email: RESEND_API_KEY not set — email not sent (to=%s subject=%r)",
            to, subject,
        )
        return None

    try:
        import resend

        resend.api_key = api_key

        params: dict = {
            "from": from_address or _get_from_address(),
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
        # Return the real Resend ID, or a generated marker if Resend
        # didn't return one (still counts as successful delivery).
        # Never return a fake ID that could be mistaken for a real one.
        if email_id:
            return str(email_id)
        # Resend accepted the email but didn't return an ID — rare but possible.
        # Generate a local marker that's obviously not a Resend ID.
        import uuid
        local_id = f"local:{uuid.uuid4().hex[:12]}"
        log.info("email: Resend returned no ID, using local marker %s", local_id)
        return local_id

    except Exception as exc:
        log.error(
            "email: send failed to=%s subject=%r: %s",
            to, subject, exc
        )
        return None
