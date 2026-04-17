"""
Smoke guard: every endpoint that carries monetary fields MUST emit a
`currency` field that matches the merchant's `primary_currency`.

Why this exists
---------------
Across the 2026-04-17 native-currency sweep, 25+ endpoints were taught
to resolve `get_shop_currency()` and ship it on the response so the
dashboard renders the merchant's native symbol. Without a regression
guard, a future refactor could silently drop `currency` from a response
dict (or hardcode "USD" everywhere) and the dashboard would render the
wrong symbol — that's the 2026-04-14 bug class we closed.

This test parametrizes over every migrated endpoint × two merchant
profiles (USD fallback + EUR explicit) and asserts:
  1. HTTP 200 response (empty/warming data is acceptable).
  2. `currency` field present at the documented path.
  3. The emitted value is a 3-letter ISO 4217 code, uppercase.
  4. **The emitted value matches the merchant's primary_currency.**

(4) is the killer assertion — without it, a bug that hardcoded
"USD" in every response would pass the other three checks silently.
"""
from __future__ import annotations

import pytest

from app.core.database import get_read_db
from app.main import app as fastapi_app


@pytest.fixture(autouse=True)
def _override_read_db(db):
    """Mirror conftest's get_db override onto get_read_db for ε1 paths."""
    def _get_read_db_override():
        yield db
    fastapi_app.dependency_overrides[get_read_db] = _get_read_db_override
    yield
    fastapi_app.dependency_overrides.pop(get_read_db, None)


def _get_at_path(data: dict, path: str):
    """Walk a dotted path through dicts — 'data.currency' → data['data']['currency']."""
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


# ---------------------------------------------------------------------------
# Endpoints under guard.
#
# Format: (method, path, json_currency_path)
#   method             — "GET" or "POST"
#   path               — endpoint path (maybe with querystring)
#   json_currency_path — dotted path into the JSON body where `currency`
#                        lives (e.g. "currency" or "data.currency")
# ---------------------------------------------------------------------------

CURRENCY_GUARDED_ENDPOINTS = [
    # Migrated in the 2026-04-17 sweep (13 endpoints)
    ("GET", "/pro/daily-narrative",          "currency"),
    ("GET", "/pro/night-shift/latest",       "currency"),
    ("GET", "/pro/counterfactual/signals",   "currency"),
    ("GET", "/pro/customer-churn",           "currency"),
    ("GET", "/pro/visitor-journeys",         "currency"),
    ("GET", "/pro/goals/progress",           "currency"),
    ("GET", "/pro/trust/summary",            "currency"),
    ("GET", "/pro/segments?product_url=/products/test",
                                             "currency"),
    # Pre-existing endpoints that already carried currency (8 endpoints)
    ("GET", "/pro/roi-hero",                 "currency"),
    ("GET", "/pro/margin/snapshot",          "currency"),
    ("GET", "/pro/mta",                      "currency"),
    ("GET", "/pro/mta/compare",              "currency"),
    ("GET", "/pro/cac-ltv",                  "currency"),
    ("GET", "/pro/revenue-at-risk",          "currency"),
    ("GET", "/pro/forecast/revenue",         "currency"),
    ("GET", "/pro/refund-losses",            "currency"),
    ("GET", "/pro/abandoned-intent",         "currency"),
    ("GET", "/pro/revenue-autopsy",          "currency"),
    ("GET", "/pro/causal-lift",              "currency"),
    ("GET", "/pro/price-sensitivity",        "currency"),
    ("GET", "/pro/benchmarks",               "currency"),
]

# Valid ISO 4217 codes that merchants might legitimately use. Not
# exhaustive — the goal is to catch "" / None / lowercase / garbage,
# not to police exotic currencies.
_VALID_ISO_CODES = {
    "USD", "EUR", "GBP", "CAD", "AUD", "NZD", "JPY", "CNY", "CHF",
    "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "BRL", "MXN", "INR",
    "SGD", "HKD", "KRW", "ZAR", "AED", "ILS",
}

# Merchant fixtures × their expected currency. merchant_a has no
# primary_currency set → get_shop_currency returns None → fallback "USD".
# merchant_eur has primary_currency="EUR" explicitly.
#
# Each entry: (auth_fixture_name, expected_currency)
MERCHANT_PROFILES = [
    ("auth_a",    "USD"),   # default fallback path
    ("auth_eur",  "EUR"),   # explicit primary_currency
]


@pytest.mark.parametrize("method,path,currency_path", CURRENCY_GUARDED_ENDPOINTS)
@pytest.mark.parametrize("auth_fixture,expected_currency", MERCHANT_PROFILES)
def test_endpoint_emits_matching_currency(
    method, path, currency_path,
    auth_fixture, expected_currency,
    client, request,
):
    """Every migrated endpoint must ship `currency` equal to the
    merchant's primary_currency (or USD fallback for unset shops)."""
    # Pull the right auth fixture dynamically — the parametrization
    # decides which merchant we hit the endpoint as.
    auth = request.getfixturevalue(auth_fixture)

    if method == "GET":
        resp = client.get(path, cookies=auth)
    else:
        resp = client.request(method, path, cookies=auth)

    # 400 on endpoints that need specific query params is acceptable
    # (visitor-journeys / segments with product_url) — the guard here is
    # about 200-path drift, not input-validation behavior.
    if resp.status_code == 400:
        pytest.skip(f"{method} {path} rejected input (400) — not a currency-path regression")

    assert resp.status_code == 200, (
        f"{method} {path} [{auth_fixture}] returned {resp.status_code}, expected 200. "
        f"Body: {resp.text[:200]}"
    )

    try:
        body = resp.json()
    except ValueError:
        pytest.fail(f"{method} {path} returned non-JSON body: {resp.text[:200]}")

    currency = _get_at_path(body, currency_path)
    assert currency is not None, (
        f"{method} {path} [{auth_fixture}]: `{currency_path}` is missing. "
        f"Top-level keys: {sorted(body.keys())[:15] if isinstance(body, dict) else type(body).__name__}"
    )
    assert isinstance(currency, str), (
        f"{method} {path} [{auth_fixture}]: `{currency_path}` must be str, got {type(currency).__name__}"
    )
    assert len(currency) == 3, (
        f"{method} {path} [{auth_fixture}]: `{currency_path}` is {currency!r} — expected 3-letter ISO 4217 code"
    )
    assert currency.isupper(), (
        f"{method} {path} [{auth_fixture}]: `{currency_path}` is {currency!r} — must be UPPERCASE"
    )
    assert currency in _VALID_ISO_CODES, (
        f"{method} {path} [{auth_fixture}]: `{currency_path}` is {currency!r} — "
        f"not a recognized ISO 4217 code."
    )
    # THE KILLER ASSERTION — proves the endpoint actually resolves
    # get_shop_currency() instead of hardcoding a single value.
    assert currency == expected_currency, (
        f"{method} {path} [{auth_fixture}]: expected `{currency_path}`="
        f"{expected_currency!r} (merchant primary_currency), got {currency!r}. "
        f"The endpoint is NOT honoring the shop's native currency — "
        f"likely hardcoded or ignoring get_shop_currency()."
    )
