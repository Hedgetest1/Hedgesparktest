"""
brief.py — /brief/today and /brief/today/pro endpoints.

Product boundary
----------------
Lite route  GET /brief/today
  Returns the daily brief with diagnostic fields only.
  Prescriptive fields are stripped at this API boundary.

  Lite fields: brief_date, generated_at, headline, top_product_url,
               top_product_label, top_signal_type, signals_count,
               summary_generated, metrics_snapshot (without human_action).

  Stripped:    top_action, summary_text,
               and human_action from each metrics_snapshot entry.

Pro route   GET /brief/today/pro
  Identical to the Lite response PLUS top_action, summary_text, and
  human_action inside each metrics_snapshot entry.
  Backend-enforced via require_pro_plan (HTTP 403 for non-Pro shops).

Both routes share _get_full_brief(), a three-level cache (Redis → DB →
on-demand generation).  _strip_to_lite() is applied at the Lite route
boundary; the service layer and cache always hold the full (Pro-shaped)
response so the two routes never need separate cache keys.

Field classification
--------------------
Descriptive: brief_date, generated_at, top_product_url, top_product_label,
             top_signal_type, signals_count, summary_generated
Diagnostic:  headline  (what is happening — always shown)
Prescriptive (Pro only):
             top_action     (what the merchant should do — top signal)
             summary_text   (Pro AI narrative — populated by AI worker)
             human_action   (per-product prescriptive sentence in snapshot)

No AI call is made on the cold path.  summary_text is populated only by
the optional AI worker step (Phase 2, Pro plan).  Both routes always
return within the normal DB read time budget.

Request
-------
    GET /brief/today?shop=<shop_domain>          (Lite)
    GET /brief/today/pro?shop=<shop_domain>      (Pro)
    Headers: X-API-Key (when DASHBOARD_API_KEY is configured)

Response
--------
    200 OK — JSON dict with daily brief fields (see above for per-tier fields).
    400 if shop param is missing or invalid (from require_shop).
    403 if Pro route called by non-Pro shop (from require_pro_plan).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.core.deps import require_api_key, require_pro_plan, require_shop
from app.core.redis_client import KEY_BRIEF, TTL_BRIEF, cache_get, cache_set
from app.models.daily_brief import DailyBrief
from app.services.brief_engine import generate_brief

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/brief", tags=["brief"])

# Prescriptive top-level fields excluded from the Lite response.
# human_action inside metrics_snapshot entries is stripped separately.
_LITE_BRIEF_EXCLUDE: set[str] = {"top_action", "summary_text"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _serialize(row: DailyBrief) -> dict:
    """
    Serialise a DailyBrief ORM row to a JSON-safe dict.

    Always includes all fields (Pro-shaped).  Prescriptive fields are
    stripped by _strip_to_lite() at the Lite route boundary — not here.

    metrics_snapshot is decoded from its stored JSON string to a list so
    the client receives a native array rather than a raw string.
    """
    snapshot: list = []
    if row.metrics_snapshot:
        try:
            snapshot = json.loads(row.metrics_snapshot)
        except (json.JSONDecodeError, ValueError):
            snapshot = []

    return {
        "brief_date": row.brief_date.isoformat() if row.brief_date else None,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        "headline": row.headline,
        "top_product_url": row.top_product_url,
        "top_product_label": row.top_product_label,
        "top_signal_type": row.top_signal_type,
        "top_action": row.top_action,
        "signals_count": row.signals_count or 0,
        "metrics_snapshot": snapshot,
        "summary_text": row.summary_text,
        "summary_generated": bool(row.summary_generated),
    }


def _strip_to_lite(data: dict) -> dict:
    """
    Strip prescriptive fields for Lite callers.

    Removes top_action and summary_text from the top-level dict.
    Removes human_action from every metrics_snapshot entry.

    The service layer and Redis cache always hold the full (Pro-shaped)
    dict — stripping happens only at the Lite route boundary.
    """
    result = {k: v for k, v in data.items() if k not in _LITE_BRIEF_EXCLUDE}
    if result.get("metrics_snapshot"):
        result["metrics_snapshot"] = [
            {k: v for k, v in entry.items() if k != "human_action"}
            for entry in result["metrics_snapshot"]
        ]
    return result


def _read_today(db, shop_domain: str) -> DailyBrief | None:
    """Return today's DailyBrief row for the shop, or None."""
    return (
        db.query(DailyBrief)
        .filter(
            DailyBrief.shop_domain == shop_domain,
            DailyBrief.brief_date == datetime.utcnow().date(),
        )
        .order_by(DailyBrief.generated_at.desc())
        .first()
    )


