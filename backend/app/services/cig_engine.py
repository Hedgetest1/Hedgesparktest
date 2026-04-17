"""
cig_engine.py — Commerce Intelligence Graph computation engine.

Aggregates intelligence across all merchants to build cohort-level insights.
Feeds cross-store intelligence back into individual SIPs for:
  - Cold-start bootstrap (new merchants get Day 1 intelligence)
  - Decision support (CIG fills gaps when SIP is immature)
  - Benchmarking (where does this store stand vs peers?)

Architecture:
  1. Build store fingerprints from SIP data
  2. Assign stores to cohorts via dimensional bucketing
  3. Aggregate SIP data within cohorts (anonymized, weighted)
  4. Generate 5 intelligence types per cohort
  5. Map each merchant to top 3 closest cohorts with similarity scores
  6. Bootstrap new merchants with CIG intelligence

Safety:
  - Minimum 3 merchants per cohort (anonymization)
  - Outlier filtering (trim extreme values before aggregation)
  - Confidence scoring per insight
  - No raw merchant data leaks into cohort tables

Schedule: Weekly (Sunday night), called by aggregation worker.
"""
from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# ── Constants ──
_MIN_COHORT_SIZE = 3
_OUTLIER_TRIM_PCT = 0.10  # trim top/bottom 10% before averaging


# ══════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════

def compute_cig(conn: Connection) -> int:
    """
    Full CIG computation cycle. Returns count of cohorts computed.

    Steps:
    1. Load all SIPs with confidence >= "low" and data_points >= 50
    2. Build fingerprint for each store
    3. Assign to dimensional cohorts
    4. Aggregate each cohort (only if >= 3 members)
    5. Generate insights + playbooks
    6. Upsert cig_cohorts + cig_merchant_mappings
    """
    # Step 1: Load SIPs
    rows = conn.execute(text("""
        SELECT shop_domain, baseline_cart_rate, baseline_scroll_depth, baseline_dwell_time,
               baseline_return_rate, baseline_views_per_product, baseline_mobile_pct,
               nudge_type_scores, best_nudge_by_signal, signal_frequency_30d,
               traffic_source_quality, price_sensitivity_bands,
               data_points_total, confidence_level, trust_score
        FROM store_intelligence_profiles
        WHERE data_points_total >= 50
    """)).fetchall()

    if len(rows) < _MIN_COHORT_SIZE:
        log.info("cig_engine: only %d stores with data — skipping (need %d)", len(rows), _MIN_COHORT_SIZE)
        return 0

    # Step 2: Build fingerprints
    stores: list[dict] = []
    for r in rows:
        fp = _build_fingerprint(r)
        if fp:
            stores.append(fp)

    if len(stores) < _MIN_COHORT_SIZE:
        return 0

    # Step 3: Assign to cohorts
    cohort_groups: dict[str, list[dict]] = defaultdict(list)
    for s in stores:
        key = _cohort_key(s)
        cohort_groups[key].append(s)

    # Step 4 + 5: Aggregate and generate insights
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    computed = 0

    for key, members in cohort_groups.items():
        if len(members) < _MIN_COHORT_SIZE:
            continue

        cohort = _aggregate_cohort(key, members)
        _upsert_cohort(conn, cohort, now)
        computed += 1

    # Step 6: Map merchants to cohorts
    all_cohort_keys = [k for k, m in cohort_groups.items() if len(m) >= _MIN_COHORT_SIZE]
    for s in stores:
        _map_merchant_to_cohorts(conn, s, cohort_groups, all_cohort_keys, now)

    conn.commit()
    log.info("cig_engine: computed %d cohorts from %d stores", computed, len(stores))
    return computed


