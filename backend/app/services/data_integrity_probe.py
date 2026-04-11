"""
data_integrity_probe.py — Deterministic semantic drift detection.

Closes the "silent data corruption" blind spot. Before this module, the
self-healing pipeline only reacted to *crashes* (exceptions, Sentry errors,
webhook failures). A silent bug — revenue calculation drifting, attribution
rate silently collapsing, a nudge lift quietly decaying — would never fire
an alert, and the pipeline would never learn about it.

This probe runs a small, curated set of **per-shop semantic health checks**
against real merchant data. Each check compares a recent window (7 days)
against a baseline window (8–30 days) and flags statistically meaningful
regressions. When a check fails, we write a `semantic_drift` ops_alert;
bugfix_pipeline.run_bug_triage Rule 6 then promotes it to a candidate like
any other incident.

Principles
----------
- **Deterministic**. No LLM, no randomness. Same inputs → same alerts.
- **Cheap**. Every query is indexed and bounded; the whole probe sweeps
  N active merchants in one agent cycle.
- **Fail-closed on query errors**. If a subcheck explodes, log and move on;
  probe failure never cascades into other merchants.
- **Stable fingerprint per (shop, check)**. Multiple consecutive drifts of
  the same type on the same shop collapse into one alert via dedup_5min
  in alerting.write_alert, and one candidate via _should_skip_source.
- **Narrow scope**. We monitor what we already emit; we never introduce
  new metrics. This probe is about correctness of the existing product,
  not about proposing new features.

Checks
------
1. ATTRIBUTION_DRIFT — attributed_orders / total_orders dropped >10pp
2. ORDER_COLLAPSE  — 7-day order count dropped >50% vs 8–30 baseline
3. AOV_DRIFT       — average order value moved >25% in either direction
4. NUDGE_LIFT_DECAY — a nudge's measured lift dropped >60% without redeploy

Cooldown is enforced at call site (agent_worker): the probe is expensive
enough that we run it every 6h, not every cycle.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("data_integrity_probe")

# ---------------------------------------------------------------------------
# Thresholds — tuned conservatively. Too sensitive → alert spam; too loose →
# we miss the silent bugs we are supposed to catch. Keep numeric, keep here.
# ---------------------------------------------------------------------------

_ATTRIBUTION_DROP_PP = 10.0      # absolute percentage points drop
_ORDER_COLLAPSE_RATIO = 0.5      # 7d < baseline * 0.5 → alert
_AOV_DRIFT_RATIO_HI = 1.25       # 7d > baseline * 1.25 → alert
_AOV_DRIFT_RATIO_LO = 0.75       # 7d < baseline * 0.75 → alert
_NUDGE_LIFT_DECAY_RATIO = 0.4    # 7d < baseline * 0.4 → alert

# Minimum sample sizes below which we refuse to draw conclusions.
_MIN_ORDERS_FOR_ATTRIBUTION = 30
_MIN_ORDERS_FOR_AOV = 20
_MIN_NUDGE_EXPOSURES = 200

# Merchants to sweep per cycle.
_MAX_MERCHANTS_PER_CYCLE = 50


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class DriftFinding:
    """One semantic drift the probe caught on one shop."""
    check: str                 # e.g. "attribution_drift"
    shop_domain: str
    severity: str              # "warning" | "critical"
    summary: str               # human-readable
    detail: dict               # structured, safe-to-serialize


@dataclass
class ProbeResult:
    checks_run: int = 0
    findings: list[DriftFinding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-check query helpers
# ---------------------------------------------------------------------------


def _active_shops(db: Session, limit: int) -> list[str]:
    """Return active shop domains that had at least 1 order in the last 30 days.
    Filters to merchants that could produce meaningful signals."""
    rows = db.execute(text("""
        SELECT DISTINCT m.shop_domain
        FROM merchants m
        INNER JOIN shop_orders o
          ON o.shop_domain = m.shop_domain
         AND o.created_at >= :cutoff
        WHERE m.install_status = 'active'
        LIMIT :lim
    """), {"cutoff": _now() - timedelta(days=30), "lim": limit}).fetchall()
    return [r[0] for r in rows if r[0]]


def _check_attribution_drift(db: Session, shop: str) -> DriftFinding | None:
    """Attribution rate (7d) vs baseline (8–30d). Drop >10pp → alert.

    This is the single highest-value check: an attribution collapse is how
    a tracker bug, a cookie policy change, or a pipeline regression
    manifests silently in production.
    """
    now = _now()
    cutoff_recent = now - timedelta(days=7)
    cutoff_baseline_start = now - timedelta(days=30)
    cutoff_baseline_end = now - timedelta(days=7)

    # visitor_purchase_sessions is linked to shop_orders via shopify_order_id.
    recent = db.execute(text("""
        SELECT
            COUNT(*) AS total,
            COUNT(CASE WHEN vps.shopify_order_id IS NOT NULL THEN 1 END) AS attributed
        FROM shop_orders o
        LEFT JOIN visitor_purchase_sessions vps
          ON vps.shopify_order_id = o.shopify_order_id
         AND vps.shop_domain = o.shop_domain
        WHERE o.shop_domain = :shop
          AND o.created_at >= :cutoff
    """), {"shop": shop, "cutoff": cutoff_recent}).fetchone()

    baseline = db.execute(text("""
        SELECT
            COUNT(*) AS total,
            COUNT(CASE WHEN vps.shopify_order_id IS NOT NULL THEN 1 END) AS attributed
        FROM shop_orders o
        LEFT JOIN visitor_purchase_sessions vps
          ON vps.shopify_order_id = o.shopify_order_id
         AND vps.shop_domain = o.shop_domain
        WHERE o.shop_domain = :shop
          AND o.created_at >= :start
          AND o.created_at <  :end
    """), {
        "shop": shop, "start": cutoff_baseline_start, "end": cutoff_baseline_end,
    }).fetchone()

    if not recent or not baseline:
        return None
    if (recent[0] or 0) < _MIN_ORDERS_FOR_ATTRIBUTION or (baseline[0] or 0) < _MIN_ORDERS_FOR_ATTRIBUTION:
        return None

    recent_rate = (recent[1] / recent[0]) * 100 if recent[0] else 0
    baseline_rate = (baseline[1] / baseline[0]) * 100 if baseline[0] else 0
    drop_pp = baseline_rate - recent_rate

    if drop_pp < _ATTRIBUTION_DROP_PP:
        return None

    return DriftFinding(
        check="attribution_drift",
        shop_domain=shop,
        severity="critical" if drop_pp >= 20 else "warning",
        summary=(
            f"Attribution rate dropped {drop_pp:.1f}pp on {shop} "
            f"(baseline={baseline_rate:.1f}% → recent={recent_rate:.1f}%)"
        ),
        detail={
            "recent_window_days": 7,
            "baseline_window_days": 23,
            "recent_total_orders": int(recent[0]),
            "recent_attributed": int(recent[1] or 0),
            "recent_rate_pct": round(recent_rate, 2),
            "baseline_total_orders": int(baseline[0]),
            "baseline_attributed": int(baseline[1] or 0),
            "baseline_rate_pct": round(baseline_rate, 2),
            "drop_pp": round(drop_pp, 2),
        },
    )


def _check_order_collapse(db: Session, shop: str) -> DriftFinding | None:
    """Order count (7d) vs baseline (8–30d, daily-normalized). <50% → alert.

    Detects webhook silent failures: the merchant is still selling, but our
    ingestion stopped receiving orders.
    """
    now = _now()
    recent_days = 7
    baseline_days = 23

    row = db.execute(text("""
        SELECT
            COUNT(CASE WHEN created_at >= :recent_cut THEN 1 END) AS recent_cnt,
            COUNT(CASE WHEN created_at >= :base_start AND created_at < :base_end THEN 1 END) AS baseline_cnt
        FROM shop_orders
        WHERE shop_domain = :shop
          AND created_at >= :base_start
    """), {
        "shop": shop,
        "recent_cut": now - timedelta(days=recent_days),
        "base_start": now - timedelta(days=recent_days + baseline_days),
        "base_end": now - timedelta(days=recent_days),
    }).fetchone()

    if not row:
        return None
    recent_cnt = int(row[0] or 0)
    baseline_cnt = int(row[1] or 0)
    if baseline_cnt == 0:
        return None

    # Daily-normalize: compare per-day average
    recent_per_day = recent_cnt / recent_days
    baseline_per_day = baseline_cnt / baseline_days
    if baseline_per_day < 1.0:  # Very small shops → skip
        return None
    ratio = recent_per_day / baseline_per_day
    if ratio >= _ORDER_COLLAPSE_RATIO:
        return None

    return DriftFinding(
        check="order_collapse",
        shop_domain=shop,
        severity="critical" if ratio < 0.2 else "warning",
        summary=(
            f"Order volume collapsed on {shop}: "
            f"{recent_per_day:.1f}/day (last 7d) vs {baseline_per_day:.1f}/day baseline "
            f"(ratio={ratio:.2f})"
        ),
        detail={
            "recent_orders": recent_cnt,
            "baseline_orders": baseline_cnt,
            "recent_per_day": round(recent_per_day, 2),
            "baseline_per_day": round(baseline_per_day, 2),
            "ratio": round(ratio, 3),
        },
    )


def _check_aov_drift(db: Session, shop: str) -> DriftFinding | None:
    """Average Order Value drift — detects silent pricing/calculation bugs.

    An AOV that swings 25%+ in either direction without order count change
    usually means an upstream calculation is broken, currency mismatches are
    happening, or tax/shipping handling regressed.
    """
    now = _now()
    row = db.execute(text("""
        SELECT
            AVG(CASE WHEN created_at >= :recent_cut THEN total_price END) AS recent_aov,
            COUNT(CASE WHEN created_at >= :recent_cut THEN 1 END) AS recent_n,
            AVG(CASE WHEN created_at >= :base_start AND created_at < :base_end THEN total_price END) AS baseline_aov,
            COUNT(CASE WHEN created_at >= :base_start AND created_at < :base_end THEN 1 END) AS baseline_n
        FROM shop_orders
        WHERE shop_domain = :shop
          AND created_at >= :base_start
    """), {
        "shop": shop,
        "recent_cut": now - timedelta(days=7),
        "base_start": now - timedelta(days=30),
        "base_end": now - timedelta(days=7),
    }).fetchone()

    if not row:
        return None
    recent_aov = float(row[0] or 0)
    recent_n = int(row[1] or 0)
    baseline_aov = float(row[2] or 0)
    baseline_n = int(row[3] or 0)
    if recent_n < _MIN_ORDERS_FOR_AOV or baseline_n < _MIN_ORDERS_FOR_AOV:
        return None
    if baseline_aov <= 0:
        return None

    ratio = recent_aov / baseline_aov
    if _AOV_DRIFT_RATIO_LO <= ratio <= _AOV_DRIFT_RATIO_HI:
        return None

    direction = "spike" if ratio > 1 else "collapse"
    return DriftFinding(
        check="aov_drift",
        shop_domain=shop,
        severity="warning",
        summary=(
            f"AOV {direction} on {shop}: "
            f"baseline={baseline_aov:.2f} → recent={recent_aov:.2f} (ratio={ratio:.2f})"
        ),
        detail={
            "recent_aov": round(recent_aov, 2),
            "recent_n": recent_n,
            "baseline_aov": round(baseline_aov, 2),
            "baseline_n": baseline_n,
            "ratio": round(ratio, 3),
            "direction": direction,
        },
    )


def _check_nudge_lift_decay(db: Session) -> list[DriftFinding]:
    """Global (not per-shop): find active nudges whose measured lift has
    decayed sharply vs their own baseline window. A nudge that was +80%
    two weeks ago and is now +5% without a deploy = silent measurement bug
    or a copy that has lost effectiveness — either way, worth a look.

    We run this check globally because nudge_events are already per-nudge
    and the query is cheap; no need to multiply by merchant count.
    """
    findings: list[DriftFinding] = []
    now = _now()
    rows = db.execute(text("""
        SELECT
            n.id,
            n.shop_domain,
            -- recent (last 7d) exposed CVR
            COALESCE(SUM(
                CASE WHEN ne.event_type = 'exposed' AND ne.created_at >= :recent_cut THEN 1 ELSE 0 END
            ), 0) AS recent_exposures,
            COALESCE(SUM(
                CASE WHEN ne.event_type = 'purchase_after_exposed' AND ne.created_at >= :recent_cut THEN 1 ELSE 0 END
            ), 0) AS recent_purchases,
            -- baseline (8–30d) exposed CVR
            COALESCE(SUM(
                CASE WHEN ne.event_type = 'exposed' AND ne.created_at < :recent_cut AND ne.created_at >= :base_start THEN 1 ELSE 0 END
            ), 0) AS baseline_exposures,
            COALESCE(SUM(
                CASE WHEN ne.event_type = 'purchase_after_exposed' AND ne.created_at < :recent_cut AND ne.created_at >= :base_start THEN 1 ELSE 0 END
            ), 0) AS baseline_purchases
        FROM active_nudges n
        LEFT JOIN nudge_events ne ON ne.nudge_id = n.id
        WHERE n.status = 'active'
          AND n.created_at < :base_start
        GROUP BY n.id, n.shop_domain
        LIMIT 200
    """), {
        "recent_cut": now - timedelta(days=7),
        "base_start": now - timedelta(days=30),
    }).fetchall()

    for row in rows:
        nudge_id = int(row[0])
        shop = row[1]
        recent_exp = int(row[2] or 0)
        recent_pur = int(row[3] or 0)
        base_exp = int(row[4] or 0)
        base_pur = int(row[5] or 0)

        if recent_exp < _MIN_NUDGE_EXPOSURES or base_exp < _MIN_NUDGE_EXPOSURES:
            continue
        recent_cvr = recent_pur / recent_exp
        base_cvr = base_pur / base_exp
        if base_cvr <= 0.005:  # nothing to decay from
            continue
        ratio = recent_cvr / base_cvr
        if ratio >= _NUDGE_LIFT_DECAY_RATIO:
            continue

        findings.append(DriftFinding(
            check="nudge_lift_decay",
            shop_domain=shop,
            severity="warning",
            summary=(
                f"Nudge #{nudge_id} ({shop}) CVR decayed: "
                f"baseline={base_cvr*100:.2f}% → recent={recent_cvr*100:.2f}% (ratio={ratio:.2f})"
            ),
            detail={
                "nudge_id": nudge_id,
                "recent_exposures": recent_exp,
                "recent_purchases": recent_pur,
                "recent_cvr_pct": round(recent_cvr * 100, 3),
                "baseline_exposures": base_exp,
                "baseline_purchases": base_pur,
                "baseline_cvr_pct": round(base_cvr * 100, 3),
                "ratio": round(ratio, 3),
            },
        ))
    return findings


# ---------------------------------------------------------------------------
# Main entry point — called by aggregation_worker every 6h
# ---------------------------------------------------------------------------


def run_probe(db: Session, max_shops: int = _MAX_MERCHANTS_PER_CYCLE) -> ProbeResult:
    """
    Run all semantic integrity checks for up to `max_shops` active merchants
    and emit ops_alerts for any drift found. Returns a ProbeResult summary.

    Idempotent: the underlying alerting.write_alert dedups on (source, type,
    shop) within 5 minutes, and the triage pipeline dedups further by
    source_ref, so running the probe every 6h does not produce duplicate
    candidates.
    """
    result = ProbeResult()

    try:
        shops = _active_shops(db, limit=max_shops)
    except Exception as exc:
        log.warning("data_integrity_probe: active_shops query failed: %s", exc)
        result.errors.append(f"active_shops:{type(exc).__name__}")
        return result

    per_shop_checks = (
        ("attribution_drift", _check_attribution_drift),
        ("order_collapse",    _check_order_collapse),
        ("aov_drift",         _check_aov_drift),
    )

    for shop in shops:
        for check_name, fn in per_shop_checks:
            result.checks_run += 1
            try:
                finding = fn(db, shop)
            except Exception as exc:
                result.errors.append(f"{check_name}:{shop}:{type(exc).__name__}")
                log.debug("data_integrity_probe: %s on %s failed: %s", check_name, shop, exc)
                continue
            if finding is not None:
                result.findings.append(finding)

    # Global nudge check — one sweep, not per-shop
    try:
        result.checks_run += 1
        result.findings.extend(_check_nudge_lift_decay(db))
    except Exception as exc:
        result.errors.append(f"nudge_lift_decay:{type(exc).__name__}")
        log.debug("data_integrity_probe: nudge_lift_decay failed: %s", exc)

    # Emit alerts for findings
    if result.findings:
        from app.services.alerting import write_alert
        for f in result.findings:
            try:
                write_alert(
                    db,
                    severity=f.severity,
                    source=f"probe:{f.check}:{f.shop_domain}",
                    alert_type="semantic_drift",
                    summary=f.summary,
                    shop_domain=f.shop_domain,
                    detail=f.detail,
                )
            except Exception as exc:
                result.errors.append(f"alert_write:{f.check}:{type(exc).__name__}")
                log.debug("data_integrity_probe: alert write failed: %s", exc)

    if result.findings or result.errors:
        log.info(
            "data_integrity_probe: checks=%d findings=%d errors=%d",
            result.checks_run, len(result.findings), len(result.errors),
        )

    return result
