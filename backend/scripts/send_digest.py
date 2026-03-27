"""
send_digest.py — Manually send a weekly revenue digest for one merchant.

Usage:
    cd /opt/wishspark/backend
    python -m scripts.send_digest
    python -m scripts.send_digest --shop other-store.myshopify.com
    python -m scripts.send_digest --dry-run   # assemble + format, don't send
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.core.database import SessionLocal
from app.models.merchant import Merchant
from app.services.weekly_digest import assemble_digest
from app.services.digest_formatter import format_digest
from app.core.email import send_email

DEFAULT_SHOP = "hedgespark-dev.myshopify.com"


def main() -> None:
    parser = argparse.ArgumentParser(description="Send weekly revenue digest")
    parser.add_argument("--shop", default=DEFAULT_SHOP)
    parser.add_argument("--dry-run", action="store_true", help="Assemble and print, don't send")
    parser.add_argument("--to", default=None, help="Override recipient email")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Resolve merchant
        merchant = db.query(Merchant).filter(Merchant.shop_domain == args.shop).first()
        if not merchant:
            print(f"FATAL: no merchant row for shop={args.shop}")
            sys.exit(1)

        recipient = args.to or merchant.contact_email
        if not recipient:
            print(f"FATAL: no contact_email for shop={args.shop} and no --to override")
            sys.exit(1)

        # Assemble
        print(f"Assembling digest for {args.shop}...")
        digest = assemble_digest(db, args.shop, merchant_plan=merchant.plan or "lite")
        if digest is None:
            print("No orders in the last 14 days — nothing to send.")
            sys.exit(0)

        # Format
        html, plain = format_digest(digest)

        # Print summary
        tw = digest["this_week"]
        print(f"  Revenue:  {digest['currency']} {tw['revenue']:,.2f}")
        print(f"  Orders:   {tw['order_count']}")
        print(f"  AOV:      {digest['currency']} {tw['aov']:,.2f}")
        if digest.get("revenue_delta_pct") is not None:
            print(f"  WoW:      {digest['revenue_delta_pct']:+.1f}%")
        if digest.get("insight"):
            print(f"  Insight:  {digest['insight']['message']}")
        print()

        if args.dry_run:
            print("=== DRY RUN — email not sent ===")
            print()
            print(plain)
            print()
            print(f"HTML length: {len(html)} chars")
            return

        # Send
        subject = (
            f"Your weekly revenue: {digest['currency']} {tw['revenue']:,.2f} "
            f"({tw['order_count']} orders)"
        )
        print(f"Sending to {recipient}...")
        ok = send_email(to=recipient, subject=subject, html=html, text=plain)
        if ok:
            print(f"SENT successfully to {recipient}")
        else:
            print("SEND FAILED — check logs / RESEND_API_KEY")
            sys.exit(1)

    finally:
        db.close()


if __name__ == "__main__":
    main()