def bootstrap_new_merchant(db: Session, shop_domain: str) -> dict | None:
    """
    Bootstrap a new merchant with CIG intelligence.

    Called when SIP confidence is "low" — injects cohort-level defaults
    to accelerate the cold-start phase.

    Returns a dict of bootstrap values or None if no suitable cohort found.
    """
    mapping = db.execute(
        text("SELECT primary_cohort_key, primary_similarity FROM cig_merchant_mappings WHERE shop_domain = :shop"),
        {"shop": shop_domain},
    ).fetchone()

    if not mapping or not mapping[0]:
        return None

    cohort = db.execute(
        text("SELECT * FROM cig_cohorts WHERE cohort_key = :key"),
        {"key": mapping[0]},
    ).mappings().first()

    if not cohort or cohort["merchant_count"] < _MIN_COHORT_SIZE:
        return None

    similarity = mapping[1] or 0

    bootstrap = {
        "source": "cig",
        "cohort_key": mapping[0],
        "similarity": similarity,
        "nudge_type_scores": cohort["nudge_effectiveness"],
        "baseline_cart_rate": cohort["avg_cart_rate"],
        "baseline_scroll_depth": cohort["avg_scroll_depth"],
        "baseline_dwell_time": cohort["avg_dwell_time"],
        "playbooks": cohort["playbooks"],
        "confidence": cohort["confidence_level"],
    }

    log.info("cig_engine: bootstrap for %s from cohort %s (sim=%.2f, n=%d)",
             shop_domain, mapping[0], similarity, cohort["merchant_count"])

    return bootstrap


def get_cig_nudge_recommendation(db: Session, shop_domain: str, signal_type: str) -> tuple[str | None, float | None, str]:
    """
    Get CIG-informed nudge recommendation for a specific signal.
    Used when SIP has no learned data for this signal/nudge combination.

    Returns (nudge_type, confidence_score, reason) or (None, None, "no CIG data").
    """
    mapping = db.execute(
        text("SELECT primary_cohort_key FROM cig_merchant_mappings WHERE shop_domain = :shop"),
        {"shop": shop_domain},
    ).fetchone()

    if not mapping or not mapping[0]:
        return None, None, "No CIG mapping"

    cohort = db.execute(
        text("SELECT playbooks, nudge_effectiveness, confidence_level FROM cig_cohorts WHERE cohort_key = :key"),
        {"key": mapping[0]},
    ).mappings().first()

    if not cohort:
        return None, None, "Cohort not found"

    # Check playbooks first (signal-specific)
    playbooks = cohort["playbooks"] or []
    for p in playbooks:
        if p.get("signal") == signal_type and p.get("avg_lift", 0) > 0:
            return p["best_nudge"], p.get("avg_lift", 0), f"CIG playbook: {p['best_nudge']} (lift={p['avg_lift']:.0%}, n={p.get('n', 0)})"

    # Fall back to overall nudge effectiveness
    effectiveness = cohort["nudge_effectiveness"] or {}
    if effectiveness:
        best = max(effectiveness.items(), key=lambda x: x[1].get("avg_lift", 0) if isinstance(x[1], dict) else 0, default=None)
        if best:
            lift = best[1].get("avg_lift", 0) if isinstance(best[1], dict) else 0
            n = best[1].get("n", 0) if isinstance(best[1], dict) else 0
            if lift > 0:
                return best[0], lift, f"CIG best nudge: {best[0]} (lift={lift:.0%}, n={n})"

    return None, None, "No CIG nudge data"


# ══════════════════════════════════════════════════════════════════════════
# Fingerprint + Cohort Assignment
# ══════════════════════════════════════════════════════════════════════════

def _build_fingerprint(row) -> dict | None:
    """Build a multi-dimensional fingerprint from a SIP row."""
    cart_rate = float(row[1]) if row[1] is not None else None
    scroll = float(row[2]) if row[2] is not None else None
    dwell = float(row[3]) if row[3] is not None else None
    return_rate = float(row[4]) if row[4] is not None else None
    vpd = float(row[5]) if row[5] is not None else None
    mobile = float(row[6]) if row[6] is not None else None
    data_points = int(row[12])

    if vpd is None:
        return None

    # Compute AOV from order data would be ideal, but we use cart_rate as proxy
    # for store "tier" combined with traffic volume
    return {
        "shop_domain": row[0],
        "cart_rate": cart_rate or 0,
        "scroll_depth": scroll or 0,
        "dwell_time": dwell or 0,
        "return_rate": return_rate or 0,
        "views_per_product": vpd,
        "mobile_pct": mobile or 0.5,
        "nudge_type_scores": row[7],
        "best_nudge_by_signal": row[8],
        "signal_frequency": row[9],
        "traffic_source_quality": row[10],
        "price_sensitivity": row[11],
        "data_points": data_points,
        "confidence": row[13],
        "trust_score": float(row[14]) if row[14] else 0.5,
    }


