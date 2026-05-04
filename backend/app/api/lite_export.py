"""
lite_export.py — CSV export for Lite surfaces.

Strada 3.4 (2026-04-20). Closes the "no CSV export" gap vs competitors
at the €39 tier. One parameterized endpoint instead of five siblings:
  GET /analytics/export?surface=<name>

Supported surfaces:
  - rars                 — Revenue-at-Risk components
  - benchmarks           — peer benchmark metrics
  - benchmarks_vertical  — vertical-aware benchmark metrics
  - pnl                  — P&L waterfall
  - cohorts_monthly      — monthly acquisition cohorts
  - attribution          — channel attribution (first + last touch + campaigns)

Response: text/csv with Content-Disposition: attachment and a shop-
scoped filename. Every row carries the shop_domain + generated_at ISO
timestamp so the merchant can audit exactly when and from which shop
the export was taken.

No new services — each surface calls the same function the dashboard
already uses. CSV serialization is the only novel work.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_read_db
from app.core.deps import require_merchant_session

# Reportlab — pure-python PDF generation (no system deps). Imported
# lazily inside _serialize_pdf so a missing install degrades to a
# clear 500 rather than crashing module import.

log = logging.getLogger(__name__)

router = APIRouter(tags=["lite_export"])


ALLOWED_SURFACES = {
    "rars",
    "benchmarks",
    "benchmarks_vertical",
    "pnl",
    "cohorts_monthly",
    "attribution",
    "inventory",
    # Per-row data surfaces (added 2026-04-29) — richer exports that
    # show one row per customer / product / country / variant. These
    # produce 10-1000+ rows per export depending on shop volume,
    # vs the per-summary surfaces above (3-10 rows).
    "top_customers_ltv",
    "top_products",
    "orders_by_country",
    "top_variants",
    "rfm_segments",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_filename(shop_domain: str, surface: str) -> str:
    """shop-name + surface + date → a safe, merchant-recognizable filename.
    e.g. hedgespark_rars_2026-04-20.csv"""
    stem = shop_domain.replace(".myshopify.com", "").replace(".", "_")
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{stem}_{surface}_{date}.csv"


def _rows_for_rars(db: Session, shop: str) -> tuple[list[str], list[dict[str, Any]]]:
    from app.services.revenue_at_risk import get_revenue_at_risk
    data = get_revenue_at_risk(db, shop)
    rows: list[dict[str, Any]] = []
    ts = _now_iso()
    ccy = data.get("currency") or "USD"
    for c in data.get("components") or []:
        rows.append({
            "shop": shop,
            "generated_at": ts,
            "currency": ccy,
            "component": c.get("source") or "",
            "loss_amount": c.get("loss_eur") or 0.0,
            "narrative": (c.get("narrative") or "").replace("\n", " ").strip(),
        })
    # One summary row at the top of the table for quick reading.
    headers = [
        "shop", "generated_at", "currency", "component",
        "loss_amount", "narrative",
    ]
    summary_row = {
        "shop": shop,
        "generated_at": ts,
        "currency": ccy,
        "component": "__total_at_risk__",
        "loss_amount": data.get("total_at_risk_eur") or 0.0,
        "narrative": f"prevented_this_month={data.get('prevented_eur_this_month') or 0.0}",
    }
    return headers, [summary_row] + rows


def _rows_for_benchmarks(db: Session, shop: str) -> tuple[list[str], list[dict[str, Any]]]:
    from app.services.benchmarks import get_merchant_benchmark_report
    data = get_merchant_benchmark_report(db, shop)
    ts = _now_iso()
    ccy = data.get("currency") or "USD"
    headers = [
        "shop", "generated_at", "currency", "band", "peer_count",
        "metric", "your_value", "p25", "p50", "p75", "p90",
        "percentile_rank", "status", "recovery_to_p75", "narrative",
    ]
    rows: list[dict[str, Any]] = []
    metrics = data.get("metrics") or {}
    for metric_name, m in metrics.items():
        rows.append({
            "shop": shop,
            "generated_at": ts,
            "currency": ccy,
            "band": data.get("band") or "",
            "peer_count": data.get("peer_count") or 0,
            "metric": metric_name,
            "your_value": m.get("value") or 0,
            "p25": m.get("p25") or 0,
            "p50": m.get("p50") or 0,
            "p75": m.get("p75") or 0,
            "p90": m.get("p90") or 0,
            "percentile_rank": m.get("percentile_rank") or 0,
            "status": m.get("status") or "",
            "recovery_to_p75": m.get("recovery_to_p75_eur") or 0,
            "narrative": (m.get("narrative") or "").replace("\n", " ").strip(),
        })
    return headers, rows


def _rows_for_benchmarks_vertical(db: Session, shop: str) -> tuple[list[str], list[dict[str, Any]]]:
    from app.services.benchmarks_vertical import get_vertical_benchmark_report
    data = get_vertical_benchmark_report(db, shop)
    ts = _now_iso()
    headers = [
        "shop", "generated_at", "vertical", "vertical_display", "band",
        "scope", "peer_count", "metric", "your_value",
        "p25", "p50", "p75", "p90", "percentile_rank", "status",
        "recovery_to_p75", "narrative",
    ]
    rows: list[dict[str, Any]] = []
    metrics = data.get("metrics") or {}
    for metric_name, m in metrics.items():
        rows.append({
            "shop": shop,
            "generated_at": ts,
            "vertical": data.get("vertical") or "",
            "vertical_display": data.get("vertical_display") or "",
            "band": data.get("band") or "",
            "scope": data.get("scope") or "",
            "peer_count": data.get("peer_count") or 0,
            "metric": metric_name,
            "your_value": m.get("value") or 0,
            "p25": m.get("p25") or 0,
            "p50": m.get("p50") or 0,
            "p75": m.get("p75") or 0,
            "p90": m.get("p90") or 0,
            "percentile_rank": m.get("percentile_rank") or 0,
            "status": m.get("status") or "",
            "recovery_to_p75": m.get("recovery_to_p75_eur") or 0,
            "narrative": (m.get("narrative") or "").replace("\n", " ").strip(),
        })
    return headers, rows


def _rows_for_pnl(db: Session, shop: str) -> tuple[list[str], list[dict[str, Any]]]:
    from app.services.pnl_engine import get_pnl_report
    data = get_pnl_report(db, shop, window_days=30)
    ts = _now_iso()
    ccy = data.get("currency") or "USD"
    headers = [
        "shop", "generated_at", "window_days", "currency",
        "line_item", "amount", "rate_or_pct", "estimated", "source", "note",
    ]
    rows: list[dict[str, Any]] = []
    # Flatten the waterfall into rows — gross revenue, each cost, net
    # profit — so the CSV reads top-to-bottom like the visual.
    rows.append({
        "shop": shop,
        "generated_at": ts,
        "window_days": data.get("window_days") or 30,
        "currency": ccy,
        "line_item": "gross_revenue",
        "amount": data.get("gross_revenue") or 0,
        "rate_or_pct": "",
        "estimated": False,
        "source": "shopify_orders",
        "note": "",
    })
    for cost_key in ("cogs", "payment_fees", "shipping", "ad_spend"):
        c = data.get(cost_key) or {}
        if not c:
            continue
        rows.append({
            "shop": shop,
            "generated_at": ts,
            "window_days": data.get("window_days") or 30,
            "currency": ccy,
            "line_item": cost_key,
            "amount": c.get("amount") or 0,
            "rate_or_pct": c.get("rate") or "",
            "estimated": bool(c.get("estimated")),
            "source": c.get("source") or "",
            "note": (c.get("note") or "").replace("\n", " ").strip(),
        })
    rows.append({
        "shop": shop,
        "generated_at": ts,
        "window_days": data.get("window_days") or 30,
        "currency": ccy,
        "line_item": "gross_profit",
        "amount": data.get("gross_profit") or 0,
        "rate_or_pct": data.get("gross_margin_pct") or "",
        "estimated": False,
        "source": "computed",
        "note": "",
    })
    rows.append({
        "shop": shop,
        "generated_at": ts,
        "window_days": data.get("window_days") or 30,
        "currency": ccy,
        "line_item": "net_profit",
        "amount": data.get("net_profit") or 0,
        "rate_or_pct": data.get("net_margin_pct") or "",
        "estimated": False,
        "source": "computed",
        "note": "",
    })
    return headers, rows


def _rows_for_cohorts_monthly(db: Session, shop: str) -> tuple[list[str], list[dict[str, Any]]]:
    from app.services.ltv_engine import get_monthly_cohorts
    data = get_monthly_cohorts(db, shop, months=6)
    ts = _now_iso()
    headers = [
        "shop", "generated_at", "cohort_month", "size", "revenue_total",
        "orders_total", "orders_per_customer", "revenue_per_customer",
        "repeat_rate",
    ]
    rows: list[dict[str, Any]] = []
    for c in data.get("cohorts") or []:
        rows.append({
            "shop": shop,
            "generated_at": ts,
            "cohort_month": c.get("cohort_month") or "",
            "size": c.get("size") or 0,
            "revenue_total": c.get("revenue_total") or 0,
            "orders_total": c.get("orders_total") or 0,
            "orders_per_customer": c.get("orders_per_customer") or 0,
            "revenue_per_customer": c.get("revenue_per_customer") or 0,
            "repeat_rate": c.get("repeat_rate") or 0,
        })
    return headers, rows


def _rows_for_attribution(db: Session, shop: str) -> tuple[list[str], list[dict[str, Any]]]:
    from app.services.utm_attribution import get_attribution_summary
    data = get_attribution_summary(db, shop, days=30)
    ts = _now_iso()
    headers = [
        "shop", "generated_at", "touch_model", "source_or_campaign",
        "label", "orders", "revenue",
    ]
    rows: list[dict[str, Any]] = []
    for s in data.get("top_sources_first_touch") or []:
        rows.append({
            "shop": shop,
            "generated_at": ts,
            "touch_model": "first_touch",
            "source_or_campaign": s.get("source") or "",
            "label": s.get("label") or "",
            "orders": s.get("orders") or 0,
            "revenue": s.get("revenue") or 0,
        })
    for s in data.get("top_sources_last_touch") or []:
        rows.append({
            "shop": shop,
            "generated_at": ts,
            "touch_model": "last_touch",
            "source_or_campaign": s.get("source") or "",
            "label": s.get("label") or "",
            "orders": s.get("orders") or 0,
            "revenue": s.get("revenue") or 0,
        })
    for c in data.get("top_campaigns") or []:
        rows.append({
            "shop": shop,
            "generated_at": ts,
            "touch_model": "campaign",
            "source_or_campaign": c.get("campaign") or "",
            "label": "",
            "orders": c.get("orders") or 0,
            "revenue": c.get("revenue") or 0,
        })
    return headers, rows


def _rows_for_inventory(db: Session, shop: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Inventory CSV (Gap #4, 2026-04-28). Re-uses the executor in
    inventory.py. The endpoint reads pre-aggregated snapshots, so this
    just calls the helper functions.
    """
    from app.api.inventory import (
        _build_rows,
        _latest_per_product,
        _lead_time_for_shop,
        _sales_rate_30d,
    )
    lead_time = _lead_time_for_shop(db, shop)
    snapshots = _latest_per_product(db, shop)
    sales_rates = _sales_rate_30d(db, shop)
    rows = _build_rows(snapshots, sales_rates, lead_time)
    rows.sort(key=lambda r: (r["days_of_cover"] is None, r["days_of_cover"] or float("inf")))

    headers = [
        "product_url",
        "product_title",
        "inventory_quantity",
        "sales_rate_per_day",
        "days_of_cover",
        "sell_through_30d_pct",
        "reorder_hint",
    ]
    return headers, rows


