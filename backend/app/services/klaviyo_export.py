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
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.audience_segments import segment_product_visitors


class _SignalPushCounters(NamedTuple):
    """Self-documenting return type for _process_intent_signal.

    Promoted from 3-tuple → NamedTuple 2026-05-13 (post-A3 polish):
    the composer adds counters across signals via field-named access
    (`total.pushed += sig.pushed`); positional destructuring breaks
    silently when fields are reordered. NamedTuple preserves tuple
    compatibility AND gives auto-completion at every callsite.
    """
    pushed: int
    anonymous: int
    errors: int

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
                    "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
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


# ---------------------------------------------------------------------------
# Execution opportunity → Klaviyo sync
# ---------------------------------------------------------------------------

KLAVIYO_LISTS_URL = "https://a.klaviyo.com/api/lists/"
KLAVIYO_PROFILES_URL = "https://a.klaviyo.com/api/profile-import/"
_PROFILE_BATCH_SIZE = 100


def _klaviyo_headers(api_key: str) -> dict:
    """Standard Klaviyo v3 headers. API key from caller (env var)."""
    return {
        "Authorization": f"Klaviyo-API-Key {api_key}",
        "Content-Type":  "application/json",
        "revision":      "2024-02-15",
    }


def get_exposed_audience_with_emails(
    db: Session,
    shop_domain: str,
    execution_id: str,
) -> list[dict]:
    """
    Get exposed audience members with resolved emails.

    STRICT: only group_type='exposed' — holdout NEVER included.
    Only visitors with valid email are returned.

    Returns: [{visitor_id, email}]
    """
    # Step 1: Get exposed visitor_ids
    rows = db.execute(
        text("""
            SELECT visitor_id
            FROM execution_audiences
            WHERE shop_domain = :shop
              AND execution_id = :eid
              AND group_type = 'exposed'
        """),
        {"shop": shop_domain, "eid": execution_id},
    ).fetchall()

    visitor_ids = [r[0] for r in rows]
    if not visitor_ids:
        return []

    # Step 2: Resolve emails (reuses existing identity resolution)
    email_map = _resolve_visitor_emails(db, shop_domain, visitor_ids)

    # Step 3: Return only those with valid email
    return [
        {"visitor_id": vid, "email": email}
        for vid, email in email_map.items()
        if email and "@" in email
    ]


def sync_execution_to_klaviyo(
    db: Session,
    shop_domain: str,
    execution_id: str,
    klaviyo_api_key: str,
    product_a: str = "",
    product_b: str = "",
    suggested_message: str = "",
) -> dict:
    """
    Full Klaviyo sync for an execution opportunity:

    1. Get exposed audience with emails (holdout excluded)
    2. Find or create Klaviyo list: "HS_EXEC_{execution_id}"
    3. Batch-push profiles to the list
    4. Push a "WishSpark — Upsell Opportunity" event per profile

    The merchant creates a Klaviyo Flow triggered by list membership.
    Adding profiles to the list fires the flow automatically.

    Returns:
        {
            "list_id":    str | None,
            "synced":     int,     # profiles added to list
            "anonymous":  int,     # visitors without email (skipped)
            "errors":     int,
            "total_exposed": int,
        }
    """
    headers = _klaviyo_headers(klaviyo_api_key)

    # Step 1: Get exposed audience with emails
    audience = get_exposed_audience_with_emails(db, shop_domain, execution_id)
    total_exposed_ids = db.execute(
        text("""
            SELECT COUNT(*) FROM execution_audiences
            WHERE shop_domain = :shop AND execution_id = :eid AND group_type = 'exposed'
        """),
        {"shop": shop_domain, "eid": execution_id},
    ).scalar() or 0

    anonymous = total_exposed_ids - len(audience)

    if not audience:
        return {
            "list_id": None, "synced": 0,
            "anonymous": anonymous, "errors": 0,
            "total_exposed": total_exposed_ids,
        }

    # Step 2: Find or create Klaviyo list
    list_name = f"HS_EXEC_{execution_id}"
    list_id = _find_or_create_list(headers, list_name)
    if list_id is None:
        return {
            "list_id": None, "synced": 0,
            "anonymous": anonymous, "errors": 1,
            "total_exposed": total_exposed_ids,
        }

    # Step 3: Batch import profiles + add to list
    synced = 0
    errors = 0

    for i in range(0, len(audience), _PROFILE_BATCH_SIZE):
        batch = audience[i:i + _PROFILE_BATCH_SIZE]
        profile_ids = _batch_import_profiles(headers, batch, shop_domain,
                                              execution_id, product_a, product_b)
        if profile_ids:
            ok = _add_profiles_to_list(headers, list_id, profile_ids)
            if ok:
                synced += len(profile_ids)
            else:
                errors += 1
        else:
            errors += 1

    log.info(
        "klaviyo_export: execution sync shop=%s exec=%s "
        "list=%s synced=%d anonymous=%d errors=%d",
        shop_domain, execution_id, list_id, synced, anonymous, errors,
    )

    return {
        "list_id": list_id,
        "synced": synced,
        "anonymous": anonymous,
        "errors": errors,
        "total_exposed": total_exposed_ids,
    }


