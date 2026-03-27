"""
live_alerts.py — /analytics/alerts and /analytics/alerts/pro endpoints.

Product boundary
----------------
Lite route  GET /analytics/alerts
  Returns diagnostic alert fields only: type, priority, message.
  message is a plain-English count sentence describing what is happening
  (e.g. "14 high-intent visitors browsing now").  It is diagnostic — it
  belongs in Lite in full.  The `action` field is absent.

Pro route   GET /analytics/alerts/pro
  Identical to the Lite response PLUS `action` per alert — a prescriptive
  sentence telling the merchant what to do about the alert.
  Backend-enforced via require_pro_plan (HTTP 403 for non-Pro shops).

Both routes call _build_alerts() which always computes all fields including
`action`.  The Lite route strips `action` at the API boundary; the service
function itself is plan-agnostic.

Field classification
--------------------
Descriptive:        type, priority
Diagnostic (Lite):  message  — what is happening (count-based observation)
Prescriptive (Pro): action   — what the merchant should do about it

Why the Lite boundary is here
------------------------------
`message` is a count sentence ("14 high-intent visitors browsing now").
It describes a fact about the store.  Truncating or hiding it in Lite is
dishonest product gating — Lite merchants are entitled to know what is
happening.  The prescriptive response (what to do about it) is the genuine
Action Layer addition that justifies the Pro tier.

To add a new alert type:
  1. Add a detection block in _build_alerts() that appends to result with
     all three fields (type, priority, message, action).
  2. action must be a plain-English prescriptive sentence (imperative mood).
  3. The new type is automatically Lite-safe (message visible) and Pro-gated
     (action stripped by the Lite route).
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.database import engine
from app.core.deps import require_merchant_session, require_pro_session

router = APIRouter(prefix="/analytics", tags=["analytics"])

# ---------------------------------------------------------------------------
# Prescriptive action text per alert type — PRO ONLY.
# These are plain-English imperative sentences telling the merchant what to
# do.  They are the Action Layer addition that distinguishes Pro from Lite.
# Kept here rather than a separate file because there are only three alert
# types and co-locating them with the detection logic is clearer.
# ---------------------------------------------------------------------------
_ALERT_ACTIONS: dict[str, str] = {
    "HOT_TRAFFIC_CLUSTER": (
        "Trigger a targeted pop-up or limited-time offer for these high-intent "
        "visitors before they exit — they have the highest likelihood of converting."
    ),
    "CHECKOUT_ACTIVITY": (
        "Review your checkout flow for friction points — consider launching an "
        "abandoned cart recovery sequence or adding a one-click upsell at this step."
    ),
    "PRODUCT_INTEREST": (
        "Add urgency signals (low-stock notice, social proof, or a time-limited "
        "discount) to your most-viewed product pages to convert passive interest "
        "into purchases."
    ),
}

# Fields safe to return to Lite subscribers.
# action is intentionally absent — it is prescriptive (Pro only).
_LITE_ALERT_FIELDS: set[str] = {"type", "priority", "message"}


# ---------------------------------------------------------------------------
# Shared detection helper — always builds full (Pro-shaped) alert list.
# Prescriptive fields are stripped by the Lite route boundary, not here.
# ---------------------------------------------------------------------------
def _build_alerts(shop: str) -> list[dict]:
    """
    Run the alert detection SQL and return the full alert list.

    Each dict contains: type, priority, message, action.
    The Lite route strips `action` before returning to the caller.
    The Pro route returns the list as-is.

    Detection signals:
      HOT_TRAFFIC_CLUSTER  — visitors with scroll >= 70%, dwell >= 20s, click >= 1
      CHECKOUT_ACTIVITY    — any checkout URL views in the events table
      PRODUCT_INTEREST     — any product URL views in the events table
    """
    query = text("""
        WITH visitor_stats AS (
            SELECT
                visitor_id,
                MAX(url) AS url,
                MAX(COALESCE(dwell_seconds,0)) AS dwell,
                MAX(COALESCE(max_scroll_depth,0)) AS scroll,
                COUNT(*) FILTER (WHERE event_type='click') AS clicks
            FROM events
            WHERE shop_domain = :shop_domain
            GROUP BY visitor_id
        ),
        hot_visitors AS (
            SELECT COUNT(*) AS hot_count
            FROM visitor_stats
            WHERE scroll >= 70 AND dwell >= 20 AND clicks >= 1
        ),
        checkout_activity AS (
            SELECT COUNT(*) AS checkout_views
            FROM events
            WHERE shop_domain = :shop_domain
              AND url LIKE '%checkout%'
        ),
        product_activity AS (
            SELECT COUNT(*) AS product_views
            FROM events
            WHERE shop_domain = :shop_domain
              AND (url LIKE '%product%' OR url LIKE '%test.html%')
        )
        SELECT
            (SELECT hot_count FROM hot_visitors) AS hot_visitors,
            (SELECT checkout_views FROM checkout_activity) AS checkout_views,
            (SELECT product_views FROM product_activity) AS product_views
    """)
    with engine.begin() as conn:
        row = conn.execute(query, {"shop_domain": shop}).mappings().first()

    result: list[dict] = []

    if row["hot_visitors"] >= 1:
        result.append({
            "type": "HOT_TRAFFIC_CLUSTER",
            "message": f"{row['hot_visitors']} high-intent visitors browsing now",
            "priority": "HIGH",
            "action": _ALERT_ACTIONS["HOT_TRAFFIC_CLUSTER"],
        })

    if row["checkout_views"] >= 1:
        result.append({
            "type": "CHECKOUT_ACTIVITY",
            "message": f"{row['checkout_views']} checkout page views detected",
            "priority": "MEDIUM",
            "action": _ALERT_ACTIONS["CHECKOUT_ACTIVITY"],
        })

    if row["product_views"] >= 1:
        result.append({
            "type": "PRODUCT_INTEREST",
            "message": f"{row['product_views']} product page views happening",
            "priority": "LOW",
            "action": _ALERT_ACTIONS["PRODUCT_INTEREST"],
        })

    return result


# ---------------------------------------------------------------------------
# Lite route — GET /analytics/alerts
#
# Diagnostic fields only. `action` is stripped at this boundary.
# ---------------------------------------------------------------------------
@router.get("/alerts")
def alerts(
    shop: str = Depends(require_merchant_session),
):
    """
    Lite alert list — diagnostic fields only (type, priority, message).

    message describes what is happening (count-based observation) and is
    fully visible to Lite subscribers.  It is diagnostic, not prescriptive.

    action is excluded from this response.
    Pro subscribers call /analytics/alerts/pro to receive it.

    Lite boundary: type, priority, message (what is happening)
    Pro boundary:  action              (what to do about it)
    """
    raw = _build_alerts(shop)
    lite = [{k: v for k, v in alert.items() if k in _LITE_ALERT_FIELDS} for alert in raw]
    return {"alerts": lite}


# ---------------------------------------------------------------------------
# Pro route — GET /analytics/alerts/pro
#
# Full alert including `action`. Backend-enforced via require_pro_plan.
# ---------------------------------------------------------------------------
@router.get("/alerts/pro")
def alerts_pro(
    shop: str = Depends(require_pro_session),
):
    """
    Pro alert list — full response including `action` per alert.

    Backend-enforced: require_pro_plan raises HTTP 403 if the shop does not
    have an active Pro plan (merchants.plan != "pro" or billing_active == False).
    API key and shop-domain validation are composed inside require_pro_plan.

    Returns the same alert list as /analytics/alerts plus `action` — a
    plain-English prescriptive sentence telling the merchant what to do.

    Lite boundary: type, priority, message (served by /analytics/alerts)
    Pro boundary:  action per alert     (served here — plan-enforced)
    """
    return {"alerts": _build_alerts(shop)}