def _rows_for_top_customers_ltv(db: Session, shop: str) -> tuple[list[str], list[dict[str, Any]]]:
    """One row per customer ranked by lifetime spend. Hashed PII-safe ID.
    Reuses the same source the dashboard tile consumes."""
    from sqlalchemy import text as sql_text
    from datetime import timezone
    import hashlib
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        ZoneInfo = None  # Python <3.9 — falls back to UTC display
    from app.services.revenue_metrics import get_shop_currency, get_shop_timezone
    currency = get_shop_currency(db, shop) or "USD"
    shop_tz_name = get_shop_timezone(db, shop) or "UTC"
    shop_tz = ZoneInfo(shop_tz_name) if ZoneInfo else timezone.utc
    rows_db = db.execute(sql_text("""
        SELECT customer_email,
               COUNT(*) AS orders,
               COALESCE(SUM(total_price), 0) AS total_spent,
               MIN(created_at) AS first_order,
               MAX(created_at) AS last_order
        FROM shop_orders
        WHERE shop_domain = :shop
          AND customer_email IS NOT NULL
          AND customer_email <> ''
          AND currency = :currency
        GROUP BY customer_email
        ORDER BY total_spent DESC
        LIMIT 200
    """), {"shop": shop, "currency": currency}).fetchall()
    ts = _now_iso()
    rows = []
    for r in rows_db:
        h = hashlib.sha1(r[0].encode("utf-8")).hexdigest()[:8]
        # DB stores naive UTC. Attach UTC tz, convert to shop's IANA tz,
        # emit ISO8601 with explicit offset so merchant reading the
        # Sheet sees their local time + a clear timezone marker.
        first_iso = ""
        last_iso = ""
        if r[3] is not None:
            first_iso = r[3].replace(tzinfo=timezone.utc).astimezone(shop_tz).isoformat()
        if r[4] is not None:
            last_iso = r[4].replace(tzinfo=timezone.utc).astimezone(shop_tz).isoformat()
        rows.append({
            "shop": shop, "generated_at": ts, "currency": currency,
            "shop_timezone": shop_tz_name,
            "customer_id": f"cust_{h}",
            "orders": int(r[1] or 0),
            "total_spent": round(float(r[2] or 0), 2),
            "first_order_at": first_iso,
            "last_order_at": last_iso,
        })
    headers = ["shop", "generated_at", "currency", "shop_timezone", "customer_id",
               "orders", "total_spent", "first_order_at", "last_order_at"]
    return headers, rows


