"""
klaviyo_export.py — Klaviyo behavioral segment export.

Exports WishSpark behavioral intelligence to Klaviyo via the Klaviyo v3 API.

Identity model
--------------
WishSpark visitor_ids are pseudonymous localStorage UUIDs.  To push events
to Klaviyo profiles, we need a Klaviyo identifier (email or phone).

v1 Identity Resolution:
    Known visitors: cross-reference visitor_purchase_sessions → shop_orders
    to find visitors who have previously purchased.  shop_orders contains
    the buyer's email from the Shopify webhook payload.

    Anonymous visitors: counted but not pushed to Klaviyo (no email to key on).

What we push to Klaviyo
-----------------------
For each identified HOT visitor on a product, we send a Klaviyo Track event:

    event name:   "WishSpark — High Intent Signal"
    properties:   {
        product_url:       str,
        behavioral_index:  float,
        visit_count:       int,
        avg_scroll:        float,
        avg_dwell_secs:    float,
        revenue_window:    float,  # per-visitor estimated revenue
        source:            "wishspark",
    }

Merchants can use this event in Klaviyo flows:
    "When WishSpark — High Intent Signal is received → send email"

Public interface
----------------
    get_segment_with_identity(db, shop_domain, product_url, hours=72) -> dict
        Returns HOT segment with identity resolution — shows which visitors
        are identifiable and what would be pushed to Klaviyo.

    push_segment_to_klaviyo(db, shop_domain, product_url,
                             klaviyo_private_key, hours=72) -> dict
        Pushes identified HOT visitors to Klaviyo Events API.
        Returns {"pushed": int, "anonymous": int, "errors": int}
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.audience_segments import segment_product_visitors

log = logging.getLogger(__name__)

KLAVIYO_EVENTS_URL = "https://a.klaviyo.com/api/events/"
_REQUEST_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Identity resolution — find emails for visitor_ids via purchase history
# ---------------------------------------------------------------------------

def _resolve_visitor_emails(
    db: Session,
    shop_domain: str,
    visitor_ids: list[str],
) -> dict[str, str]:
    """
    Cross-reference visitor_ids with shop_orders to find known emails.

    Returns a dict mapping visitor_id → email for identified visitors.
    Only includes visitors who have previously purchased.
    """
    if not visitor_ids:
        return {}

    try:
        rows = db.execute(
            text("""
                SELECT vps.visitor_id, so.customer_email
                FROM visitor_purchase_sessions vps
                JOIN shop_orders so
                    ON so.shopify_order_id = vps.shopify_order_id
                   AND so.shop_domain      = vps.shop_domain
                WHERE vps.shop_domain = :shop
                  AND vps.visitor_id   = ANY(:visitor_ids)
                  AND so.customer_email IS NOT NULL
                  AND so.customer_email != ''
            """),
            {"shop": shop_domain, "visitor_ids": visitor_ids},
        ).fetchall()

        return {str(row[0]): str(row[1]) for row in rows}

    except Exception as exc:
        log.error(
            "klaviyo_export: email resolution failed shop=%s: %s",
            shop_domain, exc,
        )
        return {}


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def get_segment_with_identity(
    db: Session,
    shop_domain: str,
    product_url: str,
    hours: int = 72,
) -> dict:
    """
    Get HOT segment enriched with identity status.

    Returns:
        {
            "product_url":         str,
            "total_hot_visitors":  int,
            "identified":          int,    # have Shopify email
            "anonymous":           int,    # no email available
            "klaviyo_ready":       list,   # [{visitor_id, email, behavioral_index, ...}]
            "revenue_window":      float,
            "segment_meta":        dict,
        }
    """
    segment = segment_product_visitors(db, shop_domain, product_url, hours)
    hot = segment.get("hot", {})
    hot_visitors = hot.get("visitors", [])
    visitor_ids = [v["visitor_id"] for v in hot_visitors]

    email_map = _resolve_visitor_emails(db, shop_domain, visitor_ids)

    klaviyo_ready = []
    anonymous_count = 0

    for v in hot_visitors:
        vid = v["visitor_id"]
        email = email_map.get(vid)
        if email:
            klaviyo_ready.append({
                "visitor_id":       vid,
                "email":            email,
                "behavioral_index": v["behavioral_index"],
                "visit_count":      v["visit_count"],
                "avg_scroll":       v["avg_scroll"],
                "avg_dwell_secs":   v["avg_dwell_secs"],
            })
        else:
            anonymous_count += 1

    return {
        "product_url":        product_url,
        "total_hot_visitors": hot.get("visitor_count", 0),
        "identified":         len(klaviyo_ready),
        "anonymous":          anonymous_count,
        "klaviyo_ready":      klaviyo_ready,
        "revenue_window":     hot.get("estimated_revenue_window", 0.0),
        "segment_meta": {
            "calibration_state": segment.get("meta", {}).get("calibration_state"),
            "aov_used":          segment.get("meta", {}).get("aov_used"),
            "window_hours":      hours,
        },
    }


def push_segment_to_klaviyo(
    db: Session,
    shop_domain: str,
    product_url: str,
    klaviyo_private_key: str,
    hours: int = 72,
) -> dict:
    """
    Push identified HOT visitors to Klaviyo Events API v3.

    Each visitor receives a "WishSpark — High Intent Signal" event.
    Anonymous visitors (no email) are counted but not pushed.

    Returns:
        {"pushed": int, "anonymous": int, "errors": int}
    """
    segment_data = get_segment_with_identity(db, shop_domain, product_url, hours)
    klaviyo_ready = segment_data["klaviyo_ready"]
    anonymous_count = segment_data["anonymous"]
    revenue_window = segment_data["revenue_window"]

    pushed = 0
    errors = 0

    headers = {
        "Authorization": f"Klaviyo-API-Key {klaviyo_private_key}",
        "Content-Type":  "application/json",
        "revision":      "2024-02-15",  # Klaviyo API v3 revision
    }

    for visitor in klaviyo_ready:
        payload = {
            "data": {
                "type": "event",
                "attributes": {
                    "metric": {
                        "data": {
                            "type": "metric",
                            "attributes": {"name": "WishSpark — High Intent Signal"},
                        }
                    },
                    "profile": {
                        "data": {
                            "type": "profile",
                            "attributes": {"email": visitor["email"]},
                        }
                    },
                    "properties": {
                        "product_url":       product_url,
                        "behavioral_index":  visitor["behavioral_index"],
                        "visit_count":       visitor["visit_count"],
                        "avg_scroll_pct":    visitor["avg_scroll"],
                        "avg_dwell_secs":    visitor["avg_dwell_secs"],
                        "shop_domain":       shop_domain,
                        "source":            "wishspark",
                    },
                    "time": datetime.utcnow().isoformat() + "Z",
                },
            }
        }

        try:
            resp = httpx.post(
                KLAVIYO_EVENTS_URL,
                headers=headers,
                json=payload,
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            pushed += 1
        except httpx.HTTPStatusError as exc:
            log.error(
                "klaviyo_export: HTTP %d pushing event email=%s shop=%s: %s",
                exc.response.status_code,
                visitor["email"][:3] + "***",  # partial email in logs
                shop_domain,
                exc.response.text[:200],
            )
            errors += 1
        except Exception as exc:
            log.error(
                "klaviyo_export: error pushing event shop=%s: %s",
                shop_domain, exc,
            )
            errors += 1

    log.info(
        "klaviyo_export: push complete shop=%s product=%s "
        "pushed=%d anonymous=%d errors=%d",
        shop_domain, product_url, pushed, anonymous_count, errors,
    )

    return {
        "pushed":    pushed,
        "anonymous": anonymous_count,
        "errors":    errors,
    }
