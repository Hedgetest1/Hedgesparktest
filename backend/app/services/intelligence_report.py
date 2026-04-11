"""
intelligence_report.py — CIG-powered intelligence report generator.

Produces two outputs:
  1. Merchant report: per-store insights (vs cohort benchmarks)
  2. Public report: anonymized "State of Shopify Conversion" authority content

Uses CIG cohort data + SIP profiles. No LLM — deterministic templates.

Design:
  - Merchant report: specific, actionable, trust-building
  - Public report: aggregate, authoritative, curiosity-generating
  - Both reinforce "Proof-Based Revenue Intelligence" positioning
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def generate_merchant_intelligence(db: Session, shop_domain: str) -> list[dict]:
    """
    Generate per-store intelligence insights using SIP + CIG.
    Returns a list of insight dicts for inclusion in weekly digest.
    """
    # Load SIP
    sip_row = db.execute(
        text("""SELECT baseline_cart_rate, baseline_scroll_depth, baseline_dwell_time,
                       baseline_mobile_pct, traffic_source_quality, trust_score,
                       confidence_level, autonomy_level, total_positive_outcomes, total_rollbacks
                FROM store_intelligence_profiles WHERE shop_domain = :shop"""),
        {"shop": shop_domain},
    ).fetchone()

    if not sip_row:
        return []

    cart_rate = float(sip_row[0]) if sip_row[0] else None
    scroll = float(sip_row[1]) if sip_row[1] else None
    dwell = float(sip_row[2]) if sip_row[2] else None
    mobile = float(sip_row[3]) if sip_row[3] else None
    source_quality = sip_row[4]
    trust = float(sip_row[5]) if sip_row[5] else 0.5
    confidence = sip_row[6]
    autonomy = int(sip_row[7]) if sip_row[7] else 0
    positives = int(sip_row[8]) if sip_row[8] else 0
    rollbacks = int(sip_row[9]) if sip_row[9] else 0

    # Load CIG cohort for benchmarking
    cohort_row = db.execute(
        text("""SELECT c.avg_cart_rate, c.p25_cart_rate, c.p75_cart_rate,
                       c.avg_scroll_depth, c.avg_dwell_time, c.merchant_count,
                       c.nudge_effectiveness, c.confidence_level
                FROM cig_merchant_mappings m
                JOIN cig_cohorts c ON c.cohort_key = m.primary_cohort_key
                WHERE m.shop_domain = :shop"""),
        {"shop": shop_domain},
    ).fetchone()

    insights: list[dict] = []

    # ── Insight 1: Cart rate vs cohort ──
    if cart_rate is not None and cohort_row and cohort_row[0]:
        cohort_cr = float(cohort_row[0])
        p25 = float(cohort_row[1]) if cohort_row[1] else None
        p75 = float(cohort_row[2]) if cohort_row[2] else None
        n = int(cohort_row[5])

        if cohort_cr > 0:
            ratio = cart_rate / cohort_cr
            if ratio > 1.1:
                insights.append({
                    "type": "benchmark_positive",
                    "headline": f"Your cart rate ({cart_rate:.1%}) is above your peer average ({cohort_cr:.1%})",
                    "detail": f"Based on {n} similar stores. Keep doing what works.",
                })
            elif ratio < 0.7 and p25:
                insights.append({
                    "type": "benchmark_opportunity",
                    "headline": f"Your cart rate ({cart_rate:.1%}) is below similar stores ({cohort_cr:.1%})",
                    "detail": f"Stores like yours average {cohort_cr:.1%}. The bottom 25% is at {p25:.1%}. There's room to improve.",
                })

    # ── Insight 2: Traffic source quality ──
    if source_quality and isinstance(source_quality, dict):
        sorted_sources = sorted(source_quality.items(), key=lambda x: x[1], reverse=True)
        if len(sorted_sources) >= 2:
            best_src, best_score = sorted_sources[0]
            worst_src, worst_score = sorted_sources[-1]
            if best_score > 1.3 and worst_score < 0.7:
                insights.append({
                    "type": "source_quality",
                    "headline": f"{best_src.title()} traffic converts {best_score:.1f}x better than {worst_src.title()}",
                    "detail": f"Consider reallocating spend from {worst_src} to {best_src} for higher-converting traffic.",
                })

    # ── Insight 3: Autonomy status ──
    if autonomy >= 3 and positives > 0:
        insights.append({
            "type": "autonomy_status",
            "headline": f"HedgeSpark is running autonomously (Level {autonomy})",
            "detail": f"{positives} proven positive outcomes so far. The system is earning your trust through results.",
        })
    elif autonomy < 3 and confidence in ("medium", "high"):
        insights.append({
            "type": "autonomy_progress",
            "headline": "HedgeSpark is building your store's intelligence profile",
            "detail": "As the system proves results, it will earn autonomy to act on its own. No shortcuts — only measured outcomes.",
        })

    return insights[:5]


def generate_public_intelligence(db: Session) -> dict | None:
    """
    Generate the "State of Shopify Conversion" public report.
    Anonymized aggregate data from CIG cohorts.

    Returns a dict ready for email template rendering, or None if insufficient data.
    """
    # Aggregate across all cohorts
    row = db.execute(text("""
        SELECT
            COUNT(*) AS cohort_count,
            SUM(merchant_count) AS total_merchants,
            AVG(avg_cart_rate) FILTER (WHERE avg_cart_rate > 0) AS global_avg_cart_rate,
            AVG(avg_scroll_depth) FILTER (WHERE avg_scroll_depth > 0) AS global_avg_scroll,
            AVG(avg_dwell_time) FILTER (WHERE avg_dwell_time > 0) AS global_avg_dwell,
            SUM(total_data_points) AS total_data_points
        FROM cig_cohorts
        WHERE merchant_count >= 3
    """)).fetchone()

    if not row or not row[0] or row[0] == 0:
        return None

    cohort_count = int(row[0])
    total_merchants = int(row[1]) if row[1] else 0
    avg_cr = float(row[2]) if row[2] else None
    avg_scroll = float(row[3]) if row[3] else None
    avg_dwell = float(row[4]) if row[4] else None
    total_dp = int(row[5]) if row[5] else 0

    if total_merchants < 10:
        return None  # Not enough data for public authority

    # Top signals across all cohorts
    signal_rows = db.execute(text("""
        SELECT signal_distribution FROM cig_cohorts WHERE merchant_count >= 3
    """)).fetchall()

    signal_agg: dict[str, float] = {}
    signal_count = 0
    for (dist,) in signal_rows:
        if dist and isinstance(dist, dict):
            signal_count += 1
            for sig, pct in dist.items():
                signal_agg[sig] = signal_agg.get(sig, 0) + float(pct)

    top_signals = []
    if signal_count > 0:
        top_signals = sorted(
            [{"signal": s, "pct": round(v / signal_count, 3)} for s, v in signal_agg.items()],
            key=lambda x: x["pct"],
            reverse=True,
        )[:5]

    # Best nudge types
    nudge_rows = db.execute(text("""
        SELECT nudge_effectiveness FROM cig_cohorts WHERE merchant_count >= 3 AND nudge_effectiveness IS NOT NULL
    """)).fetchall()

    nudge_agg: dict[str, list[float]] = {}
    for (eff,) in nudge_rows:
        if eff and isinstance(eff, dict):
            for nt, data in eff.items():
                if isinstance(data, dict) and data.get("avg_lift"):
                    nudge_agg.setdefault(nt, []).append(float(data["avg_lift"]))

    top_nudges = sorted(
        [{"type": nt, "avg_effectiveness": round(sum(v) / len(v), 3), "stores": len(v)}
         for nt, v in nudge_agg.items() if len(v) >= 3],
        key=lambda x: x["avg_effectiveness"],
        reverse=True,
    )[:3]

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    return {
        "generated_at": now.isoformat(),
        "period": "Last 7 days",
        "total_merchants": total_merchants,
        "total_data_points": total_dp,
        "avg_cart_rate": round(avg_cr, 4) if avg_cr else None,
        "avg_scroll_depth": round(avg_scroll, 1) if avg_scroll else None,
        "avg_dwell_seconds": round(avg_dwell, 1) if avg_dwell else None,
        "top_signals": top_signals,
        "top_nudge_types": top_nudges,
        "headline": f"Shopify Conversion Intelligence — {total_merchants} stores analyzed",
        "subheadline": "Proof-based insights from real holdout-tested experiments.",
        "cta_text": "Get intelligence like this for your store",
        "cta_url": "https://www.hedgesparkhq.com",
    }