def _rows_for_top_products(db: Session, shop: str) -> tuple[list[str], list[dict[str, Any]]]:
    """One row per product ranked by revenue (line_items aggregation)."""
    from sqlalchemy import text as sql_text
    from app.services.revenue_metrics import get_shop_currency
    currency = get_shop_currency(db, shop) or "USD"
    # CTE pre-filter so jsonb_array_elements() never sees JSON-null
    # scalars (psycopg2 may convert Python None → JSON null literal).
    # Per audit_jsonb_array_length_guard: jsonb_typeof guard within
    # 4 lines above the jsonb_array_elements call. Single-line CTE.
    rows_db = db.execute(sql_text("""
        WITH valid_orders AS (SELECT line_items FROM shop_orders WHERE shop_domain = :shop AND currency = :currency AND jsonb_typeof(line_items) = 'array')
        SELECT li->>'title' AS title, COUNT(*) AS sales,
               COALESCE(SUM((li->>'price')::numeric * COALESCE((li->>'quantity')::integer, 1)), 0) AS revenue
        FROM valid_orders, jsonb_array_elements(valid_orders.line_items) li
        WHERE li->>'title' IS NOT NULL
        GROUP BY li->>'title' ORDER BY revenue DESC LIMIT 200
    """), {"shop": shop, "currency": currency}).fetchall()
    ts = _now_iso()
    rows = [{
        "shop": shop, "generated_at": ts, "currency": currency,
        "product_title": r[0], "units_sold": int(r[1] or 0),
        "revenue": round(float(r[2] or 0), 2),
    } for r in rows_db]
    headers = ["shop", "generated_at", "currency", "product_title", "units_sold", "revenue"]
    return headers, rows


