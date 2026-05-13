"""
pnl_engine.py — Profit & Loss computation for the Profit Intelligence cassettone.

The killer feature that closes the gap vs Lifetimely / Triple Whale on their
core territory: "I don't just tell you your revenue, I tell you what you keep."

Model
-----
Gross Revenue      SUM(shop_orders.total_price) — from real Shopify orders
− COGS             per-product from product_costs when available, else
                   gross_revenue × shop_cost_defaults.default_cogs_pct,
                   else gross_revenue × _DEFAULT_COGS_PCT (40%)
− Payment fees     gross_revenue × payment_pct + order_count × payment_flat
                   (defaults: 2.9% + 0.30/order, shop can override)
− Shipping cost    order_count × default_shipping_per_order
                   (default: 5.00/order, shop can override)
− Ad spend         From shop_cost_defaults.ad_spend_manual_monthly scaled to
                   the window — bridge until Phase 3 wires Meta/Google OAuth
= Gross Profit     everything above accounted for
Net Profit         = Gross Profit − Ad Spend

Precision upgrade ladder
------------------------
"rough"    — zero config: using all module defaults
"refined"  — shop_cost_defaults row exists with at least one non-NULL value,
             OR at least one product_costs row exists
"exact"    — shop_cost_defaults has ad_spend_manual_monthly set AND at least
             80% of last-30d revenue comes from products with a real COGS row.
             (Phase 3 will tighten this to require Meta+Google OAuth live.)

Data sources in priority order
------------------------------
1. Per-product real COGS (`product_costs.cogs_per_unit`) — joined against
   shop_orders.line_items by product_key.
2. Shop-level defaults (`shop_cost_defaults.default_cogs_pct`) as fallback
   percentage for line items without a matching product_costs row.
3. Module-level constants below as last-resort fallback for shops that have
   never entered any cost config.

Every cost component in the response carries an `estimated` flag + `source`
label so the UI can honestly render "estimated" vs "real" badges per line.

Historical note
---------------
v1 (2026-04-11 night): pure module defaults, no DB reads. Shipped the killer
cassettone in an afternoon. Used the 40% placeholder across the board.
v2 (this file): adds real DB reads against product_costs + shop_cost_defaults.
The Settings UI in the dashboard lets merchants override every default and
the precision field reflects how much real data is in play.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.revenue_metrics import get_shop_currency

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level defaults — used only when the shop has entered NO cost config.
# Every one of these can be overridden per-shop via shop_cost_defaults, and
# COGS can be overridden per-product via product_costs.
# ---------------------------------------------------------------------------

_DEFAULT_COGS_PCT:           float = 0.40
_DEFAULT_PAYMENT_PCT:        float = 0.029
_DEFAULT_PAYMENT_FLAT:       float = 0.30
_DEFAULT_SHIPPING_PER_ORDER: float = 5.00

# "exact" precision gate — real COGS must cover this fraction of revenue.
_EXACT_COGS_COVERAGE_THRESHOLD = 0.80


# ---------------------------------------------------------------------------
# P&L pipeline helpers — each stage is a pure function consuming the prior
# stage's output. `get_pnl_report` is the composer. Refactor 2026-05-13
# (A3 close): 250-LOC god function → composer + 8 pure helpers + 1 DB fetch.
# Identical contract preserved (every response key + value identical to v2).
# ---------------------------------------------------------------------------


def _resolve_rates(cost_cfg: dict) -> dict:
    """Resolve per-shop cost-config row → typed rates + is_custom flags.
    A None config value falls back to the module-level default; the
    is_custom companion records whether the merchant has overridden."""
    def _or(val, fallback: float) -> float:
        return fallback if val is None else float(val)
    return {
        "cogs_pct_default": _or(cost_cfg.get("default_cogs_pct"), _DEFAULT_COGS_PCT),
        "payment_pct":      _or(cost_cfg.get("payment_pct"), _DEFAULT_PAYMENT_PCT),
        "payment_flat":     _or(cost_cfg.get("payment_flat"), _DEFAULT_PAYMENT_FLAT),
        "shipping_per_ord": _or(cost_cfg.get("default_shipping_per_order"), _DEFAULT_SHIPPING_PER_ORDER),
        "ad_spend_monthly": _or(cost_cfg.get("ad_spend_manual_monthly"), 0.0),
        "cogs_pct_is_custom":     cost_cfg.get("default_cogs_pct") is not None,
        "payment_pct_is_custom":  cost_cfg.get("payment_pct") is not None,
        "payment_flat_is_custom": cost_cfg.get("payment_flat") is not None,
        "shipping_is_custom":     cost_cfg.get("default_shipping_per_order") is not None,
        "ad_spend_is_manual":     cost_cfg.get("ad_spend_manual_monthly") is not None,
    }


_REVENUE_SQL = text("""
    SELECT
        COUNT(*)::int                AS order_count,
        COALESCE(SUM(total_price), 0) AS gross_revenue
    FROM shop_orders
    WHERE shop_domain = :shop
      AND created_at >= NOW() - make_interval(days => :days)
      AND (:currency IS NULL OR currency = :currency)
