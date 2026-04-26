"""Regression test for the 2026-04-26 founder-directive Lite unlock sprint.

The 4 base analytics that competitors (Lifetimely Free, Shopify Free, Peel,
Better Reports, OrderMetrics, Profit Bee) ship at $0–$70 are now mirrored
on /analytics/* with require_merchant_session. The matching /pro/* routes
keep require_pro_session.

If a future refactor silently re-gates one of these to Pro-only, this
test breaks before merchants discover the regression. Tied to founder
directive 2026-04-26 + memory `project_lite_features_audit_2026_04_25.md`.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Lite-tier accessibility — Lite/Starter merchant must get 200 on each
# unlocked endpoint, NOT 401 or 403. Empty payload shape is fine (cold-start
# is the expected state for a freshly-installed merchant).
# ---------------------------------------------------------------------------


def test_lite_can_read_weekly_cohort_matrix(client, auth_b):
    r = client.get("/analytics/cohorts/weekly?weeks=8", cookies=auth_b)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "cohorts" in body
    assert "avg_week_1_retention" in body
    assert "total_customers" in body


def test_lite_can_read_predicted_ltv(client, auth_b):
    r = client.get("/analytics/cohorts/ltv/customers?limit=10", cookies=auth_b)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["shop_domain"] == "test-shop-b.myshopify.com"
    assert "customers" in body
    assert "count" in body


def test_lite_can_read_refund_losses(client, auth_b):
    r = client.get("/analytics/refund-losses", cookies=auth_b)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["shop_domain"] == "test-shop-b.myshopify.com"
    assert "products" in body
    assert "total_loss_eur_per_month" in body
    assert "currency" in body


def test_lite_can_read_audience_segments(client, auth_b):
    r = client.get(
        "/analytics/segments?product_url=/products/test-handle&hours=72",
        cookies=auth_b,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["shop_domain"] == "test-shop-b.myshopify.com"
    assert body["product_url"] == "/products/test-handle"
    for tier_key in ("hot", "warm", "cold"):
        assert tier_key in body
        assert "visitor_count" in body[tier_key]


def test_lite_can_read_segment_compare(client, auth_b):
    r = client.get(
        "/analytics/segments/compare"
        "?product_a=/products/a&product_b=/products/b&hours=72",
        cookies=auth_b,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["shop_domain"] == "test-shop-b.myshopify.com"
    assert "product_a" in body
    assert "product_b" in body
    assert "delta" in body


# ---------------------------------------------------------------------------
# Pro endpoints stay Pro — Lite merchant gets 403 on /pro/* equivalents.
# Confirms the unlock split the routes rather than collapsing them.
# ---------------------------------------------------------------------------


def test_pro_endpoints_still_reject_lite(client, auth_b):
    pro_paths = (
        "/pro/cohorts?weeks=8",
        "/pro/cohorts/ltv/customers?limit=10",
        "/pro/refund-losses",
        "/pro/segments?product_url=/products/test-handle&hours=72",
        "/pro/segments/compare?product_a=/products/a&product_b=/products/b&hours=72",
    )
    for path in pro_paths:
        r = client.get(path, cookies=auth_b)
        assert r.status_code == 403, f"{path}: expected 403 got {r.status_code}"


# ---------------------------------------------------------------------------
# Pro tier still works on both Pro AND Lite endpoints. require_merchant_session
# is the floor — Pro merchants pass the auth check on /analytics/* too.
# ---------------------------------------------------------------------------


def test_pro_tier_accesses_both_pro_and_lite_routes(client, auth_a):
    routes = (
        ("/analytics/cohorts/weekly?weeks=8", 200),
        ("/pro/cohorts?weeks=8", 200),
        ("/analytics/refund-losses", 200),
        ("/pro/refund-losses", 200),
        ("/analytics/segments?product_url=/products/test&hours=72", 200),
        ("/pro/segments?product_url=/products/test&hours=72", 200),
    )
    for path, expected in routes:
        r = client.get(path, cookies=auth_a)
        assert r.status_code == expected, f"{path}: expected {expected} got {r.status_code}"


# ---------------------------------------------------------------------------
# Tenant isolation — the shop_domain in every response comes from the
# session cookie, not from any query param. Forging ?shop= must not leak
# data from another tenant.
# ---------------------------------------------------------------------------


def test_lite_unlocks_are_tenant_isolated(client, auth_b, merchant_a):
    """Lite merchant b passing ?shop=<merchant_a> must NOT receive a's data."""
    routes = (
        "/analytics/cohorts/weekly",
        "/analytics/cohorts/ltv/customers",
        "/analytics/refund-losses",
    )
    for path in routes:
        sep = "&" if "?" in path else "?"
        r = client.get(f"{path}{sep}shop=test-shop-a.myshopify.com", cookies=auth_b)
        assert r.status_code == 200, r.text
        body = r.json()
        # shop_domain field is omitted from the cohort response shape;
        # only assert when the response carries it.
        if "shop_domain" in body:
            assert body["shop_domain"] == "test-shop-b.myshopify.com", (
                f"{path}: query-param shop= leaked merchant_a data"
            )
