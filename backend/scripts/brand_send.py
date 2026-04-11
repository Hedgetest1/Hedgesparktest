"""
brand_send.py — Send the 4 brand-validated email previews.

Approved by operator. Sends to tedialarana@gmail.com from dev@hedgesparkhq.com.
Logs all sends with resend_id.
"""
import sys
sys.path.append("/opt/wishspark/backend")

from app.core.env_bootstrap import load_env
load_env()

from app.core.email import send_email

# Import the builders
from scripts.brand_preview import (
    build_welcome,
    build_revenue_trigger,
    build_weekly_digest,
    build_reengagement,
)

TO = "tedialarana@gmail.com"
FROM = "Hedge Spark <dev@hedgesparkhq.com>"

emails = [
    ("1_welcome", build_welcome),
    ("2_revenue_trigger", build_revenue_trigger),
    ("3_weekly_digest", build_weekly_digest),
    ("4_reengagement", build_reengagement),
]

results = []

for name, builder in emails:
    subject, html, plain = builder()

    resend_id = send_email(
        to=TO,
        subject=subject,
        html=html,
        text=plain,
        from_address=FROM,
    )

    status = "SENT" if resend_id else "FAILED"
    results.append({"name": name, "subject": subject, "status": status, "resend_id": resend_id})
    print(f"{status}: {name} | {subject} | resend_id={resend_id}")

print(f"\nDone. {sum(1 for r in results if r['status'] == 'SENT')}/4 sent successfully.")