def _insert_brief(db, brief_dict: dict) -> DailyBrief | None:
    """
    Insert a brief row.  Returns the inserted row on success.
    Returns None on IntegrityError (another request already inserted
    the row for today — the caller should re-read).
    All other exceptions propagate to the caller.
    """
    row = DailyBrief(
        shop_domain=brief_dict["shop_domain"],
        brief_date=brief_dict["brief_date"],
        generated_at=brief_dict.get("generated_at") or datetime.utcnow(),
        headline=brief_dict["headline"],
        top_product_url=brief_dict.get("top_product_url"),
        top_product_label=brief_dict.get("top_product_label"),
        top_signal_type=brief_dict.get("top_signal_type"),
        top_action=brief_dict.get("top_action"),
        signals_count=brief_dict.get("signals_count", 0),
        metrics_snapshot=brief_dict.get("metrics_snapshot"),
        summary_text=brief_dict.get("summary_text"),
        summary_generated=brief_dict.get("summary_generated", False),
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
        return row
    except IntegrityError:
        db.rollback()
        return None


def _get_full_brief(shop: str) -> dict:
    """
    Three-level cache (Redis → DB → on-demand generation).

    Always returns the full (Pro-shaped) response including all prescriptive
    fields.  The Redis cache also stores the full response.  Callers that
    serve the Lite route must apply _strip_to_lite() before returning.

    Sharing one cache key between Lite and Pro is safe because the Pro-shaped
    response is a strict superset of the Lite response — stripping happens at
    the API boundary, not in the cache.
    """
    redis_key = KEY_BRIEF.format(shop=shop)

    # ------------------------------------------------------------------ #
    # Level 1 — Redis cache                                               #
    # ------------------------------------------------------------------ #
    cached = cache_get(redis_key)
    if cached is not None:
        return cached

    db = SessionLocal()
    try:
        # ---------------------------------------------------------------- #
        # Level 2 — DB read                                               #
        # ---------------------------------------------------------------- #
        row = _read_today(db, shop)
        if row is not None:
            result = _serialize(row)
            cache_set(redis_key, result, TTL_BRIEF)
            return result

        # ---------------------------------------------------------------- #
        # Level 3 — on-demand generation                                  #
        # ---------------------------------------------------------------- #
        brief_dict = generate_brief(shop)
        inserted = _insert_brief(db, brief_dict)

        if inserted is None:
            # Another concurrent request won the race — re-read the winner.
            row = _read_today(db, shop)
            if row is not None:
                result = _serialize(row)
                cache_set(redis_key, result, TTL_BRIEF)
                return result
            # Extremely unlikely: still not found after race — return the
            # generated dict directly without caching or DB.
            logger.warning(
                "brief._get_full_brief(%r): insert lost race but re-read also missed",
                shop,
            )
            snapshot: list = []
            if brief_dict.get("metrics_snapshot"):
                try:
                    snapshot = json.loads(brief_dict["metrics_snapshot"])
                except (json.JSONDecodeError, ValueError):
                    snapshot = []
            return {
                "brief_date": brief_dict["brief_date"].isoformat(),
                "generated_at": brief_dict["generated_at"].isoformat(),
                "headline": brief_dict["headline"],
                "top_product_url": brief_dict.get("top_product_url"),
                "top_product_label": brief_dict.get("top_product_label"),
                "top_signal_type": brief_dict.get("top_signal_type"),
                "top_action": brief_dict.get("top_action"),
                "signals_count": brief_dict.get("signals_count", 0),
                "metrics_snapshot": snapshot,
                "summary_text": brief_dict.get("summary_text"),
                "summary_generated": brief_dict.get("summary_generated", False),
            }

        result = _serialize(inserted)
        cache_set(redis_key, result, TTL_BRIEF)
        return result

    except Exception as exc:
        logger.error("brief._get_full_brief(%r): unexpected error — %s", shop, exc)
        raise

    finally:
        db.close()


# ---------------------------------------------------------------------------
# Lite route — GET /brief/today
#
# Diagnostic fields only. top_action, summary_text, and human_action inside
# each metrics_snapshot entry are stripped at this boundary.
# ---------------------------------------------------------------------------
@router.get("/today")
def get_today_brief(
    shop: str = Depends(require_shop),
    _: None = Depends(require_api_key),
):
    """
    Lite daily brief — diagnostic fields only.

    Uses a three-level cache (Redis → DB → on-demand generation).
    No AI call is made on this path.

    Lite boundary: headline, top_signal_type, top_product_url/label,
                   signals_count, summary_generated, metrics_snapshot
                   (without human_action per entry).

    Prescriptive fields stripped: top_action, summary_text,
    and human_action from each metrics_snapshot entry.

    Pro subscribers call /brief/today/pro to receive the full response.
    """
    return _strip_to_lite(_get_full_brief(shop))


# ---------------------------------------------------------------------------
# Pro route — GET /brief/today/pro
#
# Full response including top_action, summary_text, and human_action inside
# each metrics_snapshot entry. Backend-enforced via require_pro_plan.
# ---------------------------------------------------------------------------
@router.get("/today/pro")
def get_today_brief_pro(
    shop: str = Depends(require_pro_plan),
):
    """
    Pro daily brief — full response including prescriptive fields.

    Backend-enforced: require_pro_plan raises HTTP 403 if the shop does not
    have an active Pro plan (merchants.plan != "pro" or billing_active == False).
    API key and shop-domain validation are composed inside require_pro_plan.

    Returns the same response as /brief/today plus:
      top_action    — prescriptive sentence for the top signal
      summary_text  — Pro AI narrative (null until AI worker runs)
      human_action  — prescriptive sentence per metrics_snapshot entry

    Lite boundary: diagnostic fields (served by /brief/today)
    Pro boundary:  prescriptive fields (served here — plan-enforced)
    """
    return _get_full_brief(shop)
