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

log = logging.getLogger(__name__)

router = APIRouter(tags=["lite_export"])


ALLOWED_SURFACES = {
    "rars",
    "benchmarks",
    "benchmarks_vertical",
    "pnl",
    "cohorts_monthly",
    "attribution",
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


_ROW_BUILDERS = {
    "rars": _rows_for_rars,
    "benchmarks": _rows_for_benchmarks,
    "benchmarks_vertical": _rows_for_benchmarks_vertical,
    "pnl": _rows_for_pnl,
    "cohorts_monthly": _rows_for_cohorts_monthly,
    "attribution": _rows_for_attribution,
}


def _serialize_csv(headers: list[str], rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


@router.get(
    "/analytics/export",
    # CSV endpoint — no JSON response schema to bind in TypeScript.
    # Exclude from OpenAPI so the audit_openapi_types + response_model
    # audits don't flag this as an untyped JSON route. The dashboard
    # consumes via plain fetch() + blob download, not apiClient.
    include_in_schema=False,
)
def export_surface_csv(
    surface: str = Query(..., description="Surface to export (see ALLOWED_SURFACES)"),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Export a Lite surface as CSV. One parameterized endpoint serves
    all supported surfaces; each builder returns (headers, rows) from
    the same service the dashboard already calls. Returns
    text/csv with a Content-Disposition attachment header so browsers
    trigger a download. Every row carries shop + generated_at so the
    export is self-identifying."""
    key = (surface or "").strip().lower()
    if key not in ALLOWED_SURFACES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown surface '{surface}'. allowed: {sorted(ALLOWED_SURFACES)}",
        )
    builder = _ROW_BUILDERS[key]
    try:
        headers, rows = builder(db, shop)
    except Exception as exc:
        log.warning("lite_export: surface=%s shop=%s failed: %s", key, shop, exc)
        raise HTTPException(status_code=500, detail="export failed")

    body = _serialize_csv(headers, rows)
    filename = _safe_filename(shop, key)
    return StreamingResponse(
        io.BytesIO(body.encode("utf-8")),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
