"""
url_utils.py — URL normalization helpers for Hedge Spark.

Canonical product URL format: /products/{handle}
  - path-only (no scheme, host, query, or fragment)
  - single path segment after /products/
  - lowercase handle preserved as-is (Shopify handles are already lowercase)

normalize_product_url() is safe to call at every write boundary:
  - returns None for non-product input (home, collection, checkout, etc.)
  - idempotent: already-canonical paths pass through unchanged
"""
from __future__ import annotations

import re

# Matches /products/{handle} anywhere in a string.
# Capture group 1 = the canonical path segment.
_PRODUCT_RE = re.compile(r"(/products/[^/?#\s]+)")


def normalize_product_url(raw: str | None) -> str | None:
    """
    Extract and return the canonical /products/{handle} path from raw.

    Handles:
      - Full URLs:  https://shop.myshopify.com/products/my-widget?variant=123
                    → /products/my-widget
      - Path+query: /products/my-widget?variant=123  → /products/my-widget
      - Already canonical: /products/my-widget       → /products/my-widget
      - Non-product pages: /, /collections/all, /cart → None

    Returns None for empty, None, or non-product input.
    """
    if not raw:
        return None
    m = _PRODUCT_RE.search(raw)
    if not m:
        return None
    return m.group(1)