def _cohort_key(fp: dict) -> str:
    """Assign a store to a dimensional cohort bucket."""
    vpd = fp["views_per_product"]
    mobile = fp["mobile_pct"]

    # Traffic band
    if vpd < 5:
        traffic = "low"
    elif vpd < 50:
        traffic = "mid"
    else:
        traffic = "high"

    # Mobile band
    if mobile < 0.4:
        mobile_b = "low"
    elif mobile < 0.7:
        mobile_b = "mid"
    else:
        mobile_b = "high"

    # AOV proxy: use cart_rate as a behavioral segment
    cr = fp["cart_rate"]
    if cr < 0.02:
        aov = "low"
    elif cr < 0.05:
        aov = "mid"
    else:
        aov = "high"

    return f"{aov}:{traffic}:{mobile_b}"


def _cohort_similarity(fp: dict, cohort_key: str) -> float:
    """Compute similarity between a store fingerprint and a cohort."""
    parts = cohort_key.split(":")
    if len(parts) != 3:
        return 0.0

    aov_b, traffic_b, mobile_b = parts
    own_key = _cohort_key(fp)
    own_parts = own_key.split(":")

    # Simple: count matching dimensions
    matches = sum(1 for a, b in zip(own_parts, parts) if a == b)
    return matches / 3.0


# ══════════════════════════════════════════════════════════════════════════
# Cohort Aggregation
# ══════════════════════════════════════════════════════════════════════════

def _aggregate_cohort(key: str, members: list[dict]) -> dict:
    """Aggregate member SIPs into cohort-level intelligence."""
    parts = key.split(":")
    aov_b, traffic_b, mobile_b = parts if len(parts) == 3 else ("mid", "mid", "mid")

    n = len(members)
    total_dp = sum(m["data_points"] for m in members)

    # Trimmed means (remove outliers)
    cart_rates = _trimmed_values([m["cart_rate"] for m in members])
    scrolls = _trimmed_values([m["scroll_depth"] for m in members])
    dwells = _trimmed_values([m["dwell_time"] for m in members])
    returns = _trimmed_values([m["return_rate"] for m in members])

    # Percentiles for cart rate
    sorted_cr = sorted(m["cart_rate"] for m in members)
    p25 = sorted_cr[max(0, int(n * 0.25))] if n > 0 else None
    p75 = sorted_cr[min(n - 1, int(n * 0.75))] if n > 0 else None

    # Aggregate nudge effectiveness
    nudge_eff = _aggregate_nudge_effectiveness(members)

    # Signal distribution
    signal_dist = _aggregate_signal_distribution(members)

    # Traffic source quality
    traffic_q = _aggregate_traffic_quality(members)

    # Price sensitivity
    price_sens = _aggregate_price_sensitivity(members)

    # Playbooks: best nudge per signal type
    playbooks = _build_playbooks(members)

    # Confidence
    if n >= 10 and total_dp >= 10000:
        confidence = "high"
    elif n >= 5 and total_dp >= 2000:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "cohort_key": key,
        "aov_band": aov_b,
        "traffic_band": traffic_b,
        "mobile_band": mobile_b,
        "avg_cart_rate": _safe_mean(cart_rates),
        "avg_scroll_depth": _safe_mean(scrolls),
        "avg_dwell_time": _safe_mean(dwells),
        "avg_return_rate": _safe_mean(returns),
        "p25_cart_rate": p25,
        "p75_cart_rate": p75,
        "nudge_effectiveness": nudge_eff,
        "signal_distribution": signal_dist,
        "price_sensitivity": price_sens,
        "traffic_quality": traffic_q,
        "playbooks": playbooks,
        "merchant_count": n,
        "total_data_points": total_dp,
        "confidence_level": confidence,
    }