""")


def _fetch_revenue_summary(
    db: Session, shop_domain: str, window_days: int, currency: str | None,
) -> dict:
    """Pull gross revenue + order count over the window. Returns
    {"order_count": 0, "gross_revenue": 0.0} on DB error (logged)."""
    try:
        row = db.execute(
            _REVENUE_SQL,
            {"shop": shop_domain, "days": window_days, "currency": currency},
        ).fetchone()
    except Exception as exc:
        log.error("pnl_engine: revenue query failed shop=%s: %s", shop_domain, exc)
        return {"order_count": 0, "gross_revenue": 0.0, "error": True}
    return {
        "order_count":   int(row[0] or 0) if row else 0,
        "gross_revenue": round(float(row[1] or 0), 2) if row else 0.0,
        "error":         False,
    }


def _compute_cogs_summary(
    real_cogs_amount: float, covered_revenue: float,
    gross_revenue: float, cogs_pct_default: float,
) -> dict:
    """Blend real per-product COGS with percentage fallback on uncovered
    revenue. Returns the estimate, the fallback amount, and the coverage
    fraction (used to drive precision)."""
    uncovered_revenue = max(0.0, gross_revenue - covered_revenue)
    cogs_fallback = round(uncovered_revenue * cogs_pct_default, 2)
    cogs_estimate = round(real_cogs_amount + cogs_fallback, 2)
    cogs_coverage = (
        round(covered_revenue / gross_revenue, 4) if gross_revenue > 0 else 0.0
    )
    return {
        "cogs_estimate":     cogs_estimate,
        "cogs_fallback":     cogs_fallback,
        "real_cogs_amount":  real_cogs_amount,
        "cogs_coverage":     cogs_coverage,
    }


def _compute_cost_stack(
    *, rates: dict, gross_revenue: float, order_count: int, window_days: int,
) -> dict:
    """Deterministic non-COGS cost components: payment / shipping / ad spend.
    Ad spend is the manual monthly figure linearly scaled to the window,
    zero when the merchant has not entered an estimate yet."""
    payment_fees = round(
        gross_revenue * rates["payment_pct"] + order_count * rates["payment_flat"], 2
    )
    shipping_cost = round(order_count * rates["shipping_per_ord"], 2)
    ad_spend = (
        round(rates["ad_spend_monthly"] * (window_days / 30.0), 2)
        if rates["ad_spend_is_manual"] else 0.0
    )
    return {
        "payment_fees":  payment_fees,
        "shipping_cost": shipping_cost,
        "ad_spend":      ad_spend,
    }


def _compute_profit_lines(
    *, gross_revenue: float, cogs_estimate: float, cost_stack: dict,
) -> dict:
    """Gross/net profit + margins + total costs from the prior stages.
    All ratios are zero-safe when gross_revenue is 0."""
    payment_fees = cost_stack["payment_fees"]
    shipping_cost = cost_stack["shipping_cost"]
    ad_spend = cost_stack["ad_spend"]
    total_costs = round(cogs_estimate + payment_fees + shipping_cost + ad_spend, 2)
    gross_profit = round(gross_revenue - cogs_estimate - payment_fees - shipping_cost, 2)
    net_profit = round(gross_profit - ad_spend, 2)
    gross_margin_pct = (
        round((gross_profit / gross_revenue) * 100, 1) if gross_revenue > 0 else 0.0
    )
    net_margin_pct = (
        round((net_profit / gross_revenue) * 100, 1) if gross_revenue > 0 else 0.0
    )
    return {
        "total_costs":      total_costs,
        "gross_profit":     gross_profit,
        "net_profit":       net_profit,
        "gross_margin_pct": gross_margin_pct,
        "net_margin_pct":   net_margin_pct,
    }


def _derive_precision(
    *, rates: dict, cogs_coverage: float, products_with_real_cogs: int,
) -> str:
    """3-tier precision label: rough → refined → exact.

    exact:   real COGS covers ≥80% of revenue AND ad spend is entered.
    refined: at least one shop_cost_defaults override OR product_costs row.
    rough:   all module defaults (the un-configured baseline).
    """
    has_any_custom = any([
        rates["cogs_pct_is_custom"],
        rates["payment_pct_is_custom"],
        rates["payment_flat_is_custom"],
        rates["shipping_is_custom"],
        rates["ad_spend_is_manual"],
        products_with_real_cogs > 0,
    ])
    if cogs_coverage >= _EXACT_COGS_COVERAGE_THRESHOLD and rates["ad_spend_is_manual"]:
        return "exact"
    return "refined" if has_any_custom else "rough"


def _build_verdict(net_margin_pct: float, currency: str) -> str:
    """Human-readable margin verdict in 4 bands."""
    from app.core.currency import currency_symbol
    symbol = currency_symbol(currency)
    if net_margin_pct >= 20:
        return (
            f"You keep ~{net_margin_pct:.0f}¢ of every {symbol}1 — "
            "healthy margin range for DTC."
        )
    if net_margin_pct >= 10:
        return (
            f"You keep ~{net_margin_pct:.0f}¢ of every {symbol}1 — "
            "tight but viable. Watch your COGS."
        )
    if net_margin_pct > 0:
        return (
            f"Only ~{net_margin_pct:.0f}¢ of every {symbol}1 stays with you "
            "— margin is too thin to scale."
        )
    return (
        "Estimated costs exceed revenue. Enter real COGS to see if this is "
        "a true loss or a default overestimate."
    )


def _build_cogs_meta(
    *, rates: dict, cogs_coverage: float, products_with_real_cogs: int,
) -> dict:
    """4-branch (source, estimated_flag, note) tuple for the cogs line item.
    Drives the UI's per-line 'estimated' vs 'real' badges."""
    cogs_pct_default = rates["cogs_pct_default"]
    cogs_pct_is_custom = rates["cogs_pct_is_custom"]
    if products_with_real_cogs > 0 and cogs_coverage >= 0.999:
        source = "per_product_exact"
        estimated = False
    elif products_with_real_cogs > 0:
        source = "per_product_partial"
        estimated = True
    elif cogs_pct_is_custom:
        source = "shop_default_pct_custom"
        estimated = True
    else:
        source = "default_40pct"
        estimated = True

    if products_with_real_cogs > 0:
        note = (
            f"Real per-product COGS on {products_with_real_cogs} products covers "
            f"{int(cogs_coverage * 100)}% of revenue — remainder estimated at "
            f"{int(cogs_pct_default * 100)}%."
        )
    elif cogs_pct_is_custom:
        note = f"Using custom shop default {int(cogs_pct_default * 100)}% COGS."
    else:
        note = "Using module default 40% COGS — enter real cost data for precision."
    return {"source": source, "estimated": estimated, "note": note}


