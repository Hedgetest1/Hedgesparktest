#!/usr/bin/env python3
"""
rotate_encryption_key.py — Re-encrypt all merchant secrets with the active key.

Usage:
    # 1. Set the NEW key as MERCHANT_TOKEN_ENCRYPTION_KEY in .env
    # 2. Set the OLD key as MERCHANT_TOKEN_ENCRYPTION_KEY_PREV in .env
    # 3. Restart backend (pm2 restart wishspark-backend)
    # 4. Run this script:
    cd /opt/wishspark/backend
    python -m scripts.rotate_encryption_key
    python -m scripts.rotate_encryption_key --dry-run   # preview only

    # 5. After all rows are v2, remove MERCHANT_TOKEN_ENCRYPTION_KEY_PREV from .env
    # 6. Restart backend again

The script:
  - Reads all merchants with encrypted access_token or encrypted_klaviyo_key
  - Decrypts with whichever key works (active or previous)
  - Re-encrypts with the active key (v2 scheme)
  - Writes the updated ciphertext back

This is MANUAL-GATED. It does not run automatically.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import argparse
from app.core.database import SessionLocal
from app.core.token_crypto import re_encrypt, is_encrypted, _SCHEME_V1, _SCHEME_V2, _KEY, _KEY_PREV
from app.models.merchant import Merchant


def main():
    parser = argparse.ArgumentParser(description="Rotate encryption key for merchant secrets")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()

    if _KEY is None:
        print("FATAL: MERCHANT_TOKEN_ENCRYPTION_KEY is not set.")
        sys.exit(1)

    if _KEY_PREV is None:
        print("WARNING: MERCHANT_TOKEN_ENCRYPTION_KEY_PREV is not set.")
        print("  Only v2-encrypted and plaintext values will be processed.")
        print("  v1 values encrypted with a different key will fail.")
        print()

    db = SessionLocal()
    try:
        merchants = db.query(Merchant).all()
        print(f"Scanning {len(merchants)} merchant(s)...")

        rotated = 0
        skipped = 0
        failed = 0

        for m in merchants:
            changes = {}

            # Rotate access_token
            if m.access_token and is_encrypted(m.access_token):
                if m.access_token.startswith(_SCHEME_V2):
                    pass  # already v2
                else:
                    new_val = re_encrypt(m.access_token)
                    if new_val and new_val != m.access_token:
                        changes["access_token"] = new_val
                    elif new_val is None:
                        print(f"  FAIL {m.shop_domain}: access_token decryption failed")
                        failed += 1

            # Rotate encrypted_klaviyo_key
            if m.encrypted_klaviyo_key and is_encrypted(m.encrypted_klaviyo_key):
                if m.encrypted_klaviyo_key.startswith(_SCHEME_V2):
                    pass
                else:
                    new_val = re_encrypt(m.encrypted_klaviyo_key)
                    if new_val and new_val != m.encrypted_klaviyo_key:
                        changes["encrypted_klaviyo_key"] = new_val
                    elif new_val is None:
                        print(f"  FAIL {m.shop_domain}: klaviyo key decryption failed")
                        failed += 1

            if changes:
                if args.dry_run:
                    print(f"  DRY  {m.shop_domain}: would rotate {list(changes.keys())}")
                else:
                    for col, val in changes.items():
                        setattr(m, col, val)
                    print(f"  DONE {m.shop_domain}: rotated {list(changes.keys())}")
                rotated += 1
            else:
                skipped += 1

        if not args.dry_run:
            db.commit()

        print(f"\nResult: rotated={rotated} skipped={skipped} failed={failed}")
        if args.dry_run:
            print("(dry run — no changes written)")

    finally:
        db.close()


if __name__ == "__main__":
    main()
