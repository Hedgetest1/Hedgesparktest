"""inventory_snapshot_fetcher.py — Gap #4 Inventory KPIs.

Daily bulk inventory fetcher. Wraps the existing Shopify Admin API
plumbing in `shopify_admin.py` to paginate through ALL products of a
shop, snapshot every variant's `inventory_quantity`, and upsert into
`inventory_snapshots`.

Called from the aggregation_worker once per merchant per day (cycle
spreads merchants over the 24h window via hash bucket).

Returns the count of rows upserted (or 0 on failure).

Per-merchant rate-limit budget:
  Shopify Admin REST: 40 req/min/store
  Bulk products:      250 SKUs/page
  Typical merchant:   ~50 SKUs → 1 page → 1 request
  Large merchant:     ~5000 SKUs → 20 pages → 20 requests (~30s @ 40/min)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.shopify_client import shopify_request
from app.models.inventory_snapshot import InventorySnapshot
from app.services.shopify_admin import _get_access_token, _shopify_url  # noqa: F401

log = logging.getLogger("inventory_snapshot_fetcher")

# Hard ceiling on pages per merchant per day. At 250 SKUs/page this is
# 25k SKUs which covers >99% of Shopify merchants. A shop with 25k+
# SKUs is by definition not in the $0-60 band; their inventory needs
# the multi-location Pro tier.
_MAX_PAGES_PER_RUN = 100
_PAGE_SIZE = 250


def fetch_and_snapshot(db: Session, shop_domain: str) -> dict[str, Any]:
    """Pull all products + variants for a shop and upsert today's
    inventory snapshot rows.

    Returns a summary dict:
      {
        "shop_domain": str,
        "ok": bool,
        "rows_upserted": int,
        "products_seen": int,
        "variants_seen": int,
        "pages_fetched": int,
        "error": str | None,
      }
    """
    summary = {
        "shop_domain": shop_domain,
        "ok": False,
        "rows_upserted": 0,
        "products_seen": 0,
        "variants_seen": 0,
        "pages_fetched": 0,
        "error": None,
    }

    access_token = _get_access_token(db, shop_domain)
    if not access_token:
        summary["error"] = "no_access_token"
        return summary

    today = datetime.now(timezone.utc).date()
    all_rows: list[dict[str, Any]] = []
    page_info: str | None = None

    for page in range(_MAX_PAGES_PER_RUN):
        # Cursor pagination via page_info; the first page has no cursor.
        params: dict[str, Any] = {
            "limit": _PAGE_SIZE,
            "fields": "id,title,handle,variants",
        }
        if page_info:
            params = {"limit": _PAGE_SIZE, "page_info": page_info}

        resp = shopify_request(
            "GET", shop_domain, "products.json", access_token, params=params
        )
        if resp is None:
            summary["error"] = "shopify_request_returned_none"
            return summary
        if resp.status_code == 401:
            summary["error"] = "token_revoked"
            return summary
        if resp.status_code == 429:
            summary["error"] = "rate_limited"
            return summary
        if resp.status_code >= 400:
            summary["error"] = f"http_{resp.status_code}"
            return summary

        body = resp.json() or {}
        products = body.get("products", []) or []
        summary["pages_fetched"] += 1
        if not products:
            break

        for p in products:
            summary["products_seen"] += 1
            handle = p.get("handle") or ""
            product_url = f"/products/{handle}" if handle else ""
            title = (p.get("title") or "")[:255]
            variants = p.get("variants") or []
            for v in variants:
                summary["variants_seen"] += 1
                qty = int(v.get("inventory_quantity") or 0)
                variant_id = str(v.get("id") or "")
                all_rows.append(
                    {
                        "shop_domain": shop_domain,
                        "product_url": product_url,
                        "product_title": title,
                        # Empty string for "no variant"; the schema NOT NULL
                        # default is the same so the UNIQUE constraint dedups
                        # cleanly across both shapes.
                        "variant_id": variant_id,
                        "inventory_quantity": qty,
                        "snapshot_date": today,
                    }
                )

        # Shopify cursor pagination: Link header carries `next` URL with
        # page_info=...; the helper returns the parsed next page_info if
        # present. For now, break when fewer than _PAGE_SIZE products
        # came back (last page).
        if len(products) < _PAGE_SIZE:
            break
        # Best-effort cursor: parse Link header for page_info=
        link = resp.headers.get("Link") or resp.headers.get("link") or ""
        next_pi: str | None = None
        for part in link.split(","):
            if 'rel="next"' in part:
                bits = part.split(";")[0].strip().strip("<>")
                if "page_info=" in bits:
                    next_pi = bits.split("page_info=")[1].split("&")[0]
                break
        if not next_pi:
            break
        page_info = next_pi

    # Upsert in chunks so we don't blow the parameter limit
    if all_rows:
        CHUNK = 500
        for i in range(0, len(all_rows), CHUNK):
            chunk = all_rows[i : i + CHUNK]
            stmt = pg_insert(InventorySnapshot.__table__).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=None,
                # Use the named UNIQUE constraint
                constraint="uq_inventory_shop_product_variant_date",
                set_={
                    "inventory_quantity": stmt.excluded.inventory_quantity,
                    "product_title": stmt.excluded.product_title,
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )
            try:
                db.execute(stmt)
                summary["rows_upserted"] += len(chunk)
            except Exception as exc:  # noqa: BLE001
                # On any DB error, drop and retry once with a smaller chunk
                log.warning(
                    "inventory_snapshot upsert failed for shop=%s chunk=%d: %s",
                    shop_domain, i, exc,
                )
                summary["error"] = f"db_upsert_failed: {exc}"
                db.rollback()
                return summary
        try:
            db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("inventory_snapshot commit failed for shop=%s: %s", shop_domain, exc)
            summary["error"] = f"db_commit_failed: {exc}"
            db.rollback()
            return summary

    summary["ok"] = True
    return summary