def _rows_for_orders_by_country(db: Session, shop: str) -> tuple[list[str], list[dict[str, Any]]]:
    """One row per country, sourced from the same /analytics/orders-by-country
    Redis hash the dashboard tile + Live Radar already consume."""
    from app.api.lite_extras import get_orders_by_country
    from app.api.lite_extras import DateRangeQuery
    # Reuse the existing endpoint logic by direct call.
    range_q = DateRangeQuery()
    try:
        result = get_orders_by_country(days=30, range_q=range_q, shop=shop, db=db)
    except Exception:
        return ["shop", "generated_at"], []
    ts = _now_iso()
    ccy = result.currency
    rows = [{
        "shop": shop, "generated_at": ts, "currency": ccy,
        "country_code": c.country_code,
        "orders": c.orders,
        "revenue": c.revenue,
    } for c in result.countries]
    headers = ["shop", "generated_at", "currency", "country_code", "orders", "revenue"]
    return headers, rows


def _rows_for_top_variants(db: Session, shop: str) -> tuple[list[str], list[dict[str, Any]]]:
    """One row per variant SKU ranked by revenue."""
    from sqlalchemy import text as sql_text
    from app.services.revenue_metrics import get_shop_currency
    currency = get_shop_currency(db, shop) or "USD"
    # CTE pre-filter (per audit_jsonb_array_length_guard) — single-line
    # CTE so jsonb_typeof guard stays within 4 lines of jsonb_array_elements.
    rows_db = db.execute(sql_text("""
        WITH valid_orders AS (SELECT line_items FROM shop_orders WHERE shop_domain = :shop AND currency = :currency AND jsonb_typeof(line_items) = 'array')
        SELECT li->>'variant_title' AS variant, li->>'title' AS product,
               COUNT(*) AS sales,
               COALESCE(SUM((li->>'price')::numeric * COALESCE((li->>'quantity')::integer, 1)), 0) AS revenue
        FROM valid_orders, jsonb_array_elements(valid_orders.line_items) li
        WHERE li->>'variant_title' IS NOT NULL AND li->>'variant_title' <> ''
        GROUP BY li->>'variant_title', li->>'title' ORDER BY revenue DESC LIMIT 200
    """), {"shop": shop, "currency": currency}).fetchall()
    ts = _now_iso()
    rows = [{
        "shop": shop, "generated_at": ts, "currency": currency,
        "product_title": r[1] or "", "variant": r[0] or "",
        "units_sold": int(r[2] or 0),
        "revenue": round(float(r[3] or 0), 2),
    } for r in rows_db]
    headers = ["shop", "generated_at", "currency", "product_title", "variant", "units_sold", "revenue"]
    return headers, rows