def _assemble_costs_block(
    *, rates: dict, cost_stack: dict, cogs_summary: dict,
    cogs_meta: dict, window_days: int, currency: str,
) -> dict:
    """Compose the per-line cost block: 4 sources × (amount, rate, source, note)."""
    from app.core.currency import currency_symbol
    symbol = currency_symbol(currency)

    payment_pct = rates["payment_pct"]
    payment_flat = rates["payment_flat"]
    payment_is_custom = (
        rates["payment_pct_is_custom"] or rates["payment_flat_is_custom"]
    )
    shipping_per_ord = rates["shipping_per_ord"]
    shipping_is_custom = rates["shipping_is_custom"]
    ad_spend_monthly = rates["ad_spend_monthly"]
    ad_spend_is_manual = rates["ad_spend_is_manual"]

    return {
        "cogs": {
            "amount":    cogs_summary["cogs_estimate"],
            "rate":      rates["cogs_pct_default"],
            "estimated": cogs_meta["estimated"],
            "source":    cogs_meta["source"],
            "note":      cogs_meta["note"],
        },
        "payment_fees": {
            "amount":    cost_stack["payment_fees"],
            "rate":      payment_pct,
            "flat":      payment_flat,
            "estimated": not payment_is_custom,
            "source":    "shop_custom" if payment_is_custom else "shopify_payments_standard",
            "note": (
                f"Custom payment rates: {payment_pct*100:.2f}% + {payment_flat:.2f}/order."
                if payment_is_custom
                else f"Shopify Payments standard ({payment_pct*100:.1f}% + {payment_flat:.2f}/order)."
            ),
        },
        "shipping": {
            "amount":    cost_stack["shipping_cost"],
            "rate":      shipping_per_ord,
            "estimated": not shipping_is_custom,
            "source":    "shop_custom" if shipping_is_custom else "default_5_per_order",
            "note": (
                f"Custom shipping estimate: {shipping_per_ord:.2f} per order."
                if shipping_is_custom
                else f"Default {shipping_per_ord:.2f}/order shipping estimate — configure your real rate."
            ),
        },
        "ad_spend": {
            "amount":    cost_stack["ad_spend"],
            "estimated": True,
            "source":    "manual_monthly_entry" if ad_spend_is_manual else "not_tracked_yet",
            "note": (
                f"Manual monthly ad spend ({symbol}{ad_spend_monthly:.0f}/mo) scaled to "
                f"{window_days}-day window. Connect Meta Ads + Google Ads for real "
                "campaign-level ROAS."
                if ad_spend_is_manual
                else "Ad spend not tracked yet — enter a rough monthly figure or "
                     "connect Meta + Google Ads."
            ),
        },
    }


