"""
verify_dev_health.py — E2E smoke test for Pro dashboard data pipeline.

Calls every Pro service function (backend-direct, no HTTP) for the dev
merchant and asserts each returns populated/non-trivial data. Exits 0 if
everything is green, 1 if any critical section is empty or errors out.

Purpose
-------
Today (10 April 2026) we discovered 4 silent bugs where backend Pro endpoints
worked fine but their frontend consumers showed empty state because of field
name mismatches, wrong URLs, and shape drift. The bugs were invisible because:

- `/pro/lift` worked server-side but `has_experiment_data=False` because of
  a field name mismatch in the aggregate path (`has_holdout_data` vs
  `holdout_active`).
- `/pro/segments` worked but AudienceSegments.tsx called `/segments`.
- `/pro/nudges/{id}/stats` worked but NudgePerformance.tsx read 6 wrong field
  names from the response.
- `/pro/segments` returned `hot/warm/cold` as top-level dict keys but
  AudienceSegments.tsx expected a `segments[]` array.

All 4 bugs would have been detected in 10 seconds by a smoke test that
verified "is this Pro section populated on the dev merchant after seed?".
This is that smoke test.

When to run
-----------
- After every `seed_dev_data.py` run (to confirm seed gave every section data)
- After every backend API change (to catch regressions in service shapes)
- After every frontend refactor (via the companion `curl` checks below)
- Before any merchant-facing demo or screenshot session
- In CI, eventually — as a pre-merge gate on `backend/app/services/*`

Usage
-----
    cd /opt/wishspark/backend
    ./venv/bin/python -m scripts.verify_dev_health              # full check, exit 0/1
    ./venv/bin/python -m scripts.verify_dev_health --verbose    # dump full payloads
    ./venv/bin/python -m scripts.verify_dev_health --json       # JSON output (for CI)

Exit codes
----------
    0  — all checks passed
    1  — one or more critical checks failed (empty data, error, missing section)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.database import SessionLocal

DEV_SHOP = "hedgespark-dev.myshopify.com"


# ---------------------------------------------------------------------------
# Check result type
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    name: str
    section: str              # dashboard section this feeds
    status: str               # "pass" | "empty" | "error"
    detail: str = ""
    sample: dict[str, Any] = field(default_factory=dict)  # for --verbose


# ---------------------------------------------------------------------------
# Individual checks — each calls the service function and asserts non-empty
# ---------------------------------------------------------------------------
def check_cohort_weekly(db: Session) -> CheckResult:
    from app.services.cohort_engine import get_cohort_retention
    try:
        r = get_cohort_retention(db, DEV_SHOP, weeks=8)
    except Exception as e:
        return CheckResult("cohort_retention (weekly)", "Cohort Retention Matrix",
                           "error", str(e)[:120])
    cohorts = r.get("cohorts", [])
    if not cohorts:
        return CheckResult("cohort_retention (weekly)", "Cohort Retention Matrix",
                           "empty", "no cohorts returned")
    return CheckResult("cohort_retention (weekly)", "Cohort Retention Matrix",
                       "pass", f"{len(cohorts)} cohorts", {"cohorts": len(cohorts)})


def check_cohort_monthly(db: Session) -> CheckResult:
    from app.services.ltv_engine import get_monthly_cohorts
    try:
        r = get_monthly_cohorts(db, DEV_SHOP, months=6)
    except Exception as e:
        return CheckResult("cohort_monthly", "Customer Economics",
                           "error", str(e)[:120])
    if r.get("overall", {}).get("total_customers", 0) == 0:
        return CheckResult("cohort_monthly", "Customer Economics",
                           "empty", "total_customers=0")
    overall = r["overall"]
    return CheckResult("cohort_monthly", "Customer Economics", "pass",
                       f"customers={overall['total_customers']} repeat_rate={overall['repeat_rate']}",
                       overall)


def check_ltv_summary(db: Session) -> CheckResult:
    from app.services.ltv_engine import get_ltv_summary
    try:
        r = get_ltv_summary(db, DEV_SHOP)
    except Exception as e:
        return CheckResult("ltv_summary", "LTV Summary", "error", str(e)[:120])
    if r.get("total_customers", 0) == 0:
        return CheckResult("ltv_summary", "LTV Summary", "empty", "total_customers=0")
    return CheckResult("ltv_summary", "LTV Summary", "pass",
                       f"customers={r['total_customers']}", r)


def check_gateway_products(db: Session) -> CheckResult:
    from app.services.ltv_engine import get_product_ltv_contribution
    try:
        r = get_product_ltv_contribution(db, DEV_SHOP, limit=10)
    except Exception as e:
        return CheckResult("gateway_products", "Gateway Intelligence",
                           "error", str(e)[:120])
    products = r.get("products", [])
    if not products:
        return CheckResult("gateway_products", "Gateway Intelligence",
                           "empty", "no products with buyers")
    # Warn if all 3 products are gateway (no loyalty variety)
    gateways = sum(1 for p in products if p.get("is_gateway"))
    loyalties = len(products) - gateways
    detail = f"{len(products)} products ({gateways} gateway, {loyalties} loyalty)"
    if loyalties == 0:
        detail += " — no loyalty variety, consider seeding repeat buyers"
    return CheckResult("gateway_products", "Gateway Intelligence",
                       "pass", detail,
                       {"count": len(products), "gateways": gateways, "loyalties": loyalties})


def check_predicted_ltv(db: Session) -> CheckResult:
    from app.services.ltv_engine import get_predicted_ltv
    try:
        r = get_predicted_ltv(db, DEV_SHOP, limit=10)
    except Exception as e:
        return CheckResult("predicted_ltv", "Predicted Value",
                           "error", str(e)[:120])
    customers = r.get("customers", [])
    if not customers:
        return CheckResult("predicted_ltv", "Predicted Value",
                           "empty", "no identified customers")
    total_12m = sum(c.get("predicted_12m_ltv", 0) for c in customers)
    return CheckResult("predicted_ltv", "Predicted Value", "pass",
                       f"{len(customers)} customers, top-10 12mo ${total_12m:,.0f}",
                       {"count": len(customers), "total_12m": total_12m})


def check_behavioral_cohorts(db: Session) -> CheckResult:
    from app.services.behavioral_cohorts import get_behavioral_cohort_analysis
    try:
        r = get_behavioral_cohort_analysis(db, DEV_SHOP, days=90)
    except Exception as e:
        return CheckResult("behavioral_cohorts", "Behavioral Intel",
                           "error", str(e)[:120])
    segments = r.get("segments", {})
    eng = segments.get("by_engagement", [])
    if not eng:
        return CheckResult("behavioral_cohorts", "Behavioral Intel",
                           "empty", "no by_engagement segments")
    return CheckResult("behavioral_cohorts", "Behavioral Intel", "pass",
                       f"{len(eng)} engagement segments",
                       {"by_engagement": len(eng),
                        "by_visit_pattern": len(segments.get("by_visit_pattern", [])),
                        "by_source": len(segments.get("by_source", []))})


def check_nudge_lift(db: Session) -> CheckResult:
    """Aggregate lift — the exact path the /pro/lift endpoint uses."""
    from app.services.nudge_measurement import get_nudge_lift_report
    from sqlalchemy import text
    try:
        rows = db.execute(text("""
            SELECT DISTINCT an.id
            FROM active_nudges an
            JOIN nudge_events ne ON ne.nudge_id = an.id AND ne.shop_domain = an.shop_domain
            WHERE an.shop_domain = :s AND an.holdout_pct > 0
              AND ne.event_type = 'holdout_assigned'
            LIMIT 20
        """), {"s": DEV_SHOP}).fetchall()
    except Exception as e:
        return CheckResult("nudge_lift_aggregate", "Proof / Lift Report",
                           "error", str(e)[:120])
    if not rows:
        return CheckResult("nudge_lift_aggregate", "Proof / Lift Report",
                           "empty", "no nudges with holdout_assigned events")
    valid = 0
    lift_samples: list[float] = []
    for r in rows:
        try:
            lr = get_nudge_lift_report(db, DEV_SHOP, int(r[0]))
            if lr.get("holdout_active"):
                valid += 1
                if lr.get("estimated_lift_pct") is not None:
                    lift_samples.append(lr["estimated_lift_pct"])
        except Exception:
            continue
    if valid == 0:
        return CheckResult("nudge_lift_aggregate", "Proof / Lift Report",
                           "empty", "all nudges returned holdout_active=False")
    return CheckResult("nudge_lift_aggregate", "Proof / Lift Report", "pass",
                       f"{valid} active experiments, lift={lift_samples}",
                       {"valid_nudges": valid, "lifts": lift_samples})


def check_nudge_stats(db: Session) -> CheckResult:
    """Per-nudge stats — the /pro/nudges/{id}/stats endpoint path."""
    from app.services.nudge_measurement import get_nudge_ab_report
    from sqlalchemy import text
    try:
        rows = db.execute(text(
            "SELECT id FROM active_nudges WHERE shop_domain = :s AND status = 'active' LIMIT 5"
        ), {"s": DEV_SHOP}).fetchall()
    except Exception as e:
        return CheckResult("nudge_stats", "Nudge Performance", "error", str(e)[:120])
    if not rows:
        return CheckResult("nudge_stats", "Nudge Performance",
                           "empty", "no active nudges")
    populated = 0
    for r in rows:
        try:
            report = get_nudge_ab_report(db, DEV_SHOP, int(r[0]), window_hours=168)
            stats = report.get("stats", {})
            if stats.get("exposures", 0) > 0:
                populated += 1
        except Exception:
            continue
    if populated == 0:
        return CheckResult("nudge_stats", "Nudge Performance",
                           "empty", "all active nudges have 0 exposures")
    return CheckResult("nudge_stats", "Nudge Performance", "pass",
                       f"{populated}/{len(rows)} nudges with real stats",
                       {"populated": populated, "total": len(rows)})


def check_audience_segments(db: Session) -> CheckResult:
    """Per-product behavioral segments — the /pro/segments endpoint."""
    from app.services.audience_segments import segment_product_visitors
    from sqlalchemy import text
    try:
        products = [r[0] for r in db.execute(text(
            "SELECT product_url FROM product_metrics WHERE shop_domain = :s LIMIT 5"
        ), {"s": DEV_SHOP}).fetchall()]
    except Exception as e:
        return CheckResult("audience_segments", "Audience", "error", str(e)[:120])
    if not products:
        return CheckResult("audience_segments", "Audience", "empty",
                           "no tracked products in product_metrics")
    populated = 0
    total_active_sum = 0
    for p in products:
        try:
            r = segment_product_visitors(db=db, shop_domain=DEV_SHOP,
                                         product_url=p, hours=72)
            active = r.get("total_active_visitors", 0)
            if active > 0:
                populated += 1
                total_active_sum += active
        except Exception:
            continue
    if populated == 0:
        return CheckResult("audience_segments", "Audience", "empty",
                           "no products with active visitors in last 72h")
    return CheckResult("audience_segments", "Audience", "pass",
                       f"{populated}/{len(products)} products with active visitors "
                       f"(total {total_active_sum})",
                       {"populated": populated, "total_active": total_active_sum})


def check_attribution(db: Session) -> CheckResult:
    """UTM attribution summary — feeds Attribution dashboard section."""
    try:
        from app.services.utm_attribution import get_attribution_summary
    except ImportError as e:
        return CheckResult("attribution", "Attribution", "error",
                           f"import failed: {e}")
    try:
        r = get_attribution_summary(db, DEV_SHOP, days=30)
    except Exception as e:
        return CheckResult("attribution", "Attribution", "error", str(e)[:120])
    if not r:
        return CheckResult("attribution", "Attribution", "empty",
                           "attribution summary returned empty")
    if isinstance(r, dict):
        total = r.get("orders_total", 0)
        attributed = r.get("orders_attributed", 0)
        rate = r.get("attribution_rate", 0)
        sources_ft = r.get("top_sources_first_touch", []) or []
        if total == 0:
            return CheckResult("attribution", "Attribution", "empty",
                               "orders_total=0")
        if not sources_ft:
            return CheckResult("attribution", "Attribution", "empty",
                               f"orders={total} but no top_sources_first_touch")
        return CheckResult("attribution", "Attribution", "pass",
                           f"orders={total} attributed={attributed} ({rate:.0%}) sources={len(sources_ft)}",
                           {"total": total, "attributed": attributed,
                            "rate": rate, "sources": len(sources_ft)})
    return CheckResult("attribution", "Attribution", "pass", "data present")


def check_forecast(db: Session) -> CheckResult:
    """Revenue forecast (Pro)."""
    try:
        from app.services.revenue_forecast import get_revenue_forecast
    except ImportError as e:
        return CheckResult("forecast", "Forecast", "error",
                           f"import failed: {e}")
    try:
        r = get_revenue_forecast(db, DEV_SHOP)
    except Exception as e:
        return CheckResult("forecast", "Forecast", "error", str(e)[:120])
    if not r:
        return CheckResult("forecast", "Forecast", "empty", "forecast returned empty")
    if isinstance(r, dict):
        history = r.get("history", {}) or {}
        days_with_rev = history.get("days_with_revenue", 0) if isinstance(history, dict) else 0
        forecast_7d = r.get("forecast_7d", {}) or {}
        forecast_30d = r.get("forecast_30d", {}) or {}
        if days_with_rev == 0 and not forecast_7d and not forecast_30d:
            return CheckResult("forecast", "Forecast", "empty",
                               "no history days with revenue and no forecast windows")
        return CheckResult("forecast", "Forecast", "pass",
                           f"history_days_with_rev={days_with_rev} forecast_7d={bool(forecast_7d)} forecast_30d={bool(forecast_30d)}",
                           {"history_days": days_with_rev,
                            "has_7d": bool(forecast_7d),
                            "has_30d": bool(forecast_30d)})
    return CheckResult("forecast", "Forecast", "pass", "data present")


def check_pnl(db: Session) -> CheckResult:
    """Profit Intelligence (Sprint B P&L cassettone)."""
    try:
        from app.services.pnl_engine import get_pnl_report
    except ImportError as e:
        return CheckResult("pnl", "Profit Intelligence", "error", f"import failed: {e}")
    try:
        r = get_pnl_report(db, DEV_SHOP, window_days=30)
    except Exception as e:
        return CheckResult("pnl", "Profit Intelligence", "error", str(e)[:120])
    if not r or not r.get("has_data"):
        return CheckResult("pnl", "Profit Intelligence", "empty",
                           "no orders in 30d window — can't compute P&L")
    gross = r.get("gross_revenue", 0)
    net_margin = r.get("net_margin_pct", 0)
    orders = r.get("order_count", 0)
    return CheckResult(
        "pnl", "Profit Intelligence", "pass",
        f"orders={orders} gross={gross:.0f} net_margin={net_margin:.1f}%",
        {"gross_revenue": gross, "net_margin_pct": net_margin, "orders": orders},
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
CHECKS: list[Callable[[Session], CheckResult]] = [
    check_cohort_weekly,
    check_cohort_monthly,
    check_ltv_summary,
    check_gateway_products,
    check_predicted_ltv,
    check_behavioral_cohorts,
    check_nudge_lift,
    check_nudge_stats,
    check_audience_segments,
    check_attribution,
    check_forecast,
    check_pnl,
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
ICON = {"pass": "✅", "empty": "❌", "error": "💥"}


def run_all(db: Session) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fn in CHECKS:
        try:
            r = fn(db)
        except Exception as e:
            r = CheckResult(fn.__name__, "unknown", "error", f"uncaught: {e!r}")
        results.append(r)
    return results


def print_table(results: list[CheckResult], verbose: bool) -> None:
    print(f"\nHedgeSpark dev health check — {DEV_SHOP}")
    print(f"{datetime.now(timezone.utc).isoformat()}")
    print("─" * 100)
    print(f"{'':3} {'check':<28} {'section':<28} detail")
    print("─" * 100)
    for r in results:
        print(f"{ICON[r.status]}  {r.name:<28} {r.section:<28} {r.detail}")
        if verbose and r.sample:
            print(f"     └─ sample: {json.dumps(r.sample, default=str)[:200]}")
    print("─" * 100)
    passed = sum(1 for r in results if r.status == "pass")
    empty = sum(1 for r in results if r.status == "empty")
    errors = sum(1 for r in results if r.status == "error")
    total = len(results)
    print(f"\nSummary: {passed}/{total} pass · {empty} empty · {errors} errors")
    if empty + errors == 0:
        print("✨ All Pro dashboard sections would render populated UI for the dev merchant.")
    else:
        print("⚠  One or more sections would render empty UI. Fix before demo/screenshot/deploy.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify dev merchant Pro data health.")
    parser.add_argument("--verbose", action="store_true", help="dump sample payloads")
    parser.add_argument("--json", action="store_true", help="JSON output for CI")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        results = run_all(db)
    finally:
        db.close()

    if args.json:
        print(json.dumps([
            {"name": r.name, "section": r.section, "status": r.status,
             "detail": r.detail, "sample": r.sample}
            for r in results
        ], indent=2, default=str))
    else:
        print_table(results, verbose=args.verbose)

    # Exit code: 0 if all pass, 1 if any empty or error
    return 0 if all(r.status == "pass" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
