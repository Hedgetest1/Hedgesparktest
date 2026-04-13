"""
validate_action_candidates.py — One-shot validation helper for GET /actions/candidates/pro.

What this script does
---------------------
1. Inserts a temporary Pro merchant row for SHOP_DOMAIN.
2. Calls GET /actions/candidates/pro against the running backend.
3. Prints the full JSON response.
4. Deletes the temporary merchant row in a finally block — cleanup is guaranteed
   even if the HTTP call fails or the endpoint raises an error.

Run from the backend directory with the project venv:
    cd /opt/wishspark/backend
    venv/bin/python3 scripts/validate_action_candidates.py

Expected sparse output
----------------------
At the time of writing, only legacy.myshopify.com has live data.
The engine will produce candidates based on opportunity signals mapped to
action types (SCARCITY_NUDGE, RETARGET_HOT_TRAFFIC, etc.).

Output depends on data availability — price_intelligence, unique_product_detection,
and visitor_product_state must have matching product keys.
Once product_url is normalised across all tables, the full candidate set will
appear here automatically with no changes to this script.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Bootstrap: add backend/ (parent of scripts/) to sys.path so that
# `from app.*` imports resolve regardless of how this script is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone

import httpx

# ---------------------------------------------------------------------------
# Configuration — change only these two constants if needed
# ---------------------------------------------------------------------------
SHOP_DOMAIN = "legacy.myshopify.com"
BASE_URL    = "http://localhost:8000"
# ---------------------------------------------------------------------------


def _insert_merchant(db) -> None:
    from app.models.merchant import Merchant
    row = Merchant(
        shop_domain    = SHOP_DOMAIN,
        plan           = "pro",
        billing_active = True,
        installed_at   = datetime.now(tz=timezone.utc).replace(tzinfo=None),
    )
    db.add(row)
    db.commit()
    print(f"[setup]   Inserted Pro merchant: {SHOP_DOMAIN}")


def _delete_merchant(db) -> None:
    from app.models.merchant import Merchant
    deleted = (
        db.query(Merchant)
        .filter(Merchant.shop_domain == SHOP_DOMAIN)
        .delete()
    )
    db.commit()
    print(f"[teardown] Removed merchant row(s): {deleted}")


def _call_endpoint() -> dict:
    url = f"{BASE_URL}/actions/candidates/pro"
    print(f"[request]  GET {url}?shop={SHOP_DOMAIN}")
    response = httpx.get(url, params={"shop": SHOP_DOMAIN}, timeout=30)
    print(f"[response] HTTP {response.status_code}")
    response.raise_for_status()
    return response.json()


def main() -> None:
    # Import here so the script fails fast if the venv / env is wrong,
    # before any DB mutation happens.
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        _insert_merchant(db)

        data = _call_endpoint()

        print("\n" + "=" * 60)
        print("  /actions/candidates/pro — response")
        print("=" * 60)
        print(json.dumps(data, indent=2, default=str))
        print("=" * 60)

        n = data.get("total_candidates", "?")
        print(f"\n[result]   {n} candidate(s) returned.")
        if n == 0 or n == "?":
            print(
                "[note]     Zero candidates is expected in the current data state.\n"
                "           Signals use path-style product_url (/products/...).\n"
                "           price_intelligence, unique_product_detection, and\n"
                "           visitor_product_state still use old full URLs.\n"
                "           Fix: normalise product_url at write time in the workers."
            )

    except httpx.HTTPStatusError as exc:
        print(f"[error]    HTTP {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"[error]    {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        _delete_merchant(db)
        db.close()


if __name__ == "__main__":
    main()
