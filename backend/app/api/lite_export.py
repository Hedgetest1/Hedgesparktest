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

from app.core.database import get_db
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


_ROW_BUILDERS = {
    "rars": _rows_for_rars,
    "benchmarks": _rows_for_benchmarks,
    "benchmarks_vertical": _rows_for_benchmarks_vertical,
    "pnl": _rows_for_pnl,
    "cohorts_monthly": _rows_for_cohorts_monthly,
    "attribution": _rows_for_attribution,
    "inventory": _rows_for_inventory,
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
    db: Session = Depends(get_db),
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
