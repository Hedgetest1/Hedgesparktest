"""
shopify_cogs_sync.py — Auto-import real product COGS from Shopify Admin API.

The killer Phase 3 piece: a merchant installs HedgeSpark, this service fires
once at onboarding (or on-demand via the Settings button), and the Profit
Intelligence cassettone upgrades from "rough" to "refined" precision with
zero manual input. Real per-product cost data straight from Shopify's
`productVariant.inventoryItem.unitCost` field.

Why GraphQL, not REST
---------------------
We initially wired this via REST (`/products.json` + `/inventory_items.json`)
but discovered `inventory_items.json` requires the `read_inventory` scope,
which is NOT part of the base HedgeSpark install. Forcing every existing
merchant to re-authorize for a new scope is a terrible UX.

GraphQL's `productVariant.inventoryItem.unitCost` is accessible with just
`read_products` — the scope we already have. Same data, zero re-auth. Shopify
confirmed this scope asymmetry in their own docs; this is why every modern
Shopify integration uses GraphQL for cost reads.

How it works
------------
1. Cursor-paginate `products(first: 100) { edges { cursor node { ... } } }`
   fetching products + variants + inventoryItem.unitCost in one shot.
2. Aggregate by product: arithmetic mean of variant unit costs (excluding
   variants with null cost).
3. Upsert into `product_costs` via the same path the manual Settings UI uses.
   Provenance is set to `"shopify_admin_api"` so the Settings UI can show
   "imported from Shopify" badges.

product_key matching contract
-----------------------------
We use the Shopify numeric product_id as a STRING (extracted from the GraphQL
`gid://shopify/Product/{id}` global ID). This matches what
`shop_orders.line_items` stores in the `product_id` field — meaning the moment
this sync runs, `pnl_engine._compute_real_cogs` picks up real per-product COGS
on the very next `/pro/pnl` call.

Idempotent + non-destructive
----------------------------
- Calling twice is safe: second call updates the same rows in place.
- Merchant-entered manual rows (source="manual" with a non-null cost) are
  NEVER overwritten. Only rows with source="shopify_admin_api" or with a
  null cost are touched.
- Shopify-side null costs are reported in `skipped_no_cost` so the Settings
  UI can surface "you have N products with no cost set in Shopify — enter
  them there to complete your P&L".

Scope required
--------------
`read_products` — standard scope every HedgeSpark install already requests.
No re-auth needed. (REST would have required `read_inventory` upgrade.)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.core.shopify_client import shopify_request
from app.models.merchant import Merchant
from app.models.product_cost import ProductCost

log = logging.getLogger(__name__)

_PAGE_SIZE = 100
_MAX_PAGES = 50      # safety cap: 5000 products per sync run
_VARIANTS_PER_PRODUCT = 50


# GraphQL cursor pagination. Fetches products with all their variants and the
# unitCost field in a single round trip per page. The `$cursor` variable is
# omitted on the first call and set from the previous page's endCursor after.
_GRAPHQL_QUERY = """
query ProductCosts($first: Int!, $cursor: String) {
  products(first: $first, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    edges {
      cursor
      node {
        id
        title
        variants(first: %(vpp)d) {
          edges {
            node {
              id
              price
              inventoryItem {
                unitCost { amount currencyCode }
              }
            }
          }
        }
      }
    }
  }
}
""" % {"vpp": _VARIANTS_PER_PRODUCT}


# Matches gid://shopify/Product/1234567890 → "1234567890"
_GID_NUMERIC_RE = re.compile(r"/(\d+)$")


def sync_product_costs_from_shopify(
    db: Session,
    shop_domain: str,
) -> dict:
    """
    Pull real product cost data from Shopify Admin GraphQL API and upsert
    into the `product_costs` table. Returns a result dict suitable for the
    POST /pro/costs/sync-from-shopify API response.
    """
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)

    # ------------------------------------------------------------------
    # 1. Decrypt the merchant's access token via the same helper every
    #    existing TIER_0 service uses (onboarding, billing_sync, etc).
    # ------------------------------------------------------------------
    access_token = _load_access_token(db, shop_domain)
    if access_token is None:
        return _result(
            shop_domain=shop_domain,
            status="error",
            reason="no_access_token",
            message="Shop has no Shopify access token. Reinstall the app to grant permissions.",
        )

    # ------------------------------------------------------------------
    # 2. Paginate products via GraphQL, collecting (product_id, title,
    #    variant_costs) tuples.
    # ------------------------------------------------------------------
    try:
        products = _fetch_all_products_graphql(shop_domain, access_token)
    except Exception as exc:
        log.error("cogs_sync: graphql fetch failed shop=%s: %s", shop_domain, exc)
        return _result(
            shop_domain=shop_domain,
            status="error",
            reason="graphql_fetch_failed",
            message=f"Couldn't fetch products from Shopify: {str(exc)[:140]}",
        )

    if not products:
        return _result(
            shop_domain=shop_domain,
            status="empty",
            reason="no_products",
            message="No products returned from Shopify Admin API — nothing to sync.",
            products_scanned=0,
        )

    # ------------------------------------------------------------------
    # 3. Aggregate by product.
    # ------------------------------------------------------------------
    inserted = 0
    updated  = 0
    skipped_no_cost = 0
    variants_scanned = 0
    shop_currency: Optional[str] = None

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        for prod in products:
            gid = prod.get("id") or ""
            product_key = _extract_numeric_id(gid)
            if not product_key:
                continue

            title = (prod.get("title") or "")[:255]
            variant_costs: list[float] = []
            variant_currency: Optional[str] = None

            for var_edge in (prod.get("variants") or {}).get("edges") or []:
                variants_scanned += 1
                var_node = var_edge.get("node") or {}
                inv_item = var_node.get("inventoryItem") or {}
                unit_cost = inv_item.get("unitCost")
                if unit_cost is None:
                    continue
                amount = _to_float(unit_cost.get("amount"))
                if amount is None or amount <= 0:
                    continue
                variant_costs.append(amount)
                if variant_currency is None:
                    variant_currency = unit_cost.get("currencyCode")

            if not variant_costs:
                skipped_no_cost += 1
                continue

            # Remember shop-level currency for defaults persistence below.
            if shop_currency is None and variant_currency is not None:
                shop_currency = variant_currency

            mean_cost = round(sum(variant_costs) / len(variant_costs), 2)

            existing = (
                db.query(ProductCost)
                .filter_by(shop_domain=shop_domain, product_key=product_key)
                .first()
            )
            if existing is None:
                db.add(ProductCost(
                    shop_domain=shop_domain,
                    product_key=product_key,
                    product_title=title,
                    cogs_per_unit=Decimal(str(mean_cost)),
                    shipping_cost_per_unit=None,
                    currency=variant_currency,
                    source="shopify_admin_api",
                    created_at=now,
                    updated_at=now,
                ))
                inserted += 1
            else:
                # Protect manual merchant entries — only touch rows that were
                # already auto-imported, or rows with no cost set.
                can_update = (
                    existing.source == "shopify_admin_api"
                    or existing.cogs_per_unit is None
                )
                if can_update:
                    existing.product_title = title or existing.product_title
                    existing.cogs_per_unit  = Decimal(str(mean_cost))
                    existing.currency       = variant_currency or existing.currency
                    existing.source         = "shopify_admin_api"
                    existing.updated_at     = now
                    updated += 1

        db.commit()
    except Exception as exc:
        db.rollback()
        log.error("cogs_sync: upsert failed shop=%s: %s", shop_domain, exc)
        return _result(
            shop_domain=shop_domain,
            status="error",
            reason="upsert_failed",
            message=f"Database write failed: {str(exc)[:120]}",
        )

    duration_ms = int(
        (datetime.now(timezone.utc).replace(tzinfo=None) - started_at).total_seconds() * 1000
    )

    log.info(
        "cogs_sync: shop=%s products=%d variants=%d inserted=%d updated=%d "
        "skipped=%d duration=%dms",
        shop_domain, len(products), variants_scanned,
        inserted, updated, skipped_no_cost, duration_ms,
    )

    total_imported = inserted + updated
    if total_imported == 0 and skipped_no_cost > 0:
        # Everything was skipped — merchant needs to set costs in Shopify.
        message = (
            f"Scanned {len(products)} products but none had a cost set in Shopify. "
            f"Enter your product costs in Shopify admin → Products → Inventory, or "
            f"set a default COGS % in the form above."
        )
        status = "empty"
    else:
        message = (
            f"Imported real cost data for {total_imported} products "
            f"({inserted} new, {updated} updated)."
            + (f" Skipped {skipped_no_cost} products with no cost set in Shopify."
               if skipped_no_cost > 0 else "")
        )
        status = "ok"

    return _result(
        shop_domain=shop_domain,
        status=status,
        reason=None,
        message=message,
        products_scanned=len(products),
        variants_scanned=variants_scanned,
        inserted=inserted,
        updated=updated,
        skipped_no_cost=skipped_no_cost,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_access_token(db: Session, shop_domain: str) -> Optional[str]:
    """
    Decrypt the merchant's Shopify access token for Admin API calls.
    Mirrors the pattern used throughout app/services/ for the same purpose.
    """
    from app.core.token_crypto import decrypt_token
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if not merchant or not merchant.access_token:
        log.warning("cogs_sync: no access_token for shop=%s", shop_domain)
        return None
    decrypted = decrypt_token(merchant.access_token)
    if decrypted is None:
        log.error("cogs_sync: token decryption failed for shop=%s", shop_domain)
    return decrypted


def _fetch_all_products_graphql(
    shop_domain: str,
    access_token: str,
) -> list[dict]:
    """
    Cursor-paginate the GraphQL products query until hasNextPage=false or
    the _MAX_PAGES safety cap is hit. Returns a flat list of product nodes
    including their embedded variants + unitCost data.
    """
    all_products: list[dict] = []
    cursor: Optional[str] = None

    for page in range(_MAX_PAGES):
        variables = {"first": _PAGE_SIZE, "cursor": cursor}
        resp = shopify_request(
            "POST", shop_domain, "graphql.json", access_token,
            json_body={"query": _GRAPHQL_QUERY, "variables": variables},
        )
        if resp is None:
            raise RuntimeError("graphql.json returned None (rate limit or permanent failure)")
        if resp.status_code >= 400:
            raise RuntimeError(f"graphql.json HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        # GraphQL errors are returned in the body with HTTP 200 — check them.
        if "errors" in data and data["errors"]:
            raise RuntimeError(f"graphql errors: {str(data['errors'])[:200]}")

        products_block = (data.get("data") or {}).get("products") or {}
        edges = products_block.get("edges") or []
        for edge in edges:
            node = edge.get("node")
            if node is not None:
                all_products.append(node)

        page_info = products_block.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break

    return all_products


def _extract_numeric_id(gid: str) -> Optional[str]:
    """
    Extract the numeric id from a Shopify GraphQL global id.
    'gid://shopify/Product/7234567890' → '7234567890'
    """
    if not gid:
        return None
    match = _GID_NUMERIC_RE.search(gid)
    if not match:
        return None
    return match.group(1)


def _to_float(v) -> Optional[float]:
    """Parse a Shopify numeric-as-string field to float, returning None on NaN/empty."""
    if v is None:
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    if f != f:  # NaN check
        return None
    return f


def _result(
    shop_domain: str,
    status: str,
    reason: Optional[str],
    message: str,
    products_scanned: int = 0,
    variants_scanned: int = 0,
    inserted: int = 0,
    updated: int = 0,
    skipped_no_cost: int = 0,
    duration_ms: int = 0,
) -> dict:
    """Build the return dict consumed by the cost_config API endpoint."""
    return {
        "shop_domain":      shop_domain,
        "status":           status,
        "reason":           reason,
        "message":          message,
        "products_scanned": products_scanned,
        "variants_scanned": variants_scanned,
        "inserted":         inserted,
        "updated":          updated,
        "skipped_no_cost":  skipped_no_cost,
        "duration_ms":      duration_ms,
    }
