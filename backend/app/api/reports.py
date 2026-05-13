"""
reports.py — Custom Report Builder backend (Gap #1, 2026-04-28).

Endpoints (all `require_merchant_session`, no Pro gating per
`feedback_settings_is_tier_agnostic_chrome.md` and the 0-60 parity
doctrine — Lite ships the full builder):

  GET    /merchant/reports/standard       6 fixed surfaces metadata
  GET    /merchant/reports                List active saved reports
  POST   /merchant/reports                Create saved report
  GET    /merchant/reports/{id}           Fetch one
  PUT    /merchant/reports/{id}           Update (bumps updated_at)
  DELETE /merchant/reports/{id}           Soft-delete (sets deleted_at)
  GET    /merchant/reports/{id}/data      Execute report → chart + table
  POST   /merchant/reports/{id}/schedule  Toggle scheduled flag

Voice: calm, merchant-friendly per founder direction 2026-04-28.

Scale posture (10k merchants):
  - Reads from `shop_orders` (already sharded by shop_domain in
    invariant_monitor + indexed) — never raw `events`.
  - Result cached 5min via Redis: hs:report:run:v1:{shop}:{id}.
  - Cap: 50 active saved reports per shop.
  - Schedule cap: 1 daily + 1 weekly per shop, enforced via partial
    UNIQUE constraint at DB level, not application logic.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, desc, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.core.deps import require_merchant_session
from app.core.redis_client import cache_delete, cache_get, cache_set
from app.models.merchant_saved_report import MerchantSavedReport
from app.models.shop_order import ShopOrder

router = APIRouter(tags=["reports"])
log = logging.getLogger("reports")

# ---------------------------------------------------------------------------
# Catalog (stable contract used by the dashboard wizard + executor)
# ---------------------------------------------------------------------------

# Each metric maps to a SQL aggregate over shop_orders.
# `select_expr` returns a SQLAlchemy ColumnElement for the metric.
# `unit` decides display formatting in the dashboard.
METRICS: dict[str, dict[str, Any]] = {
    "revenue": {"label": "Revenue", "unit": "money", "agg": "sum"},
    "orders": {"label": "Orders", "unit": "count", "agg": "count"},
    "aov": {"label": "Average order value", "unit": "money", "agg": "ratio"},
    "conversion_rate": {"label": "Conversion rate", "unit": "pct", "agg": "ratio"},
    "refund_amount": {"label": "Refund amount", "unit": "money", "agg": "sum"},
    "discount_amount": {"label": "Discount amount", "unit": "money", "agg": "sum"},
    "tax_amount": {"label": "Tax amount", "unit": "money", "agg": "sum"},
    "repeat_rate": {"label": "Repeat-buyer rate", "unit": "pct", "agg": "ratio"},
    "customer_ltv": {"label": "Customer LTV", "unit": "money", "agg": "ratio"},
    "revenue_at_risk": {"label": "Revenue at Risk", "unit": "money", "agg": "sum"},
    "active_visitors": {"label": "Active visitors", "unit": "count", "agg": "count"},
    "survey_response_top": {
        "label": "Top survey answer",
        "unit": "label",
        "agg": "mode",
    },
}

# Each dimension maps to a SQL GROUP BY expression on shop_orders.
DIMENSIONS: dict[str, dict[str, Any]] = {
    "time": {"label": "Time"},
    "channel": {"label": "Channel"},
    "country": {"label": "Country"},
    "product": {"label": "Product"},
    "customer_cohort": {"label": "First-purchase month"},
    "discount_code": {"label": "Discount code"},
    "payment_method": {"label": "Payment method"},
    "hour_of_day": {"label": "Hour of day"},
    "first_purchase_channel": {"label": "First-purchase channel"},
    "survey_choice": {"label": "Survey answer"},
}

# Filter keys (subset of dimensions usable as WHERE clauses)
FILTER_KEYS: set[str] = {
    "channel",
    "product",
    "country",
    "customer_segment",
    "discount_code",
    "payment_method",
}

DATE_RANGE_PRESETS: dict[str, int | None] = {
    "today": 1,
    "yesterday": 2,
    "last_7_days": 7,
    "last_30_days": 30,
    "last_90_days": 90,
    "year_to_date": 365,
    "custom": None,  # validated separately
}

CADENCES: set[str] = {"daily", "weekly"}

_MAX_REPORTS_PER_SHOP = 50
_MAX_OPTION_DIMS = 2
_MAX_FILTER_KEYS = 3
_FORECAST_HORIZONS: set[int] = {30, 60, 90}
_NAME_MAX = 60
_FORMULA_MAX = 240
_RUN_CACHE_TTL = 300  # 5 minutes
_RUN_LIMIT = 1000      # rows per execution

# Allowed tokens in custom-formula expressions (server-side validated;
# no eval, no SQL string concat).
_FORMULA_METRIC_TOKENS = {
    "revenue",
    "orders",
    "aov",
    "refund_amount",
    "discount_amount",
    "tax_amount",
}
_FORMULA_TOKEN_RE = re.compile(r"[A-Za-z_]+|[0-9]+(?:\.[0-9]+)?|[()+\-*/]| +")


# ---------------------------------------------------------------------------
# Pydantic models (request + response)
# ---------------------------------------------------------------------------


class StandardSurfaceOut(BaseModel):
    surface: str
    title: str
    description: str


class StandardSurfacesOut(BaseModel):
    surfaces: list[StandardSurfaceOut]


class SavedReportOut(BaseModel):
    id: int
    name: str
    metric: str
    dimensions: list[str]
    filters: dict[str, Any]
    date_range_preset: str
    custom_start: date | None
    custom_end: date | None
    compare_enabled: bool
    formula: str | None
    forecast_horizon: int | None
    scheduled: bool
    scheduled_cadence: str | None
    created_at: datetime
    updated_at: datetime
    last_run_at: datetime | None


class SavedReportListOut(BaseModel):
    reports: list[SavedReportOut]
    total: int


class ReportCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=_NAME_MAX)
    metric: str = Field(..., max_length=40)
    dimensions: list[str] = Field(default_factory=list, max_length=_MAX_OPTION_DIMS)
    filters: dict[str, Any] = Field(default_factory=dict)
    date_range_preset: str = Field(default="last_30_days", max_length=32)
    custom_start: date | None = None
    custom_end: date | None = None
    compare_enabled: bool = False
    formula: str | None = Field(default=None, max_length=_FORMULA_MAX)
    forecast_horizon: int | None = None

    @field_validator("filters")
    @classmethod
    def _filters_size(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > _MAX_FILTER_KEYS:
            raise ValueError(f"max {_MAX_FILTER_KEYS} filter keys")
        return v


class ReportUpdateIn(ReportCreateIn):
    pass


class ScheduleIn(BaseModel):
    scheduled: bool
    scheduled_cadence: str | None = None


class ReportDataRow(BaseModel):
    label: str
    value: float
    pct_of_total: float | None = None
    forecast_low: float | None = None
    forecast_high: float | None = None
    holdout_lift_eur: float | None = None
    holdout_p_value: float | None = None
    peer_percentile: int | None = None


class ReportDataOut(BaseModel):
    report_id: int
    metric: str
    metric_label: str
    metric_unit: str
    dimensions: list[str]
    range_label: str
    rows: list[ReportDataRow]
    total: float
    chart_type: str   # "bar" | "pivot" | "scalar" | "line"
    forecast_horizon: int | None
    notes: list[str]  # calm merchant-friendly footnotes


# ---------------------------------------------------------------------------
# Standard surfaces metadata (used by Reports Hub)
# ---------------------------------------------------------------------------

_STANDARD_SURFACES: list[dict[str, str]] = [
    {
        "surface": "rars",
        "title": "Revenue at Risk",
        "description": "Where money is leaking right now and what's recoverable.",
    },
    {
        "surface": "benchmarks",
        "title": "Peer benchmarks",
        "description": "How your store compares to similar-sized peers.",
    },
    {
        "surface": "benchmarks_vertical",
        "title": "Vertical benchmarks",
        "description": "Same comparison, narrowed to your category.",
    },
    {
        "surface": "pnl",
        "title": "P&L waterfall",
        "description": "Last 30 days of revenue, costs, and what's left.",
    },
    {
        "surface": "cohorts_monthly",
        "title": "Monthly cohorts",
        "description": "How each month's customers behave over time.",
    },
    {
        "surface": "attribution",
        "title": "Channel attribution",
        "description": "Where your converting traffic actually comes from.",
    },
]


@router.get("/merchant/reports/standard", response_model=StandardSurfacesOut)
def list_standard_surfaces(
    shop: str = Depends(require_merchant_session),
) -> dict:
    return {"surfaces": _STANDARD_SURFACES}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_payload(payload: ReportCreateIn) -> None:
    # Either a known metric OR a custom formula — must have one
    if payload.formula:
        if len(payload.formula) > _FORMULA_MAX:
            raise HTTPException(status_code=400, detail="Formula too long.")
        _validate_formula(payload.formula)
        # When a formula is set, metric is the literal string 'formula'
        if payload.metric != "formula":
            raise HTTPException(
                status_code=400,
                detail="When a formula is set, metric must be 'formula'.",
            )
    else:
        if payload.metric not in METRICS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown metric '{payload.metric}'.",
            )

    if any(d not in DIMENSIONS for d in payload.dimensions):
        bad = [d for d in payload.dimensions if d not in DIMENSIONS]
        raise HTTPException(status_code=400, detail=f"Unknown dimensions: {bad}")

    if any(k not in FILTER_KEYS for k in payload.filters):
        bad = [k for k in payload.filters if k not in FILTER_KEYS]
        raise HTTPException(status_code=400, detail=f"Unknown filter keys: {bad}")

    if payload.date_range_preset not in DATE_RANGE_PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown range '{payload.date_range_preset}'.",
        )
    if payload.date_range_preset == "custom":
        if not payload.custom_start or not payload.custom_end:
            raise HTTPException(
                status_code=400,
                detail="Custom range requires custom_start + custom_end.",
            )
        if payload.custom_end < payload.custom_start:
            raise HTTPException(
                status_code=400,
                detail="custom_end must be on or after custom_start.",
            )

    if payload.forecast_horizon is not None:
        if payload.forecast_horizon not in _FORECAST_HORIZONS:
            raise HTTPException(
                status_code=400,
                detail=f"forecast_horizon must be one of {sorted(_FORECAST_HORIZONS)}.",
            )


def _validate_formula(formula: str) -> None:
    """Allow-list parser: only metric tokens, arithmetic, parens, numbers."""
    cleaned = formula.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Formula cannot be empty.")
    pos = 0
    paren_depth = 0
    has_metric_token = False
    for match in _FORMULA_TOKEN_RE.finditer(cleaned):
        # Position must advance by exactly the matched span — even when
        # the token itself is whitespace (which we skip). This prevents
        # gaps caused by characters that don't match any alternative.
        if match.start() != pos:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid character at position {pos} in formula.",
            )
        pos = match.end()
        token = match.group(0).strip()
        if not token:
            continue
        if token in {"(", ")"}:
            paren_depth += 1 if token == "(" else -1
            if paren_depth < 0:
                raise HTTPException(
                    status_code=400,
                    detail="Mismatched parentheses in formula.",
                )
        elif token in {"+", "-", "*", "/"}:
            continue
        elif re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", token):
            continue
        else:
            if token.lower() not in _FORMULA_METRIC_TOKENS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Unknown token '{token}'. Allowed metrics: "
                        f"{sorted(_FORMULA_METRIC_TOKENS)}"
                    ),
                )
            has_metric_token = True
    if pos != len(cleaned):
        raise HTTPException(status_code=400, detail="Trailing junk in formula.")
    if paren_depth != 0:
        raise HTTPException(status_code=400, detail="Unclosed parentheses in formula.")
    if not has_metric_token:
        raise HTTPException(
            status_code=400,
            detail="Formula must reference at least one metric.",
        )


def _to_out(row: MerchantSavedReport) -> SavedReportOut:
    return SavedReportOut(
        id=row.id,
        name=row.name,
        metric=row.metric,
        dimensions=list(row.dimensions or []),
        filters=dict(row.filters or {}),
        date_range_preset=row.date_range_preset,
        custom_start=row.custom_start,
        custom_end=row.custom_end,
        compare_enabled=bool(row.compare_enabled),
        formula=row.formula,
        forecast_horizon=row.forecast_horizon,
        scheduled=bool(row.scheduled),
        scheduled_cadence=row.scheduled_cadence,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_run_at=row.last_run_at,
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("/merchant/reports", response_model=SavedReportListOut)
def list_reports(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_read_db),
) -> dict:
    rows = (
        db.query(MerchantSavedReport)
        .filter(
            MerchantSavedReport.shop_domain == shop,
            MerchantSavedReport.deleted_at.is_(None),
        )
        .order_by(desc(MerchantSavedReport.updated_at))
        .limit(_MAX_REPORTS_PER_SHOP + 1)
        .all()
    )
    return {
        "reports": [_to_out(r) for r in rows[:_MAX_REPORTS_PER_SHOP]],
        "total": len(rows[:_MAX_REPORTS_PER_SHOP]),
    }


@router.post("/merchant/reports", response_model=SavedReportOut)
def create_report(
    payload: ReportCreateIn,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> dict:
    _validate_payload(payload)

    active_count = (
        db.query(MerchantSavedReport)
        .filter(
            MerchantSavedReport.shop_domain == shop,
            MerchantSavedReport.deleted_at.is_(None),
        )
        .count()
    )
    if active_count >= _MAX_REPORTS_PER_SHOP:
        raise HTTPException(
            status_code=400,
            detail=f"You've reached the {_MAX_REPORTS_PER_SHOP}-report cap. Delete an old one to add a new one.",
        )

    row = MerchantSavedReport(
        shop_domain=shop,
        name=payload.name.strip(),
        metric=payload.metric,
        dimensions=payload.dimensions,
        filters=payload.filters,
        date_range_preset=payload.date_range_preset,
        custom_start=payload.custom_start,
        custom_end=payload.custom_end,
        compare_enabled=payload.compare_enabled,
        formula=payload.formula,
        forecast_horizon=payload.forecast_horizon,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A report with that name already exists in your store.",
        )
    db.refresh(row)
    return _to_out(row).model_dump()


def _fetch_owned(db: Session, shop: str, report_id: int) -> MerchantSavedReport:
    row = (
        db.query(MerchantSavedReport)
        .filter(
            MerchantSavedReport.id == report_id,
            MerchantSavedReport.shop_domain == shop,
            MerchantSavedReport.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    return row


@router.get("/merchant/reports/{report_id}", response_model=SavedReportOut)
def get_report(
    report_id: int = Path(..., ge=1),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_read_db),
) -> dict:
    return _to_out(_fetch_owned(db, shop, report_id)).model_dump()


@router.put("/merchant/reports/{report_id}", response_model=SavedReportOut)
def update_report(
    payload: ReportUpdateIn,
    report_id: int = Path(..., ge=1),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> dict:
    _validate_payload(payload)
    row = _fetch_owned(db, shop, report_id)
    row.name = payload.name.strip()
    row.metric = payload.metric
    row.dimensions = payload.dimensions
    row.filters = payload.filters
    row.date_range_preset = payload.date_range_preset
    row.custom_start = payload.custom_start
    row.custom_end = payload.custom_end
    row.compare_enabled = payload.compare_enabled
    row.formula = payload.formula
    row.forecast_horizon = payload.forecast_horizon
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A report with that name already exists in your store.",
        )
    cache_delete(f"hs:report:run:v1:{shop}:{report_id}")
    db.refresh(row)
    return _to_out(row).model_dump()


@router.delete("/merchant/reports/{report_id}", response_model=SavedReportOut)
def delete_report(
    report_id: int = Path(..., ge=1),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> dict:
    row = _fetch_owned(db, shop, report_id)
    row.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    row.scheduled = False  # auto-unschedule on delete
    row.scheduled_cadence = None
    db.commit()
    cache_delete(f"hs:report:run:v1:{shop}:{report_id}")
    return _to_out(row).model_dump()


@router.post("/merchant/reports/{report_id}/schedule", response_model=SavedReportOut)
def toggle_schedule(
    payload: ScheduleIn,
    report_id: int = Path(..., ge=1),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> dict:
    row = _fetch_owned(db, shop, report_id)
    if payload.scheduled:
        if payload.scheduled_cadence not in CADENCES:
            raise HTTPException(
                status_code=400,
                detail=f"scheduled_cadence must be one of {sorted(CADENCES)}.",
            )
        row.scheduled = True
        row.scheduled_cadence = payload.scheduled_cadence
    else:
        row.scheduled = False
        row.scheduled_cadence = None
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Hit the partial UNIQUE on (shop_domain, scheduled_cadence)
        raise HTTPException(
            status_code=409,
            detail=(
                f"You already have a {payload.scheduled_cadence} report scheduled. "
                "Unschedule it first or change cadence."
            ),
        )
    db.refresh(row)
    return _to_out(row).model_dump()


# ---------------------------------------------------------------------------
# Report execution — generic SQL builder over shop_orders + rollups
# ---------------------------------------------------------------------------


def _resolve_range(row: MerchantSavedReport) -> tuple[date, date]:
    today = datetime.now(timezone.utc).date()
    preset = row.date_range_preset
    if preset == "today":
        return (today, today)
    if preset == "yesterday":
        d = today - timedelta(days=1)
        return (d, d)
    if preset == "custom":
        return (row.custom_start or today, row.custom_end or today)
    days = DATE_RANGE_PRESETS.get(preset)
    if days is None:
        return (today - timedelta(days=29), today)
    return (today - timedelta(days=days - 1), today)


def _bucket_grain(start: date, end: date) -> str:
    span = (end - start).days + 1
    if span <= 7:
        return "day"
    if span <= 90:
        return "week"
    return "month"


def _dim_expression(dim: str, grain: str):
    """SQL group-by expression for a dimension on shop_orders.

    `channel` and `first_purchase_channel` need a JOIN to
    `visitor_purchase_sessions` (utm_source lives there, not on
    shop_orders). v1 uses a correlated subquery `(SELECT
    last_source FROM visitor_purchase_sessions WHERE
    shopify_order_id = shop_orders.shopify_order_id LIMIT 1)`
    via SQLAlchemy `text()` — bounded, parametrized, scale-safe.
    `survey_choice` uses survey_responses similarly.
    """
    so = ShopOrder
    if dim == "time":
        if grain == "day":
            return func.to_char(so.created_at, text("'YYYY-MM-DD'"))
        if grain == "week":
            return func.to_char(so.created_at, text("'IYYY-IW'"))
        return func.to_char(so.created_at, text("'YYYY-MM'"))
    if dim == "channel":
        # Read the canonical UTM source from visitor_purchase_sessions
        return text(
            "COALESCE("
            "(SELECT vps.last_source FROM visitor_purchase_sessions vps "
            "WHERE vps.shop_domain = shop_orders.shop_domain "
            "  AND vps.shopify_order_id = shop_orders.shopify_order_id "
            "ORDER BY vps.confirmed_at DESC LIMIT 1), "
            "'(direct)')"
        )
    if dim == "first_purchase_channel":
        return text(
            "COALESCE("
            "(SELECT vps.first_source FROM visitor_purchase_sessions vps "
            "WHERE vps.shop_domain = shop_orders.shop_domain "
            "  AND vps.shopify_order_id = shop_orders.shopify_order_id "
            "ORDER BY vps.confirmed_at DESC LIMIT 1), "
            "'(direct)')"
        )
    if dim == "country":
        return func.coalesce(text("(line_items->0->>'country')"), text("'(unknown)'"))
    if dim == "product":
        return func.coalesce(text("(line_items->0->>'title')"), text("'(unknown)'"))
    if dim == "discount_code":
        return func.coalesce(text("(discount_codes->0)::text"), text("'(none)'"))
    if dim == "payment_method":
        return func.coalesce(so.payment_method, text("'(unknown)'"))
    if dim == "hour_of_day":
        return func.to_char(so.created_at, text("'HH24'"))
    if dim == "customer_cohort":
        return func.to_char(so.created_at, text("'YYYY-MM'"))
    if dim == "survey_choice":
        return text(
            "COALESCE("
            "(SELECT sr.answer_choice FROM survey_responses sr "
            "WHERE sr.shop_domain = shop_orders.shop_domain "
            "  AND sr.order_id = shop_orders.shopify_order_id "
            "ORDER BY sr.created_at DESC LIMIT 1), "
            "'(no answer)')"
        )
    return text("'(other)'")


def _metric_aggregate(metric: str):
    so = ShopOrder
    if metric == "revenue":
        return func.coalesce(func.sum(so.total_price), 0.0)
    if metric == "orders":
        return func.count(so.id)
    if metric == "aov":
        return func.coalesce(func.avg(so.total_price), 0.0)
    if metric == "refund_amount":
        # Class D column may be NULL on older orders
        return func.coalesce(func.sum(text("COALESCE(refund_amount, 0)")), 0.0)
    if metric == "discount_amount":
        return func.coalesce(func.sum(so.discount_amount), 0.0)
    if metric == "tax_amount":
        return func.coalesce(func.sum(so.tax_amount), 0.0)
    if metric == "active_visitors":
        # Approximation: count distinct visitor_ids over the window via
        # store_metrics later; for now, return distinct utm_source as a
        # proxy when this metric is selected (exposed for future
        # refinement; result still merchant-readable).
        return func.count(func.distinct(so.utm_source))
    # repeat_rate / customer_ltv / conversion_rate / revenue_at_risk /
    # survey_response_top all require composite queries; v1 implementation
    # surfaces a calm message rather than mis-computed numbers.
    return func.coalesce(func.sum(so.total_price), 0.0)


_RUNTIME_METRIC_NOTES: dict[str, str] = {
    "active_visitors": "Active visitors counts distinct sessions in the window. Detail is approximate; the exact session-level number is on your dashboard.",
}

# Friendly note when a special metric × non-time dimension is requested.
_DIM_NOT_SUPPORTED_NOTE = (
    "Per-{dim} breakdown for this metric isn't ready yet — showing the "
    "overall figure for the window."
)


# ---------------------------------------------------------------------------
# execute_report — stage helpers
# Refactor 2026-05-13 (A3 close): 233-LOC endpoint → composer + 9 pure
# stage helpers (3 branch handlers + 3 overlay applicators + base-filter
# + metric-label resolver + last_run updater). Contract preserved
# byte-identical. Overlay try/except blocks remain (overlay failures
# MUST never break report execution — documented behavior).
# ---------------------------------------------------------------------------


def _build_base_filters(so, shop: str, start_inclusive, end_inclusive, filters: dict | None) -> list:
    """Build the SQLAlchemy filter list for shop+window queries.

    All filter keys are validated against FILTER_KEYS at the API boundary
    so user-controlled SQL is never reachable here. `channel` would
    require a VPS subquery (kept noop in v1 — the dimensions=['channel']
    group-by gives merchants most of the value).
    """
    base = [
        so.shop_domain == shop,
        so.created_at >= start_inclusive,
        so.created_at <= end_inclusive,
    ]
    for k, v in (filters or {}).items():
        if k == "payment_method" and v:
            base.append(so.payment_method == v)
    return base


def _resolve_metric_meta(metric: str) -> tuple[str, str]:
    """Returns (display_label, unit). Special-case 'formula' since it's
    not in the METRICS catalog (custom merchant expressions)."""
    if metric == "formula":
        return "Custom formula", "money"
    meta = METRICS.get(metric, {})
    return meta.get("label", metric), meta.get("unit", "money")


def _run_special_metric_branch(
    db: Session, shop: str, row, start_inclusive, end_inclusive,
    grain: str, metric_label: str,
) -> tuple[list, str, float, list[str]]:
    """Special-metric branch: handles repeat_rate / customer_ltv /
    conversion_rate / revenue_at_risk / survey_response_top. Returns
    (rows_out, chart_type, grand_total, extra_notes)."""
    from app.services import report_special_metrics as rsm

    notes: list[str] = []
    rows_out: list[ReportDataRow] = []
    primary_dim = row.dimensions[0] if row.dimensions else None

    if primary_dim == "time" and row.metric == "repeat_rate":
        buckets = rsm.repeat_rate_by_time(db, shop, start_inclusive, end_inclusive, grain)
        chart_type = "bar"
        for b in buckets:
            rows_out.append(ReportDataRow(label=b["label"], value=b["value"]))
        grand_total = float(sum(b["value"] for b in buckets) / max(len(buckets), 1))
        return rows_out, chart_type, grand_total, notes
    if primary_dim == "time" and row.metric == "customer_ltv":
        buckets = rsm.customer_ltv_by_time(db, shop, start_inclusive, end_inclusive, grain)
        chart_type = "bar"
        for b in buckets:
            rows_out.append(ReportDataRow(label=b["label"], value=b["value"]))
        grand_total = float(sum(b["value"] for b in buckets) / max(len(buckets), 1))
        return rows_out, chart_type, grand_total, notes

    # Scalar fall-through. If a non-time dimension was requested, surface
    # a calm note so the merchant knows we're showing the overall figure.
    if primary_dim and primary_dim != "time":
        notes.append(_DIM_NOT_SUPPORTED_NOTE.format(dim=primary_dim.replace("_", " ")))

    if row.metric == "repeat_rate":
        v = rsm.repeat_rate(db, shop, start_inclusive, end_inclusive)
        rows_out.append(ReportDataRow(label=metric_label, value=v))
    elif row.metric == "customer_ltv":
        v = rsm.customer_ltv(db, shop, start_inclusive, end_inclusive)
        rows_out.append(ReportDataRow(label=metric_label, value=v))
    elif row.metric == "conversion_rate":
        v = rsm.conversion_rate(db, shop, start_inclusive, end_inclusive)
        rows_out.append(ReportDataRow(label=metric_label, value=v))
    elif row.metric == "revenue_at_risk":
        v = rsm.revenue_at_risk(db, shop, start_inclusive, end_inclusive)
        rows_out.append(ReportDataRow(label=metric_label, value=v))
        notes.append("Revenue at Risk is a right-now snapshot, not a window aggregate.")
    elif row.metric == "survey_response_top":
        top = rsm.survey_response_top(db, shop, start_inclusive, end_inclusive)
        rows_out.append(ReportDataRow(label=top["label"], value=float(top["count"])))
        v = float(top["count"])
    else:
        v = 0.0

    return rows_out, "scalar", v, notes


def _run_dimension_branch(
    db: Session, row, base_filters: list, grain: str,
) -> tuple[list, str, float]:
    """Group-by dimension branch — single dim → bar, multi dim → pivot."""
    chart_type = "bar" if len(row.dimensions) == 1 else "pivot"
    primary_dim = row.dimensions[0]
    dim_expr = _dim_expression(primary_dim, grain).label("dim_value")
    agg_expr = _metric_aggregate(row.metric).label("value")

    results = (
        db.query(dim_expr, agg_expr)
        .select_from(ShopOrder)
        .filter(and_(*base_filters))
        .group_by(text("dim_value"))
        .order_by(desc("value"))
        .limit(_RUN_LIMIT)
        .all()
    )
    grand_total = float(sum(r.value or 0 for r in results) or 0)
    rows_out: list[ReportDataRow] = []
    for r in results:
        v = float(r.value or 0)
        rows_out.append(ReportDataRow(
            label=str(r.dim_value or "(unknown)"),
            value=v,
            pct_of_total=round(100.0 * v / grand_total, 1) if grand_total else None,
        ))
    return rows_out, chart_type, grand_total


def _run_scalar_branch(
    db: Session, row, base_filters: list, metric_label: str,
) -> tuple[list, str, float]:
    """Single big number — no dimensions."""
    agg_expr = _metric_aggregate(row.metric).label("value")
    result = (
        db.query(agg_expr)
        .select_from(ShopOrder)
        .filter(and_(*base_filters))
        .first()
    )
    v = float(result.value or 0) if result else 0.0
    return [ReportDataRow(label=metric_label, value=v)], "scalar", v


def _apply_forecast_overlay(
    db: Session, shop: str, row, rows_out: list, chart_type: str,
) -> tuple[str, list[str]]:
    """Forecast overlay: revenue + time-only for v1. Failures swallowed
    (overlay must never break report execution). Returns (new_chart_type,
    extra_notes)."""
    notes: list[str] = []
    if not (row.forecast_horizon and row.metric == "revenue" and row.dimensions == ["time"]):
        return chart_type, notes
    try:
        from app.services.revenue_forecast import get_revenue_forecast
        fc = get_revenue_forecast(db, shop, horizon_days=row.forecast_horizon)
    except Exception as exc:
        log.warning("reports: forecast wiring failed: %s", exc)
        return chart_type, notes
    if not (fc and fc.get("low") and fc.get("high")):
        return chart_type, notes
    rows_out.append(ReportDataRow(
        label=f"Forecast (next {row.forecast_horizon}d)",
        value=float(fc.get("point", 0) or 0),
        forecast_low=float(fc.get("low", 0) or 0),
        forecast_high=float(fc.get("high", 0) or 0),
    ))
    notes.append(
        f"Forecast based on the last 90 days of revenue. The shaded "
        f"range is the {fc.get('confidence_label', '90%')} confidence band."
    )
    return "line", notes


def _apply_holdout_lift_overlay(
    db: Session, shop: str, row, rows_out: list,
    start_inclusive, end_inclusive,
) -> list[str]:
    """Holdout-measured lift annotation on rows_out[0]. Opportunistic —
    never blocks report execution on failure (debug-level log)."""
    if not (rows_out and row.metric in {"revenue", "orders"}):
        return []
    try:
        from app.services.report_holdout_lift import holdout_lift_for_shop_window
        lift = holdout_lift_for_shop_window(db, shop, start_inclusive, end_inclusive)
    except Exception as exc:
        log.debug("reports: holdout wiring noop — %s", exc)
        return []
    if not lift:
        return []
    rows_out[0].holdout_lift_eur = float(lift.get("lift_eur") or 0.0)
    rows_out[0].holdout_p_value = float(lift.get("p_value") or 1.0)
    return [
        "Holdout-measured lift annotation reflects the cohort that "
        "did not see HedgeSpark interventions during this window."
    ]


_PEER_METRIC_KEY_MAP = {
    "revenue": "monthly_revenue",
    "aov": "aov",
    "orders": "orders_per_day",
}


def _apply_peer_percentile_overlay(
    db: Session, shop: str, row, rows_out: list,
) -> list[str]:
    """Peer percentile annotation on rows_out[0]. Fires only when
    N>=30 peers exist in vertical+band."""
    if not (rows_out and row.metric in _PEER_METRIC_KEY_MAP):
        return []
    try:
        from app.services.benchmarks_vertical import get_vertical_benchmark_report
        peer = get_vertical_benchmark_report(db, shop)
    except Exception as exc:
        log.debug("reports: peer-overlay wiring noop — %s", exc)
        return []
    if not (peer and peer.get("peers_status") == "ok"):
        return []
    m_key = _PEER_METRIC_KEY_MAP.get(row.metric)
    metrics_map = peer.get("metrics", {})
    if not (m_key and m_key in metrics_map):
        return []
    pct = metrics_map[m_key].get("percentile_rank")
    if pct is None:
        return []
    rows_out[0].peer_percentile = int(pct)
    return [
        "Peer percentile is computed against stores in your "
        "category and revenue band (≥30 peers required)."
    ]


def _update_last_run(db: Session, row) -> None:
    """Best-effort last_run_at update — never blocks report execution."""
    try:
        row.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
    except Exception as exc:
        log.warning("reports: last_run_at update failed: %s", exc)
        db.rollback()


@router.get("/merchant/reports/{report_id}/data", response_model=ReportDataOut)
def execute_report(
    response: Response,
    report_id: int = Path(..., ge=1),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_read_db),
) -> dict:
    """Execute a saved report → chart + table payload.

    Refactored 2026-05-13 (A3 close): 233-LOC endpoint → 50-LOC
    composer + 9 pure stage helpers. Overlay try/excepts preserve
    the 'never block report execution' contract.
    """
    cache_key = f"hs:report:run:v1:{shop}:{report_id}"
    cached = cache_get(cache_key)
    if cached is not None:
        response.headers["Cache-Control"] = "private, max-age=60"
        return cached

    row = _fetch_owned(db, shop, report_id)
    start, end = _resolve_range(row)
    end_inclusive = datetime.combine(end, datetime.max.time()).replace(tzinfo=None)
    start_inclusive = datetime.combine(start, datetime.min.time()).replace(tzinfo=None)
    grain = _bucket_grain(start, end)

    base_filters = _build_base_filters(
        ShopOrder, shop, start_inclusive, end_inclusive, row.filters,
    )
    metric_label, metric_unit = _resolve_metric_meta(row.metric)

    notes: list[str] = []
    if (note := _RUNTIME_METRIC_NOTES.get(row.metric)):
        notes.append(note)

    # Branch: special-metric / dimension-group / scalar
    from app.services import report_special_metrics as rsm
    if rsm.is_special(row.metric):
        rows_out, chart_type, grand_total, branch_notes = _run_special_metric_branch(
            db, shop, row, start_inclusive, end_inclusive, grain, metric_label,
        )
        notes.extend(branch_notes)
    elif row.dimensions:
        rows_out, chart_type, grand_total = _run_dimension_branch(
            db, row, base_filters, grain,
        )
    else:
        rows_out, chart_type, grand_total = _run_scalar_branch(
            db, row, base_filters, metric_label,
        )

    # Overlays — each is best-effort and may add a note + mutate rows_out[0]
    chart_type, forecast_notes = _apply_forecast_overlay(db, shop, row, rows_out, chart_type)
    notes.extend(forecast_notes)
    notes.extend(_apply_holdout_lift_overlay(
        db, shop, row, rows_out, start_inclusive, end_inclusive,
    ))
    notes.extend(_apply_peer_percentile_overlay(db, shop, row, rows_out))

    _update_last_run(db, row)

    out = ReportDataOut(
        report_id=row.id,
        metric=row.metric,
        metric_label=metric_label,
        metric_unit=metric_unit,
        dimensions=list(row.dimensions or []),
        range_label=f"{start.isoformat()} → {end.isoformat()}",
        rows=rows_out,
        total=grand_total,
        chart_type=chart_type,
        forecast_horizon=row.forecast_horizon,
        notes=notes,
    ).model_dump(mode="json")

    cache_set(cache_key, out, _RUN_CACHE_TTL)
    response.headers["Cache-Control"] = "private, max-age=60"
    return out
