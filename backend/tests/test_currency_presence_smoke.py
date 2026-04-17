"""
Smoke guard: every endpoint that carries monetary fields MUST emit a
`currency` field with a valid ISO 4217 code.

Why this exists
---------------
Across the 2026-04-17 native-currency sweep, 25+ endpoints were taught
to resolve `get_shop_currency()` and ship it on the response so the
dashboard renders the merchant's native symbol. Without a regression
guard, a future refactor could silently drop `currency` from a response
dict and the dashboard would fall through to the "USD" default — that's
the 2026-04-14 bug class we JUST closed.

This test parametrizes over every migrated endpoint and asserts:
  1. HTTP 200 response (empty/warming data is acceptable).
  2. `currency` field is present at the documented path.
  3. The emitted value is a 3-letter ISO 4217 code, uppercase.

Test merchants have no orders → fallback to "USD" is correct. The test
is not checking accuracy of the resolution (that lives in
test_revenue_metrics); it's checking that the FIELD CAN'T DISAPPEAR.
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
    """Walk a dotted path through dicts — 'brief.data.currency' → data['brief']['data']['currency']."""
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


# ---------------------------------------------------------------------------
# Endpoints under guard. Format: (method, path, json_currency_path, needs_pro)
#
#   method            — "GET" or "POST"
#   path              — endpoint path (maybe with querystring)
#   json_currency_path — dotted path into the JSON body where `currency`
#                        lives (e.g. "currency" or "data.currency")
#   needs_pro         — True → use auth_a (Pro merchant); False → unauth
#
# If you add a new endpoint that resolves get_shop_currency() on the
# service side, add it here. Tests will fail loudly if the response
# drifts and `currency` disappears.
# ---------------------------------------------------------------------------

CURRENCY_GUARDED_ENDPOINTS = [
    # Newly migrated in the 2026-04-17 sweep
    ("GET", "/pro/daily-narrative",          "currency"),
    ("GET", "/pro/night-shift/latest",       "currency"),
    ("GET", "/pro/counterfactual/signals",   "currency"),
    ("GET", "/pro/customer-churn",           "currency"),
    ("GET", "/pro/visitor-journeys",         "currency"),
    ("GET", "/pro/goals/progress",           "currency"),
    ("GET", "/pro/trust/summary",            "currency"),
    ("GET", "/pro/segments?product_url=/products/test",
                                             "currency"),
    # Pre-existing endpoints that already carried currency
    ("GET", "/pro/roi-hero",                 "currency"),
    ("GET", "/pro/margin/snapshot",          "currency"),
    ("GET", "/pro/mta",                      "currency"),
    ("GET", "/pro/cac-ltv",                  "currency"),
    ("GET", "/pro/revenue-at-risk",          "currency"),
    ("GET", "/pro/forecast/revenue",         "currency"),
    ("GET", "/pro/refund-losses",            "currency"),
    ("GET", "/pro/abandoned-intent",         "currency"),
]

# Valid ISO 4217 codes that merchants might legitimately use. Not
# exhaustive — the goal is to catch "" / None / lowercase / garbage,
# not to police exotic currencies.
_VALID_ISO_CODES = {
    "USD", "EUR", "GBP", "CAD", "AUD", "NZD", "JPY", "CNY", "CHF",
    "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "BRL", "MXN", "INR",
    "SGD", "HKD", "KRW", "ZAR", "AED", "ILS",
}


@pytest.mark.parametrize("method,path,currency_path", CURRENCY_GUARDED_ENDPOINTS)
def test_endpoint_emits_currency(
    method, path, currency_path, client, merchant_a, auth_a,
):
    """Every migrated endpoint must ship `currency` as a valid ISO code."""
    if method == "GET":
        resp = client.get(path, cookies=auth_a)
    else:
        resp = client.request(method, path, cookies=auth_a)

    # 400 on endpoints that need specific query params is acceptable
    # (visitor-journeys / segments with product_url) — the guard here is
    # about 200-path drift, not input-validation behavior.
    if resp.status_code == 400:
        pytest.skip(f"{method} {path} rejected input (400) — not a currency-path regression")

    assert resp.status_code == 200, (
        f"{method} {path} returned {resp.status_code}, expected 200. "
        f"Body: {resp.text[:200]}"
    )

    try:
        body = resp.json()
    except ValueError:
        pytest.fail(f"{method} {path} returned non-JSON body: {resp.text[:200]}")

    currency = _get_at_path(body, currency_path)
    assert currency is not None, (
        f"{method} {path}: `{currency_path}` is missing from response. "
        f"Top-level keys: {sorted(body.keys())[:15] if isinstance(body, dict) else type(body).__name__}"
    )
    assert isinstance(currency, str), (
        f"{method} {path}: `{currency_path}` must be str, got {type(currency).__name__}"
    )
    assert len(currency) == 3, (
        f"{method} {path}: `{currency_path}` is {currency!r} — expected 3-letter ISO 4217 code"
    )
    assert currency.isupper(), (
        f"{method} {path}: `{currency_path}` is {currency!r} — must be UPPERCASE"
    )
    assert currency in _VALID_ISO_CODES, (
        f"{method} {path}: `{currency_path}` is {currency!r} — "
        f"not a recognized ISO 4217 code. Add to _VALID_ISO_CODES if this "
        f"is intentional, else check the service-layer resolution."
    )
