"""
Shopify OAuth helpers for HedgeSpark.

All functions are pure (no DB, no FastAPI) so they are easy to unit-test.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
SHOPIFY_API_KEY = os.getenv("SHOPIFY_API_KEY", "")
SHOPIFY_API_SECRET = os.getenv("SHOPIFY_API_SECRET", "")
APP_URL = os.getenv("APP_URL", "").rstrip("/")

# Scopes required by HedgeSpark
SHOPIFY_SCOPES = "read_products,read_orders,write_script_tags"

# Callback URL that Shopify will redirect to after authorization
CALLBACK_URL = f"{APP_URL}/auth/callback"

# Regex for a valid myshopify.com shop domain
_SHOP_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*\.myshopify\.com$")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def is_valid_shop_domain(shop: str) -> bool:
    """Return True if shop looks like a real myshopify.com domain."""
    return bool(_SHOP_RE.match(shop))


# ---------------------------------------------------------------------------
# Install URL
# ---------------------------------------------------------------------------

def build_install_url(shop: str) -> str:
    """Build the Shopify OAuth authorization URL for a given shop."""
    return (
        f"https://{shop}/admin/oauth/authorize"
        f"?client_id={SHOPIFY_API_KEY}"
        f"&scope={SHOPIFY_SCOPES}"
        f"&redirect_uri={CALLBACK_URL}"
    )


# ---------------------------------------------------------------------------
# HMAC verification
# ---------------------------------------------------------------------------

def verify_hmac(params: dict[str, str], provided_hmac: str) -> bool:
    """
    Verify the HMAC signature Shopify attaches to OAuth callback requests.

    Algorithm:
      1. Remove 'hmac' from the parameter map.
      2. Sort remaining keys alphabetically.
      3. Join as 'key=value&key=value'.
      4. Compute HMAC-SHA256 using SHOPIFY_API_SECRET.
      5. Compare hex digest to provided_hmac using a timing-safe comparison.
    """
    filtered = {k: v for k, v in params.items() if k != "hmac"}
    message = "&".join(f"{k}={v}" for k, v in sorted(filtered.items()))
    digest = hmac.new(
        SHOPIFY_API_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(digest, provided_hmac)


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------

def exchange_code_for_token(shop: str, code: str) -> str:
    """
    POST to Shopify's token endpoint and return the permanent access token.

    Raises httpx.HTTPStatusError if Shopify returns a non-2xx response.
    Raises KeyError if the response body is missing 'access_token'.
    """
    url = f"https://{shop}/admin/oauth/access_token"
    payload = {
        "client_id": SHOPIFY_API_KEY,
        "client_secret": SHOPIFY_API_SECRET,
        "code": code,
    }
    response = httpx.post(url, json=payload, timeout=10.0)
    response.raise_for_status()
    return response.json()["access_token"]