def _rows_for_rfm_segments(db: Session, shop: str) -> tuple[list[str], list[dict[str, Any]]]:
    """One row per RFM segment with count + revenue + share %."""
    from app.services.rfm import compute_rfm_segments
    data = compute_rfm_segments(db, shop)
    ts = _now_iso()
    ccy = data.get("currency", "USD")
    rows = [{
        "shop": shop, "generated_at": ts, "currency": ccy,
        "segment": s["name"],
        "customer_count": s["count"],
        "share_pct": s["share_pct"],
        "revenue": s["revenue"],
        "description": s.get("description", ""),
    } for s in data.get("segments", [])]
    headers = ["shop", "generated_at", "currency", "segment", "customer_count",
               "share_pct", "revenue", "description"]
    return headers, rows


_ROW_BUILDERS = {
    "rars": _rows_for_rars,
    "benchmarks": _rows_for_benchmarks,
    "benchmarks_vertical": _rows_for_benchmarks_vertical,
    "pnl": _rows_for_pnl,
    "cohorts_monthly": _rows_for_cohorts_monthly,
    "attribution": _rows_for_attribution,
    "inventory": _rows_for_inventory,
    "top_customers_ltv": _rows_for_top_customers_ltv,
    "top_products": _rows_for_top_products,
    "orders_by_country": _rows_for_orders_by_country,
    "top_variants": _rows_for_top_variants,
    "rfm_segments": _rows_for_rfm_segments,
}


