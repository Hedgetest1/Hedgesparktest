"""
send_all_digests.py — Send weekly revenue digest to ALL eligible merchants.

Usage:
    cd /opt/wishspark/backend
    python -m scripts.send_all_digests
    python -m scripts.send_all_digests --dry-run

Eligible: install_status = 'active' AND contact_email IS NOT NULL.
Skips merchants with no orders in the last 14 days (assemble_digest returns None).
Fails per-merchant, not globally — one bad merchant doesn't block the rest.
"""
from __future__ import annotations

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.core.database import SessionLocal
from app.core.operator_blocklist import (
    is_operator_dev_shop,
    is_operator_email,
    operator_dev_shops,
)
from app.models.merchant import Merchant
from app.services.weekly_digest import assemble_digest
from app.services.digest_formatter import format_digest
from app.core.email import send_email


def main() -> None:
    parser = argparse.ArgumentParser(description="Send weekly digest to all merchants")
    parser.add_argument("--dry-run", action="store_true", help="Assemble only, don't send")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Operator/dev tenant guard (founder direttiva 2026-05-06):
        # this script previously skipped the orchestrator gate AND the
        # billing_active filter; without the operator-shop NOT IN clause
        # below, the founder's dev tenant (`hedgespark-dev.myshopify.com`)
        # would receive the same email merchants get.
        merchants = (
            db.query(Merchant)
            .filter(
                Merchant.install_status == "active",
                Merchant.contact_email.isnot(None),
                Merchant.contact_email != "",
                Merchant.billing_active == True,  # Pro merchants only
                ~Merchant.shop_domain.in_(operator_dev_shops()),
            )
            .all()
        )
        # Address-level fallback (defence in depth)
        merchants = [m for m in merchants if not is_operator_email(m.contact_email)]

        print(f"Eligible merchants: {len(merchants)}")
        sent = 0
        skipped = 0
        failed = 0

        for m in merchants:
            shop = m.shop_domain
            email = m.contact_email
            try:
                digest = assemble_digest(db, shop, merchant_plan=m.plan or "lite")
                if digest is None:
                    print(f"  SKIP {shop} — no orders in last 14 days")
                    skipped += 1
                    continue

                html, plain = format_digest(digest)
                tw = digest["this_week"]
                subject = (
                    f"Your weekly revenue: {digest['currency']} {tw['revenue']:,.2f} "
                    f"({tw['order_count']} orders)"
                )

                if args.dry_run:
                    print(f"  DRY  {shop} → {email} | {subject}")
                    sent += 1
                    continue

                ok = send_email(to=email, subject=subject, html=html, text=plain,
                               from_address="Hedge Spark <digest@hedgesparkhq.com>")
                if ok:
                    print(f"  SENT {shop} → {email}")
                    sent += 1
                else:
                    print(f"  FAIL {shop} → {email} — send_email returned False")
                    failed += 1

            except Exception as exc:
                print(f"  ERROR {shop}: {exc}")
                failed += 1

        print(f"\nDone: sent={sent} skipped={skipped} failed={failed}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