def get_pnl_report(
    db: Session,
    shop_domain: str,
    window_days: int = 30,
) -> dict:
    """
    Compute the full P&L waterfall for a shop over the last N days.

    Refactored 2026-05-13 (A3 close): 250-LOC god function → 50-LOC composer
    + 8 pure helpers + 1 DB fetch. Identical response contract preserved.

    v2 behavior: reads shop_cost_defaults + product_costs from the DB and
    uses them in priority order before falling back to module constants.
    Every cost component is tagged with an "estimated" flag so the UI can
    label default-vs-real precision per line.

    Self-healing side effect: on the first /pro/pnl call per shop per hour
    kicks off a Shopify COGS sync inline so Profit Intelligence auto-
    upgrades from "rough" to "refined" without touching the Settings UI.
    """
    window_days = max(1, min(window_days, 90))
    _maybe_auto_sync_shopify_costs(db, shop_domain)

    rates = _resolve_rates(_load_shop_cost_defaults(db, shop_domain))
    try:
        currency = get_shop_currency(db, shop_domain) or "USD"
    except Exception:
        currency = "USD"

    revenue = _fetch_revenue_summary(db, shop_domain, window_days, currency)
    if revenue.get("error") or revenue["order_count"] == 0:
        return _empty_report(window_days, currency)

    gross_revenue = revenue["gross_revenue"]
    order_count = revenue["order_count"]

    real_cogs, covered_rev, products_with_real_cogs = _compute_real_cogs(
        db, shop_domain, window_days,
    )
    cogs_summary = _compute_cogs_summary(
        real_cogs, covered_rev, gross_revenue, rates["cogs_pct_default"],
    )
    cost_stack = _compute_cost_stack(
        rates=rates, gross_revenue=gross_revenue,
        order_count=order_count, window_days=window_days,
    )
    profit = _compute_profit_lines(
        gross_revenue=gross_revenue,
        cogs_estimate=cogs_summary["cogs_estimate"],
        cost_stack=cost_stack,
    )
    precision = _derive_precision(
        rates=rates,
        cogs_coverage=cogs_summary["cogs_coverage"],
        products_with_real_cogs=products_with_real_cogs,
    )
    cogs_meta = _build_cogs_meta(
        rates=rates,
        cogs_coverage=cogs_summary["cogs_coverage"],
        products_with_real_cogs=products_with_real_cogs,
    )
    costs_block = _assemble_costs_block(
        rates=rates, cost_stack=cost_stack, cogs_summary=cogs_summary,
        cogs_meta=cogs_meta, window_days=window_days, currency=currency,
    )

    log.info(
        "pnl_engine: shop=%s window=%dd orders=%d gross=%.2f cogs=%.2f "
        "(real=%.2f, cov=%.1f%%) fees=%.2f ship=%.2f ads=%.2f net=%.2f "
        "margin=%.1f%% precision=%s",
        shop_domain, window_days, order_count, gross_revenue,
        cogs_summary["cogs_estimate"], real_cogs, cogs_summary["cogs_coverage"] * 100,
        cost_stack["payment_fees"], cost_stack["shipping_cost"], cost_stack["ad_spend"],
        profit["net_profit"], profit["net_margin_pct"], precision,
    )

    return {
        "window_days":         window_days,
        "currency":            currency,
        "precision":           precision,
        "has_data":            True,
        "order_count":         order_count,
        "gross_revenue":       gross_revenue,
        "cogs_coverage_pct":   round(cogs_summary["cogs_coverage"] * 100, 1),
        "products_with_cogs":  products_with_real_cogs,
        "costs":               costs_block,
        "total_costs":         profit["total_costs"],
        "gross_profit":        profit["gross_profit"],
        "net_profit":          profit["net_profit"],
        "gross_margin_pct":    profit["gross_margin_pct"],
        "net_margin_pct":      profit["net_margin_pct"],
        "verdict":             _build_verdict(profit["net_margin_pct"], currency),
        "generated_at":        datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Redis marker key for the self-healing Shopify COGS auto-sync. TTL is 1 hour
# so every shop gets a fresh sync at most once per hour — catches newly-added
# products quickly, never spams the Shopify Admin API.
_COGS_SYNC_MARKER_TTL = 3600  # 1 hour


def _maybe_auto_sync_shopify_costs(db: Session, shop_domain: str) -> None:
    """
    Self-healing hook that pulls real product COGS from Shopify once per
    hour per shop. Fires inline on the first /pro/pnl call after the TTL
    expires, then sets a Redis marker so subsequent calls are no-ops.

    Design decisions:
    - Synchronous (not a background task) so the merchant's very first
      dashboard load already shows "refined" precision. The 300-500ms cost
      is paid once per hour, on a call that already takes ~200ms for the
      SQL aggregates, so the user-visible delay is acceptable.
    - Idempotent by construction — the underlying sync function upserts by
      (shop_domain, product_key) and never overwrites manual entries.
    - Marker is set on BOTH success and "empty" outcomes (merchant has no
      cost data in Shopify yet) to avoid retry storms. Only real errors
      leave the marker unset so the next call tries again.
    - Catches every exception so a broken sync never breaks the P&L report.
    """
    try:
        from app.core.redis_client import cache_get, cache_set
        marker_key = f"hs:cogs_sync_done:{shop_domain}"
        if cache_get(marker_key) is not None:
            return

        from app.services.shopify_cogs_sync import sync_product_costs_from_shopify
        result = sync_product_costs_from_shopify(db, shop_domain)

        # Set marker on all non-error outcomes so we don't retry within the
        # TTL window. "empty" is not an error — it's "merchant has no costs
        # in Shopify yet", which is a stable state we shouldn't rescan every
        # page load.
        status = result.get("status")
        if status in ("ok", "empty"):
            cache_set(marker_key, "1", _COGS_SYNC_MARKER_TTL)
            log.info(
                "pnl_engine: auto-sync complete shop=%s status=%s inserted=%d updated=%d",
                shop_domain, status, result.get("inserted", 0), result.get("updated", 0),
            )
        else:
            log.warning(
                "pnl_engine: auto-sync failed shop=%s status=%s reason=%s — "
                "will retry on next /pro/pnl call",
                shop_domain, status, result.get("reason"),
            )
    except Exception as exc:
        # Best effort — a broken sync must never break the P&L report itself.
        log.warning("pnl_engine: auto-sync hook crashed shop=%s: %s", shop_domain, exc)


def _load_shop_cost_defaults(db: Session, shop_domain: str) -> dict:
    """
    Fetch the shop_cost_defaults row as a plain dict. Returns an empty dict
    when the shop has never configured any cost — callers fall back to
    module-level defaults.
    """
    try:
        row = db.execute(
            text("""
                SELECT default_cogs_pct, default_shipping_per_order,
                       payment_pct, payment_flat, ad_spend_manual_monthly,
                       currency
                FROM shop_cost_defaults
                WHERE shop_domain = :shop
            """),
            {"shop": shop_domain},
        ).fetchone()
    except Exception as exc:
        log.warning("pnl_engine: shop_cost_defaults query failed shop=%s: %s",
                    shop_domain, exc)
        return {}

    if row is None:
        return {}

    def _f(v):
        if v is None:
            return None
        if isinstance(v, Decimal):
            return float(v)
        return float(v)

    return {
        "default_cogs_pct":           _f(row[0]),
        "default_shipping_per_order": _f(row[1]),
        "payment_pct":                _f(row[2]),
        "payment_flat":               _f(row[3]),
        "ad_spend_manual_monthly":    _f(row[4]),
        "currency":                   row[5],
    }


def _compute_real_cogs(
    db: Session,
    shop_domain: str,
    window_days: int,
) -> tuple[float, float, int]:
    """
    Join shop_orders.line_items against product_costs to compute real COGS
    for line items where a matching product_costs row exists (with a
    non-NULL cogs_per_unit).

    Product key matching: first tries line_item product_id, falls back to
    line_item product_url, matching product_costs.product_key.

    Returns:
        real_cogs_amount:       SUM(cogs_per_unit × quantity) over matched line items
        covered_revenue:        SUM(price × quantity)         over matched line items
        products_with_real_cogs: distinct matching products (deduped)
    """
    try:
        row = db.execute(
            text("""
                WITH line_items_expanded AS (
                    SELECT
                        so.shopify_order_id,
                        COALESCE(
                            item->>'product_id',
                            item->>'product_url'
                        )                                 AS product_key,
                        (item->>'price')::numeric         AS unit_price,
                        (item->>'quantity')::int          AS quantity
                    FROM shop_orders so,
                         jsonb_array_elements(CASE WHEN jsonb_typeof(so.line_items) = 'array' THEN so.line_items ELSE '[]'::jsonb END) AS item
                    WHERE so.shop_domain = :shop
                      AND so.created_at >= NOW() - make_interval(days => :days)
                      AND item->>'product_id' IS NOT NULL
                      AND item->>'price'      IS NOT NULL
                      AND item->>'quantity'   IS NOT NULL
                )
                SELECT
                    COUNT(DISTINCT pc.product_key)::int                           AS matched_products,
                    COALESCE(SUM(pc.cogs_per_unit * li.quantity), 0)       AS real_cogs,
                    COALESCE(SUM(li.unit_price * li.quantity),     0)      AS covered_revenue
                FROM line_items_expanded li
                JOIN product_costs pc
                  ON pc.shop_domain = :shop
                 AND pc.product_key = li.product_key
                 AND pc.cogs_per_unit IS NOT NULL
            """),
            {"shop": shop_domain, "days": window_days},
        ).fetchone()
    except Exception as exc:
        log.warning("pnl_engine: real-cogs join failed shop=%s: %s",
                    shop_domain, exc)
        return (0.0, 0.0, 0)

    if row is None:
        return (0.0, 0.0, 0)

    matched_products = int(row[0] or 0)
    real_cogs        = round(float(row[1] or 0), 2)
    covered_revenue  = round(float(row[2] or 0), 2)
    return (real_cogs, covered_revenue, matched_products)


def get_product_margin_drag(
    db: Session,
    shop_domain: str,
    window_days: int = 30,
    limit: int = 5,
) -> dict:
    """Top-N products dragging total margin down.

    Strada 4 dominance (2026-04-20) — the per-product margin-drag
    surface Lifetimely / BeProfit don't ship at the base tier. For
    each product in the window:
      - revenue = SUM(price × quantity)
      - cogs    = SUM(cogs_per_unit × quantity) when product_costs row
                  exists; else revenue × _DEFAULT_COGS_PCT (40%) as a
                  fallback, flagged `cogs_source="default_40pct"`
      - margin_eur = revenue − cogs
      - margin_pct = margin_eur / revenue

    Ranked by LOWEST margin_pct first (worst dragger), filtered to
    products with meaningful revenue (≥ 1% of total window revenue OR
    ≥ €100 — whichever is smaller). Products below that floor are
    cost-noise, not margin drag.

    Privacy: per-product data already visible to the merchant via
    Shopify admin; no new PII exposure. Same shop_domain scoping as
    the rest of pnl_engine.

    Returns:
        {
            window_days, currency, generated_at,
            total_revenue,
            total_margin_drag_eur,   # sum of (below-avg-margin × revenue) for products shown
            products: [
                {product, title, revenue, cogs, cogs_source,
                 margin_eur, margin_pct, units_sold},
                ...
            ],
            methodology
        }
    """
    from app.services.revenue_metrics import get_shop_currency
    currency = get_shop_currency(db, shop_domain) or "USD"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        rows = db.execute(
            text("""
                WITH line_items_expanded AS (
                    SELECT
                        COALESCE(item->>'product_id', item->>'product_url') AS product_key,
                        COALESCE(
                            NULLIF(item->>'title', ''),
                            NULLIF(item->>'product_url', '')
                        ) AS title,
                        (item->>'price')::numeric  AS unit_price,
                        (item->>'quantity')::int   AS quantity
                    FROM shop_orders so,
                         jsonb_array_elements(CASE WHEN jsonb_typeof(so.line_items) = 'array' THEN so.line_items ELSE '[]'::jsonb END) AS item
                    WHERE so.shop_domain = :shop
                      AND so.created_at >= NOW() - make_interval(days => :days)
                      AND item->>'price'    IS NOT NULL
                      AND item->>'quantity' IS NOT NULL
                      AND COALESCE(item->>'product_id', item->>'product_url') IS NOT NULL
                ),
                product_rollup AS (
                    SELECT
                        product_key,
                        MAX(title) AS title,
                        SUM(unit_price * quantity) AS revenue,
                        SUM(quantity)              AS units_sold
                    FROM line_items_expanded
                    GROUP BY product_key
                )
                SELECT
                    pr.product_key,
                    pr.title,
                    pr.revenue,
                    pr.units_sold::int,
                    pc.cogs_per_unit,
                    pc.source
                FROM product_rollup pr
                LEFT JOIN product_costs pc
                  ON pc.shop_domain = :shop
                 AND pc.product_key = pr.product_key
            """),
            {"shop": shop_domain, "days": window_days},
        ).fetchall()
    except Exception as exc:
        log.warning("pnl_engine: margin-drag query failed for %s: %s", shop_domain, exc)
        return {
            "window_days":   window_days,
            "currency":      currency,
            "generated_at":  now.isoformat() + "Z",
            "total_revenue": 0.0,
            "total_margin_drag_eur": 0.0,
            "products":      [],
            "methodology":   f"Query failed: {type(exc).__name__}",
            "error":         str(exc)[:200],
        }

    if not rows:
        return {
            "window_days":   window_days,
            "currency":      currency,
            "generated_at":  now.isoformat() + "Z",
            "total_revenue": 0.0,
            "total_margin_drag_eur": 0.0,
            "products":      [],
            "methodology":   "No orders in the window yet.",
        }

    total_revenue = sum(float(r[2] or 0) for r in rows)
    if total_revenue <= 0:
        return {
            "window_days":   window_days,
            "currency":      currency,
            "generated_at":  now.isoformat() + "Z",
            "total_revenue": 0.0,
            "total_margin_drag_eur": 0.0,
            "products":      [],
            "methodology":   "No orders with revenue in the window.",
        }

    # Floor: at least €100 OR at least 1% of total revenue — whichever
    # is smaller — so tiny products don't drown the ranking.
    threshold = max(1.0, min(100.0, total_revenue * 0.01))

    computed: list[dict] = []
    for product_key, title, revenue, units_sold, cogs_per_unit, provenance in rows:
        rev = float(revenue or 0)
        if rev < threshold:
            continue
        units = int(units_sold or 0)
        if cogs_per_unit is not None and units > 0:
            cogs_total = float(cogs_per_unit) * units
            cogs_src = (provenance or "manual_entry")
        else:
            cogs_total = rev * _DEFAULT_COGS_PCT
            cogs_src = "default_40pct"
        margin_eur = rev - cogs_total
        margin_pct = (margin_eur / rev * 100.0) if rev > 0 else 0.0
        computed.append({
            "product": product_key or "",
            "title":   title or product_key or "—",
            "revenue": round(rev, 2),
            "cogs":    round(cogs_total, 2),
            "cogs_source": cogs_src,
            "margin_eur": round(margin_eur, 2),
            "margin_pct": round(margin_pct, 1),
            "units_sold": units,
        })

    # Rank: worst margin% first. Then cap at limit.
    computed.sort(key=lambda p: p["margin_pct"])
    worst = computed[:limit]

    # total_margin_drag_eur = sum of (avg_margin - product_margin) × rev
    # i.e., how much MORE profit you'd be making if each worst product
    # matched the shop average. This is the actionable number.
    if computed:
        avg_margin_pct = sum(p["margin_pct"] * p["revenue"] for p in computed) / sum(p["revenue"] for p in computed)
    else:
        avg_margin_pct = 0.0
    drag = 0.0
    for p in worst:
        delta_pct = max(0.0, avg_margin_pct - p["margin_pct"])
        drag += p["revenue"] * (delta_pct / 100.0)
    drag = round(drag, 2)

    return {
        "window_days":   window_days,
        "currency":      currency,
        "generated_at":  now.isoformat() + "Z",
        "total_revenue": round(total_revenue, 2),
        "avg_margin_pct": round(avg_margin_pct, 1),
        "total_margin_drag_eur": drag,
        "products":      worst,
        "methodology":   (
            f"Margin per product in the last {window_days}d. COGS uses "
            "exact product_costs.cogs_per_unit when available, "
            f"{int(_DEFAULT_COGS_PCT*100)}% of revenue as a fallback "
            "(flagged with `cogs_source=default_40pct`). Drag = how "
            "much more margin the top-N would produce if they matched "
            "your shop average."
        ),
    }


def _empty_report(window_days: int, currency: str = "USD") -> dict:
    """Return a structurally valid empty response when the shop has no orders."""
    return {
        "window_days":   window_days,
        "currency":      currency,
        "precision":     "rough",
        "has_data":      False,
        "order_count":   0,
        "gross_revenue": 0.0,
        "cogs_coverage_pct":  0.0,
        "products_with_cogs": 0,
        "costs": {
            "cogs":         {"amount": 0.0, "rate": _DEFAULT_COGS_PCT, "estimated": True,  "source": "default_40pct",         "note": "No orders yet."},
            "payment_fees": {"amount": 0.0, "rate": _DEFAULT_PAYMENT_PCT, "flat": _DEFAULT_PAYMENT_FLAT, "estimated": False, "source": "shopify_payments_standard", "note": "No orders yet."},
            "shipping":     {"amount": 0.0, "rate": _DEFAULT_SHIPPING_PER_ORDER, "estimated": True,  "source": "default_5_per_order",    "note": "No orders yet."},
            "ad_spend":     {"amount": 0.0, "estimated": True,  "source": "not_tracked_yet", "note": "Ad spend integration not wired yet."},
        },
        "total_costs":      0.0,
        "gross_profit":     0.0,
        "net_profit":       0.0,
        "gross_margin_pct": 0.0,
        "net_margin_pct":   0.0,
        "verdict":          "Profit intelligence activates once your first orders are received.",
        "generated_at":     datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
    }


# ============================================================================
# Profit slicing by dimension — Gap #3 close (brutal $0-70 audit 2026-04-27)
# ============================================================================
#
# Every profit-tracker competitor at $20-49 (TrueProfit, BeProfit, Lifetimely,
# Profit Calc, OrderMetrics, Putler) ships profit slicing across multiple
# dimensions. We had product (margin-drag); this adds variant, country,
# channel.
#
# Math contract — same as margin-drag:
#   revenue   = SUM(price × quantity) for line items in dimension bucket
#   cogs      = SUM(cogs_per_unit × quantity) when product_costs available;
#               else revenue × _DEFAULT_COGS_PCT (40%) fallback
#   margin    = revenue − cogs (gross profit, before payment fees / shipping
#               which apply at order level — not aggregated here so the
#               dimension comparison stays apples-to-apples)
#   margin_pct = margin / revenue when revenue > 0, else None
#
# Privacy: dimension keys (country code, channel name, variant title) are
# already merchant-visible via Shopify admin. No new PII surfaces here.
#
# COGS fallback: 40% default is the same convention as the rest of pnl_engine.
# When `cogs_source = "default_40pct"` the UI must surface the estimated flag
# so the merchant knows to upload product_costs for real precision.

from datetime import datetime as _dt_pbd, timezone as _tz_pbd

_VALID_DIMS = ("variant", "country", "channel")


def get_profit_by_dimension(
    db: Session,
    shop_domain: str,
    *,
    dim: str,
    window_days: int = 30,
    limit: int = 10,
) -> dict:
    """Profit slicing by dimension.

    Args:
        dim: one of "variant", "country", "channel"
        window_days: rolling window (1-365)
        limit: max rows returned (1-50)

    Returns dict with shape:
        {
            dim: str,
            window_days: int,
            currency: str,
            generated_at: str,
            total_revenue: float,
            total_margin: float,
            avg_margin_pct: float | None,
            rows: [
                {key: str, label: str, revenue: float, cogs: float,
                 margin: float, margin_pct: float | None,
                 units_or_orders: int, cogs_source: str},
                ...
            ],
            methodology: str,
            error: str | None,
        }

    For dim=country: joins with Redis hash hs:order_geo:{shop} populated by
    app/core/geo.record_order_geo. Cross-tenant impossible (key shop-scoped).

    For dim=channel: joins shop_orders with visitor_purchase_session on
    shopify_order_id, groups by last_source ("organic", "google_ads",
    "facebook", direct, etc). Orders without attribution session collapse
    into "(direct/unknown)" bucket.
    """
    from app.services.revenue_metrics import get_shop_currency
    currency = get_shop_currency(db, shop_domain) or "USD"
    now = _dt_pbd.now(_tz_pbd.utc).replace(tzinfo=None)

    if dim not in _VALID_DIMS:
        return {
            "dim": dim,
            "window_days": window_days,
            "currency": currency,
            "generated_at": now.isoformat() + "Z",
            "total_revenue": 0.0,
            "total_margin": 0.0,
            "avg_margin_pct": None,
            "rows": [],
            "methodology": f"Invalid dim: {dim}. Must be one of {_VALID_DIMS}.",
            "error": f"invalid_dim:{dim}",
        }

    if dim == "variant":
        return _profit_by_variant(db, shop_domain, currency, window_days, limit, now)
    if dim == "country":
        return _profit_by_country(db, shop_domain, currency, window_days, limit, now)
    return _profit_by_channel(db, shop_domain, currency, window_days, limit, now)


def _empty_dim_response(dim, window_days, currency, now, methodology):
    return {
        "dim": dim, "window_days": window_days, "currency": currency,
        "generated_at": now.isoformat() + "Z",
        "total_revenue": 0.0, "total_margin": 0.0, "avg_margin_pct": None,
        "rows": [], "methodology": methodology,
    }


def _profit_by_variant(db, shop_domain, currency, window_days, limit, now):
    """Group line_items by (product_id, variant_id, variant_title). Variant-
    level COGS not stored — fall back to product-level COGS allocated by
    quantity within variant."""
    try:
        rows = db.execute(
            text("""
                WITH expanded AS (
                    SELECT
                        COALESCE(item->>'product_id', item->>'product_url') AS product_key,
                        item->>'variant_id' AS variant_id,
                        COALESCE(NULLIF(item->>'variant_title', ''), '(no variant)') AS variant_title,
                        COALESCE(NULLIF(item->>'title', ''), '(untitled)') AS product_title,
                        (item->>'price')::numeric  AS unit_price,
                        (item->>'quantity')::int   AS quantity
                    FROM shop_orders so,
                         jsonb_array_elements(CASE WHEN jsonb_typeof(so.line_items) = 'array' THEN so.line_items ELSE '[]'::jsonb END) AS item
                    WHERE so.shop_domain = :shop
                      AND so.created_at >= NOW() - make_interval(days => :days)
                      AND item->>'price'    IS NOT NULL
                      AND item->>'quantity' IS NOT NULL
                      AND item ? 'variant_id'
                      AND COALESCE(item->>'product_id', item->>'product_url') IS NOT NULL
                ),
                variant_rollup AS (
                    SELECT
                        product_key,
                        variant_id,
                        MAX(variant_title) AS variant_title,
                        MAX(product_title) AS product_title,
                        SUM(unit_price * quantity) AS revenue,
                        SUM(quantity)              AS units_sold
                    FROM expanded
                    GROUP BY product_key, variant_id
                )
                SELECT
                    vr.product_key,
                    vr.variant_id,
                    vr.variant_title,
                    vr.product_title,
                    vr.revenue,
                    vr.units_sold,
                    pc.cogs_per_unit,
                    pc.source
                FROM variant_rollup vr
                LEFT JOIN product_costs pc
                  ON pc.shop_domain = :shop
                 AND pc.product_key = vr.product_key
                ORDER BY vr.revenue DESC
                LIMIT :limit
            """),
            {"shop": shop_domain, "days": window_days, "limit": limit},
        ).fetchall()
    except Exception as exc:
        log.warning("pnl_engine.profit_by_variant: query failed for %s: %s", shop_domain, exc)
        out = _empty_dim_response("variant", window_days, currency, now,
                                  f"Query failed: {type(exc).__name__}")
        out["error"] = str(exc)[:200]
        return out

    if not rows:
        return _empty_dim_response(
            "variant", window_days, currency, now,
            "No line-item variants in window. Pixel v15+ ingests variant_id; "
            "older orders pre-v15 lack variant data and stay uncounted."
        )

    out_rows = []
    total_revenue = 0.0
    total_margin = 0.0
    pct_values: list[float] = []
    for r in rows:
        rev = float(r[4] or 0)
        units = int(r[5] or 0)
        cogs_per_unit = float(r[6]) if r[6] is not None else None
        source = r[7] or "default_40pct"
        if cogs_per_unit is not None:
            cogs = round(cogs_per_unit * units, 2)
        else:
            cogs = round(rev * _DEFAULT_COGS_PCT, 2)
            source = "default_40pct"
        margin = round(rev - cogs, 2)
        margin_pct = round((margin / rev) * 100.0, 2) if rev > 0 else None
        label = f"{r[3]} — {r[2]}" if r[2] != "(no variant)" else str(r[3])
        out_rows.append({
            "key": str(r[1]),
            "label": label,
            "revenue": round(rev, 2),
            "cogs": cogs,
            "margin": margin,
            "margin_pct": margin_pct,
            "units_or_orders": units,
            "cogs_source": source,
        })
        total_revenue += rev
        total_margin += margin
        if margin_pct is not None:
            pct_values.append(margin_pct)

    avg = round(sum(pct_values) / len(pct_values), 2) if pct_values else None
    return {
        "dim": "variant",
        "window_days": window_days,
        "currency": currency,
        "generated_at": now.isoformat() + "Z",
        "total_revenue": round(total_revenue, 2),
        "total_margin": round(total_margin, 2),
        "avg_margin_pct": avg,
        "rows": out_rows,
        "methodology": (
            "Per-variant gross profit (revenue − COGS). Variant-level COGS not "
            "stored; fallback to product-level COGS × quantity, else 40% default. "
            "Sorted by revenue desc, top {limit}.".format(limit=limit)
        ),
    }


def _profit_by_country(db, shop_domain, currency, window_days, limit, now):
    """Aggregate per-country profit by joining shop_orders with the
    Redis geo hash populated at purchase time."""
    from datetime import timedelta
    from app.core.redis_client import _client
    from app.core.silent_fallback import record_silent_return

    rc = _client()
    if rc is None:
        record_silent_return("pnl_engine.profit_by_country.no_redis")
        return _empty_dim_response(
            "country", window_days, currency, now,
            "Country breakdown unavailable: Redis client offline."
        )

    # Window of valid YYYY-MM-DD dates (UTC). Geo hash uses UTC date keys.
    today = now.date()
    valid_dates = {
        (today - timedelta(days=d)).isoformat() for d in range(window_days)
    }

    try:
        raw = rc.hgetall(f"hs:order_geo:{shop_domain}") or {}
    except Exception as exc:
        log.warning("pnl_engine.profit_by_country: redis read failed: %s", exc)
        out = _empty_dim_response("country", window_days, currency, now,
                                  f"Redis read failed: {type(exc).__name__}")
        out["error"] = str(exc)[:200]
        return out

    by_cc: dict[str, dict[str, float]] = {}
    for raw_field, raw_value in raw.items():
        field = raw_field.decode() if isinstance(raw_field, bytes) else raw_field
        value = raw_value.decode() if isinstance(raw_value, bytes) else raw_value
        parts = field.split(":")
        if len(parts) < 3:
            continue
        cc, day, metric = parts[0], parts[1], parts[2]
        if day not in valid_dates:
            continue
        bucket = by_cc.setdefault(cc, {"orders": 0, "revenue": 0.0})
        if metric == "count":
            try: bucket["orders"] += int(value)
            except (TypeError, ValueError): continue
        elif metric.startswith("revenue_"):
            metric_ccy = metric.split("_", 1)[1]
            if metric_ccy == currency:
                try: bucket["revenue"] += float(value)
                except (TypeError, ValueError): continue

    if not by_cc:
        return _empty_dim_response(
            "country", window_days, currency, now,
            "No geo-tagged orders in window. Pixel records country at "
            "purchase time; older orders pre-pixel-v14 stay uncounted."
        )

    out_rows = []
    total_revenue = 0.0
    total_margin = 0.0
    pct_values: list[float] = []
    for cc, agg in sorted(by_cc.items(), key=lambda x: -x[1]["revenue"])[:limit]:
        rev = agg["revenue"]
        # Country-level COGS not separately tracked — apply default 40%.
        # When per-country COGS becomes a thing (rare for SMB), wire here.
        cogs = round(rev * _DEFAULT_COGS_PCT, 2)
        margin = round(rev - cogs, 2)
        margin_pct = round((margin / rev) * 100.0, 2) if rev > 0 else None
        out_rows.append({
            "key": cc,
            "label": cc,  # ISO-3166 alpha-2; UI can map to flag/name
            "revenue": round(rev, 2),
            "cogs": cogs,
            "margin": margin,
            "margin_pct": margin_pct,
            "units_or_orders": int(agg["orders"]),
            "cogs_source": "default_40pct",
        })
        total_revenue += rev
        total_margin += margin
        if margin_pct is not None:
            pct_values.append(margin_pct)

    avg = round(sum(pct_values) / len(pct_values), 2) if pct_values else None
    return {
        "dim": "country",
        "window_days": window_days,
        "currency": currency,
        "generated_at": now.isoformat() + "Z",
        "total_revenue": round(total_revenue, 2),
        "total_margin": round(total_margin, 2),
        "avg_margin_pct": avg,
        "rows": out_rows,
        "methodology": (
            "Per-country gross profit. Revenue from Redis geo hash "
            "(populated at purchase). COGS at default 40% fallback "
            "(country-specific COGS not tracked). Top {limit} by "
            "revenue.".format(limit=limit)
        ),
    }


def _profit_by_channel(db, shop_domain, currency, window_days, limit, now):
    """Aggregate per-channel profit by joining shop_orders with
    visitor_purchase_session on shopify_order_id; group by last_source."""
    try:
        rows = db.execute(
            text("""
                SELECT
                    COALESCE(NULLIF(vps.last_source, ''), '(direct/unknown)') AS channel,
                    COUNT(DISTINCT so.id) AS orders,
                    COALESCE(SUM(so.total_price), 0) AS revenue
                FROM shop_orders so
                LEFT JOIN visitor_purchase_sessions vps
                  ON vps.shop_domain = so.shop_domain
                 AND vps.shopify_order_id = so.shopify_order_id
                WHERE so.shop_domain = :shop
                  AND so.created_at >= NOW() - make_interval(days => :days)
                  AND so.total_price > 0
                  AND so.currency = :currency
                GROUP BY 1
                ORDER BY revenue DESC
                LIMIT :limit
            """),
            {"shop": shop_domain, "days": window_days,
             "currency": currency, "limit": limit},
        ).fetchall()
    except Exception as exc:
        log.warning("pnl_engine.profit_by_channel: query failed for %s: %s", shop_domain, exc)
        out = _empty_dim_response("channel", window_days, currency, now,
                                  f"Query failed: {type(exc).__name__}")
        out["error"] = str(exc)[:200]
        return out

    if not rows:
        return _empty_dim_response(
            "channel", window_days, currency, now,
            "No orders in window. Channel attribution requires visitor "
            "session continuity (pixel + identity bridge)."
        )

    out_rows = []
    total_revenue = 0.0
    total_margin = 0.0
    pct_values: list[float] = []
    for r in rows:
        rev = float(r[2] or 0)
        orders_count = int(r[1] or 0)
        # Channel-level COGS: same 40% fallback as country. When ad_spend
        # integration unblocks (post-P.IVA), THIS is where ROAS gets wired
        # in by subtracting ad_spend per channel from margin.
        cogs = round(rev * _DEFAULT_COGS_PCT, 2)
        margin = round(rev - cogs, 2)
        margin_pct = round((margin / rev) * 100.0, 2) if rev > 0 else None
        out_rows.append({
            "key": str(r[0]),
            "label": str(r[0]),
            "revenue": round(rev, 2),
            "cogs": cogs,
            "margin": margin,
            "margin_pct": margin_pct,
            "units_or_orders": orders_count,
            "cogs_source": "default_40pct",
        })
        total_revenue += rev
        total_margin += margin
        if margin_pct is not None:
            pct_values.append(margin_pct)

    avg = round(sum(pct_values) / len(pct_values), 2) if pct_values else None
    return {
        "dim": "channel",
        "window_days": window_days,
        "currency": currency,
        "generated_at": now.isoformat() + "Z",
        "total_revenue": round(total_revenue, 2),
        "total_margin": round(total_margin, 2),
        "avg_margin_pct": avg,
        "rows": out_rows,
        "methodology": (
            "Per-channel gross profit. Channel from visitor_purchase_"
            "session.last_source (UTM-deterministic at purchase). COGS "
            "at default 40% fallback. Orders without attribution session "
            "collapse into '(direct/unknown)'. Top {limit}.".format(limit=limit)
        ),
    }
