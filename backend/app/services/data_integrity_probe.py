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

from app.services.revenue_metrics import get_shop_currency, get_shop_timezone

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


# ---------------------------------------------------------------------------
# Merchant-specific baselines (2026-04-11 addition)
# ---------------------------------------------------------------------------
#
# Uniform thresholds across merchants are wrong. Christmas orders on a
# gift shop are normal; on a B2B office-supply shop they are anomalous.
# A per-merchant baseline is computed from 90d history and adjusted for
# weekday seasonality, so each shop's "normal" is its own pattern.
#
# For each (shop, metric) we compute:
#   - mean and stdev over 90d history
#   - a weekday multiplier (Mon=0.9, Tue=1.0, ..., Sat=1.3, Sun=0.8)
#     derived from the shop's own per-weekday average
#
# An anomaly is triggered when:
#   |current_value - expected_baseline_today| > 2.5 * stdev
#
# Cached 24h per shop in Redis. Pure computation, no LLM.

_BASELINE_CACHE_TTL = 24 * 3600
_BASELINE_CACHE_PREFIX = "hs:merchant_baseline:v1"
_MIN_BASELINE_DAYS = 21  # need at least 3 weeks of history to trust seasonality
_ANOMALY_STDEV_THRESHOLD = 2.5


def _compute_merchant_baseline(db: Session, shop: str) -> dict | None:
    """
    Return {mean, stdev, weekday_multipliers} for a shop's daily revenue.
    None if insufficient history.
    """
    import statistics

    now = _now()
    cutoff = now - timedelta(days=90)
    currency = get_shop_currency(db, shop)
    tz = get_shop_timezone(db, shop)
    rows = db.execute(text("""
        SELECT
            date_trunc('day', created_at AT TIME ZONE :tz)::date AS day,
            EXTRACT(DOW FROM created_at AT TIME ZONE :tz)::int AS dow,
            SUM(total_price) AS revenue
        FROM shop_orders
        WHERE shop_domain = :shop
          AND created_at >= :cutoff
          AND (:currency IS NULL OR currency = :currency)
        GROUP BY day, dow
        ORDER BY day
    """), {"shop": shop, "cutoff": cutoff, "currency": currency, "tz": tz}).fetchall()

    if len(rows) < _MIN_BASELINE_DAYS:
        return None

    revenues = [float(r[2] or 0) for r in rows]
    mean = statistics.mean(revenues) if revenues else 0.0
    stdev = statistics.stdev(revenues) if len(revenues) >= 2 else 0.0

    # Per-weekday multiplier: average revenue per weekday / overall mean
    by_dow: dict[int, list[float]] = {}
    for r in rows:
        by_dow.setdefault(int(r[1]), []).append(float(r[2] or 0))
    weekday_multipliers: dict[int, float] = {}
    for dow in range(7):
        day_vals = by_dow.get(dow, [])
        if day_vals and mean > 0:
            weekday_multipliers[dow] = round(statistics.mean(day_vals) / mean, 3)
        else:
            weekday_multipliers[dow] = 1.0

    return {
        "mean": round(mean, 2),
        "stdev": round(stdev, 2),
        "weekday_multipliers": weekday_multipliers,
        "sample_size_days": len(rows),
    }


def get_merchant_baseline(db: Session, shop: str) -> dict | None:
    """Cached baseline accessor. Returns None if insufficient history."""
    import hashlib
    key = f"{_BASELINE_CACHE_PREFIX}:{hashlib.md5(shop.encode()).hexdigest()[:16]}"
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            import json as _json
            cached = rc.get(key)
            if cached:
                return _json.loads(cached)
    except Exception as exc:
        log.warning("data_integrity_probe: baseline cache read failed: %s", exc)

    baseline = _compute_merchant_baseline(db, shop)
    if baseline is None:
        return None

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            import json as _json
            rc.setex(key, _BASELINE_CACHE_TTL, _json.dumps(baseline))
    except Exception as exc:
        log.warning("data_integrity_probe: baseline cache write failed: %s", exc)

    return baseline


def _check_merchant_anomaly(db: Session, shop: str) -> DriftFinding | None:
    """
    Per-merchant seasonality-adjusted anomaly detector. Only triggers
    when the shop has enough history AND the deviation is >2.5 stdev
    from the shop's own normal for today's weekday.
    """
    baseline = get_merchant_baseline(db, shop)
    if baseline is None:
        return None

    now = _now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    currency = get_shop_currency(db, shop)
    row = db.execute(text("""
        SELECT COALESCE(SUM(total_price), 0)
        FROM shop_orders
        WHERE shop_domain = :shop AND created_at >= :start
          AND (:currency IS NULL OR currency = :currency)
    """), {"shop": shop, "start": today_start, "currency": currency}).fetchone()
    if not row:
        return None

    today_revenue = float(row[0] or 0)
    if today_revenue == 0:
        return None  # no orders yet today — not an anomaly

    dow = today_start.weekday()
    # Python weekday: Mon=0..Sun=6. Postgres DOW: Sun=0..Sat=6. Adjust:
    pg_dow = (dow + 1) % 7
    weekday_mult = baseline["weekday_multipliers"].get(str(pg_dow), baseline["weekday_multipliers"].get(pg_dow, 1.0))
    expected = baseline["mean"] * float(weekday_mult)
    stdev = baseline["stdev"]
    if stdev == 0:
        return None

    deviation = today_revenue - expected
    stdev_multiple = abs(deviation) / stdev
    if stdev_multiple < _ANOMALY_STDEV_THRESHOLD:
        return None

    direction = "spike" if deviation > 0 else "drop"
    severity = "critical" if stdev_multiple >= 4.0 else "warning"

    return DriftFinding(
        check="merchant_anomaly",
        shop_domain=shop,
        severity=severity,
        summary=(
            f"Today's revenue on {shop} is a {direction}: "
            f"€{today_revenue:.0f} vs shop's seasonality-adjusted "
            f"expected €{expected:.0f} ({stdev_multiple:.1f}σ {direction})"
        ),
        detail={
            "today_revenue": round(today_revenue, 2),
            "expected_today": round(expected, 2),
            "weekday_multiplier": weekday_mult,
            "deviation_stdev_multiples": round(stdev_multiple, 2),
            "baseline_mean": baseline["mean"],
            "baseline_stdev": stdev,
            "sample_size_days": baseline["sample_size_days"],
            "direction": direction,
        },
    )


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
        ("merchant_anomaly",  _check_merchant_anomaly),
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
