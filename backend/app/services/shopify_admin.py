"""
shopify_admin.py — Shopify Admin REST API client.

Uses the merchant's stored OAuth access_token to call Shopify Admin API v2024-01.
All functions return None / False on failure — never raise.

Public interface
----------------
    get_product_inventory(db, shop_domain, product_url) -> dict | None
        Fetch real inventory levels for a product by storefront URL.
        Returns {"product_id", "variant_id", "inventory_quantity", "title"}

    create_discount(db, shop_domain, title, percentage, code,
                    product_ids=None, usage_limit=100) -> dict | None
        Create a percentage discount code via Shopify price rules.
        Returns {"code", "price_rule_id", "discount_code_id"}

    update_product_price(db, shop_domain, variant_id, new_price) -> bool
        Update the price of a specific variant.
        Returns True on success.

    get_shop_products(db, shop_domain, limit=10) -> list[dict]
        Fetch recent products from the store (for catalog enrichment).

Install-time helpers (async — called from OAuth callback)
---------------------------------------------------------
    ensure_orders_webhook(shop, token, app_url) -> (webhook_id, created)
        Idempotently register the orders/updated webhook.
        Checks existing webhooks before creating.
        Replaces mismatched URL webhooks.

    ensure_tracker_script_tag(shop, token, tracker_url) -> (script_tag_id, created)
        Idempotently inject spark-tracker.js via Shopify Script Tags API.
        Checks existing script tags before creating.

    NOTE on Script Tags deprecation
    --------------------------------
    Shopify began deprecating the Script Tags API for new public apps in 2024
    in favour of Theme App Extensions / App Blocks.  Script Tags continue to
    work for existing apps and are the correct pragmatic approach for v1 of
    this architecture where spark-tracker.js is a standalone hosted script.

    Migration path when required:
      1. Package spark-tracker.js as a Shopify Theme App Extension block
      2. Publish via Shopify CLI (shopify app deploy)
      3. Remove script_tag_id column when all merchants are on the extension
    Until that migration is done, Script Tags are the only viable self-serve
    injection mechanism without requiring merchants to edit their theme code.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models.merchant import Merchant

log = logging.getLogger(__name__)

SHOPIFY_API_VERSION = "2024-01"
_REQUEST_TIMEOUT = 10.0  # seconds


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_access_token(db: Session, shop_domain: str) -> Optional[str]:
    from app.core.token_crypto import decrypt_token
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
    if not merchant or not merchant.access_token:
        log.debug("shopify_admin: no access_token for shop=%s", shop_domain)
        return None
    decrypted = decrypt_token(merchant.access_token)
    if decrypted is None:
        log.error(
            "shopify_admin: token decryption failed for shop=%s — "
            "Admin API calls will fail.  Check MERCHANT_TOKEN_ENCRYPTION_KEY.",
            shop_domain,
        )
    return decrypted


def _shopify_url(shop_domain: str, path: str) -> str:
    return f"https://{shop_domain}/admin/api/{SHOPIFY_API_VERSION}/{path}"


def _extract_handle(product_url: str) -> Optional[str]:
    """
    Extract Shopify handle from a storefront product URL.

    /products/ceramic-vase           → ceramic-vase
    /products/ceramic-vase?variant=1 → ceramic-vase
    ceramic-vase                     → ceramic-vase
    """
    cleaned = product_url.strip("/")
    parts = cleaned.split("/")
    # /products/<handle>
    if len(parts) >= 2 and parts[-2] == "products":
        return parts[-1].split("?")[0]
    # bare handle (no path prefix)
    if len(parts) == 1:
        return parts[0].split("?")[0]
    return None


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def get_product_inventory(
    db: Session,
    shop_domain: str,
    product_url: str,
) -> Optional[dict]:
    """
    Fetch real inventory data for a product by its storefront URL.

    Uses: GET /admin/api/2024-01/products.json?handle={handle}

    Returns:
        {
            "product_id":         int,
            "variant_id":         int,   # first variant
            "inventory_quantity": int,   # total across all variants
            "title":              str,
        }
        or None if product not found or Admin API unavailable.
    """
    access_token = _get_access_token(db, shop_domain)
    if not access_token:
        return None

    handle = _extract_handle(product_url)
    if not handle:
        log.warning("shopify_admin: cannot extract handle from product_url=%s", product_url)
        return None

    headers = {"X-Shopify-Access-Token": access_token}

    try:
        resp = httpx.get(
            _shopify_url(shop_domain, "products.json"),
            headers=headers,
            params={"handle": handle, "fields": "id,title,variants"},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        products = resp.json().get("products", [])

        if not products:
            log.debug("shopify_admin: product not found handle=%s shop=%s", handle, shop_domain)
            return None

        product = products[0]
        variants = product.get("variants", [])
        if not variants:
            return None

        total_qty = sum(v.get("inventory_quantity") or 0 for v in variants)
        first_variant = variants[0]

        log.info(
            "shopify_admin: inventory shop=%s handle=%s product_id=%s qty=%d",
            shop_domain, handle, product["id"], total_qty,
        )

        return {
            "product_id":         product["id"],
            "variant_id":         first_variant["id"],
            "inventory_quantity": total_qty,
            "title":              product.get("title", handle),
        }

    except httpx.HTTPStatusError as exc:
        log.error(
            "shopify_admin: HTTP %d fetching inventory shop=%s handle=%s",
            exc.response.status_code, shop_domain, handle,
        )
    except Exception as exc:
        log.error(
            "shopify_admin: error fetching inventory shop=%s handle=%s: %s",
            shop_domain, handle, exc,
        )
    return None


def create_discount(
    db: Session,
    shop_domain: str,
    title: str,
    percentage: float,      # e.g. 10.0 = 10% off
    code: str,
    product_ids: Optional[list[int]] = None,
    usage_limit: int = 100,
) -> Optional[dict]:
    """
    Create a percentage discount code via Shopify Admin API.

    Flow:
        1. POST /price_rules.json  — creates the rule
        2. POST /price_rules/{id}/discount_codes.json — attaches the code

    Returns:
        {"code": str, "price_rule_id": int, "discount_code_id": int}
        or None on failure.
    """
    access_token = _get_access_token(db, shop_domain)
    if not access_token:
        return None

    headers = {
        "X-Shopify-Access-Token": access_token,
        "Content-Type": "application/json",
    }

    price_rule_body: dict = {
        "price_rule": {
            "title":              title,
            "target_type":        "line_item",
            "target_selection":   "entitled" if product_ids else "all",
            "allocation_method":  "across",
            "value_type":         "percentage",
            "value":              f"-{percentage}",
            "customer_selection": "all",
            "usage_limit":        usage_limit,
            "starts_at":          "2000-01-01T00:00:00Z",
        }
    }
    if product_ids:
        price_rule_body["price_rule"]["entitled_product_ids"] = product_ids

    try:
        rule_resp = httpx.post(
            _shopify_url(shop_domain, "price_rules.json"),
            headers=headers,
            json=price_rule_body,
            timeout=_REQUEST_TIMEOUT,
        )
        rule_resp.raise_for_status()
        price_rule_id = rule_resp.json()["price_rule"]["id"]

        code_resp = httpx.post(
            _shopify_url(shop_domain, f"price_rules/{price_rule_id}/discount_codes.json"),
            headers=headers,
            json={"discount_code": {"code": code}},
            timeout=_REQUEST_TIMEOUT,
        )
        code_resp.raise_for_status()
        discount_code_id = code_resp.json()["discount_code"]["id"]

        log.info(
            "shopify_admin: created discount code=%s rule_id=%d shop=%s",
            code, price_rule_id, shop_domain,
        )
        return {
            "code":              code,
            "price_rule_id":     price_rule_id,
            "discount_code_id":  discount_code_id,
        }

    except httpx.HTTPStatusError as exc:
        log.error(
            "shopify_admin: HTTP %d creating discount shop=%s: %s",
            exc.response.status_code, shop_domain, exc.response.text[:200],
        )
    except Exception as exc:
        log.error("shopify_admin: error creating discount shop=%s: %s", shop_domain, exc)
    return None


def update_product_price(
    db: Session,
    shop_domain: str,
    variant_id: int,
    new_price: str,   # Shopify expects string, e.g. "29.99"
) -> bool:
    """
    Update the price of a product variant.

    Uses: PUT /admin/api/2024-01/variants/{variant_id}.json

    Returns True on success, False on failure.
    """
    access_token = _get_access_token(db, shop_domain)
    if not access_token:
        return False

    headers = {
        "X-Shopify-Access-Token": access_token,
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.put(
            _shopify_url(shop_domain, f"variants/{variant_id}.json"),
            headers=headers,
            json={"variant": {"id": variant_id, "price": new_price}},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        log.info(
            "shopify_admin: updated price variant_id=%d price=%s shop=%s",
            variant_id, new_price, shop_domain,
        )
        return True

    except httpx.HTTPStatusError as exc:
        log.error(
            "shopify_admin: HTTP %d updating price variant_id=%d shop=%s",
            exc.response.status_code, variant_id, shop_domain,
        )
    except Exception as exc:
        log.error(
            "shopify_admin: error updating price variant_id=%d shop=%s: %s",
            variant_id, shop_domain, exc,
        )
    return False


def get_shop_products(
    db: Session,
    shop_domain: str,
    limit: int = 10,
) -> list[dict]:
    """
    Fetch recent products from the store.

    Returns a list of {"id", "title", "handle", "variants"} dicts.
    Returns [] on failure.
    """
    access_token = _get_access_token(db, shop_domain)
    if not access_token:
        return []

    headers = {"X-Shopify-Access-Token": access_token}

    try:
        resp = httpx.get(
            _shopify_url(shop_domain, "products.json"),
            headers=headers,
            params={"limit": limit, "fields": "id,title,handle,variants"},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("products", [])

    except Exception as exc:
        log.error("shopify_admin: error fetching products shop=%s: %s", shop_domain, exc)
    return []


# ---------------------------------------------------------------------------
# Install-time helpers — async, called from OAuth callback
# ---------------------------------------------------------------------------

_WEBHOOK_API_FORMAT = "json"


async def _ensure_webhook(
    shop: str,
    token: str,
    topic: str,
    target_address: str,
) -> tuple[Optional[str], bool]:
    """
    Idempotently register a single webhook for a shop.

    Returns (webhook_id, was_created).  webhook_id is None on failure.
    """
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    list_url = _shopify_url(shop, "webhooks.json")

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.get(
                list_url, headers=headers,
                params={"topic": topic, "limit": 50},
            )
            resp.raise_for_status()
            existing = resp.json().get("webhooks", [])

            for wh in existing:
                if wh.get("address") == target_address:
                    log.info(
                        "shopify_admin: webhook already exists shop=%s topic=%s id=%s",
                        shop, topic, wh["id"],
                    )
                    return str(wh["id"]), False

                # Wrong URL → delete and recreate
                log.info(
                    "shopify_admin: deleting stale webhook shop=%s topic=%s id=%s",
                    shop, topic, wh["id"],
                )
                await client.delete(
                    _shopify_url(shop, f"webhooks/{wh['id']}.json"),
                    headers=headers,
                )

            payload = {
                "webhook": {
                    "topic": topic,
                    "address": target_address,
                    "format": _WEBHOOK_API_FORMAT,
                }
            }
            create_resp = await client.post(list_url, headers=headers, json=payload)
            create_resp.raise_for_status()
            wh_id = str(create_resp.json()["webhook"]["id"])
            log.info(
                "shopify_admin: registered webhook shop=%s topic=%s id=%s url=%s",
                shop, topic, wh_id, target_address,
            )
            return wh_id, True

    except httpx.HTTPStatusError as exc:
        log.error(
            "shopify_admin: HTTP %d registering webhook shop=%s topic=%s: %s",
            exc.response.status_code, shop, topic,
            exc.response.text[:200],
        )
    except Exception as exc:
        log.error(
            "shopify_admin: exception registering webhook shop=%s topic=%s: %s",
            shop, topic, exc,
        )
    return None, False


async def ensure_orders_webhook(
    shop: str, token: str, app_url: str,
) -> tuple[Optional[str], bool]:
    """
    Register the app/uninstalled webhook (the only lifecycle webhook
    available without Protected Customer Data approval).

    NOTE: Order webhooks (orders/create, orders/updated, orders/paid)
    ALL require Protected Customer Data approval from Shopify.
    Until approved, revenue tracking is handled by the Shopify Custom Pixel
    (spark-pixel.js) which captures checkout_completed client-side.

    This function name is kept for backward compatibility with the OAuth
    callback and setup/repair endpoints.
    """
    return await _ensure_webhook(
        shop=shop,
        token=token,
        topic="app/uninstalled",
        target_address=f"{app_url}/webhooks/shopify/app-uninstalled",
    )


async def ensure_tracker_script_tag(
    shop:        str,
    token:       str,
    tracker_url: str,
) -> tuple[Optional[str], bool]:
    """
    Idempotently inject spark-tracker.js via the Shopify Script Tags API.

    Behaviour:
    - Lists all existing script tags for the shop.
    - If a script tag exists with src matching tracker_url → no-op.
    - If a script tag exists with a different HedgeSpark/WishSpark src URL
      (stale deployment) → deletes it, creates with new URL.
    - If no matching tag → creates one.

    Script Tag event: "onload" — fires after the page has loaded, ensuring
    the tracker does not block storefront rendering.

    NOTE: The Shopify Script Tags API is deprecated for new public apps
    (Shopify 2024 platform changes) in favour of Theme App Extensions.
    This implementation remains correct for:
      - All existing approved app installs
      - Private apps
      - Apps pending migration to Theme App Extensions
    See docstring at module top for migration guidance.

    Parameters
    ----------
    shop        Shop domain
    token       Plaintext Shopify access token
    tracker_url Full URL of spark-tracker.js
                e.g. https://api.hedgesparkhq.com/tracker.js

    Returns
    -------
    (script_tag_id: str | None, was_created: bool)
    """
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    list_url   = _shopify_url(shop, "script_tags.json")
    create_url = _shopify_url(shop, "script_tags.json")

    # Fingerprint for detecting stale tags — any src containing our API hostname
    # This catches old tracker URLs from a previous APP_URL value
    from urllib.parse import urlparse
    our_host = urlparse(tracker_url).netloc

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:

            # List existing script tags
            resp = await client.get(
                list_url,
                headers=headers,
                params={"limit": 250, "fields": "id,src,event"},
            )
            resp.raise_for_status()
            existing = resp.json().get("script_tags", [])

            for st in existing:
                src = st.get("src", "")
                if src == tracker_url:
                    # Exact match — already installed
                    log.info(
                        "shopify_admin: tracker script tag already exists "
                        "shop=%s id=%s", shop, st["id"],
                    )
                    return str(st["id"]), False

                # Stale tag from this app (same host, different URL) → clean up
                if our_host and our_host in src:
                    log.info(
                        "shopify_admin: deleting stale tracker script tag "
                        "shop=%s id=%s old_src=%s",
                        shop, st["id"], src,
                    )
                    await client.delete(
                        _shopify_url(shop, f"script_tags/{st['id']}.json"),
                        headers=headers,
                    )

            # Create the script tag
            payload = {
                "script_tag": {
                    "event": "onload",
                    "src":   tracker_url,
                }
            }
            create_resp = await client.post(create_url, headers=headers, json=payload)
            create_resp.raise_for_status()
            st_id = str(create_resp.json()["script_tag"]["id"])
            log.info(
                "shopify_admin: installed tracker script tag shop=%s id=%s src=%s",
                shop, st_id, tracker_url,
            )
            return st_id, True

    except httpx.HTTPStatusError as exc:
        log.error(
            "shopify_admin: HTTP %d installing script tag shop=%s: %s",
            exc.response.status_code, shop, exc.response.text[:200],
        )
    except Exception as exc:
        log.error(
            "shopify_admin: exception installing script tag shop=%s: %s",
            shop, exc,
        )
    return None, False
