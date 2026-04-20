"""
Operator script: send the Lite morning brief email to a specified
recipient NOW, using the specified shop as the data source.

Usage: ./venv/bin/python scripts/send_test_morning_brief.py <shop> <to_email>
"""
from __future__ import annotations

import sys

from app.core.database import SessionLocal
from app.core.email import send_email
from app.services.brief_engine import generate_brief
from app.services.lite_morning_digest import _build_email


def main():
    if len(sys.argv) < 3:
        print("usage: send_test_morning_brief.py <shop_domain> <to_email>")
        sys.exit(1)
    shop = sys.argv[1]
    to = sys.argv[2]

    db = SessionLocal()
    try:
        brief = generate_brief(db, shop)
        subject, html, plain = _build_email(shop, brief, db)
        print(f"Sending to {to}")
        print(f"Subject: {subject}")
        print(f"HTML bytes: {len(html)}")
        # Resend API confirms hedgesparkhq.com status=failed (used to
        # work through April 12, then verification broke — DKIM/SPF
        # detached DNS-side). Until founder re-verifies via
        # resend.com/domains, only onboarding@resend.dev can deliver
        # to the Resend account owner's email (tedialarana@gmail.com).
        resend_id = send_email(
            to=to,
            subject=f"[TEST v3] {subject}",
            html=html,
            text=plain,
            from_address="HedgeSpark <onboarding@resend.dev>",
        )
        if resend_id:
            print(f"✓ sent — resend_id={resend_id}")
        else:
            print("✗ send failed — see logs")
            sys.exit(2)
    finally:
        db.close()


if __name__ == "__main__":
    main()
