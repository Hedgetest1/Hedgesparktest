"""
brief_engine.py — Daily signal brief generator.

Reads OpportunitySignal rows from the last BRIEF_SIGNAL_WINDOW_HOURS hours
and produces a structured daily brief dict that is persisted to daily_briefs
by brief.py.

Public interface
----------------
    generate_brief(db: Session, shop_domain: str) -> dict

    Returns a brief dict with keys matching DailyBrief model columns.
    Never raises — returns an empty-state brief on any error.

Design
------
The function accepts an injected SQLAlchemy Session and does NOT open its
own session or connection.  All session lifecycle is owned by the caller
(brief.py's _get_full_brief via Depends(get_db)).

Callers that were previously using SessionLocal() directly must now obtain
a session via Depends(get_db) and pass it here.  This eliminates the
per-call session open that was the original implementation.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.opportunity_signal import OpportunitySignal
from app.services.signal_text import humanize_action

logger = logging.getLogger(__name__)

BRIEF_SIGNAL_WINDOW_HOURS: int = 24

_PRIORITY_BONUS: dict[str, int] = {
    "TRAFFIC_SPIKE":              40,
    "DEAD_TRAFFIC":               35,
    "HIGH_TRAFFIC_NO_CART":       30,
    "HIGH_ENGAGEMENT_NO_ACTION":  28,
    "LOW_CONVERSION_ATTENTION":   20,
    "HIGH_RETURN_LOW_CONVERSION": 18,
    "SCROLL_HIGH_NO_CLICK":       15,
    "RETURN_VISITOR_INTEREST":    10,
}

_EMPTY_STATE_HEADLINE = (
    "No product signals yet — check back after your first day of traffic."
)


def _label_from_url(url: str) -> str:
    if not url:
        return "this product"
    clean = url.split("?")[0].split("#")[0].rstrip("/")
    parts = [p for p in clean.split("/") if p]
    for i, part in enumerate(parts):
        if part == "products" and i + 1 < len(parts):
            return parts[i + 1].replace("-", " ").replace("_", " ").title()
    if parts:
        return parts[-1].replace("-", " ").replace("_", " ").title()
    return "this product"


def _rank_score(signal_type: str, signal_strength: float) -> float:
    return signal_strength * 100 + _PRIORITY_BONUS.get(signal_type, 0)


def generate_brief(db: Session, shop_domain: str) -> dict:
    """
    Generate a daily brief for the given shop using an injected DB session.

    Parameters
    ----------
    db          Active SQLAlchemy session — provided by the caller via
                Depends(get_db).  This function does NOT open or close
                sessions.
    shop_domain Merchant shop domain.

    Returns
    -------
    dict matching DailyBrief column keys.  Returns an empty-state dict
    on DB error rather than raising.
    """
    now = datetime.now(timezone.utc)
    # Strip timezone for DB comparison — OpportunitySignal.detected_at is
    # stored as naive UTC datetime (database default).
    now_naive = now.replace(tzinfo=None)
    today = now.date()
    detected_cutoff = now_naive - timedelta(hours=BRIEF_SIGNAL_WINDOW_HOURS)

    _empty: dict = {
        "shop_domain":      shop_domain,
        "brief_date":       today,
        "generated_at":     now_naive,
        "headline":         _EMPTY_STATE_HEADLINE,
        "top_product_url":  None,
        "top_product_label": None,
        "top_signal_type":  None,
        "top_action":       None,
        "signals_count":    0,
        "metrics_snapshot": json.dumps([]),
        "summary_text":     None,
        "summary_generated": False,
    }

    try:
        rows = (
            db.query(OpportunitySignal)
            .filter(
                OpportunitySignal.shop_domain == shop_domain,
                OpportunitySignal.detected_at >= detected_cutoff,
                OpportunitySignal.expires_at  >= now_naive,
            )
            .all()
        )
    except Exception as exc:
        logger.error(
            "brief_engine.generate_brief(%r): DB read failed — %s", shop_domain, exc
        )
        return _empty

    if not rows:
        return _empty

    best_per_product: dict[str, tuple[float, OpportunitySignal]] = {}

    for row in rows:
        score = _rank_score(row.signal_type, row.signal_strength)
        existing = best_per_product.get(row.product_url)
        if existing is None or score > existing[0]:
            best_per_product[row.product_url] = (score, row)

    if not best_per_product:
        return _empty

    ranked = sorted(best_per_product.values(), key=lambda t: t[0], reverse=True)

    top_score, top_row = ranked[0]
    top_url    = top_row.product_url
    top_label  = _label_from_url(top_url)
    top_signal_type = top_row.signal_type
    top_action = humanize_action(top_signal_type)
    headline   = top_row.explanation or _EMPTY_STATE_HEADLINE

    snapshot: list[dict] = []
    for _score, sig_row in ranked[:3]:
        label = _label_from_url(sig_row.product_url)
        snapshot.append({
            "product_url":    sig_row.product_url,
            "product_label":  label,
            "signal_type":    sig_row.signal_type,
            "signal_strength": sig_row.signal_strength,
            "human_label":    sig_row.explanation or "",
            "human_action":   humanize_action(sig_row.signal_type),
        })

    return {
        "shop_domain":      shop_domain,
        "brief_date":       today,
        "generated_at":     now_naive,
        "headline":         headline,
        "top_product_url":  top_url,
        "top_product_label": top_label,
        "top_signal_type":  top_signal_type,
        "top_action":       top_action,
        "signals_count":    len(rows),
        "metrics_snapshot": json.dumps(snapshot),
        "summary_text":     None,
        "summary_generated": False,
    }
