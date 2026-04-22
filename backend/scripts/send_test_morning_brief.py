"""
Operator script: send the Lite morning brief email to a specified
recipient NOW, using the specified shop as the data source.

Usage:
    ./venv/bin/python scripts/send_test_morning_brief.py <shop> <to_email>
    ./venv/bin/python scripts/send_test_morning_brief.py <shop> <to_email> <from_address>

The optional <from_address> lets the operator test sender-specific
deliverability (e.g. `digest@hedgesparkhq.com` vs `dev@hedgesparkhq.com`)
without editing env vars. Must be a full RFC-5322 form such as
`HedgeSpark <digest@hedgesparkhq.com>` or a bare address.
"""
from __future__ import annotations

import sys

from app.core.database import SessionLocal
from app.core.email import send_email
from app.services.brief_engine import generate_brief
from app.services.lite_morning_digest import _build_email


def main():
    if len(sys.argv) < 3:
        print(
            "usage: send_test_morning_brief.py <shop_domain> <to_email> "
            "[<from_address>]"
        )
        sys.exit(1)
    shop = sys.argv[1]
    to = sys.argv[2]
    from_address = sys.argv[3] if len(sys.argv) > 3 else None

    db = SessionLocal()
    try:
        brief = generate_brief(db, shop)
        subject, html, plain = _build_email(shop, brief, db)
        print(f"Sending to {to}")
        print(f"Subject: {subject}")
        print(f"From:    {from_address or '(default)'}")
        print(f"HTML bytes: {len(html)}")
        resend_id = send_email(
            to=to,
            subject=f"[TEST] {subject}",
            html=html,
            text=plain,
            from_address=from_address,
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
