"""
brand_send_welcome_v2.py — Behavioral welcome email.

TRIGGER STATE:
  Merchant just completed OAuth install. HedgeSpark connected to their store.
  Tracking script is now deployed. First visitor events are starting to flow.
  The system is already watching — this email reflects that reality back.

This is NOT a product explanation. It's a status update from a system
that is already active on their store.

Sends to tedialarana@gmail.com after generating + validating.
"""
import sys
sys.path.append("/opt/wishspark/backend")

from app.core.env_bootstrap import load_env
load_env()

from app.services.email_templates import (
    _wrap_html, _p, _button,
)
from app.services.brand_voice import validate_email_text, validate_subject_line
from app.core.email import send_email


_DASHBOARD_URL = "https://app.hedgesparkhq.com/"

shop_name = "Stella & Ivy"

subject = f"HedgeSpark is connected to {shop_name}"

body = (
    _p(
        f"HedgeSpark is now live on "
        f"<strong style='color:#f1f5f9;'>{shop_name}</strong>. "
        f"The tracking script is deployed and visitor data has started flowing.",
    )
    + _p(
        "You don't need to do anything right now. "
        "First insights will appear in your dashboard within the next few hours "
        "as traffic comes in.",
        color="#94a3b8",
    )
    + _p(
        "One thing you'll want to set up when you're ready: the "
        "<strong style='color:#e2e8f0;'>purchase pixel</strong>. "
        "Without it, HedgeSpark can see visitor behavior but can't connect it to revenue. "
        "The dashboard walks you through it — takes under 3 minutes.",
        color="#94a3b8",
    )
    + _button("Open your dashboard", _DASHBOARD_URL)
    + '<p style="margin:20px 0 0 0;font-size:12px;color:#475569;">'
    + "If anything looks off, reply to this email."
    + "</p>"
)

html = _wrap_html(subject, body, show_logo=True)

plain = (
    f"HedgeSpark is now live on {shop_name}. "
    f"The tracking script is deployed and visitor data has started flowing.\n\n"
    f"You don't need to do anything right now. "
    f"First insights will appear in your dashboard within the next few hours "
    f"as traffic comes in.\n\n"
    f"One thing you'll want to set up when you're ready: the purchase pixel. "
    f"Without it, HedgeSpark can see visitor behavior but can't connect it to revenue. "
    f"The dashboard walks you through it — takes under 3 minutes.\n\n"
    f"Open your dashboard: {_DASHBOARD_URL}\n\n"
    f"If anything looks off, reply to this email."
)

# Validate
text_check = validate_email_text(plain)
subj_check = validate_subject_line(subject)
print(f"Subject: {subject}")
print(f"Brand check (text): passed={text_check.passed}, violations={text_check.violations}, warnings={text_check.warnings}")
print(f"Brand check (subj): passed={subj_check.passed}, violations={subj_check.violations}")
print(f"Word count: {len(plain.split())}")
print()

# Send
TO = "tedialarana@gmail.com"
FROM = "Hedge Spark <dev@hedgesparkhq.com>"

resend_id = send_email(to=TO, subject=subject, html=html, text=plain, from_address=FROM)
status = "SENT" if resend_id else "FAILED"
print(f"{status}: resend_id={resend_id}")