# ---------------------------------------------------------------------------
# Klaviyo API helpers (internal)
# ---------------------------------------------------------------------------

def _find_or_create_list(headers: dict, list_name: str) -> Optional[str]:
    """Find existing list by name, or create new one. Returns list_id or None."""
    try:
        # Search for existing list
        resp = httpx.get(
            KLAVIYO_LISTS_URL,
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        for item in resp.json().get("data", []):
            if item.get("attributes", {}).get("name") == list_name:
                return item["id"]

        # Create new list
        resp = httpx.post(
            KLAVIYO_LISTS_URL,
            headers=headers,
            json={"data": {"type": "list", "attributes": {"name": list_name}}},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("id")

    except Exception as exc:
        log.error("klaviyo_export: list create/find failed: %s", exc)
        return None


def _batch_import_profiles(
    headers: dict,
    audience: list[dict],
    shop_domain: str,
    execution_id: str,
    product_a: str,
    product_b: str,
) -> list[str]:
    """
    Batch import profiles to Klaviyo. Returns list of profile IDs.
    Each profile gets custom properties for flow personalization.
    """
    profiles = []
    for member in audience:
        profiles.append({
            "type": "profile",
            "attributes": {
                "email": member["email"],
                "properties": {
                    "hs_execution_id": execution_id,
                    "hs_product_a": product_a,
                    "hs_product_b": product_b,
                    "hs_shop": shop_domain,
                    "hs_source": "hedgespark",
                },
            },
        })

    try:
        resp = httpx.post(
            KLAVIYO_PROFILES_URL,
            headers=headers,
            json={"data": {"type": "profile-bulk-import-job", "attributes": {"profiles": {"data": profiles}}}},
            timeout=_REQUEST_TIMEOUT * 3,  # batch is slower
        )
        resp.raise_for_status()
        # Profile import is async — extract imported profile IDs from response
        imported = resp.json().get("data", {}).get("relationships", {}).get("imported-profiles", {}).get("data", [])
        return [p["id"] for p in imported if p.get("id")]
    except Exception as exc:
        log.error(
            "klaviyo_export: profile import failed exec=%s: %s",
            execution_id, str(exc)[:200],
        )
        return []


def _add_profiles_to_list(headers: dict, list_id: str, profile_ids: list[str]) -> bool:
    """Add profile IDs to a Klaviyo list. Idempotent."""
    try:
        resp = httpx.post(
            f"{KLAVIYO_LISTS_URL}{list_id}/relationships/profiles",
            headers=headers,
            json={"data": [{"type": "profile", "id": pid} for pid in profile_ids]},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.error(
            "klaviyo_export: add to list failed list=%s: %s",
            list_id, str(exc)[:200],
        )
        return False


# ---------------------------------------------------------------------------
# Intent signal → Klaviyo event push
# ---------------------------------------------------------------------------

# Signal types that represent "intent without conversion"
_INTENT_SIGNAL_TYPES = frozenset({
    "HIGH_ENGAGEMENT_NO_ACTION",
    "HIGH_TRAFFIC_NO_CART",
    "SCROLL_HIGH_NO_CLICK",
    "HIGH_RETURN_LOW_CONVERSION",
    "RETURN_VISITOR_INTEREST",
})


# ---------------------------------------------------------------------------
# push_intent_signals_to_klaviyo — stage helpers
# Refactor 2026-05-13 (A3 close): 216-LOC god function → composer + 9
# pure stage helpers (SQL constant + signal fetch + dedup factory +
# eligible-visitor picker + payload builder + profile resolver + HTTP
# poster + per-signal processor). Contract preserved byte-identical.
# ---------------------------------------------------------------------------


_FRESH_INTENT_SIGNALS_SQL = text("""
    SELECT product_url, signal_type, signal_strength
    FROM opportunity_signals
    WHERE shop_domain = :shop
      AND signal_type = ANY(:types)
      AND detected_at >= :cutoff
      AND signal_strength >= 0.4
      AND (signal_confidence IS NULL OR signal_confidence != 'low')
    ORDER BY signal_strength DESC
    LIMIT 10
""")


# Warm-top threshold: visitors in the upper warm band are included
# alongside hot visitors for Klaviyo push. This captures genuinely
# engaged visitors (70%+ scroll, 20s+ dwell) who fall just below the
# conservative hot threshold — critical for early-stage stores in
# fallback calibration mode where 0.55 is extremely hard to reach.
_WARM_TOP_BI_THRESHOLD = 0.40
_DEDUP_COOLDOWN_SECONDS = 12 * 3600  # 12h SETNX cooldown


def _fetch_fresh_intent_signals(db: Session, shop_domain: str, cutoff: datetime) -> list:
    return db.execute(_FRESH_INTENT_SIGNALS_SQL, {
        "shop": shop_domain,
        "types": list(_INTENT_SIGNAL_TYPES),
        "cutoff": cutoff,
    }).fetchall()


def _make_dedup_check(shop_domain: str):
    """Returns a callable(vid, purl, stype) → bool. SETNX-based atomic
    claim — two concurrent workers can't both push the same event.
    Fail-open on Redis errors (better to risk a dup than skip)."""
    def _is_already_pushed(vid: str, purl: str, stype: str) -> bool:
        try:
            from app.core.redis_client import _client
            from app.core.silent_fallback import record_silent_return
            rc = _client()
            if rc is None:
                record_silent_return("klaviyo_export.dedup_claim")
                return False
            key = f"hs:kpush:{shop_domain}:{vid}:{purl}:{stype}"
            claimed = rc.set(key, "1", nx=True, ex=_DEDUP_COOLDOWN_SECONDS)
            return not bool(claimed)
        except Exception:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("klaviyo_export.dedup_claim_fail_open")
            return False  # fail open
    return _is_already_pushed


def _pick_eligible_visitors(segment: dict) -> list[dict]:
    """HOT segment + WARM visitors whose behavioral_index >= 0.40."""
    hot = segment.get("hot", {}).get("visitors", [])
    warm = segment.get("warm", {}).get("visitors", [])
    warm_top = [
        v for v in warm
        if v.get("behavioral_index", 0) >= _WARM_TOP_BI_THRESHOLD
    ]
    return hot + warm_top


def _resolve_profile_attrs(
    email: str | None, vid: str, allow_anon: bool,
) -> tuple[dict, str] | None:
    """Returns (profile_attrs, display_label) tuple or None when the
    visitor is anonymous and ALLOW_INSECURE_DEV is off."""
    if email:
        return {"email": email}, email[:3] + "***"
    if not allow_anon:
        return None  # production path: skip anonymous, no synthetic profiles
    return (
        {
            "email": f"{vid[:8]}@anon.hedgespark.local",
            "external_id": vid,
        },
        f"anon:{vid[:8]}",
    )


def _build_klaviyo_event_payload(
    *,
    product_url: str, signal_type: str, signal_strength: float,
    visitor: dict, profile_attrs: dict, shop_domain: str,
) -> dict:
    """Klaviyo v3 event payload — single visitor/product/signal triple."""
    return {
        "data": {
            "type": "event",
            "attributes": {
                "metric": {
                    "data": {
                        "type": "metric",
                        "attributes": {"name": "HedgeSpark — Intent Detected"},
                    }
                },
                "profile": {
                    "data": {
                        "type": "profile",
                        "attributes": profile_attrs,
                    }
                },
                "properties": {
                    "product_url":      product_url,
                    "signal_type":      signal_type,
                    "signal_strength":  round(signal_strength, 3),
                    "behavioral_index": visitor["behavioral_index"],
                    "visit_count":      visitor["visit_count"],
                    "avg_scroll_pct":   visitor["avg_scroll"],
                    "avg_dwell_secs":   visitor["avg_dwell_secs"],
                    "shop_domain":      shop_domain,
                    "source":           "hedgespark",
                },
                "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            },
        }
    }


def _post_klaviyo_event(
    *,
    headers: dict, payload: dict, profile_label: str,
    shop_domain: str, product_url: str, signal_type: str,
) -> bool:
    """POST one event to Klaviyo. Returns True on 2xx, False on any
    failure (logged). Network/HTTP failures are documented operational
    states — never raised so a single 4xx doesn't kill the whole sync."""
    log.info(
        "klaviyo_intent: ATTEMPT shop=%s signal=%s product=%s profile=%s",
        shop_domain, signal_type, product_url[:60], profile_label,
    )
    try:
        resp = httpx.post(
            KLAVIYO_EVENTS_URL, headers=headers, json=payload, timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        log.info(
            "klaviyo_intent: OK %d shop=%s profile=%s",
            resp.status_code, shop_domain, profile_label,
        )
        return True
    except httpx.HTTPStatusError as exc:
        log.error(
            "klaviyo_intent: HTTP %d shop=%s profile=%s product=%s: %s",
            exc.response.status_code, shop_domain, profile_label,
            product_url, exc.response.text[:200],
        )
        return False
    except Exception as exc:
        log.error(
            "klaviyo_intent: FAIL shop=%s profile=%s: %s",
            shop_domain, profile_label, type(exc).__name__,
        )
        return False


def _process_intent_signal(
    *,
    db: Session, shop_domain: str, product_url: str, signal_type: str,
    signal_strength: float, headers: dict, allow_anon: bool,
    is_already_pushed,
) -> _SignalPushCounters:
    """Process one (product, signal) pair. Returns a _SignalPushCounters
    NamedTuple with field-named access (pushed / anonymous / errors)."""
    try:
        segment = segment_product_visitors(db, shop_domain, product_url, hours=72)
    except Exception as exc:
        log.warning(
            "klaviyo_intent: segment failed shop=%s product=%s: %s",
            shop_domain, product_url, type(exc).__name__,
        )
        return _SignalPushCounters(pushed=0, anonymous=0, errors=0)

    eligible = _pick_eligible_visitors(segment)
    if not eligible:
        return _SignalPushCounters(pushed=0, anonymous=0, errors=0)

    email_map = _resolve_visitor_emails(
        db, shop_domain, [v["visitor_id"] for v in eligible],
    )

    pushed = 0
    anonymous = 0
    errors = 0
    for visitor in eligible:
        vid = visitor["visitor_id"]
        if is_already_pushed(vid, product_url, signal_type):
            continue
        email = email_map.get(vid)
        profile = _resolve_profile_attrs(email, vid, allow_anon)
        if profile is None:
            # Anonymous visitor in production — skip + count
            anonymous += 1
            continue
        profile_attrs, profile_label = profile
        if not email:
            # Dev-mode anonymous: still counts toward anonymous tally
            anonymous += 1
        payload = _build_klaviyo_event_payload(
            product_url=product_url, signal_type=signal_type,
            signal_strength=signal_strength, visitor=visitor,
            profile_attrs=profile_attrs, shop_domain=shop_domain,
        )
        if _post_klaviyo_event(
            headers=headers, payload=payload, profile_label=profile_label,
            shop_domain=shop_domain, product_url=product_url,
            signal_type=signal_type,
        ):
            pushed += 1
        else:
            errors += 1
    return _SignalPushCounters(pushed=pushed, anonymous=anonymous, errors=errors)


def push_intent_signals_to_klaviyo(
    db: Session,
    shop_domain: str,
) -> dict:
    """
    Push 'HedgeSpark — Intent Detected' events to Klaviyo for fresh
    high-intent signals on a shop.

    Reads OpportunitySignal rows detected in the last 15 minutes,
    resolves visitor emails via purchase history, and sends one
    Klaviyo event per identified visitor per product.

    Returns: {"pushed": int, "anonymous": int, "errors": int, "signals": int}

    Refactored 2026-05-13 (A3 close): 216-LOC god function → 35-LOC
    composer + 9 pure helpers.
    """
    import os
    from app.services.klaviyo_connection import (
        resolve_klaviyo_key, record_sync_success, record_sync_failure,
    )

    api_key = resolve_klaviyo_key(db, shop_domain)
    if not api_key:
        return {"pushed": 0, "anonymous": 0, "errors": 0, "signals": 0, "skipped": "no_key"}

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=15)
    rows = _fetch_fresh_intent_signals(db, shop_domain, cutoff)
    if not rows:
        return {"pushed": 0, "anonymous": 0, "errors": 0, "signals": 0}

    allow_anon = os.getenv("ALLOW_INSECURE_DEV", "").lower() == "true"
    headers = _klaviyo_headers(api_key)
    is_already_pushed = _make_dedup_check(shop_domain)

    total_pushed = 0
    total_anonymous = 0
    total_errors = 0
    for product_url, signal_type, signal_strength in rows:
        counters = _process_intent_signal(
            db=db, shop_domain=shop_domain,
            product_url=product_url, signal_type=signal_type,
            signal_strength=signal_strength, headers=headers,
            allow_anon=allow_anon, is_already_pushed=is_already_pushed,
        )
        total_pushed += counters.pushed
        total_anonymous += counters.anonymous
        total_errors += counters.errors

    if total_errors == 0 and total_pushed > 0:
        record_sync_success(db, shop_domain)
    elif total_errors > 0:
        record_sync_failure(db, shop_domain, f"intent push errors={total_errors}")

    log.info(
        "klaviyo_intent: shop=%s signals=%d pushed=%d anonymous=%d errors=%d",
        shop_domain, len(rows), total_pushed, total_anonymous, total_errors,
    )
    return {
        "pushed": total_pushed,
        "anonymous": total_anonymous,
        "errors": total_errors,
        "signals": len(rows),
    }