def _aggregate_nudge_effectiveness(members: list[dict]) -> dict | None:
    """Aggregate nudge_type_scores across stores."""
    agg: dict[str, list[float]] = defaultdict(list)
    for m in members:
        scores = m.get("nudge_type_scores") or {}
        if isinstance(scores, dict):
            for nt, score in scores.items():
                if isinstance(score, (int, float)):
                    agg[nt].append(float(score))

    if not agg:
        return None

    return {
        nt: {
            "avg_lift": round(_safe_mean(_trimmed_values(vals)), 4),
            "n": len(vals),
            "confidence": "high" if len(vals) >= 5 else "medium" if len(vals) >= 3 else "low",
        }
        for nt, vals in agg.items()
        if len(vals) >= 2
    }


def _aggregate_signal_distribution(members: list[dict]) -> dict | None:
    """What proportion of stores fire each signal type."""
    signal_counts: dict[str, int] = defaultdict(int)
    total = len(members)
    for m in members:
        freq = m.get("signal_frequency") or {}
        if isinstance(freq, dict):
            for sig in freq:
                signal_counts[sig] += 1

    if not signal_counts or total <= 0:
        return None

    return {sig: round(count / total, 3) for sig, count in signal_counts.items()}


def _aggregate_traffic_quality(members: list[dict]) -> dict | None:
    """Average traffic source quality across stores."""
    agg: dict[str, list[float]] = defaultdict(list)
    for m in members:
        tq = m.get("traffic_source_quality") or {}
        if isinstance(tq, dict):
            for src, score in tq.items():
                if isinstance(score, (int, float)):
                    agg[src].append(float(score))

    if not agg:
        return None

    return {src: round(_safe_mean(vals), 3) for src, vals in agg.items() if len(vals) >= 2}


def _aggregate_price_sensitivity(members: list[dict]) -> list | None:
    """Merge price sensitivity bands across stores."""
    band_agg: dict[str, list[float]] = defaultdict(list)
    for m in members:
        bands = m.get("price_sensitivity") or []
        if isinstance(bands, list):
            for b in bands:
                if isinstance(b, dict) and "range" in b and "cart_rate" in b:
                    band_agg[b["range"]].append(float(b["cart_rate"]))

    if not band_agg:
        return None

    return [
        {"range": r, "avg_cart_rate": round(_safe_mean(vals), 4), "stores": len(vals)}
        for r, vals in band_agg.items()
        if len(vals) >= 2
    ]


def _build_playbooks(members: list[dict]) -> list:
    """Build optimization playbooks: best nudge per signal type across stores."""
    # signal_type → nudge_type → scores
    combo: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    for m in members:
        bnbs = m.get("best_nudge_by_signal") or {}
        scores = m.get("nudge_type_scores") or {}
        if isinstance(bnbs, dict) and isinstance(scores, dict):
            for sig, nudge in bnbs.items():
                score = scores.get(nudge, 0)
                if isinstance(score, (int, float)):
                    combo[sig][nudge].append(float(score))

    playbooks = []
    for sig, nudge_map in combo.items():
        best_nudge = None
        best_avg = 0
        best_n = 0
        for nudge, vals in nudge_map.items():
            avg = _safe_mean(vals)
            if avg > best_avg and len(vals) >= 2:
                best_nudge = nudge
                best_avg = avg
                best_n = len(vals)

        if best_nudge and best_avg > 0:
            playbooks.append({
                "signal": sig,
                "best_nudge": best_nudge,
                "avg_lift": round(best_avg, 4),
                "n": best_n,
            })

    return playbooks


# ══════════════════════════════════════════════════════════════════════════
# Persistence
# ══════════════════════════════════════════════════════════════════════════

