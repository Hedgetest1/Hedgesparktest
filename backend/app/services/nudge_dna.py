"""
nudge_dna.py — Winning nudge pattern extractor (δ5).

Analyses every past nudge variant shown and extracts the linguistic
patterns that correlate with conversion. Feeds back into
nudge_composer as a "lessons" prompt enrichment, and surfaces the
top patterns to the merchant as "what's working" evidence.

Features extracted per variant
------------------------------
  - length_bucket       (short <=40, medium <=80, long)
  - contains_digits     (urgency number like "Only 3 left")
  - contains_emoji      (🔥 ⚡ ⏰ etc.)
  - contains_percent    ("20% off")
  - urgency_word_count  (now, hurry, quick, limited, last)
  - social_proof_word_count (people, others, selling, viewed)
  - starts_with_number
  - has_call_to_action  (buy, grab, shop, claim, try, get)

Outcome definition
------------------
Win = visitor who saw THIS variant AND purchased within 48h (via
nudge_events + visitor_purchase_sessions).
Loss = visitor who saw this variant + did NOT purchase.

Output
------
Per-feature lift table:
  {
    feature: "contains_emoji",
    with_true_conv_rate: 0.042,
    with_false_conv_rate: 0.028,
    lift_pct: 50.0,       # (with - without) / without × 100
    sample_with: 1200,
    sample_without: 1800,
    significance: "high"  # based on sample size
  }

Public API
----------
    extract_patterns(db, shop, window_days=30) -> dict
        Returns ranked feature list + top winning variants + suggested
        prompt additions for the nudge_composer.

    get_cached_dna(shop) / refresh_cached_dna(db, shop)
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

log = logging.getLogger("nudge_dna")

_CACHE_TTL_S = 4 * 3600  # 4h
_CACHE_KEY = "hs:nudge_dna:v1"
_MIN_SAMPLE_PER_VARIANT = 20  # minimum impressions to include a feature

_URGENCY_WORDS = {
    "now", "hurry", "quick", "fast", "limited", "last", "ending",
    "today", "tonight", "almost", "running", "few", "left",
}
_SOCIAL_WORDS = {
    "people", "others", "selling", "viewed", "watching", "customers",
    "popular", "loved", "favorite", "trending", "sold",
}
_CTA_WORDS = {
    "buy", "grab", "shop", "claim", "try", "get", "order", "add",
    "start", "unlock", "save", "take",
}
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F6FF"  # symbols + misc
    "\U0001F700-\U0001F9FF"  # additional
    "\U00002600-\U000027BF"  # misc symbols & dingbats
    "]"
)


@dataclass
class VariantStats:
    key: str
    copy_text: str
    impressions: int = 0
    conversions: int = 0
    features: dict[str, bool] = None

    def __post_init__(self):
        if self.features is None:
            self.features = _extract_features(self.copy_text)

    @property
    def conversion_rate(self) -> float:
        return (self.conversions / self.impressions) if self.impressions > 0 else 0.0


def _extract_features(text: str) -> dict[str, bool]:
    """Boolean features per variant."""
    t = (text or "").strip()
    t_lower = t.lower()
    words = set(re.findall(r"\b\w+\b", t_lower))

    length = len(t)
    length_bucket = (
        "short" if length <= 40 else "medium" if length <= 80 else "long"
    )

    return {
        f"length_{length_bucket}": True,
        "contains_digits": bool(re.search(r"\d", t)),
        "contains_emoji": bool(_EMOJI_RE.search(t)),
        "contains_percent": "%" in t,
        "has_urgency_word": len(words & _URGENCY_WORDS) > 0,
        "has_social_proof_word": len(words & _SOCIAL_WORDS) > 0,
        "has_cta_word": len(words & _CTA_WORDS) > 0,
        "starts_with_number": bool(re.match(r"^\d", t)),
        "has_exclamation": "!" in t,
    }


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def get_cached_dna(shop_domain: str) -> dict | None:
    rc = _redis()
    if rc is None:
        return None
    try:
        raw = rc.get(f"{_CACHE_KEY}:{shop_domain}")
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


def _cache_dna(shop_domain: str, data: dict) -> None:
    rc = _redis()
    if rc is None:
        return
    try:
        rc.setex(
            f"{_CACHE_KEY}:{shop_domain}", _CACHE_TTL_S, json.dumps(data, default=str)
        )
    except Exception:
        pass


def extract_patterns(
    db: Session, shop_domain: str, window_days: int = 30
) -> dict:
    """Analyse nudge performance and return feature-lift ranking.

    Returns:
        {
            "shop_domain": str,
            "window_days": int,
            "total_impressions": int,
            "total_conversions": int,
            "overall_conversion_rate": float,
            "features": [  # sorted by lift descending
                {feature, with_true_rate, with_false_rate, lift_pct,
                 sample_with, sample_without, significance}
            ],
            "top_variants": [{variant_key, copy_text, conv_rate, impressions}],
            "lessons_for_composer": [str],  # prompt additions
        }
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    since = now - timedelta(days=window_days)

    # Pull nudge impressions + variants from nudge_events joined with
    # active_nudges to recover the copy_config per variant.
    try:
        rows = db.execute(
            sql_text(
                """
                SELECT
                    ne.visitor_id,
                    ne.created_at,
                    ne.variant_name,
                    an.copy_variants
                FROM nudge_events ne
                LEFT JOIN active_nudges an ON an.id = ne.nudge_id
                WHERE ne.shop_domain = :shop
                  AND ne.created_at >= :since
                  AND ne.event_type = 'nudge_impression'
                LIMIT 20000
                """
            ),
            {"shop": shop_domain, "since": since},
        ).fetchall()
    except Exception as exc:
        log.warning("nudge_dna: impression query failed: %s", exc)
        rows = []

    if not rows:
        return _empty_dna(shop_domain, window_days)

    # Build per-variant stats
    variants: dict[str, VariantStats] = {}

    for row in rows:
        visitor_id, row_ts, variant_name, copy_variants_json = row
        ts = row_ts
        if not variant_name or not visitor_id:
            continue

        # Parse copy text for this variant
        copy_text = ""
        try:
            if copy_variants_json:
                parsed = (
                    copy_variants_json
                    if isinstance(copy_variants_json, list)
                    else json.loads(copy_variants_json)
                )
                for v in parsed or []:
                    if v.get("variant_name") == variant_name:
                        cfg = v.get("copy_config", {})
                        copy_text = " ".join(
                            str(cfg.get(k, "") or "")
                            for k in ("headline", "subtext", "badge")
                        ).strip()
                        break
        except Exception:
            pass

        if not copy_text:
            continue

        key = variant_name
        stats = variants.get(key)
        if stats is None:
            stats = VariantStats(key=key, copy_text=copy_text)
            variants[key] = stats

        stats.impressions += 1

        # Was there a purchase within 48h of this impression?
        try:
            hit = db.execute(
                sql_text(
                    """
                    SELECT 1 FROM visitor_purchase_sessions
                    WHERE shop_domain = :shop
                      AND visitor_id = :vid
                      AND confirmed_at BETWEEN :lo AND :hi
                    LIMIT 1
                    """
                ),
                {
                    "shop": shop_domain,
                    "vid": visitor_id,
                    "lo": ts,
                    "hi": ts + timedelta(hours=48),
                },
            ).fetchone()
            if hit is not None:
                stats.conversions += 1
        except Exception:
            pass

    if not variants:
        return _empty_dna(shop_domain, window_days)

    # Aggregate feature lift: for each feature, compute conv rate with
    # and without it.
    feature_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"imp_true": 0, "conv_true": 0, "imp_false": 0, "conv_false": 0}
    )

    for stats in variants.values():
        if stats.impressions < _MIN_SAMPLE_PER_VARIANT:
            continue
        for feat_name, present in stats.features.items():
            bucket = feature_totals[feat_name]
            if present:
                bucket["imp_true"] += stats.impressions
                bucket["conv_true"] += stats.conversions
            else:
                bucket["imp_false"] += stats.impressions
                bucket["conv_false"] += stats.conversions

    features_ranked: list[dict] = []
    total_imp = sum(s.impressions for s in variants.values())
    total_conv = sum(s.conversions for s in variants.values())

    for feat_name, bucket in feature_totals.items():
        imp_t, conv_t = bucket["imp_true"], bucket["conv_true"]
        imp_f, conv_f = bucket["imp_false"], bucket["conv_false"]
        if imp_t < 30 or imp_f < 30:
            continue  # not enough data
        rate_t = conv_t / imp_t if imp_t > 0 else 0.0
        rate_f = conv_f / imp_f if imp_f > 0 else 0.0
        lift_pct = ((rate_t - rate_f) / rate_f * 100) if rate_f > 0 else 0.0
        sig = "high" if min(imp_t, imp_f) >= 200 else "medium" if min(imp_t, imp_f) >= 80 else "low"
        features_ranked.append(
            {
                "feature": feat_name,
                "with_true_rate": round(rate_t, 4),
                "with_false_rate": round(rate_f, 4),
                "lift_pct": round(lift_pct, 1),
                "sample_with": imp_t,
                "sample_without": imp_f,
                "significance": sig,
            }
        )

    features_ranked.sort(key=lambda f: f["lift_pct"], reverse=True)

    # Top variants by conversion rate (min impressions)
    top_variants = sorted(
        [
            {
                "variant_key": s.key,
                "copy_text": s.copy_text[:160],
                "conversion_rate": round(s.conversion_rate, 4),
                "impressions": s.impressions,
                "conversions": s.conversions,
            }
            for s in variants.values()
            if s.impressions >= _MIN_SAMPLE_PER_VARIANT
        ],
        key=lambda v: v["conversion_rate"],
        reverse=True,
    )[:5]

    # Turn top features into prompt additions for nudge_composer
    lessons: list[str] = []
    for f in features_ranked[:4]:
        if f["lift_pct"] > 15 and f["significance"] != "low":
            human = _humanize_feature(f["feature"])
            lessons.append(
                f"{human} boosts conversion ~{int(f['lift_pct'])}% in this shop"
            )

    result = {
        "shop_domain": shop_domain,
        "window_days": window_days,
        "total_impressions": total_imp,
        "total_conversions": total_conv,
        "overall_conversion_rate": round(
            total_conv / total_imp if total_imp > 0 else 0.0, 4
        ),
        "features": features_ranked,
        "top_variants": top_variants,
        "lessons_for_composer": lessons,
        "generated_at": now.isoformat(),
    }

    _cache_dna(shop_domain, result)
    return result


def _humanize_feature(key: str) -> str:
    mapping = {
        "length_short": "Short copy (under 40 chars)",
        "length_medium": "Medium-length copy",
        "length_long": "Longer copy",
        "contains_digits": "Including a specific number",
        "contains_emoji": "Leading with an emoji",
        "contains_percent": "Showing a % discount",
        "has_urgency_word": "Urgency language (now, hurry, ending)",
        "has_social_proof_word": "Social proof words (people, others, trending)",
        "has_cta_word": "Clear call-to-action (buy, grab, claim)",
        "starts_with_number": "Starting with a number",
        "has_exclamation": "Using exclamation marks",
    }
    return mapping.get(key, key)


def _empty_dna(shop: str, window_days: int) -> dict:
    return {
        "shop_domain": shop,
        "window_days": window_days,
        "status": "insufficient_data",
        "total_impressions": 0,
        "total_conversions": 0,
        "overall_conversion_rate": 0.0,
        "features": [],
        "top_variants": [],
        "lessons_for_composer": [],
    }
