# test-coverage: superseded by Brain Vero pivot 2026-05-07; Stage 2-C deletion pending
"""
evolution_business_outcomes.py — REVENUE feedback loop for evolution proposals.

The tech loop (evolution_outcomes.py) measures whether a proposal's fix
reduced alerts / worker errors. This module measures whether the same
proposal moved REVENUE / CONVERSION metrics.

Why a second loop
-----------------
An architectural refactor can be TECH_SUCCESS (no more alerts) and
BUSINESS_NOISE (didn't move the needle). A nudge-targeting rewrite can
be TECH_STABLE (no alerts change) and BUSINESS_SUCCESS (+12% CVR).
Without both axes, Monthly Opus optimizes for correctness, not money.

Attribution approach — HONEST, NOT CAUSAL
-----------------------------------------
For each applied proposal we compute a **trend-adjusted pre/post delta**:

    BEFORE  = metrics over 14d window ending at applied_at
    AFTER   = metrics over 14d window starting at (applied_at + 2d settling)
    CONTROL = metrics over 14d window ending 28d before applied_at
              (same season, pre-existing trend)

    raw_delta           = AFTER  - BEFORE
    pre_existing_trend  = BEFORE - CONTROL
    trend_adjusted      = raw_delta - pre_existing_trend

We classify the ABSOLUTE and RELATIVE trend-adjusted delta:

    improved       : relative change >= +5% AND sample_size >= min_sample
    declined       : relative change <= -5% AND sample_size >= min_sample
    stable         : |relative change| < 5% AND sample_size >= min_sample
    inconclusive   : sample_size < min_sample
    not_applicable : proposal's domain has no plausible revenue link
    pending        : not enough time has elapsed since applied_at

THIS IS NOT CAUSAL — no holdout, no RCT. We explicitly disclose this.
We DO trend-adjust and gate on sample size, which is the best honest
approximation for system-wide, non-A/B-able strategic proposals.

Business-domain classification (deterministic heuristic)
--------------------------------------------------------
A proposal is tagged as revenue-linked only when its text or target file
suggests a revenue/conversion touchpoint. Otherwise business_outcome is
set to 'not_applicable' — explicit, honest, audit-clean.
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.evolution_proposal import EvolutionProposal

log = logging.getLogger("evolution_business_outcomes")

# Measurement parameters
_WINDOW_DAYS = 14          # BEFORE / AFTER / CONTROL window length
_SETTLING_DAYS = 2         # skip first 2 days after apply (change takes effect)
_MIN_ORDERS = 50           # below this, mark inconclusive
_MIN_VISITORS = 2_000      # below this, mark inconclusive
_IMPROVED_THRESHOLD = 0.05 # +5% relative trend-adjusted change → improved
_DECLINED_THRESHOLD = -0.05

_BATCH_SIZE = 25

# Keywords that indicate a revenue/conversion touchpoint. If any appears in
# the proposal's reason OR target_file, we measure business impact; else
# we mark business_outcome='not_applicable'.
_REVENUE_KEYWORDS = re.compile(
    r"(convers|cart|checkout|nudge|tracker|attribution|revenue|funnel|"
    r"product_metric|visitor|purchase|upsell|pricing|discount|"
    r"abandon|retarget|segment|campaign|klaviyo|email)",
    re.IGNORECASE,
)

_REVENUE_FILE_PATTERNS = re.compile(
    r"(tracker/|/nudge|attribution|product_metric|visitor_|opportunity|"
    r"order_ingestion|shopify_admin|action_|weekly_digest)",
    re.IGNORECASE,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Public: classify business_domain (deterministic)
# ---------------------------------------------------------------------------

def classify_business_domain(proposal: EvolutionProposal) -> str:
    """
    Return one of:
      'conversion'     — proposal plausibly affects CVR / revenue
      'infra'          — architectural / reliability only, no revenue link
    """
    text_parts = [proposal.reason or "", proposal.expected_impact or ""]
    target = proposal.target_file or ""
    if _REVENUE_KEYWORDS.search(" ".join(text_parts)):
        return "conversion"
    if _REVENUE_FILE_PATTERNS.search(target):
        return "conversion"
    return "infra"


# ---------------------------------------------------------------------------
# Aggregate metric query — global across active merchants
# ---------------------------------------------------------------------------

def _metrics_sql(shop_filter: bool) -> text:
    """Build the aggregate metric query, optionally restricted to shop_domain IN (:shops)."""
    shop_where_events = "AND shop_domain = ANY(:shops)" if shop_filter else ""
    shop_where_orders = "AND shop_domain = ANY(:shops)" if shop_filter else ""
    return text(f"""
        WITH visitors AS (
            SELECT COUNT(DISTINCT visitor_id) AS n
            FROM events
            WHERE event_type = 'product_view'
              AND timestamp >= :start_epoch_ms
              AND timestamp <  :end_epoch_ms
              {shop_where_events}
        ),
        atc AS (
            SELECT COUNT(DISTINCT visitor_id) AS n
            FROM events
            WHERE event_type = 'add_to_cart'
              AND timestamp >= :start_epoch_ms
              AND timestamp <  :end_epoch_ms
              {shop_where_events}
        ),
        orders AS (
            SELECT COUNT(*) AS n, COALESCE(SUM(total_price), 0) AS revenue
            FROM shop_orders
            WHERE created_at >= :start_ts
              AND created_at <  :end_ts
              AND (:currency IS NULL OR currency = :currency)
              {shop_where_orders}
        )
        SELECT
            visitors.n  AS visitors,
            atc.n       AS atc_visitors,
            orders.n    AS orders,
            orders.revenue AS revenue
        FROM visitors, atc, orders
    """)


def _metrics_for_window(
    db: Session, start: datetime, end: datetime,
    scope_shops: list[str] | None = None,
) -> dict:
    """
    Return aggregated metrics over [start, end). If scope_shops is a non-empty
    list, restricts to those shop_domains; otherwise measures globally.
    """
    from app.services.revenue_metrics import get_shop_currency
    start_epoch_ms = int(start.replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_epoch_ms = int(end.replace(tzinfo=timezone.utc).timestamp() * 1000)
    currency = get_shop_currency(db, scope_shops[0]) if scope_shops and len(scope_shops) == 1 else None
    params = {
        "start_epoch_ms": start_epoch_ms,
        "end_epoch_ms": end_epoch_ms,
        "start_ts": start,
        "end_ts": end,
        "currency": currency,
    }
    if scope_shops:
        params["shops"] = list(scope_shops)
        sql = _metrics_sql(shop_filter=True)
    else:
        sql = _metrics_sql(shop_filter=False)

    row = db.execute(sql, params).fetchone()
    visitors = int(row[0] or 0)
    atc_visitors = int(row[1] or 0)
    orders = int(row[2] or 0)
    revenue = float(row[3] or 0.0)

    cvr = (orders / visitors) if visitors > 0 else 0.0
    atc_rate = (atc_visitors / visitors) if visitors > 0 else 0.0
    rpv = (revenue / visitors) if visitors > 0 else 0.0
    aov = (revenue / orders) if orders > 0 else 0.0

    return {
        "visitors": visitors,
        "atc_visitors": atc_visitors,
        "orders": orders,
        "revenue": round(revenue, 2),
        "cvr": round(cvr, 6),
        "atc_rate": round(atc_rate, 6),
        "rpv": round(rpv, 4),
        "aov": round(aov, 2),
    }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _two_proportion_z(n1: int, x1: int, n2: int, x2: int) -> float:
    """
    Two-proportion z-statistic comparing p1 = x1/n1 vs p2 = x2/n2.
    Returns 0.0 when inputs are degenerate (avoids ZeroDivisionError).
    Positive z → p2 > p1.
    """
    if n1 <= 0 or n2 <= 0:
        return 0.0
    p1 = x1 / n1
    p2 = x2 / n2
    pooled = (x1 + x2) / (n1 + n2)
    if pooled <= 0 or pooled >= 1:
        return 0.0
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0
    return (p2 - p1) / se


def _compute_confidence(
    *, orders_combined: int, abs_z: float, cvr_rel: float, rpv_rel: float,
) -> float:
    """
    Collapse sample-size, statistical-significance, and cross-metric
    consistency into a single 0.0–1.0 confidence score.

      sample_factor       0.0 at 50 orders → 1.0 at 500 orders
      significance_factor 0.0 at z=0       → 1.0 at z=3.0
      consistency_factor  1.0 if CVR and RPV trend the same direction, else 0.5

    Final confidence = min(sample_factor, significance_factor) * consistency_factor.
    Min-aggregate on the first two: BOTH must be satisfied. Consistency
    scales the result down when signals disagree.
    """
    sample_factor = max(0.0, min(1.0, (orders_combined - 50) / 450.0))
    significance_factor = max(0.0, min(1.0, abs_z / 3.0))
    same_sign = (cvr_rel >= 0 and rpv_rel >= 0) or (cvr_rel <= 0 and rpv_rel <= 0)
    consistency_factor = 1.0 if same_sign else 0.5
    return round(min(sample_factor, significance_factor) * consistency_factor, 3)


def assess_data_quality(before: dict, after: dict, control: dict) -> tuple[str, list[str]]:
    """
    Sanity-check the raw metric windows before any decision is made.

    Returns (quality_level, issues) where:
      HIGH   — all windows have sane values; safe to act autonomously
      MEDIUM — minor anomalies detected; measurement allowed, but decision
               engine should NOT auto-rollback on this evidence alone
      LOW    — data is broken (impossible values, empty windows, traffic
               cliffs). Outcome MUST be forced to 'inconclusive' and the
               decision engine MUST NOT act.

    This is the last line of defense against a delayed Shopify webhook
    or a broken pixel producing a phantom "revenue declined" signal that
    would otherwise trigger an autonomous code rollback.
    """
    issues: list[str] = []
    windows = {"before": before, "after": after, "control": control}

    # LOW-severity checks (any one makes quality=LOW). Tolerant of partial
    # window dicts — treats missing keys as absent data, not as failure.
    for name, w in windows.items():
        visitors = int(w.get("visitors", 0) or 0)
        orders = int(w.get("orders", 0) or 0)
        atc = int(w.get("atc_visitors", 0) or 0)
        cvr = float(w.get("cvr", 0.0) or 0.0)
        if visitors == 0:
            issues.append(f"{name}:empty_visitors_window")
        if visitors > 0 and orders > visitors:
            issues.append(
                f"{name}:orders_exceed_visitors "
                f"({orders} orders / {visitors} visitors)"
            )
        if visitors > 0 and atc > visitors:
            issues.append(
                f"{name}:atc_exceeds_visitors "
                f"({atc} atc / {visitors} visitors)"
            )
        if cvr > 0.30:  # 30% CVR is impossible for real ecommerce
            issues.append(f"{name}:impossibly_high_cvr={cvr:.4f}")
        # Cross-check: orders exist but visitor tracking is silent
        if visitors == 0 and orders > 0:
            issues.append(f"{name}:orders_without_visitor_tracking")

    if issues:
        return "LOW", issues

    # MEDIUM-severity checks (stats work but signal is suspect)
    bv = int(before.get("visitors", 0) or 0)
    av = int(after.get("visitors", 0) or 0)
    if bv > 0 and av > 0:
        ratio = av / bv
        if ratio < 0.3 or ratio > 3.0:
            issues.append(f"visitor_volume_shift:{ratio:.2f}x (before={bv} after={av})")

    bo = int(before.get("orders", 0) or 0)
    ao = int(after.get("orders", 0) or 0)
    if bo > 0 and ao > 0:
        order_ratio = ao / bo
        if order_ratio < 0.3 or order_ratio > 3.0:
            issues.append(f"order_volume_shift:{order_ratio:.2f}x (before={bo} after={ao})")

    # AOV sanity: a 5x jump in AOV within 14d is almost certainly a data
    # glitch (currency change, test orders, mis-parse of line_items JSON).
    baov = float(before.get("aov", 0.0) or 0.0)
    aaov = float(after.get("aov", 0.0) or 0.0)
    if baov > 0 and aaov > 0:
        aov_ratio = aaov / baov
        if aov_ratio < 0.2 or aov_ratio > 5.0:
            issues.append(f"aov_shift:{aov_ratio:.2f}x (before={baov:.2f} after={aaov:.2f})")

    if issues:
        return "MEDIUM", issues
    return "HIGH", []


def _classify_delta(
    before: dict, after: dict, control: dict, *, min_orders: int, min_visitors: int,
) -> tuple[str, dict]:
    """
    Trend-adjusted classification of BUSINESS outcome.

    Primary signal is CVR (orders/visitors) tested with a two-proportion
    z-test. AOV/RPV are secondary (consistency check).
    Returns (business_outcome, detail_dict). detail_dict includes
    confidence_score ready for decision-engine consumption.
    """
    total_orders = after["orders"] + before["orders"]
    total_visitors = after["visitors"] + before["visitors"]

    # DATA TRUST GATE — check data sanity BEFORE anything else. A LOW-
    # quality window means the underlying data is broken (delayed webhooks,
    # pixel outage, impossible values); force inconclusive and drop
    # confidence to 0 so the decision engine cannot act on phantom signals.
    data_quality, quality_issues = assess_data_quality(before, after, control)
    if data_quality == "LOW":
        return "inconclusive", {
            "reason": "data_quality_low",
            "data_quality": "LOW",
            "data_quality_issues": quality_issues,
            "orders_combined": total_orders,
            "visitors_combined": total_visitors,
            "confidence_score": 0.0,
        }

    if total_orders < min_orders or total_visitors < min_visitors:
        return "inconclusive", {
            "reason": "sample_too_small",
            "data_quality": data_quality,
            "data_quality_issues": quality_issues,
            "orders_combined": total_orders,
            "visitors_combined": total_visitors,
            "min_orders_required": min_orders,
            "min_visitors_required": min_visitors,
            "confidence_score": 0.0,
        }

    raw_cvr_delta = after["cvr"] - before["cvr"]
    trend_cvr = before["cvr"] - control["cvr"]
    adjusted_cvr_delta = raw_cvr_delta - trend_cvr

    # Relative change vs BEFORE baseline
    rel_cvr_change = (adjusted_cvr_delta / before["cvr"]) if before["cvr"] > 0 else 0.0

    raw_rpv_delta = after["rpv"] - before["rpv"]
    trend_rpv = before["rpv"] - control["rpv"]
    adjusted_rpv_delta = raw_rpv_delta - trend_rpv
    rel_rpv_change = (adjusted_rpv_delta / before["rpv"]) if before["rpv"] > 0 else 0.0

    # Two-proportion z-test on CVR (orders / visitors)
    z = _two_proportion_z(
        n1=before["visitors"], x1=before["orders"],
        n2=after["visitors"], x2=after["orders"],
    )
    confidence = _compute_confidence(
        orders_combined=total_orders, abs_z=abs(z),
        cvr_rel=rel_cvr_change, rpv_rel=rel_rpv_change,
    )
    # MEDIUM quality → halve confidence so the decision engine will not
    # auto-rollback on suspect data (0.70 threshold becomes unreachable).
    if data_quality == "MEDIUM":
        confidence = round(confidence * 0.5, 3)

    detail = {
        "primary_signal": "cvr",
        "data_quality": data_quality,
        "data_quality_issues": quality_issues,
        "cvr_before": before["cvr"],
        "cvr_after": after["cvr"],
        "cvr_control": control["cvr"],
        "cvr_raw_delta": round(raw_cvr_delta, 6),
        "cvr_trend": round(trend_cvr, 6),
        "cvr_trend_adjusted_delta": round(adjusted_cvr_delta, 6),
        "cvr_trend_adjusted_relative": round(rel_cvr_change, 4),
        "rpv_before": before["rpv"],
        "rpv_after": after["rpv"],
        "rpv_control": control["rpv"],
        "rpv_trend_adjusted_delta": round(adjusted_rpv_delta, 4),
        "rpv_trend_adjusted_relative": round(rel_rpv_change, 4),
        "orders_combined": total_orders,
        "visitors_combined": total_visitors,
        "z_score": round(z, 3),
        "confidence_score": confidence,
        "attribution_type": "quasi-causal",
        "disclosure": "trend-adjusted pre/post delta with two-proportion z-test; NOT RCT (no holdout)",
    }

    if rel_cvr_change >= _IMPROVED_THRESHOLD:
        return "improved", detail
    if rel_cvr_change <= _DECLINED_THRESHOLD:
        return "declined", detail
    return "stable", detail


# ---------------------------------------------------------------------------
# Measurement orchestration
# ---------------------------------------------------------------------------

def measure_business_impact(db: Session, proposal: EvolutionProposal) -> tuple[str, dict]:
    """
    Measure the business impact of a single EvolutionProposal.

    Returns (business_outcome, evidence_dict). Does NOT write to the DB —
    the caller (propagate_business_outcomes) writes.
    """
    domain = classify_business_domain(proposal)
    if domain == "infra":
        return "not_applicable", {
            "domain": "infra",
            "reason": "no revenue touchpoint detected in reason/target_file",
        }

    if proposal.applied_at is None:
        return "pending", {"reason": "not yet applied"}

    applied_at = proposal.applied_at
    now = _now()

    # The AFTER window must have fully elapsed before we measure.
    after_start = applied_at + timedelta(days=_SETTLING_DAYS)
    after_end = after_start + timedelta(days=_WINDOW_DAYS)
    if now < after_end:
        remaining = (after_end - now).days
        return "pending", {
            "reason": "after-window not yet complete",
            "days_remaining": remaining,
        }

    before_end = applied_at
    before_start = before_end - timedelta(days=_WINDOW_DAYS)
    control_end = before_start - timedelta(days=_WINDOW_DAYS)
    control_start = control_end - timedelta(days=_WINDOW_DAYS)

    # Scope — micro-attribution. If the proposal declares affected_shop_domains,
    # restrict all windows to those shops. Otherwise, global measurement.
    scope_shops: list[str] = []
    try:
        if proposal.affected_shop_domains:
            raw = json.loads(proposal.affected_shop_domains)
            if isinstance(raw, list):
                scope_shops = [s for s in raw if isinstance(s, str) and s]
    except (ValueError, TypeError):
        scope_shops = []

    # Try CAUSAL (RCT) measurement FIRST when the proposal declares linked
    # nudges with an active holdout. If that succeeds with enough samples,
    # skip the trend-adjusted path entirely and return a causal evidence.
    try:
        from app.services.evolution_causal_attribution import try_causal_measurement
        causal = try_causal_measurement(
            db, proposal, window_start=after_start, window_end=after_end,
        )
    except Exception as exc:
        log.warning("causal measurement failed (non-fatal): %s", type(exc).__name__)
        causal = None

    if causal is not None:
        outcome, causal_evidence = causal
        evidence = {
            "domain": domain,
            "window_days": _WINDOW_DAYS,
            "settling_days": _SETTLING_DAYS,
            "applied_at": applied_at.isoformat(),
            "scope": {"shops": scope_shops or None, "measured_globally": not scope_shops},
            "causal": causal_evidence,
            "classification": {
                "primary_signal": "cvr_causal_delta",
                "confidence_score": causal_evidence["confidence_score"],
                "attribution_type": "causal",
                "disclosure": causal_evidence["disclosure"],
            },
        }
        return outcome, evidence

    before = _metrics_for_window(db, before_start, before_end, scope_shops=scope_shops or None)
    after = _metrics_for_window(db, after_start, after_end, scope_shops=scope_shops or None)
    control = _metrics_for_window(db, control_start, control_end, scope_shops=scope_shops or None)

    outcome, detail = _classify_delta(
        before, after, control,
        min_orders=_MIN_ORDERS, min_visitors=_MIN_VISITORS,
    )

    # Per-shop breakdown (micro-attribution): when scoped, compute the
    # same windows per shop so operators see WHICH shops moved. Capped at
    # 20 shops to bound query cost.
    per_shop: dict[str, dict] = {}
    if scope_shops and len(scope_shops) <= 20:
        for shop in scope_shops:
            try:
                sb = _metrics_for_window(db, before_start, before_end, scope_shops=[shop])
                sa = _metrics_for_window(db, after_start, after_end, scope_shops=[shop])
                sc = _metrics_for_window(db, control_start, control_end, scope_shops=[shop])
                sh_outcome, sh_detail = _classify_delta(
                    sb, sa, sc,
                    min_orders=max(10, _MIN_ORDERS // 5),
                    min_visitors=max(200, _MIN_VISITORS // 10),
                )
                per_shop[shop] = {
                    "outcome": sh_outcome,
                    "cvr_before": sb["cvr"], "cvr_after": sa["cvr"],
                    "orders_combined": sb["orders"] + sa["orders"],
                    "confidence_score": sh_detail.get("confidence_score", 0.0),
                }
            except Exception:
                continue

    evidence = {
        "domain": domain,
        "window_days": _WINDOW_DAYS,
        "settling_days": _SETTLING_DAYS,
        "applied_at": applied_at.isoformat(),
        "scope": {
            "shops": scope_shops or None,
            "measured_globally": not scope_shops,
        },
        "windows": {
            "control": [control_start.isoformat(), control_end.isoformat()],
            "before": [before_start.isoformat(), before_end.isoformat()],
            "after": [after_start.isoformat(), after_end.isoformat()],
        },
        "metrics": {"before": before, "after": after, "control": control},
        "per_shop": per_shop or None,
        "classification": detail,
    }
    return outcome, evidence


def propagate_business_outcomes(db: Session) -> dict:
    """
    Scan proposals with tech outcome already measured but business
    outcome still NULL, and evaluate them.

    We gate on outcome_status IS NOT NULL so we only measure proposals
    that have been both applied AND had their tech outcome settled. This
    keeps the AFTER window aligned with a known-good applied_at timestamp.

    Returns: {"scanned": n, "measured": n, "pending": n, "not_applicable": n}
    """
    summary = {"scanned": 0, "measured": 0, "pending": 0, "not_applicable": 0}

    rows = (
        db.query(EvolutionProposal)
        .filter(
            EvolutionProposal.applied_at.isnot(None),
            EvolutionProposal.outcome_status.isnot(None),
            EvolutionProposal.business_outcome.is_(None),
        )
        .order_by(EvolutionProposal.applied_at.asc())
        .limit(_BATCH_SIZE)
        .all()
    )

    for prop in rows:
        summary["scanned"] += 1
        try:
            outcome, evidence = measure_business_impact(db, prop)
        except Exception as exc:
            log.warning(
                "evolution_business_outcomes: measurement failed proposal=%d: %s",
                prop.id, type(exc).__name__,
            )
            continue

        if outcome == "pending":
            summary["pending"] += 1
            # Don't persist 'pending' — re-evaluate next cycle.
            continue

        prop.business_outcome = outcome
        prop.business_measured_at = _now()
        prop.business_evidence = json.dumps(evidence, default=str)
        # Lift confidence out of the evidence classification block so the
        # decision engine can read it without re-parsing JSON.
        confidence = None
        if isinstance(evidence.get("classification"), dict):
            confidence = evidence["classification"].get("confidence_score")
        if isinstance(confidence, (int, float)):
            prop.confidence_score = float(confidence)
        if outcome == "not_applicable":
            summary["not_applicable"] += 1
        else:
            summary["measured"] += 1

    db.flush()
    if summary["measured"] > 0 or summary["not_applicable"] > 0:
        log.info(
            "evolution_business_outcomes: scanned=%d measured=%d not_applicable=%d pending=%d",
            summary["scanned"], summary["measured"],
            summary["not_applicable"], summary["pending"],
        )
    return summary


# ---------------------------------------------------------------------------
# Multi-dimensional outcome combination
# ---------------------------------------------------------------------------

def combined_outcome_label(tech: str | None, business: str | None) -> str:
    """
    Collapse (tech_outcome, business_outcome) into a single 5-class label
    that Monthly Opus can reason about directly.

      TECH_SUCCESS     tech effective, business not_applicable / pending / stable
      BUSINESS_SUCCESS business improved, tech not effective or unknown
      BOTH             tech effective AND business improved
      NEITHER          tech ineffective OR business declined (and not the above)
      NOISE            inconclusive or no signal
    """
    t = tech or ""
    b = business or ""

    tech_win = (t == "effective")
    tech_fail = (t == "ineffective")
    biz_win = (b == "improved")
    biz_fail = (b == "declined")
    biz_neutral = b in ("stable", "not_applicable")

    if tech_win and biz_win:
        return "BOTH"
    if biz_win:
        return "BUSINESS_SUCCESS"
    if tech_win and (biz_neutral or b == "" or b == "pending"):
        return "TECH_SUCCESS"
    if tech_fail or biz_fail:
        return "NEITHER"
    return "NOISE"


# ---------------------------------------------------------------------------
# Prioritization engine — score NEW proposals from historical outcomes
# ---------------------------------------------------------------------------

def compute_category_success_rates(db: Session, days: int = 180) -> dict:
    """
    Aggregate historical business outcomes grouped by domain.

    Returns:
      {
        "conversion": {"improved": n, "declined": n, "stable": n,
                       "total": n, "success_rate": 0.XX},
        "infra":      {"improved": 0, "declined": 0, "stable": 0,
                       "total": n, "success_rate": 0.0}
      }
    """
    cutoff = _now() - timedelta(days=days)
    rows = (
        db.query(EvolutionProposal)
        .filter(
            EvolutionProposal.business_measured_at >= cutoff,
            EvolutionProposal.business_outcome.isnot(None),
        )
        .all()
    )

    stats = {
        "conversion": {"improved": 0, "declined": 0, "stable": 0, "total": 0},
        "infra": {"improved": 0, "declined": 0, "stable": 0, "total": 0},
    }
    for r in rows:
        domain = classify_business_domain(r)
        bucket = stats.setdefault(
            domain, {"improved": 0, "declined": 0, "stable": 0, "total": 0}
        )
        o = r.business_outcome
        if o in ("improved", "declined", "stable"):
            bucket[o] += 1
            bucket["total"] += 1

    for d, s in stats.items():
        denom = s["improved"] + s["declined"]
        s["success_rate"] = round(s["improved"] / denom, 3) if denom > 0 else 0.0
    return stats


def compute_priority_score(
    proposal: EvolutionProposal,
    category_success_rates: dict,
    reinforcement_weights: dict | None = None,
) -> dict:
    """
    Compute a 0–100 priority score for a NEW (unmeasured) proposal, based on
    historical outcomes in its business domain.

    Score components:
      - category_confidence (0–60): how often proposals in this domain have
        delivered business improvement historically.
      - urgency             (0–30): LEVEL_2 (wants PR review soon) > LEVEL_3.
      - domain_weight       (0–10): conversion domain scores higher than infra.

    After summing, the score is MULTIPLIED by a reinforcement multiplier
    derived from historical wins vs losses in this domain (range [0.5, 1.5]).
    This is the closed-loop reinforcement: winning domains get boosted,
    losing domains get penalized. Passing reinforcement_weights=None makes
    the multiplier 1.0 (backward compatible).

    Returns: {"score": int, "breakdown": {...}}
    """
    domain = classify_business_domain(proposal)
    rates = category_success_rates.get(domain, {"total": 0, "success_rate": 0.0})

    # category_confidence: 0 if no history, scales to 60 at 100% success rate
    # and >= 10 measured samples. With few samples, confidence is dampened.
    total_samples = rates.get("total", 0)
    sample_weight = min(1.0, total_samples / 10.0)
    category_confidence = 60.0 * rates["success_rate"] * sample_weight

    urgency = 30.0 if proposal.risk_level == "LEVEL_2" else 15.0
    domain_weight = 10.0 if domain == "conversion" else 3.0

    raw_score = category_confidence + urgency + domain_weight

    # Reinforcement multiplier closes the loop: domains that produced
    # BOTH / BUSINESS_SUCCESS outcomes get amplified; domains that produced
    # NEITHER outcomes get damped. Default 1.0 when no data.
    multiplier = 1.0
    if reinforcement_weights is not None:
        from app.services.evolution_reinforcement import reinforcement_multiplier
        multiplier = reinforcement_multiplier(domain, reinforcement_weights)

    score = int(round(raw_score * multiplier))
    return {
        "score": min(100, max(0, score)),
        "breakdown": {
            "category_confidence": round(category_confidence, 1),
            "urgency": urgency,
            "domain_weight": domain_weight,
            "reinforcement_multiplier": multiplier,
            "domain": domain,
            "historical_samples": total_samples,
            "historical_success_rate": rates["success_rate"],
        },
    }


# ---------------------------------------------------------------------------
# Anti-bullshit filter
# ---------------------------------------------------------------------------

def should_reject_proposal(
    proposal_dict: dict,
    category_success_rates: dict,
    *,
    min_category_samples: int = 5,
    reject_below_success_rate: float = 0.20,
) -> tuple[bool, str]:
    """
    Decide whether to auto-reject an incoming Opus proposal based on the
    historical success rate of its business domain.

    Returns (should_reject: bool, reason: str).

    Heuristic: if the proposal's domain has >= min_category_samples measured
    historical proposals AND the success rate is below reject_below_success_rate,
    we block the proposal. Domains with little history are NEVER rejected —
    we don't want to block exploration.
    """
    # Build a minimal pseudo-proposal for classification
    class _P:
        reason = proposal_dict.get("reasoning", "") or ""
        expected_impact = proposal_dict.get("expected_impact", "") or ""
        target_file = proposal_dict.get("target_file", "") or ""
    domain = classify_business_domain(_P())
    rates = category_success_rates.get(domain, {"total": 0, "success_rate": 0.0})
    if rates["total"] < min_category_samples:
        return False, "insufficient_history"
    if rates["success_rate"] < reject_below_success_rate:
        return True, (
            f"domain={domain} success_rate={rates['success_rate']*100:.0f}% "
            f"over {rates['total']} measured proposals — below threshold"
        )
    return False, "ok"
