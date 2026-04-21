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


def get_pnl_report(
    db: Session,
    shop_domain: str,
    window_days: int = 30,
) -> dict:
    """
    Compute the full P&L waterfall for a shop over the last N days.

    v2 behavior: reads shop_cost_defaults and product_costs from the DB and
    uses them in priority order before falling back to module constants. Every
    cost component is tagged with an "estimated" flag so the UI can honestly
    label default-vs-real precision on a per-line basis.

    Self-healing side effect: on the first /pro/pnl call per shop per hour,
    kicks off a Shopify COGS sync inline so the merchant's Profit Intelligence
    auto-upgrades from "rough" to "refined" precision without ever touching
    the Settings UI. See _maybe_auto_sync_shopify_costs below.
    """
    window_days = max(1, min(window_days, 90))

    # Auto-sync hook — fires at most once per hour per shop, idempotent, safe
    # to fail silently. Adds ~300-500ms to the first call after the TTL
    # expires, then is a no-op for the next hour.
    _maybe_auto_sync_shopify_costs(db, shop_domain)

    # ------------------------------------------------------------------
    # 1. Load shop cost config (nullable — may not exist).
    # ------------------------------------------------------------------
    cost_cfg = _load_shop_cost_defaults(db, shop_domain)

    # Resolved rates (config row > module default).
    def _or_default(val, fallback: float) -> float:
        if val is None:
            return fallback
        return float(val)

    cogs_pct_default = _or_default(cost_cfg.get("default_cogs_pct"),           _DEFAULT_COGS_PCT)
    payment_pct      = _or_default(cost_cfg.get("payment_pct"),                _DEFAULT_PAYMENT_PCT)
    payment_flat     = _or_default(cost_cfg.get("payment_flat"),               _DEFAULT_PAYMENT_FLAT)
    shipping_per_ord = _or_default(cost_cfg.get("default_shipping_per_order"), _DEFAULT_SHIPPING_PER_ORDER)
    ad_spend_monthly = _or_default(cost_cfg.get("ad_spend_manual_monthly"),    0.0)

    # Non-default flags — each tracked so the precision calc can see what's real.
    cogs_pct_is_custom      = cost_cfg.get("default_cogs_pct") is not None
    payment_pct_is_custom   = cost_cfg.get("payment_pct") is not None
    payment_flat_is_custom  = cost_cfg.get("payment_flat") is not None
    shipping_is_custom      = cost_cfg.get("default_shipping_per_order") is not None
    ad_spend_is_manual      = cost_cfg.get("ad_spend_manual_monthly") is not None

    # ------------------------------------------------------------------
    # 2. Pull gross revenue + order count from shop_orders.
    # ------------------------------------------------------------------
    currency = get_shop_currency(db, shop_domain)
    try:
        row = db.execute(
            text("""
                SELECT
                    COUNT(*)::int                        AS order_count,
                    COALESCE(SUM(total_price), 0) AS gross_revenue
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND created_at >= NOW() - make_interval(days => :days)
                  AND (:currency IS NULL OR currency = :currency)
            """),
            {"shop": shop_domain, "days": window_days, "currency": currency},
        ).fetchone()
    except Exception as exc:
        log.error("pnl_engine: revenue query failed shop=%s: %s", shop_domain, exc)
        return _empty_report(window_days, currency or "USD")

    order_count   = int(row[0] or 0) if row else 0
    gross_revenue = round(float(row[1] or 0), 2) if row else 0.0

    if order_count == 0:
        return _empty_report(window_days, currency or "USD")

    # ------------------------------------------------------------------
    # 3. Resolve native currency for display.
    # ------------------------------------------------------------------
    try:
        currency = get_shop_currency(db, shop_domain) or "USD"
    except Exception:
        currency = "USD"

    # ------------------------------------------------------------------
    # 4. Per-product real COGS — join line_items against product_costs.
    # Returns (real_cogs_amount, covered_revenue, products_with_cogs_count).
    # ------------------------------------------------------------------
    real_cogs_amount, covered_revenue, products_with_real_cogs = (
        _compute_real_cogs(db, shop_domain, window_days)
    )

    # Revenue NOT covered by real per-product COGS → use percentage fallback.
    uncovered_revenue = max(0.0, gross_revenue - covered_revenue)
    cogs_fallback     = round(uncovered_revenue * cogs_pct_default, 2)
    cogs_estimate     = round(real_cogs_amount + cogs_fallback, 2)

    # Coverage fraction — drives the precision level calc below.
    cogs_coverage = round(covered_revenue / gross_revenue, 4) if gross_revenue > 0 else 0.0

    # ------------------------------------------------------------------
    # 5. Remaining cost stack — deterministic from resolved rates.
    # ------------------------------------------------------------------
    payment_fees  = round(gross_revenue * payment_pct + order_count * payment_flat, 2)
    shipping_cost = round(order_count * shipping_per_ord, 2)

    # Manual monthly ad spend scales linearly to the window.
    # 30d window + 30-day monthly => exactly the monthly figure.
    ad_spend = round(ad_spend_monthly * (window_days / 30.0), 2) if ad_spend_is_manual else 0.0

    total_costs_tracked = round(cogs_estimate + payment_fees + shipping_cost + ad_spend, 2)
    gross_profit        = round(gross_revenue - cogs_estimate - payment_fees - shipping_cost, 2)
    net_profit          = round(gross_profit - ad_spend, 2)

    gross_margin_pct = round((gross_profit / gross_revenue) * 100, 1) if gross_revenue > 0 else 0.0
    net_margin_pct   = round((net_profit   / gross_revenue) * 100, 1) if gross_revenue > 0 else 0.0

    # ------------------------------------------------------------------
    # 6. Precision level — how much of this P&L is "real" vs "estimated".
    # ------------------------------------------------------------------
    has_any_custom = any([
        cogs_pct_is_custom, payment_pct_is_custom, payment_flat_is_custom,
        shipping_is_custom, ad_spend_is_manual, products_with_real_cogs > 0,
    ])

    if (cogs_coverage >= _EXACT_COGS_COVERAGE_THRESHOLD and ad_spend_is_manual):
        precision = "exact"
    elif has_any_custom:
        precision = "refined"
    else:
        precision = "rough"

    # ------------------------------------------------------------------
    # 7. Human-readable verdict.
    # ------------------------------------------------------------------
    from app.core.currency import currency_symbol
    symbol = currency_symbol(currency)
    if net_margin_pct >= 20:
        verdict = (
            f"You keep ~{net_margin_pct:.0f}¢ of every {symbol}1 — healthy margin range for DTC."
        )
    elif net_margin_pct >= 10:
        verdict = (
            f"You keep ~{net_margin_pct:.0f}¢ of every {symbol}1 — tight but viable. Watch your COGS."
        )
    elif net_margin_pct > 0:
        verdict = (
            f"Only ~{net_margin_pct:.0f}¢ of every {symbol}1 stays with you — margin is too thin to scale."
        )
    else:
        verdict = (
            "Estimated costs exceed revenue. Enter real COGS to see if this is a true loss or a default overestimate."
        )

    log.info(
        "pnl_engine: shop=%s window=%dd orders=%d gross=%.2f cogs=%.2f "
        "(real=%.2f, cov=%.1f%%) fees=%.2f ship=%.2f ads=%.2f net=%.2f "
        "margin=%.1f%% precision=%s",
        shop_domain, window_days, order_count, gross_revenue,
        cogs_estimate, real_cogs_amount, cogs_coverage * 100,
        payment_fees, shipping_cost, ad_spend,
        net_profit, net_margin_pct, precision,
    )

    # Source labels drive the UI's per-line "estimated" vs "real" badges.
    if products_with_real_cogs > 0 and cogs_coverage >= 0.999:
        cogs_source = "per_product_exact"
        cogs_estimated_flag = False
    elif products_with_real_cogs > 0:
        cogs_source = "per_product_partial"
        cogs_estimated_flag = True
    elif cogs_pct_is_custom:
        cogs_source = "shop_default_pct_custom"
        cogs_estimated_flag = True
    else:
        cogs_source = "default_40pct"
        cogs_estimated_flag = True

    cogs_note = (
        f"Real per-product COGS on {products_with_real_cogs} products covers "
        f"{int(cogs_coverage * 100)}% of revenue — remainder estimated at "
        f"{int(cogs_pct_default * 100)}%."
        if products_with_real_cogs > 0
        else (
            f"Using custom shop default {int(cogs_pct_default * 100)}% COGS."
            if cogs_pct_is_custom
            else "Using module default 40% COGS — enter real cost data for precision."
        )
    )

    return {
        "window_days":   window_days,
        "currency":      currency,
        "precision":     precision,
        "has_data":      True,
        "order_count":   order_count,
        "gross_revenue": gross_revenue,
        "cogs_coverage_pct":   round(cogs_coverage * 100, 1),
        "products_with_cogs":  products_with_real_cogs,
        "costs": {
            "cogs": {
                "amount":    cogs_estimate,
                "rate":      cogs_pct_default,
                "estimated": cogs_estimated_flag,
                "source":    cogs_source,
                "note":      cogs_note,
            },
            "payment_fees": {
                "amount":    payment_fees,
                "rate":      payment_pct,
                "flat":      payment_flat,
                "estimated": not (payment_pct_is_custom or payment_flat_is_custom),
                "source":    "shop_custom" if (payment_pct_is_custom or payment_flat_is_custom) else "shopify_payments_standard",
                "note":      (
                    f"Custom payment rates: {payment_pct*100:.2f}% + {payment_flat:.2f}/order."
                    if (payment_pct_is_custom or payment_flat_is_custom)
                    else f"Shopify Payments standard ({payment_pct*100:.1f}% + {payment_flat:.2f}/order)."
                ),
            },
            "shipping": {
                "amount":    shipping_cost,
                "rate":      shipping_per_ord,
                "estimated": not shipping_is_custom,
                "source":    "shop_custom" if shipping_is_custom else "default_5_per_order",
                "note":      (
                    f"Custom shipping estimate: {shipping_per_ord:.2f} per order."
                    if shipping_is_custom
                    else f"Default {shipping_per_ord:.2f}/order shipping estimate — configure your real rate."
                ),
            },
            "ad_spend": {
                "amount":    ad_spend,
                "estimated": True,  # manual entry is still not exact ROAS
                "source":    "manual_monthly_entry" if ad_spend_is_manual else "not_tracked_yet",
                "note":      (
                    f"Manual monthly ad spend ({symbol}{ad_spend_monthly:.0f}/mo) scaled to {window_days}-day window. "
                    "Connect Meta Ads + Google Ads for real campaign-level ROAS."
                    if ad_spend_is_manual
                    else "Ad spend not tracked yet — enter a rough monthly figure or connect Meta + Google Ads."
                ),
            },
        },
        "total_costs":       total_costs_tracked,
        "gross_profit":      gross_profit,
        "net_profit":        net_profit,
        "gross_margin_pct":  gross_margin_pct,
        "net_margin_pct":    net_margin_pct,
        "verdict":           verdict,
        "generated_at":      datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
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
                         jsonb_array_elements(so.line_items) AS item
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
                         jsonb_array_elements(so.line_items) AS item
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
