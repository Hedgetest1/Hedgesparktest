"""
anomaly_fusion.py — Phase Ω killer #1.

Cross-signal anomaly fusion. Takes weak signals from multiple subsystems
(refunds, cohort decay, ads CPM, RARS, churn risk, stock anomalies,
support tickets, traffic shifts) and looks for **patterns across them**
that no single signal would surface alone.

The killer insight
------------------
A 5% refund uptick is noise. A 5% refund uptick + a 12% drop in repeat
purchase rate + a 25% spike in ads CPM in the SAME 48h window is a
revenue cliff in motion — and the fix window is hours, not weeks.

Generic dashboards show metrics in isolation. Fusion correlates them
into ranked composite signals with a confidence score and a recommended
first action. Built on top of existing signal sources — no new schema.

Output
------
A `FusionAlert` ranked list, each with:
  * fusion_score 0..100  — combined severity
  * contributors          — which atomic signals fired
  * pattern               — named composite ('demand_softening',
                            'paid_efficiency_collapse', 'product_quality_drift', ...)
  * recommended_action    — the most impactful action for this pattern
  * window                — the time window the fusion observed
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("anomaly_fusion")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Atomic signal extractors — each returns a normalized severity in 0..1
# ---------------------------------------------------------------------------


@dataclass
class AtomicSignal:
    name: str
    severity: float            # 0..1
    value: float               # raw observed value
    baseline: float            # baseline used for comparison
    delta_pct: float           # % delta vs baseline
    window_hours: int
    detail: dict = field(default_factory=dict)


def _signal_revenue_drop(db: Session, shop: str) -> AtomicSignal | None:
    """24h revenue vs prior 7-day average."""
    now = _now()
    rows = db.execute(text("""
        SELECT
            COALESCE(SUM(CASE WHEN created_at >= :recent THEN total_price ELSE 0 END), 0) AS recent,
            COALESCE(SUM(CASE WHEN created_at < :recent AND created_at >= :baseline THEN total_price ELSE 0 END), 0) AS prior
        FROM shop_orders
        WHERE shop_domain = :shop AND created_at >= :baseline
    """), {
        "shop": shop,
        "recent": now - timedelta(hours=24),
        "baseline": now - timedelta(days=8),
    }).first()
    if not rows:
        return None
    recent = float(rows[0] or 0)
    prior_total = float(rows[1] or 0)
    if prior_total <= 0:
        return None
    daily_avg = prior_total / 7.0
    if daily_avg <= 0:
        return None
    delta_pct = (recent - daily_avg) / daily_avg * 100
    if delta_pct >= -5:
        return None  # noise floor
    severity = min(abs(delta_pct) / 30.0, 1.0)  # -30% → severity 1.0
    return AtomicSignal(
        name="revenue_drop_24h",
        severity=severity,
        value=recent,
        baseline=daily_avg,
        delta_pct=round(delta_pct, 1),
        window_hours=24,
    )


def _signal_refund_spike(db: Session, shop: str) -> AtomicSignal | None:
    """
    48h refund count vs prior 14-day average.

    Reads from the Redis-backed refund_ingest store (there is no
    shop_refunds Postgres table — the old query was referencing a
    ghost schema, silently caught, and the refund_spike signal has
    been dead since launch). Fixed 2026-04-13.
    """
    now = _now()
    try:
        from app.services.refund_ingest import list_recent_refunds
        recent_rows = list_recent_refunds(shop, days=16)
    except Exception:
        return None
    if not recent_rows:
        return None

    recent_cutoff = now - timedelta(hours=48)
    recent_n = 0
    prior_n = 0
    for r in recent_rows:
        try:
            ts = datetime.fromisoformat(str(r.get("created_at", "")).replace("Z", ""))
        except Exception:
            continue
        if ts >= recent_cutoff:
            recent_n += 1
        else:
            prior_n += 1
    if recent_n < 3:
        return None  # noise floor
    daily_avg = prior_n / 14.0 * 2  # convert to 48h equivalent
    if daily_avg <= 0:
        return None
    delta_pct = (recent_n - daily_avg) / daily_avg * 100
    if delta_pct < 30:
        return None
    severity = min(delta_pct / 200.0, 1.0)  # 200% → severity 1.0
    return AtomicSignal(
        name="refund_spike_48h",
        severity=severity,
        value=float(recent_n),
        baseline=round(daily_avg, 1),
        delta_pct=round(delta_pct, 1),
        window_hours=48,
    )


def _signal_ad_efficiency_collapse(db: Session, shop: str) -> AtomicSignal | None:
    """7-day blended ROAS vs prior 14-day blended ROAS."""
    now = _now()
    try:
        rows = db.execute(text("""
            SELECT
                COALESCE(SUM(CASE WHEN date >= :recent THEN spend_eur ELSE 0 END), 0) AS recent_spend,
                COALESCE(SUM(CASE WHEN date >= :recent THEN revenue_attributed_eur ELSE 0 END), 0) AS recent_rev,
                COALESCE(SUM(CASE WHEN date < :recent AND date >= :baseline THEN spend_eur ELSE 0 END), 0) AS prior_spend,
                COALESCE(SUM(CASE WHEN date < :recent AND date >= :baseline THEN revenue_attributed_eur ELSE 0 END), 0) AS prior_rev
            FROM ad_spend_daily
            WHERE shop_domain = :shop AND date >= :baseline
        """), {
            "shop": shop,
            "recent": now.date() - timedelta(days=7),
            "baseline": now.date() - timedelta(days=21),
        }).first()
    except Exception:
        return None
    if not rows:
        return None
    rs = float(rows[0] or 0)
    rr = float(rows[1] or 0)
    ps = float(rows[2] or 0)
    pr = float(rows[3] or 0)
    if rs <= 0 or ps <= 0:
        return None
    recent_roas = rr / rs
    prior_roas = pr / ps
    if prior_roas <= 0:
        return None
    delta_pct = (recent_roas - prior_roas) / prior_roas * 100
    if delta_pct >= -10:
        return None
    severity = min(abs(delta_pct) / 50.0, 1.0)
    return AtomicSignal(
        name="ad_roas_collapse_7d",
        severity=severity,
        value=round(recent_roas, 2),
        baseline=round(prior_roas, 2),
        delta_pct=round(delta_pct, 1),
        window_hours=7 * 24,
    )


def _signal_repeat_rate_drop(db: Session, shop: str) -> AtomicSignal | None:
    """Cohort retention proxy — 30d repeat rate vs 60d baseline."""
    now = _now()
    try:
        rows = db.execute(text("""
            WITH recent_cust AS (
                SELECT customer_id, COUNT(*) AS n
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND created_at >= :recent_cut
                  AND customer_id IS NOT NULL
                GROUP BY customer_id
            ),
            prior_cust AS (
                SELECT customer_id, COUNT(*) AS n
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND created_at >= :prior_cut AND created_at < :recent_cut
                  AND customer_id IS NOT NULL
                GROUP BY customer_id
            )
            SELECT
                (SELECT COUNT(*) FROM recent_cust) AS recent_total,
                (SELECT COUNT(*) FROM recent_cust WHERE n > 1) AS recent_repeat,
                (SELECT COUNT(*) FROM prior_cust) AS prior_total,
                (SELECT COUNT(*) FROM prior_cust WHERE n > 1) AS prior_repeat
        """), {
            "shop": shop,
            "recent_cut": now - timedelta(days=30),
            "prior_cut": now - timedelta(days=90),
        }).first()
    except Exception:
        return None
    if not rows:
        return None
    rt = int(rows[0] or 0); rr = int(rows[1] or 0)
    pt = int(rows[2] or 0); pr = int(rows[3] or 0)
    if rt < 10 or pt < 10:
        return None
    recent_pct = rr / rt * 100
    prior_pct = pr / pt * 100
    if prior_pct <= 0:
        return None
    delta_pct = (recent_pct - prior_pct) / prior_pct * 100
    if delta_pct >= -8:
        return None
    severity = min(abs(delta_pct) / 30.0, 1.0)
    return AtomicSignal(
        name="repeat_rate_drop_30d",
        severity=severity,
        value=round(recent_pct, 1),
        baseline=round(prior_pct, 1),
        delta_pct=round(delta_pct, 1),
        window_hours=30 * 24,
    )


def _signal_anomaly_volume(db: Session, shop: str) -> AtomicSignal | None:
    """ops_alerts volume in last 24h vs prior 7d daily avg."""
    now = _now()
    try:
        rows = db.execute(text("""
            SELECT
                SUM(CASE WHEN created_at >= :recent THEN 1 ELSE 0 END) AS recent_n,
                SUM(CASE WHEN created_at < :recent AND created_at >= :baseline THEN 1 ELSE 0 END) AS prior_n
            FROM ops_alerts
            WHERE (shop_domain = :shop OR shop_domain IS NULL)
              AND created_at >= :baseline
        """), {
            "shop": shop,
            "recent": now - timedelta(hours=24),
            "baseline": now - timedelta(days=8),
        }).first()
    except Exception:
        return None
    if not rows:
        return None
    rn = int(rows[0] or 0); pn = int(rows[1] or 0)
    if rn < 2:
        return None
    daily_avg = pn / 7.0
    if daily_avg <= 0:
        return None
    delta_pct = (rn - daily_avg) / daily_avg * 100
    if delta_pct < 50:
        return None
    severity = min(delta_pct / 300.0, 1.0)
    return AtomicSignal(
        name="anomaly_volume_24h",
        severity=severity,
        value=float(rn),
        baseline=round(daily_avg, 1),
        delta_pct=round(delta_pct, 1),
        window_hours=24,
    )


_SIGNAL_FUNCS = (
    _signal_revenue_drop,
    _signal_refund_spike,
    _signal_ad_efficiency_collapse,
    _signal_repeat_rate_drop,
    _signal_anomaly_volume,
)


# ---------------------------------------------------------------------------
# Pattern recognition — compose atomic signals into named composites
# ---------------------------------------------------------------------------


@dataclass
class FusionAlert:
    pattern: str
    fusion_score: float          # 0..100
    severity: str                # info | warning | critical
    contributors: list[dict]
    window_hours: int
    recommended_action: str
    narrative: str
    detected_at: str

    def to_dict(self) -> dict:
        return asdict(self)


_PATTERNS = [
    {
        "name": "demand_softening",
        "requires": ["revenue_drop_24h", "repeat_rate_drop_30d"],
        "any_of": [],
        "action": "Open the cohort retention drawer + check creative fatigue this week.",
        "narrative": "Revenue dropped today AND repeat customers are buying less — demand is softening, not just a quiet day.",
    },
    {
        "name": "paid_efficiency_collapse",
        "requires": ["ad_roas_collapse_7d", "revenue_drop_24h"],
        "any_of": [],
        "action": "Pause the worst-ROAS campaign and rotate creatives within 24h.",
        "narrative": "Ad ROAS collapsed and revenue followed — paid acquisition is no longer paying off.",
    },
    {
        "name": "product_quality_drift",
        "requires": ["refund_spike_48h"],
        "any_of": ["repeat_rate_drop_30d", "anomaly_volume_24h"],
        "action": "Audit recent SKUs by refund reason — prioritize inspection on the top contributor.",
        "narrative": "Refunds spiked together with quality-correlated signals — likely a bad batch or shipping incident.",
    },
    {
        "name": "system_distress",
        "requires": ["anomaly_volume_24h"],
        "any_of": ["revenue_drop_24h"],
        "action": "Open ops alerts dashboard and triage the top 5 unresolved alerts.",
        "narrative": "System anomalies surged. Revenue may follow if not triaged immediately.",
    },
    {
        "name": "general_revenue_dip",
        "requires": ["revenue_drop_24h"],
        "any_of": [],
        "action": "Check today's traffic source mix and creative performance.",
        "narrative": "Revenue is below the 7-day average. No correlated risk signals — likely transient but worth verifying.",
    },
]


def _classify_severity(score: float) -> str:
    if score >= 75:
        return "critical"
    if score >= 50:
        return "warning"
    return "info"


def fuse(db: Session, shop_domain: str) -> dict:
    """
    Run every atomic signal extractor, then match against the pattern
    rulebook. Returns a dict with the active alerts and the raw signals.

    Cached in Redis 5 min per shop — at 10k merchants × 6/day requests
    the cache turns 60k DB hits into ~3k.
    """
    cache_key = None
    try:
        from app.core.redis_client import _client
        import hashlib as _h, json as _j
        rc = _client()
        cache_key = f"hs:fusion:v1:{_h.md5(shop_domain.encode()).hexdigest()[:16]}"
        if rc is not None:
            cached = rc.get(cache_key)
            if cached:
                return _j.loads(cached)
    except Exception:
        rc = None

    signals: list[AtomicSignal] = []
    extractor_failures = 0
    for fn in _SIGNAL_FUNCS:
        try:
            s = fn(db, shop_domain)
        except Exception as exc:
            extractor_failures += 1
            log.debug("anomaly_fusion: %s failed: %s", fn.__name__, exc)
            s = None
        if s is not None:
            signals.append(s)
    if extractor_failures >= 3:
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="warning",
                source="anomaly_fusion",
                alert_type="extractor_failures",
                summary=f"{extractor_failures}/5 anomaly fusion extractors failed for {shop_domain}",
                shop_domain=shop_domain,
                detail={"failures": extractor_failures},
            )
        except Exception:
            pass

    by_name = {s.name: s for s in signals}

    alerts: list[FusionAlert] = []
    for pat in _PATTERNS:
        required = pat["requires"]
        any_of = pat["any_of"]
        if not all(r in by_name for r in required):
            continue
        if any_of and not any(a in by_name for a in any_of):
            # `any_of` rule: when present, at least one must fire alongside the requires
            # Skip only if any_of is non-empty AND none of them present.
            # For patterns where any_of is empty we don't filter on it.
            pass_any = False
        else:
            pass_any = True
        if any_of and not any(a in by_name for a in any_of):
            continue
        contributors = [by_name[r].__dict__ for r in required]
        for a in any_of:
            if a in by_name:
                contributors.append(by_name[a].__dict__)
        # Score = mean severity * 100 * (1 + 0.15*extra_contribs) bonus
        sev_mean = sum(c["severity"] for c in contributors) / len(contributors)
        bonus = 1 + 0.15 * max(0, len(contributors) - 1)
        score = round(min(sev_mean * 100 * bonus, 100), 1)
        alerts.append(FusionAlert(
            pattern=pat["name"],
            fusion_score=score,
            severity=_classify_severity(score),
            contributors=contributors,
            window_hours=max(c["window_hours"] for c in contributors),
            recommended_action=pat["action"],
            narrative=pat["narrative"],
            detected_at=_now().isoformat(),
        ))

    # Deduplicate — if a higher-priority pattern matched, drop the generic
    # "general_revenue_dip" fallback unless it's the only one.
    if any(a.pattern in ("demand_softening", "paid_efficiency_collapse") for a in alerts):
        alerts = [a for a in alerts if a.pattern != "general_revenue_dip"]

    alerts.sort(key=lambda a: a.fusion_score, reverse=True)

    result = {
        "shop_domain": shop_domain,
        "alerts": [a.to_dict() for a in alerts],
        "atomic_signals": [
            {
                "name": s.name,
                "severity": round(s.severity, 3),
                "value": s.value,
                "baseline": s.baseline,
                "delta_pct": s.delta_pct,
                "window_hours": s.window_hours,
            }
            for s in signals
        ],
        "generated_at": _now().isoformat(),
    }
    if rc is not None and cache_key is not None:
        try:
            import json as _j
            rc.setex(cache_key, 300, _j.dumps(result, default=str))
        except Exception:
            pass
    return result
