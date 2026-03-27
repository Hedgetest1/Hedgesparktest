"""
force_webhook.py — Dev-only: force orders/updated webhook registration for a shop.

Bypasses API auth. Loads token from DB exactly as production does.

Usage:
    cd /opt/wishspark/backend
    python -m scripts.force_webhook
    python -m scripts.force_webhook --shop other-store.myshopify.com
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Ensure backend root is on sys.path so `app.*` imports resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.core.database import SessionLocal
from app.core.token_crypto import decrypt_token
from app.models.merchant import Merchant
from app.services.shopify_admin import ensure_orders_webhook

DEFAULT_SHOP = "hedgespark-dev.myshopify.com"


def main() -> None:
    parser = argparse.ArgumentParser(description="Force webhook registration (dev tool)")
    parser.add_argument("--shop", default=DEFAULT_SHOP, help="Shop domain")
    args = parser.parse_args()

    shop = args.shop
    app_url = os.getenv("APP_URL", "").rstrip("/")
    if not app_url:
        print("FATAL: APP_URL not set in environment / .env")
        sys.exit(1)

    print(f"shop:    {shop}")
    print(f"app_url: {app_url}")
    print(f"topic:   orders/updated")
    print(f"target:  {app_url}/webhooks/shopify/orders")
    print()

    # --- Load token from DB (same path as production) ---
    db = SessionLocal()
    try:
        merchant = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
        if merchant is None:
            print(f"FATAL: no merchant row for shop={shop}")
            sys.exit(1)

        if not merchant.access_token:
            print(f"FATAL: merchant.access_token is NULL for shop={shop}")
            sys.exit(1)

        plaintext_token = decrypt_token(merchant.access_token)
        if not plaintext_token:
            print("FATAL: decrypt_token() returned None — check MERCHANT_TOKEN_ENCRYPTION_KEY")
            sys.exit(1)

        print(f"token:   {'*' * 8}{plaintext_token[-4:]}")
    finally:
        db.close()

    # --- Register webhook ---
    print("\nRegistering webhook with Shopify …")
    webhook_id, was_created = asyncio.run(
        ensure_orders_webhook(shop, plaintext_token, app_url)
    )

    if webhook_id is None:
        print("FAILED — ensure_orders_webhook returned None. Check logs above.")
        sys.exit(1)

    status = "CREATED" if was_created else "ALREADY EXISTS"
    print(f"\nResult:     {status}")
    print(f"webhook_id: {webhook_id}")
    print(f"topic:      orders/updated")
    print(f"address:    {app_url}/webhooks/shopify/orders")


if __name__ == "__main__":
    main()
