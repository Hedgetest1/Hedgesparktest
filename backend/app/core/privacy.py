"""
privacy.py — PII masking helpers for logs + error traces.

GDPR Art. 5(1)(f) and Art. 32 require that personal data is protected
against unauthorized disclosure. Plaintext email addresses in log files,
Sentry traces, and PM2 crash reports are a classic compliance gap:
logs persist indefinitely, flow into monitoring pipelines, and are
viewed by operators who have no lawful basis to see the PII.

Use these helpers at every log-call site that would otherwise emit a
full email address. They preserve enough signal for debugging (domain
stays intact, first 2 chars of local part) without leaking the identity.

    log.warning("send failed to=%s", mask_email(to_email))
    # → "send failed to=fo***@example.com"
"""
from __future__ import annotations


def mask_email(email: str | None) -> str:
    """Return a masked version of the email suitable for logs.

    Examples:
        mask_email("alice@example.com")     -> "al***@example.com"
        mask_email("ab@x.co")               -> "a***@x.co"
        mask_email("x@y.io")                -> "***@y.io"
        mask_email("nope")                  -> "***"
        mask_email(None)                    -> "***"

    Never raises; always returns a string.
    """
    if not email or not isinstance(email, str):
        return "***"
    if "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    if not local or not domain:
        return "***"
    if len(local) <= 1:
        visible = ""
    elif len(local) == 2:
        visible = local[0]
    else:
        visible = local[:2]
    return f"{visible}***@{domain}"
