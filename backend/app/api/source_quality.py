"""
Traffic Intelligence Engine — source attribution for the top product of the shop
over the last 24 hours.

NO synthetic data, NO fixed percentages, NO LLM generation.
Every number and every label is derived deterministically from real event rows.

Product boundary
----------------
Lite route  GET /analytics/source-quality
  Exposes descriptive and diagnostic fields only:
    Descriptive:  visitors, views, avg_dwell, avg_scroll, cart_conversions,
                  hot_visitors
    Diagnostic:   quality_label, quality_score, attention_label, insight

Pro route   GET /analytics/source-quality/pro
  Identical to the Lite response PLUS one prescriptive field per source row:
    Prescriptive: action_insight   (per-source recommended action sentence)

Both routes share _build_source_response() for all SQL and scoring logic.
_action_insight() is called only by the Pro route — never by the Lite route.
Any future engineer or AI agent must keep this boundary intact.

Algorithm
---------
1. Find the most-viewed product for this shop in the last 24 h.
   Uses the dedicated product_url column (migration k9a1b2c3d4e5).
   "Most viewed" = highest COUNT of events with a non-NULL product_url.

2. For that product, aggregate per source_type using a two-level GROUP BY:

   Inner level — one row per (source_type, visitor_id):
     view_count      events that are neither scroll nor dwell_time
     max_dwell       highest dwell_seconds recorded for this visitor
     max_scroll      highest max_scroll_depth recorded for this visitor
     had_wishlist    true if visitor fired at least one wishlist_add
     had_deep_dwell  true if any dwell_time event exceeded 30 s
     had_deep_scroll true if any scroll event exceeded 50 %

   Outer level — one row per source_type:
     visitors         COUNT of inner rows (= distinct visitors per source)
     views            SUM(view_count)
     avg_dwell        AVG(max_dwell per visitor)
     avg_scroll       AVG(max_scroll per visitor)
     cart_conversions SUM of had_wishlist flags
     hot_visitors     exact intersection: visitors with BOTH had_deep_dwell
                      AND had_deep_scroll (true count, not min() approximation)

3. quality_score (0–100 integer) — deterministic weighted formula:
     Component   Weight   Derivation
     ─────────── ──────   ──────────────────────────────────────────────────
     hot_rate      40 pt  hot_visitors / visitors
     avg_dwell     30 pt  capped at 120 s → normalised 0–1
     avg_scroll    20 pt  capped at 100 % → normalised 0–1
     cart_rate     10 pt  cart_conversions / visitors, capped at 1.0
     ─────────── ──────
     Total        100 pt

   Low-data rows (visitors < MIN_SIGNAL_VISITORS) receive score 0 and
   are labelled "Low signal" regardless of other metrics.

4. quality_label — derived from raw thresholds:
     Strong intent  dwell > 30 s  AND scroll > 50 %  AND hot_rate >= 0.20
     Mixed intent   dwell > 15 s  OR  scroll > 30 %  OR  cart_conversions > 0
     Low quality    everything else

5. attention_label — one label per row, mutually exclusive:
     "Best source"  highest quality_score AND visitors >= MIN_SIGNAL_VISITORS
                    AND score >= BEST_SOURCE_MIN_SCORE
     "Low signal"   visitors < MIN_SIGNAL_VISITORS
     "Needs work"   all remaining rows

6. insight — shop-level diagnostic sentence derived from explicit rule
   branches. No LLM involved.

Thresholds
----------
  MIN_SIGNAL_VISITORS = 3    fewer visitors → "Low signal", score forced to 0
  BEST_SOURCE_MIN_SCORE = 20 avoids crowning a source with no meaningful data
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.database import engine
from app.core.deps import require_merchant_session, require_pro_session

router = APIRouter(prefix="/analytics", tags=["analytics"])

# 24-hour window expressed in milliseconds (events.timestamp is epoch ms).
_WINDOW_MS = 86_400 * 1_000

MIN_SIGNAL_VISITORS   = 3
BEST_SOURCE_MIN_SCORE = 20


# ---------------------------------------------------------------------------
# quality_score — deterministic 0-100 integer
# ---------------------------------------------------------------------------
def _quality_score(
    avg_dwell: Optional[float],
    avg_scroll: Optional[float],
    hot_visitors: int,
    visitors: int,
    cart_conversions: int,
) -> int:
    if visitors <= 0:
        return 0

    hot_rate  = hot_visitors / visitors
    dwell     = avg_dwell  or 0.0
    scroll    = avg_scroll or 0.0
    cart_rate = min(cart_conversions / visitors, 1.0)

    raw = (
        min(hot_rate, 1.0)         * 40.0 +
        min(dwell,  120.0) / 120.0 * 30.0 +
        min(scroll, 100.0) / 100.0 * 20.0 +
        cart_rate                  * 10.0
    )
    return int(round(raw))


# ---------------------------------------------------------------------------
# quality_label — threshold-based intent classification
# ---------------------------------------------------------------------------
def _quality_label(
    avg_dwell: Optional[float],
    avg_scroll: Optional[float],
    hot_visitors: int,
    visitors: int,
    cart_conversions: int,
) -> str:
    dwell    = avg_dwell  or 0.0
    scroll   = avg_scroll or 0.0
    hot_rate = hot_visitors / visitors if visitors > 0 else 0.0

    if dwell > 30 and scroll > 50 and hot_rate >= 0.20:
        return "Strong intent"
    if dwell > 15 or scroll > 30 or cart_conversions > 0:
        return "Mixed intent"
    return "Low quality"


# ---------------------------------------------------------------------------
# _action_insight — per-source prescriptive sentence
#
# PRO ONLY — called exclusively by the Pro route below.
# Do not call this from the Lite route or add its output to the Lite response.
# The Lite/Pro boundary is: Lite = diagnostic (what is happening),
#                           Pro  = prescriptive (what to do about it).
# ---------------------------------------------------------------------------
def _action_insight(
    avg_dwell: Optional[float],
    avg_scroll: Optional[float],
    cart_conversions: int,
    visitors: int,
) -> str:
    if visitors < 2:
        return "Not enough data yet → wait for more traffic"

    dwell  = avg_dwell  or 0.0
    scroll = avg_scroll or 0.0

    if avg_scroll is None and dwell < 20:
        return "Users drop early → review ad targeting or align landing page with ad message"

    if scroll > 70 and dwell > 40:
        return "Top performing source → increase budget or send more traffic here"

    if scroll > 50 and cart_conversions == 0:
        return "Users engage but don't convert → test pricing, trust signals, or checkout flow"

    return "Traffic is moderate → improve product page clarity and CTA"


# ---------------------------------------------------------------------------
# attention_label — two-pass assignment (needs all scores present)
# ---------------------------------------------------------------------------
def _assign_attention_labels(sources: list[dict]) -> None:
    # Pass 1 — force low-data rows to score 0.
    for s in sources:
        if s["visitors"] < MIN_SIGNAL_VISITORS:
            s["quality_score"] = 0
            s["attention_label"] = "Low signal"
        else:
            s["attention_label"] = "Needs work"

    # Pass 2 — crown the best eligible row.
    eligible = [s for s in sources if s["attention_label"] != "Low signal"]
    if eligible:
        best = max(eligible, key=lambda s: s["quality_score"])
        if best["quality_score"] >= BEST_SOURCE_MIN_SCORE:
            best["attention_label"] = "Best source"


# ---------------------------------------------------------------------------
# insight — shop-level diagnostic sentence
# ---------------------------------------------------------------------------
def _insight(sources: list[dict]) -> str:
    if not sources:
        return "Not enough source data collected yet."

    signal_sources = [s for s in sources if s["attention_label"] != "Low signal"]
    if not signal_sources:
        return "Traffic is still being collected — check back once more events arrive."

    def _name(slug: str) -> str:
        names = {
            "direct":     "Direct",
            "google":     "Google",
            "bing":       "Bing",
            "yahoo":      "Yahoo",
            "duckduckgo": "DuckDuckGo",
            "facebook":   "Facebook",
            "instagram":  "Instagram",
            "tiktok":     "TikTok",
            "twitter":    "Twitter / X",
            "pinterest":  "Pinterest",
            "linkedin":   "LinkedIn",
            "youtube":    "YouTube",
            "reddit":     "Reddit",
            "snapchat":   "Snapchat",
            "amazon":     "Amazon",
            "ebay":       "eBay",
            "etsy":       "Etsy",
            "email":      "Email",
            "referral":   "Referral",
            "unknown":    "Unattributed",
        }
        return names.get(slug, slug.capitalize())

    best = max(signal_sources, key=lambda s: s["quality_score"])
    best_name = _name(best["source_type"])

    if best["quality_label"] == "Strong intent":
        return f"{best_name} is driving the strongest qualified traffic for this product."

    low_sources = [
        s for s in signal_sources
        if s["quality_label"] == "Low quality" and s["source_type"] != best["source_type"]
    ]
    if best["quality_label"] == "Mixed intent" and low_sources:
        weak = max(low_sources, key=lambda s: s["views"])
        weak_name = _name(weak["source_type"])
        return (
            f"{best_name} brings the most qualified visitors, "
            f"but {weak_name} traffic shows little downstream action."
        )

    if best["quality_label"] == "Mixed intent":
        if best["cart_conversions"] > 0:
            return (
                f"{best_name} is leading with moderate engagement "
                f"and {best['cart_conversions']} cart conversion(s) — worth nurturing."
            )
        return f"{best_name} is driving most views with moderate engagement."

    return (
        "Traffic quality is low across all sources — "
        "focus on improving page engagement to convert more visitors."
    )


# ---------------------------------------------------------------------------
# _build_source_response — shared SQL execution and source list construction
#
# Returns the complete Lite-shaped response dict:
#   { product_url, sources: [...diagnostic fields...], insight }
#
# The sources list contains all descriptive + diagnostic fields but NO
# action_insight.  The Pro route adds action_insight after calling this.
# ---------------------------------------------------------------------------
def _build_source_response(shop: str) -> dict:
    top_product_sql = text("""
        SELECT product_url
        FROM events
        WHERE shop_domain = :shop
          AND product_url IS NOT NULL
          AND timestamp   >= (EXTRACT(EPOCH FROM NOW()) * 1000 - :window_ms)
        GROUP BY product_url
        ORDER BY COUNT(*) DESC
        LIMIT 1
    """)

    # Two-level GROUP BY for exact per-source metrics.
    #
    # Inner subquery (visitor_agg) — one row per (source_type, visitor_id):
    #   view_count      events that are neither scroll nor dwell_time
    #   max_dwell       highest dwell_seconds recorded for this visitor
    #   max_scroll      highest max_scroll_depth recorded for this visitor
    #   had_wishlist    true if visitor fired at least one wishlist_add
    #   had_deep_dwell  true if any dwell_time event exceeded 30 s
    #   had_deep_scroll true if any scroll event exceeded 50 %
    #
    # Outer query — one row per source_type:
    #   hot_visitors    exact intersection: visitors with BOTH flags true
    source_sql = text("""
        SELECT
            va.source_type,
            COUNT(*)                                                                  AS visitors,
            SUM(va.view_count)                                                        AS views,
            AVG(va.max_dwell)                                                         AS avg_dwell,
            AVG(va.max_scroll)                                                        AS avg_scroll,
            SUM(CASE WHEN va.had_wishlist    THEN 1 ELSE 0 END)                       AS cart_conversions,
            SUM(CASE WHEN va.had_deep_dwell AND va.had_deep_scroll THEN 1 ELSE 0 END) AS hot_visitors
        FROM (
            SELECT
                COALESCE(e.source_type, 'unknown')                                        AS source_type,
                e.visitor_id,
                SUM(CASE WHEN e.event_type NOT IN ('scroll', 'dwell_time')
                         THEN 1 ELSE 0 END)                                               AS view_count,
                MAX(CASE WHEN e.event_type = 'dwell_time'
                         THEN NULLIF(e.dwell_seconds, 0) END)                             AS max_dwell,
                MAX(CASE WHEN e.event_type = 'scroll'
                         THEN e.max_scroll_depth END)                                     AS max_scroll,
                BOOL_OR(e.event_type = 'wishlist_add')                                    AS had_wishlist,
                BOOL_OR(e.event_type = 'dwell_time' AND e.dwell_seconds > 30)             AS had_deep_dwell,
                BOOL_OR(e.event_type = 'scroll'     AND e.max_scroll_depth > 50)          AS had_deep_scroll
            FROM events e
            WHERE e.shop_domain = :shop
              AND e.product_url = :product_url
              AND e.timestamp   >= (EXTRACT(EPOCH FROM NOW()) * 1000 - :window_ms)
            GROUP BY COALESCE(e.source_type, 'unknown'), e.visitor_id
        ) va
        GROUP BY va.source_type
        ORDER BY visitors DESC
    """)

    with engine.begin() as conn:
        top_row = conn.execute(
            top_product_sql,
            {"shop": shop, "window_ms": _WINDOW_MS},
        ).mappings().first()

        if top_row is None:
            return {
                "product_url": None,
                "sources": [],
                "insight": "Not enough source data collected yet.",
            }

        product_url = top_row["product_url"]

        raw_rows = conn.execute(
            source_sql,
            {"shop": shop, "product_url": product_url, "window_ms": _WINDOW_MS},
        ).mappings().all()

    if not raw_rows:
        return {
            "product_url": product_url,
            "sources": [],
            "insight": "Not enough source data collected yet.",
        }

    sources: list[dict] = []
    for row in raw_rows:
        visitors   = int(row["visitors"])
        cart_conv  = int(row["cart_conversions"])
        hot_v      = int(row["hot_visitors"])
        avg_dwell  = float(row["avg_dwell"])  if row["avg_dwell"]  is not None else None
        avg_scroll = float(row["avg_scroll"]) if row["avg_scroll"] is not None else None

        # Lite fields — descriptive and diagnostic only.
        # Do NOT add action_insight here; the Pro route adds it after this call.
        sources.append({
            "source_type":      row["source_type"],
            "visitors":         visitors,
            "views":            int(row["views"]),
            "avg_dwell":        round(avg_dwell,  1) if avg_dwell  is not None else None,
            "avg_scroll":       round(avg_scroll, 1) if avg_scroll is not None else None,
            "cart_conversions": cart_conv,
            "hot_visitors":     hot_v,
            "quality_label":    _quality_label(avg_dwell, avg_scroll, hot_v, visitors, cart_conv),
            "quality_score":    _quality_score(avg_dwell, avg_scroll, hot_v, visitors, cart_conv),
            # attention_label injected by _assign_attention_labels below
        })

    _assign_attention_labels(sources)

    return {
        "product_url": product_url,
        "sources":     sources,
        "insight":     _insight(sources),
    }


# ---------------------------------------------------------------------------
# Lite route — GET /analytics/source-quality
#
# Returns descriptive + diagnostic fields only.
# Prescriptive action_insight is NOT included here.
# See Pro route below for the full response.
# ---------------------------------------------------------------------------
@router.get("/source-quality")
def source_quality(
    shop: str = Depends(require_merchant_session),
):
    """
    Lite Traffic Intelligence — descriptive and diagnostic fields only.

    Uses a two-level GROUP BY query:
      Inner level: per (source_type, visitor_id) — computes per-visitor flags.
      Outer level: per source_type — aggregates visitor-level signals.

    Prescriptive action_insight is excluded from this response.
    Pro subscribers call /analytics/source-quality/pro instead.
    """
    return _build_source_response(shop)


# ---------------------------------------------------------------------------
# Pro route — GET /analytics/source-quality/pro
#
# Returns the same response as the Lite route PLUS action_insight per source.
# action_insight is a prescriptive sentence — it belongs to Pro only.
#
# Lite boundary: quality_label, quality_score, attention_label, insight
#               (what is happening — diagnostic)
# Pro boundary:  action_insight per source row
#               (what to do about it — prescriptive)
# ---------------------------------------------------------------------------
@router.get("/source-quality/pro")
def source_quality_pro(
    shop: str = Depends(require_pro_session),
):
    """
    Pro Traffic Intelligence — identical to /analytics/source-quality but
    includes action_insight (prescriptive recommended action) per source row.

    Backend-enforced: require_pro_plan raises HTTP 403 if the shop does not
    have an active Pro plan (merchants.plan != "pro" or billing_active == False).
    API key and shop-domain validation are composed inside require_pro_plan.

    Lite boundary: quality_label, quality_score, attention_label, insight
                   (served by /analytics/source-quality — no plan check)
    Pro boundary:  action_insight per source row
                   (served here — plan-enforced in backend)
    """
    response = _build_source_response(shop)

    # Enrich each source row with the prescriptive action sentence.
    # _action_insight() is defined in this module and intentionally kept
    # separate from the Lite fields — it is the only Pro-exclusive field here.
    for src in response["sources"]:
        src["action_insight"] = _action_insight(
            avg_dwell=src["avg_dwell"],
            avg_scroll=src["avg_scroll"],
            cart_conversions=src["cart_conversions"],
            visitors=src["visitors"],
        )

    return response