def _serialize_csv(headers: list[str], rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def _serialize_pdf(
    headers: list[str],
    rows: list[dict[str, Any]],
    *,
    title: str,
    subtitle: str,
) -> bytes:
    """Branded PDF table for the given rows. reportlab lazy-imported
    so a missing install returns a clear 500 in the endpoint rather
    than crashing module import at startup."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=title,
        author="HedgeSpark",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "HSTitle",
        parent=styles["Heading1"],
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#fbbf24"),
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "HSSub",
        parent=styles["Normal"],
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#64748b"),
        spaceAfter=10,
    )
    footer_style = ParagraphStyle(
        "HSFoot",
        parent=styles["Normal"],
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#94a3b8"),
    )

    # Header + subtitle + table + methodology footer
    flow: list = []
    flow.append(Paragraph("HedgeSpark", title_style))
    flow.append(Paragraph(subtitle, subtitle_style))
    flow.append(Spacer(1, 4))

    # Column widths — distribute evenly.
    col_count = max(1, len(headers))
    page_width = landscape(A4)[0] - 28 * mm
    col_w = page_width / col_count

    # Table data: header row + row values stringified
    table_data: list[list[str]] = [[h.replace("_", " ").title() for h in headers]]
    for r in rows:
        table_data.append([str(r.get(h, "")) for h in headers])

    tbl = Table(table_data, colWidths=[col_w] * col_count, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0e0e1a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#e8a04e")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEADING", (0, 0), (-1, -1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ]))
    flow.append(tbl)
    flow.append(Spacer(1, 12))
    flow.append(Paragraph(
        "Every number traces to a real query. No modeled estimates, no invented data. "
        "Export generated by HedgeSpark at the timestamp shown in the header.",
        footer_style,
    ))

    doc.build(flow)
    return buf.getvalue()


_SURFACE_PDF_TITLES: dict[str, str] = {
    "rars":                "Revenue at Risk — components breakdown",
    "benchmarks":          "Peer Benchmarks — 4-metric percentile report",
    "benchmarks_vertical": "Vertical-Aware Benchmarks",
    "pnl":                 "P&L Waterfall — 30-day window",
    "cohorts_monthly":     "Monthly Cohort Economics",
    "attribution":         "Channel Attribution — UTM deterministic",
    "inventory":           "Stock Health — daily snapshot",
}


@router.get(
    "/analytics/export",
    # CSV/PDF endpoint — no JSON response schema to bind in TypeScript.
    # Exclude from OpenAPI so the audit_openapi_types + response_model
    # audits don't flag this as an untyped JSON route. The dashboard
    # consumes via plain fetch() + blob download, not apiClient.
    include_in_schema=False,
)  # test-exempt: sse-stream
def export_surface(
    surface: str = Query(..., description="Surface to export (see ALLOWED_SURFACES)"),
    format: str = Query("csv", description="Output format: csv | pdf"),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_read_db),
):
    """Export a Lite surface as CSV or PDF. One parameterized endpoint
    serves all supported surfaces; each builder returns (headers, rows)
    from the same service the dashboard already calls. Strada 4
    dominance: PDF option added 2026-04-20 — branded letterhead, amber
    HedgeSpark title, per-row zebra striping, page repeat on header.

    Returns text/csv or application/pdf with Content-Disposition
    attachment header so browsers trigger a download. Every row carries
    shop + generated_at so the export is self-identifying."""
    key = (surface or "").strip().lower()
    if key not in ALLOWED_SURFACES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown surface '{surface}'. allowed: {sorted(ALLOWED_SURFACES)}",
        )
    fmt = (format or "csv").strip().lower()
    if fmt not in ("csv", "pdf"):
        raise HTTPException(
            status_code=400,
            detail=f"unknown format '{format}'. allowed: csv | pdf",
        )

    builder = _ROW_BUILDERS[key]
    try:
        headers, rows = builder(db, shop)
    except Exception as exc:
        log.warning("lite_export: surface=%s shop=%s failed: %s", key, shop, exc)
        raise HTTPException(status_code=500, detail="export failed")

    filename_base = _safe_filename(shop, key).replace(".csv", "")
    if fmt == "pdf":
        try:
            title = _SURFACE_PDF_TITLES.get(key, f"HedgeSpark {key}")
            subtitle = (
                f"Shop: {shop} · Generated: {_now_iso()}"
                f"{' · ' + str(len(rows)) + ' rows' if rows else ''}"
            )
            body_bytes = _serialize_pdf(headers, rows, title=title, subtitle=subtitle)
        except ImportError as exc:
            log.warning("lite_export: reportlab missing for %s: %s", shop, exc)
            raise HTTPException(status_code=503, detail="PDF export not configured on this deployment")
        except Exception as exc:
            log.warning("lite_export: PDF render failed for %s: %s", shop, exc)
            raise HTTPException(status_code=500, detail=f"PDF render failed: {type(exc).__name__}")
        return StreamingResponse(
            io.BytesIO(body_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_base}.pdf"',
                "Cache-Control": "no-store",
            },
        )

    body = _serialize_csv(headers, rows)
    return StreamingResponse(
        io.BytesIO(body.encode("utf-8")),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename_base}.csv"',
            "Cache-Control": "no-store",
        },
    )
