"""
sip_engine.py — Store Intelligence Profile computation.

Computes per-merchant behavioral baselines, learned thresholds, nudge
effectiveness scores, and traffic/price intelligence from existing
product_metrics, events, nudge_events, and active_nudges data.

Called by the aggregation worker after store_metrics are refreshed.
Results are upserted into store_intelligence_profiles and optionally
snapshotted to sip_snapshots (weekly).

Design principles:
  - All queries use existing indexed columns
  - Computation is bounded: never scans unbounded event ranges
  - Graceful degradation: missing data → null fields, confidence stays low
  - No LLM calls — purely deterministic computation
  - Idempotent: safe to re-run at any point
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

log = logging.getLogger(__name__)

# ── Confidence thresholds ──
_CONFIDENCE_LOW = 500
_CONFIDENCE_MEDIUM = 2000
_CONFIDENCE_HIGH = 5000

# ── Price bands for sensitivity analysis ──
_PRICE_BANDS = [
    (0, 15, "0-15"),
    (15, 30, "15-30"),
    (30, 50, "30-50"),
    (50, 100, "50-100"),
    (100, 250, "100-250"),
    (250, 99999, "250+"),
]


def compute_sip(
    conn: Connection,
    shop_domain: str,
    *,
    vertical: str | None = None,
) -> dict[str, Any] | None:
    """
    Compute the full Store Intelligence Profile for one merchant.

    Returns a dict ready for upsert, or None if insufficient data.
    All queries are scoped to shop_domain and bounded time windows.

    `vertical` (Sprint 2 #4): when caller passes a classified vertical
    (one of vertical_classifier._VERTICALS), the SIP record gains a
    `vertical_prior` block exposing the deterministic Bayesian-shrinkage
    blend toward the vertical baseline. Anti-cold-start: a shop with
    100 events can get an honest cart_rate estimate by leaning on the
    vertical's median CVR with strength=200, falling back to pure shop
    signal as data grows. Caller passes None when classification is
    unavailable; SIP still computes correctly without the prior block.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    week_ago_ms = int((now - timedelta(days=7)).timestamp() * 1000)
    month_ago_ms = int((now - timedelta(days=30)).timestamp() * 1000)

    # ── 1. Count total data points (events in last 30 days) ──
    row = conn.execute(
        text("SELECT COUNT(*) FROM events WHERE shop_domain = :shop AND timestamp > :ts"),
        {"shop": shop_domain, "ts": month_ago_ms},
    ).fetchone()
    data_points = row[0] if row else 0

    if data_points < 10:
        return None  # Not enough data to compute anything meaningful

    # ── 2. Behavioral baselines from product_metrics ──
    baselines = _compute_baselines(conn, shop_domain)

    # ── 3. Traffic source quality ──
    source_quality = _compute_source_quality(conn, shop_domain, week_ago_ms)

    # ── 4. Price sensitivity bands ──
    price_bands = _compute_price_sensitivity(conn, shop_domain)

    # ── 5. Learned thresholds (adaptive based on store's own data) ──
    learned = _compute_learned_thresholds(conn, shop_domain, baselines)

    # ── 6. Nudge effectiveness (from proof data) ──
    nudge_scores, best_by_signal = _compute_nudge_effectiveness(conn, shop_domain)

    # ── 7. Signal frequency (last 30 days) ──
    signal_freq = _compute_signal_frequency(conn, shop_domain)

    # ── 8. Temporal patterns ──
    temporal = _compute_temporal_patterns(conn, shop_domain, week_ago_ms)

    # ── 9. Confidence level ──
    if data_points >= _CONFIDENCE_HIGH:
        confidence = "high"
    elif data_points >= _CONFIDENCE_MEDIUM:
        confidence = "medium"
    else:
        confidence = "low"

    # ── 9b. Trust score + 4-dim trust profile ──
    # Computed only when confidence in (medium, high). For low-confidence
    # shops the score stays at the neutral default 0.5 (no claim) — wiring
    # it on low-volume shops would produce noisy false-low alerts. The
    # 4 dimensions are: execution_reliability (apply success), measurement_
    # integrity (holdout p<0.05 rate), outcome_quality (positive lift rate),
    # stability (consistency over rolling 4-week window). Born 2026-05-02
    # to make the landing claim "studies you week-after-week" operationally
    # observable as merchant volume grows.
    trust_score, trust_profile = _compute_trust(conn, shop_domain, confidence)

    # ── 9c. Vertical-tuned prior (Sprint 2 #4 anti-cold-start) ──
    # Deterministic Bayesian shrinkage toward vertical baselines for
    # low-data shops. n_prior=200 means: a shop with 200 observed events
    # is weighted equally with the vertical prior; below that, the prior
    # dominates; above that, shop signal dominates. Pure data, no LLM.
    # Stored in the SIP dict only — NOT a DB column (caller consumes
    # in-memory; sip_snapshots.profile_data preserves the historical
    # value via JSONB serialization).
    vertical_prior = _compute_vertical_prior(
        vertical=vertical,
        observed_cart_rate=baselines.get("cart_rate"),
        data_points=data_points,
        confidence=confidence,
    )

    # ── 10. CIG bootstrap (inject cross-store intelligence when SIP is immature) ──
    if confidence == "low" and not nudge_scores:
        try:
            cig_defaults = _cig_bootstrap(conn, shop_domain)
            if cig_defaults:
                nudge_scores = cig_defaults.get("nudge_type_scores") or nudge_scores
                best_by_signal = cig_defaults.get("best_nudge_by_signal") or best_by_signal
                log.info("sip_engine: CIG bootstrap applied for %s", shop_domain)
        except Exception as exc:
            log.warning("sip_engine: CIG bootstrap failed: %s", exc)

    return {
        "shop_domain": shop_domain,
        "profile_version": 1,
        "baseline_cart_rate": baselines.get("cart_rate"),
        "baseline_scroll_depth": baselines.get("scroll_depth"),
        "baseline_dwell_time": baselines.get("dwell_time"),
        "baseline_return_rate": baselines.get("return_rate"),
        "baseline_views_per_product": baselines.get("views_per_product"),
        "baseline_mobile_pct": baselines.get("mobile_pct"),
        "learned_thresholds": learned,
        "traffic_source_quality": source_quality,
        "price_sensitivity_bands": price_bands,
        "nudge_type_scores": nudge_scores,
        "best_nudge_by_signal": best_by_signal,
        "peak_traffic_hours": temporal,
        "signal_frequency_30d": signal_freq,
        "data_points_total": data_points,
        "confidence_level": confidence,
        "trust_score": trust_score,
        "trust_profile": trust_profile,
        "vertical_prior": vertical_prior,
        "computed_at": now,
    }


def _compute_vertical_prior(
    *,
    vertical: str | None,
    observed_cart_rate: float | None,
    data_points: int,
    confidence: str,
) -> dict | None:
    """Sprint 2 #4 — vertical-tuned prior block.

    Returns None when no vertical is supplied. Otherwise returns a dict
    containing the vertical's industry baselines plus a blended
    cart_rate (deterministic Bayesian shrinkage). The blended value is
    only marked `applied=True` for low-confidence shops — high-confidence
    shops have enough signal that the shrinkage effectively becomes a
    no-op (n_observed >> n_prior).

    Why deterministic-first: founder direttiva 2026-05-09 — "tutto
    superiore ai competitor architetturalmente, niente circa/quasi
    previsionale". The prior is named, the n_prior strength is named,
    the blend formula is verifiable file:line. TW Moby cannot match
    this without admitting their vertical baselines are LLM hallucination.
    """
    if not vertical:
        return None
    try:
        from app.services.vertical_prompt_pack import get_profile
        from app.core.stats import vertical_blend
    except Exception:
        return None

    profile = get_profile(vertical)
    # Convert vertical CVR percentage → fraction matching baseline_cart_rate units
    vertical_cart_rate_frac = profile.cvr_baseline_pct / 100.0
    # n_observed proxy: 30d events. Capped at 5k to bound shrinkage
    # (a hot shop with 50k events still receives the prior, just at
    # vanishing weight ratio 200 / 5200 ≈ 3.8%).
    n_obs_for_blend = min(int(data_points or 0), 5000)
    blended = vertical_blend(
        observed=observed_cart_rate,
        prior=vertical_cart_rate_frac,
        n_observed=n_obs_for_blend,
        n_prior=200,
    )
    return {
        "vertical": vertical,
        "vertical_display": profile.display_name,
        "cvr_baseline_pct": profile.cvr_baseline_pct,
        "aov_baseline_eur": profile.aov_baseline_eur,
        "n_prior_strength": 200,
        "n_observed": n_obs_for_blend,
        "blended_cart_rate": _round(blended),
        # `applied=True` flags low-confidence shops where the prior
        # materially shifts the estimate. High-confidence shops still
        # get the block (for telemetry + Sprint 4 endpoint surfacing)
        # but with applied=False so consumers know shop signal dominates.
        "applied": confidence == "low",
    }


def _compute_trust(
    conn: Connection, shop_domain: str, confidence: str,
) -> tuple[float, dict | None]:
    """Compute (trust_score, trust_profile) for one merchant.

    Confidence gate: only computes for medium/high — low-confidence
    shops keep the neutral default 0.5 (no claim) to avoid noisy
    false-low signals on dev/synthetic shops.

    Sources (all bounded to 30-day window). Sprint 1 #1 of per-shop
    learning engine roadmap — extended 2026-05-09 to read Brain Vero
    decision ledger in addition to legacy action_outcomes:

      execution_reliability  = brain_decisions dispatched without limb
                                error / total dispatched (Brain Vero)
      measurement_integrity  = (action_outcomes measured / attempted) +
                                (brain_decisions outcome_status set /
                                 elapsed-window total). Both sources
                                weight-averaged by sample size.
      outcome_quality        = (action_outcomes 'improved' /
                                measured) + (brain_decisions
                                'effective' / measured). Combined.
      stability              = 1 - normalized variance of trust_score
                                week-over-week (4 weekly snapshots).
                                Falls back to 0.5 if <2 snapshots
                                exist (cold start protection).

    Returns (mean of 4 dims, JSON-encoded profile) or (0.5, None) when
    insufficient data.
    """
    if confidence not in ("medium", "high"):
        return 0.5, None
    try:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        cutoff = _dt.now(_tz.utc).replace(tzinfo=None) - _td(days=30)

        # measurement_integrity + outcome_quality from action_outcomes
        row = conn.execute(text(
            """
            SELECT
                COUNT(*) FILTER (WHERE outcome_status IN ('improved','no_effect','regressed')) AS measured,
                COUNT(*) FILTER (WHERE outcome_status = 'improved') AS improved
            FROM action_outcomes
            WHERE shop_domain = :shop
              AND evaluated_at >= :cutoff
            """
        ), {"cutoff": cutoff, "shop": shop_domain}).fetchone()
        ao_measured = int(row[0] or 0) if row else 0
        ao_improved = int(row[1] or 0) if row else 0

        # Brain Vero v0.4 — brain_decisions outcome ledger (Sprint 1 #1)
        # eligible window: only decisions whose outcome_window has
        # elapsed (otherwise they cannot have been measured yet).
        bd_row = conn.execute(text(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE decision_at + (COALESCE(outcome_window_hours, 24) * INTERVAL '1 hour') <= NOW()
                ) AS elapsed,
                COUNT(*) FILTER (
                    WHERE outcome_status IS NOT NULL
                ) AS measured,
                COUNT(*) FILTER (
                    WHERE outcome_status = 'effective'
                ) AS effective,
                COUNT(*) FILTER (
                    WHERE limb_dispatched IS NOT NULL
                      AND (limb_response::jsonb->>'status') = 'ok'
                ) AS dispatched_ok,
                COUNT(*) FILTER (
                    WHERE limb_dispatched IS NOT NULL
                ) AS dispatched_total
            FROM brain_decisions
            WHERE shop_domain = :shop
              AND decision_at >= :cutoff
            """
        ), {"cutoff": cutoff, "shop": shop_domain}).fetchone()
        bd_elapsed = int(bd_row[0] or 0) if bd_row else 0
        bd_measured = int(bd_row[1] or 0) if bd_row else 0
        bd_effective = int(bd_row[2] or 0) if bd_row else 0
        bd_dispatched_ok = int(bd_row[3] or 0) if bd_row else 0
        bd_dispatched_total = int(bd_row[4] or 0) if bd_row else 0

        # measurement_integrity — weighted across both sources
        ao_mi = 1.0 if ao_measured > 0 else None
        bd_mi = (bd_measured / bd_elapsed) if bd_elapsed > 0 else None
        if ao_mi is not None and bd_mi is not None:
            measurement_integrity = (
                ao_mi * ao_measured + bd_mi * bd_elapsed
            ) / (ao_measured + bd_elapsed)
        elif bd_mi is not None:
            measurement_integrity = bd_mi
        elif ao_mi is not None:
            measurement_integrity = ao_mi
        else:
            measurement_integrity = 0.5

        # outcome_quality — weighted across both sources
        ao_oq = (ao_improved / ao_measured) if ao_measured > 0 else None
        bd_oq = (bd_effective / bd_measured) if bd_measured > 0 else None
        if ao_oq is not None and bd_oq is not None:
            outcome_quality = (
                ao_oq * ao_measured + bd_oq * bd_measured
            ) / (ao_measured + bd_measured)
        elif bd_oq is not None:
            outcome_quality = bd_oq
        elif ao_oq is not None:
            outcome_quality = ao_oq
        else:
            outcome_quality = 0.5

        # execution_reliability — brain_decisions dispatch success rate
        if bd_dispatched_total > 0:
            execution_reliability = bd_dispatched_ok / bd_dispatched_total
        else:
            execution_reliability = 0.5  # no dispatch volume yet

        # stability — variance of weekly trust scores (4 weeks).
        # Reads sip_snapshots.profile_data JSONB; falls back to 0.5
        # when <2 distinct scores (cold start, or all-stub history
        # with same score → no signal, no claim).
        try:
            stab_rows = conn.execute(text(
                """
                SELECT (profile_data::jsonb->>'trust_score')::float AS trust_score
                FROM sip_snapshots
                WHERE shop_domain = :shop
                  AND snapshot_week >= :cutoff
                ORDER BY snapshot_week DESC
                LIMIT 4
                """
            ), {"cutoff": cutoff, "shop": shop_domain}).fetchall()
            scores = [float(r[0]) for r in stab_rows if r[0] is not None]
            distinct = len(set(round(s, 4) for s in scores))
            if len(scores) >= 2 and distinct >= 2:
                mean = sum(scores) / len(scores)
                variance = sum((s - mean) ** 2 for s in scores) / len(scores)
                # Normalize: variance 0 = perfect stability (1.0);
                # variance 0.0625 (0.25 std dev) or higher = chaos (0.0).
                # Trust scores are bounded [0,1] so variance max is 0.25.
                stability = max(0.0, 1.0 - (variance / 0.0625))
            else:
                stability = 0.5
        except Exception as exc:
            log.warning("sip_engine: stability variance read failed for %s: %s", shop_domain, exc)
            stability = 0.5

        score = (
            execution_reliability + measurement_integrity
            + outcome_quality + stability
        ) / 4.0
        score = max(0.0, min(1.0, score))
        profile = {
            "execution_reliability": round(execution_reliability, 3),
            "measurement_integrity": round(measurement_integrity, 3),
            "outcome_quality": round(outcome_quality, 3),
            "stability": round(stability, 3),
            "overall": round(score, 3),
            "data_window_days": 30,
            "evidence": {
                "action_outcomes": {
                    "measured": ao_measured, "improved": ao_improved,
                },
                "brain_decisions": {
                    "elapsed": bd_elapsed,
                    "measured": bd_measured,
                    "effective": bd_effective,
                    "dispatched_total": bd_dispatched_total,
                    "dispatched_ok": bd_dispatched_ok,
                },
            },
        }
        return round(score, 3), profile
    except Exception as exc:
        log.warning("sip_engine: trust score computation failed for %s: %s", shop_domain, exc)
        return 0.5, None


def _autonomy_level_from_trust(trust_score: float, confidence: str) -> int:
    """Compute autonomy_level (0-5) from trust_score + confidence.
    Documented in StoreIntelligenceProfile: 0=observe, 1=suggest,
    2=assisted, 3=semi-auto, 4=full-auto, 5=aggressive.
    Promotion is monotonic — never demote based on a single computation
    (caller checks current vs new and keeps the max). Born 2026-05-02
    elite-tier brutal-CTO follow-up to make the autonomy doctrine
    operational at merchant volume."""
    if confidence == "low":
        return 0  # observe-only until enough data
    if confidence == "medium":
        if trust_score >= 0.85:
            return 2
        if trust_score >= 0.70:
            return 1
        return 0
    # confidence == "high"
    if trust_score >= 0.95:
        return 5
    if trust_score >= 0.85:
        return 4
    if trust_score >= 0.75:
        return 3
    if trust_score >= 0.65:
        return 2
    if trust_score >= 0.50:
        return 1
    return 0


def upsert_sip(conn: Connection, sip: dict[str, Any]) -> None:
    """Upsert one store_intelligence_profiles row."""
    # Compute autonomy_level from trust_score + confidence; never DEMOTE
    # below the existing row's level (monotonic promotion). Pre-fetch
    # current to enforce the floor.
    new_trust = float(sip.get("trust_score") or 0.5)
    new_autonomy = _autonomy_level_from_trust(
        new_trust, sip.get("confidence_level") or "low"
    )
    try:
        cur = conn.execute(text(
            "SELECT autonomy_level FROM store_intelligence_profiles WHERE shop_domain = :s"
        ), {"s": sip["shop_domain"]}).fetchone()
        if cur and int(cur[0] or 0) > new_autonomy:
            new_autonomy = int(cur[0])  # monotonic floor
    except Exception:
        pass  # SILENT-EXCEPT-OK: monotonic-floor read is best-effort; on miss we use the freshly-computed new_autonomy (no regression risk).
    sip["autonomy_level"] = new_autonomy

    conn.execute(
        text("""
            INSERT INTO store_intelligence_profiles (
                shop_domain, profile_version,
                baseline_cart_rate, baseline_scroll_depth, baseline_dwell_time,
                baseline_return_rate, baseline_views_per_product, baseline_mobile_pct,
                learned_thresholds, traffic_source_quality, price_sensitivity_bands,
                nudge_type_scores, best_nudge_by_signal,
                peak_traffic_hours, signal_frequency_30d,
                data_points_total, confidence_level, computed_at, updated_at,
                trust_score, trust_profile, autonomy_level
            ) VALUES (
                :shop_domain, :profile_version,
                :baseline_cart_rate, :baseline_scroll_depth, :baseline_dwell_time,
                :baseline_return_rate, :baseline_views_per_product, :baseline_mobile_pct,
                :learned_thresholds, :traffic_source_quality, :price_sensitivity_bands,
                :nudge_type_scores, :best_nudge_by_signal,
                :peak_traffic_hours, :signal_frequency_30d,
                :data_points_total, :confidence_level, :computed_at, NOW(),
                :trust_score, :trust_profile, :autonomy_level
            )
            ON CONFLICT (shop_domain) DO UPDATE SET
                profile_version = EXCLUDED.profile_version,
                baseline_cart_rate = EXCLUDED.baseline_cart_rate,
                baseline_scroll_depth = EXCLUDED.baseline_scroll_depth,
                baseline_dwell_time = EXCLUDED.baseline_dwell_time,
                baseline_return_rate = EXCLUDED.baseline_return_rate,
                baseline_views_per_product = EXCLUDED.baseline_views_per_product,
                baseline_mobile_pct = EXCLUDED.baseline_mobile_pct,
                learned_thresholds = EXCLUDED.learned_thresholds,
                traffic_source_quality = EXCLUDED.traffic_source_quality,
                price_sensitivity_bands = EXCLUDED.price_sensitivity_bands,
                nudge_type_scores = EXCLUDED.nudge_type_scores,
                best_nudge_by_signal = EXCLUDED.best_nudge_by_signal,
                peak_traffic_hours = EXCLUDED.peak_traffic_hours,
                signal_frequency_30d = EXCLUDED.signal_frequency_30d,
                data_points_total = EXCLUDED.data_points_total,
                confidence_level = EXCLUDED.confidence_level,
                trust_score = EXCLUDED.trust_score,
                trust_profile = EXCLUDED.trust_profile,
                autonomy_level = EXCLUDED.autonomy_level,
                computed_at = EXCLUDED.computed_at,
                updated_at = NOW()
        """),
        {
            **sip,
            # Cast JSONB fields to strings for psycopg2
            "learned_thresholds": _json(sip.get("learned_thresholds")),
            "traffic_source_quality": _json(sip.get("traffic_source_quality")),
            "price_sensitivity_bands": _json(sip.get("price_sensitivity_bands")),
            "nudge_type_scores": _json(sip.get("nudge_type_scores")),
            "best_nudge_by_signal": _json(sip.get("best_nudge_by_signal")),
            "peak_traffic_hours": _json(sip.get("peak_traffic_hours")),
            "signal_frequency_30d": _json(sip.get("signal_frequency_30d")),
            "trust_profile": _json(sip.get("trust_profile")),
        },
    )


def maybe_snapshot(conn: Connection, sip: dict[str, Any]) -> None:
    """Insert weekly snapshot if one doesn't exist for the current week."""
    import json
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Monday of current week
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    exists = conn.execute(
        text("""
            SELECT 1 FROM sip_snapshots
            WHERE shop_domain = :shop AND snapshot_week = :week
            LIMIT 1
        """),
        {"shop": sip["shop_domain"], "week": week_start},
    ).fetchone()

    if exists:
        return

    # Build snapshot data (exclude non-serializable fields)
    snapshot_data = {k: v for k, v in sip.items() if k not in ("computed_at",)}

    conn.execute(
        text("""
            INSERT INTO sip_snapshots (shop_domain, snapshot_week, profile_data, baseline_cart_rate, data_points)
            VALUES (:shop, :week, :data, :cart_rate, :points)
            ON CONFLICT (shop_domain, snapshot_week) DO NOTHING
        """),
        {
            "shop": sip["shop_domain"],
            "week": week_start,
            "data": json.dumps(snapshot_data, default=str),
            "cart_rate": sip.get("baseline_cart_rate"),
            "points": sip.get("data_points_total", 0),
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# Internal computation functions
# ══════════════════════════════════════════════════════════════════════════

def _compute_baselines(conn: Connection, shop: str) -> dict:
    """Store-wide baselines from product_metrics (7d rolling window)."""
    row = conn.execute(
        text("""
            SELECT
                CASE WHEN SUM(views_7d) > 0
                     THEN SUM(cart_conversions_7d)::FLOAT / SUM(views_7d)
                     ELSE NULL END                          AS cart_rate,
                AVG(avg_scroll_24h)                         AS scroll_depth,
                AVG(avg_dwell_24h)                          AS dwell_time,
                CASE WHEN SUM(unique_visitors_7d) > 0
                     THEN SUM(return_visitor_count_7d)::FLOAT / SUM(unique_visitors_7d)
                     ELSE NULL END                          AS return_rate,
                AVG(views_7d)                               AS views_per_product,
                CASE WHEN SUM(views_7d) > 0
                     THEN SUM(views_mobile)::FLOAT * 7 / SUM(views_7d)
                     ELSE NULL END                          AS mobile_pct,
                COUNT(*)                                    AS product_count
            FROM product_metrics
            WHERE shop_domain = :shop
              AND views_7d > 0
        """),
        {"shop": shop},
    ).fetchone()

    if not row or not row[6]:  # product_count
        return {}

    return {
        "cart_rate": _round(row[0]),
        "scroll_depth": _round(row[1]),
        "dwell_time": _round(row[2]),
        "return_rate": _round(row[3]),
        "views_per_product": _round(row[4]),
        "mobile_pct": _round(min(row[5], 1.0) if row[5] else None),
    }


def _compute_source_quality(conn: Connection, shop: str, cutoff_ms: int) -> dict | None:
    """Per-source conversion quality score (cart_rate relative to store average)."""
    rows = conn.execute(
        text("""
            SELECT
                COALESCE(NULLIF(utm_source, ''), source_type, 'direct') AS src,
                COUNT(*) FILTER (WHERE event_type = 'product_view')     AS views,
                COUNT(*) FILTER (WHERE event_type = 'add_to_cart')      AS carts
            FROM events
            WHERE shop_domain = :shop
              AND timestamp > :ts
              AND event_type IN ('product_view', 'add_to_cart')
            GROUP BY 1
            HAVING COUNT(*) FILTER (WHERE event_type = 'product_view') >= 10
        """),
        {"shop": shop, "ts": cutoff_ms},
    ).fetchall()

    if not rows:
        return None

    total_views = sum(r[1] for r in rows)
    total_carts = sum(r[2] for r in rows)
    overall_rate = total_carts / total_views if total_views > 0 else 0

    if overall_rate == 0:
        return None

    return {
        r[0]: _round(((r[2] / r[1]) / overall_rate) if r[1] > 0 else 0)
        for r in rows
        if r[0]
    }


def _compute_price_sensitivity(conn: Connection, shop: str) -> list | None:
    """Cart rate by price band (from product_metrics + shop_orders for AOV)."""
    # Use revenue_24h / purchases_24h as proxy for product price
    rows = conn.execute(
        text("""
            SELECT
                CASE WHEN purchases_24h > 0 THEN revenue_24h / purchases_24h ELSE NULL END AS est_price,
                views_7d,
                cart_conversions_7d
            FROM product_metrics
            WHERE shop_domain = :shop
              AND views_7d > 5
        """),
        {"shop": shop},
    ).fetchall()

    if not rows:
        return None

    bands: dict[str, dict] = {}
    for lo, hi, label in _PRICE_BANDS:
        bands[label] = {"range": label, "views": 0, "carts": 0, "products": 0}

    for price, views, carts in rows:
        if price is None:
            continue
        for lo, hi, label in _PRICE_BANDS:
            if lo <= price < hi:
                bands[label]["views"] += views or 0
                bands[label]["carts"] += carts or 0
                bands[label]["products"] += 1
                break

    result = []
    for b in bands.values():
        if b["views"] > 0:
            b["cart_rate"] = _round(b["carts"] / b["views"])
            del b["carts"]
            del b["views"]
            result.append(b)

    return result if result else None


def _compute_learned_thresholds(conn: Connection, shop: str, baselines: dict) -> dict | None:
    """
    Adaptive thresholds based on store's own traffic patterns.

    The global constants assume: 20 views = sufficient traffic.
    A high-traffic store with 200 views/product/day should have a higher floor.
    A low-traffic store with 5 views/product/day should have a lower floor.
    """
    vpd = baselines.get("views_per_product")
    if vpd is None:
        return None
    vpd = float(vpd)

    # Views floor: max(5, store_avg * 0.3) — never below 5
    views_floor = max(5, round(vpd * 0.3))

    dwell = baselines.get("dwell_time")
    dwell = float(dwell) if dwell is not None else None
    # Dwell floor: 60% of store average (below this = "dead traffic" for THIS store)
    dwell_floor = round(dwell * 0.6, 1) if dwell and dwell > 2 else 5

    return_rate = baselines.get("return_rate")
    return_rate = float(return_rate) if return_rate is not None else None
    # Return visitor floor: adaptive based on store's return rate
    return_floor = max(3, round(5 * (1 - (return_rate or 0))))

    cart_rate = baselines.get("cart_rate")
    cart_rate = float(cart_rate) if cart_rate is not None else None
    # Low conversion threshold: 40% of store's baseline (not a global 2%)
    low_conv_threshold = round(cart_rate * 0.4, 4) if cart_rate and cart_rate > 0 else 0.02

    return {
        "views_floor": views_floor,
        "dwell_floor": dwell_floor,
        "return_floor": return_floor,
        "low_conv_threshold": low_conv_threshold,
    }


def _compute_nudge_effectiveness(conn: Connection, shop: str) -> tuple[dict | None, dict | None]:
    """
    Compute per-nudge-type effectiveness from active_nudges + nudge_events + purchases.

    Scores each nudge variant type by its proven impact. Requires nudges to have been
    active long enough to generate nudge_events with measurable outcomes.
    """
    rows = conn.execute(
        text("""
            SELECT
                an.copy_variant                                         AS nudge_type,
                an.product_url,
                os.signal_type,
                COUNT(DISTINCT ne.visitor_id) FILTER (WHERE ne.event_type = 'shown')  AS shown,
                COUNT(DISTINCT ne.visitor_id) FILTER (WHERE ne.event_type = 'clicked') AS clicked
            FROM active_nudges an
            JOIN nudge_events ne ON ne.nudge_id = an.id AND ne.shop_domain = an.shop_domain
            LEFT JOIN opportunity_signals os
                ON os.shop_domain = an.shop_domain AND os.product_url = an.product_url
            WHERE an.shop_domain = :shop
              AND an.status IN ('active', 'expired', 'deactivated')
              AND COALESCE(an.is_bootstrap, false) = false
            GROUP BY 1, 2, 3
            HAVING COUNT(DISTINCT ne.visitor_id) FILTER (WHERE ne.event_type = 'shown') >= 10
        """),
        {"shop": shop},
    ).fetchall()

    if not rows:
        return None, None

    # Aggregate by nudge_type
    type_scores: dict[str, dict] = {}
    signal_scores: dict[str, dict] = {}

    for nudge_type, _product, signal_type, shown, clicked in rows:
        if not nudge_type:
            continue
        click_rate = clicked / shown if shown > 0 else 0

        if nudge_type not in type_scores:
            type_scores[nudge_type] = {"total_shown": 0, "total_clicked": 0}
        type_scores[nudge_type]["total_shown"] += shown
        type_scores[nudge_type]["total_clicked"] += clicked

        if signal_type:
            key = f"{signal_type}:{nudge_type}"
            if key not in signal_scores:
                signal_scores[key] = {"shown": 0, "clicked": 0}
            signal_scores[key]["shown"] += shown
            signal_scores[key]["clicked"] += clicked

    # Compute scores (0-1 range based on click rate relative to best)
    nudge_rates = {
        nt: d["total_clicked"] / d["total_shown"] if d["total_shown"] > 0 else 0
        for nt, d in type_scores.items()
    }
    max_rate = max(nudge_rates.values()) if nudge_rates else 1
    nudge_type_scores = {
        nt: _round(rate / max_rate) if max_rate > 0 else 0
        for nt, rate in nudge_rates.items()
    }

    # Best nudge per signal type
    best_by_signal: dict[str, str] = {}
    for key, d in signal_scores.items():
        signal_type, nudge_type = key.split(":", 1)
        rate = d["clicked"] / d["shown"] if d["shown"] > 0 else 0
        if signal_type not in best_by_signal or rate > signal_scores.get(
            f"{signal_type}:{best_by_signal[signal_type]}", {}
        ).get("clicked", 0) / max(signal_scores.get(
            f"{signal_type}:{best_by_signal[signal_type]}", {}
        ).get("shown", 1), 1):
            best_by_signal[signal_type] = nudge_type

    return nudge_type_scores or None, best_by_signal or None


def _compute_signal_frequency(conn: Connection, shop: str) -> dict | None:
    """Count of each signal type detected in the last 30 days (from opportunity_signals)."""
    # opportunity_signals has a 24h TTL so we can't look back 30 days from live rows.
    # Instead, count from current signals (what's active now).
    rows = conn.execute(
        text("""
            SELECT signal_type, COUNT(*)
            FROM opportunity_signals
            WHERE shop_domain = :shop
            GROUP BY signal_type
        """),
        {"shop": shop},
    ).fetchall()

    if not rows:
        return None

    return {r[0]: r[1] for r in rows}


def _compute_temporal_patterns(conn: Connection, shop: str, cutoff_ms: int) -> list | None:
    """Traffic and conversion by hour-of-day (last 7 days)."""
    rows = conn.execute(
        text("""
            SELECT
                EXTRACT(HOUR FROM TO_TIMESTAMP(timestamp / 1000.0)) AS hr,
                COUNT(*) FILTER (WHERE event_type = 'product_view')  AS views,
                COUNT(*) FILTER (WHERE event_type = 'add_to_cart')   AS carts
            FROM events
            WHERE shop_domain = :shop
              AND timestamp > :ts
              AND event_type IN ('product_view', 'add_to_cart')
            GROUP BY 1
            ORDER BY 1
        """),
        {"shop": shop, "ts": cutoff_ms},
    ).fetchall()

    if not rows:
        return None

    return [
        {"hour": int(r[0]), "views": r[1], "carts": r[2]}
        for r in rows
        if r[1] > 0
    ]


# ── Helpers ──

def _round(v: float | None, digits: int = 4) -> float | None:
    return round(float(v), digits) if v is not None else None


def _json(v: Any) -> str | None:
    """Serialize JSONB-bound values for psycopg2."""
    import json
    if v is None:
        return None
    return json.dumps(v, default=str)


def _cig_bootstrap(conn: Connection, shop_domain: str) -> dict | None:
    """
    Load CIG cross-store defaults for a new merchant.
    Returns nudge_type_scores and best_nudge_by_signal from the best-matching cohort.
    """
    row = conn.execute(
        text("""
            SELECT c.nudge_effectiveness, c.playbooks
            FROM cig_merchant_mappings m
            JOIN cig_cohorts c ON c.cohort_key = m.primary_cohort_key
            WHERE m.shop_domain = :shop AND c.merchant_count >= 3
        """),
        {"shop": shop_domain},
    ).fetchone()

    if not row:
        return None

    nudge_eff = row[0]  # JSONB
    playbooks = row[1]  # JSONB

    # Convert nudge effectiveness to scores
    scores = {}
    if nudge_eff and isinstance(nudge_eff, dict):
        for nt, data in nudge_eff.items():
            if isinstance(data, dict):
                scores[nt] = round(min(1.0, data.get("avg_lift", 0) * 2), 4)

    # Build best-by-signal from playbooks
    best = {}
    if playbooks and isinstance(playbooks, list):
        for p in playbooks:
            if isinstance(p, dict) and p.get("signal") and p.get("best_nudge"):
                best[p["signal"]] = p["best_nudge"]

    if not scores and not best:
        return None

    return {"nudge_type_scores": scores or None, "best_nudge_by_signal": best or None}