def _upsert_cohort(conn: Connection, cohort: dict, now: datetime) -> None:
    conn.execute(
        text("""
            INSERT INTO cig_cohorts (
                cohort_key, aov_band, traffic_band, mobile_band,
                avg_cart_rate, avg_scroll_depth, avg_dwell_time, avg_return_rate,
                p25_cart_rate, p75_cart_rate,
                nudge_effectiveness, signal_distribution, price_sensitivity,
                traffic_quality, playbooks,
                merchant_count, total_data_points, confidence_level,
                computed_at, updated_at
            ) VALUES (
                :cohort_key, :aov_band, :traffic_band, :mobile_band,
                :avg_cart_rate, :avg_scroll_depth, :avg_dwell_time, :avg_return_rate,
                :p25_cart_rate, :p75_cart_rate,
                :nudge_effectiveness, :signal_distribution, :price_sensitivity,
                :traffic_quality, :playbooks,
                :merchant_count, :total_data_points, :confidence_level,
                :computed_at, :computed_at
            )
            ON CONFLICT (cohort_key) DO UPDATE SET
                avg_cart_rate = EXCLUDED.avg_cart_rate,
                avg_scroll_depth = EXCLUDED.avg_scroll_depth,
                avg_dwell_time = EXCLUDED.avg_dwell_time,
                avg_return_rate = EXCLUDED.avg_return_rate,
                p25_cart_rate = EXCLUDED.p25_cart_rate,
                p75_cart_rate = EXCLUDED.p75_cart_rate,
                nudge_effectiveness = EXCLUDED.nudge_effectiveness,
                signal_distribution = EXCLUDED.signal_distribution,
                price_sensitivity = EXCLUDED.price_sensitivity,
                traffic_quality = EXCLUDED.traffic_quality,
                playbooks = EXCLUDED.playbooks,
                merchant_count = EXCLUDED.merchant_count,
                total_data_points = EXCLUDED.total_data_points,
                confidence_level = EXCLUDED.confidence_level,
                computed_at = EXCLUDED.computed_at,
                updated_at = EXCLUDED.computed_at
        """),
        {
            **cohort,
            "computed_at": now,
            "nudge_effectiveness": json.dumps(cohort.get("nudge_effectiveness")),
            "signal_distribution": json.dumps(cohort.get("signal_distribution")),
            "price_sensitivity": json.dumps(cohort.get("price_sensitivity")),
            "traffic_quality": json.dumps(cohort.get("traffic_quality")),
            "playbooks": json.dumps(cohort.get("playbooks")),
        },
    )


def _map_merchant_to_cohorts(
    conn: Connection, fp: dict, cohort_groups: dict, valid_keys: list[str], now: datetime,
) -> None:
    """Map a merchant to their top 3 closest cohorts."""
    if not valid_keys:
        return

    similarities = [(key, _cohort_similarity(fp, key)) for key in valid_keys]
    similarities.sort(key=lambda x: x[1], reverse=True)

    primary = similarities[0] if len(similarities) > 0 else (None, None)
    secondary = similarities[1] if len(similarities) > 1 else (None, None)
    tertiary = similarities[2] if len(similarities) > 2 else (None, None)

    conn.execute(
        text("""
            INSERT INTO cig_merchant_mappings (
                shop_domain, primary_cohort_key, primary_similarity,
                secondary_cohort_key, secondary_similarity,
                tertiary_cohort_key, tertiary_similarity,
                fingerprint, computed_at, updated_at
            ) VALUES (
                :shop, :pk, :ps, :sk, :ss, :tk, :ts, :fp, :now, :now
            )
            ON CONFLICT (shop_domain) DO UPDATE SET
                primary_cohort_key = EXCLUDED.primary_cohort_key,
                primary_similarity = EXCLUDED.primary_similarity,
                secondary_cohort_key = EXCLUDED.secondary_cohort_key,
                secondary_similarity = EXCLUDED.secondary_similarity,
                tertiary_cohort_key = EXCLUDED.tertiary_cohort_key,
                tertiary_similarity = EXCLUDED.tertiary_similarity,
                fingerprint = EXCLUDED.fingerprint,
                computed_at = EXCLUDED.computed_at,
                updated_at = EXCLUDED.updated_at
        """),
        {
            "shop": fp["shop_domain"],
            "pk": primary[0], "ps": primary[1],
            "sk": secondary[0], "ss": secondary[1],
            "tk": tertiary[0], "ts": tertiary[1],
            "fp": json.dumps({k: v for k, v in fp.items()
                              if k in ("cart_rate", "scroll_depth", "dwell_time",
                                       "return_rate", "views_per_product", "mobile_pct")}),
            "now": now,
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════

def _trimmed_values(vals: list[float]) -> list[float]:
    """Remove top/bottom outliers before aggregation."""
    if len(vals) < 5:
        return vals
    n = len(vals)
    trim = max(1, int(n * _OUTLIER_TRIM_PCT))
    sorted_vals = sorted(vals)
    return sorted_vals[trim:n - trim]


def _safe_mean(vals: list[float]) -> float:
    if not vals:
        return 0.0
    return sum(vals) / len(vals)
