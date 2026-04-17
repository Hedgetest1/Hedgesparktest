"""
share_engine.py — Viral share loop engine.

Generates shareable proof assets from nudge lift data. Every positive
result becomes a distribution asset: shareable URL, pre-formatted
social text, and OG-ready metadata.

Design:
  - Share data is a snapshot (immutable at creation time)
  - No merchant-identifiable data unless explicitly included
  - Tokens are URL-safe, non-guessable
  - Tracking is lightweight and non-blocking

Integration:
  - Called from dashboard API when merchant clicks "Share"
  - Public proof page served at /proof/[token]
  - Share events tracked for viral coefficient measurement
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.share_event import PublicProofShare, ShareEvent

log = logging.getLogger(__name__)

_SHARE_EXPIRY_DAYS = 90
_TOKEN_LENGTH = 32


def generate_share(
    db: Session,
    shop_domain: str,
    nudge_id: int | None = None,
    window_hours: int = 168,
) -> dict | None:
    """
    Generate a shareable proof asset.

    If nudge_id is provided: shares a specific nudge lift result.
    If nudge_id is None: shares the store's overall proof report.

    Returns a dict with share_token, urls, and pre-formatted text, or None if
    no proof data exists.
    """
    # Get proof data
    proof = _get_proof_data(db, shop_domain, nudge_id, window_hours)
    if not proof:
        return None

    # Generate token
    token = secrets.token_urlsafe(_TOKEN_LENGTH)

    # Build headline
    headline = _build_headline(proof)

    # Build share text
    twitter_text = _build_twitter_text(proof, token)
    generic_text = _build_generic_text(proof)

    # Create share record
    share = PublicProofShare(
        shop_domain=shop_domain,
        share_token=token,
        proof_type="nudge_lift" if nudge_id else "store_proof",
        nudge_id=nudge_id,
        proof_snapshot=_sanitize_snapshot(proof),
        headline=headline,
        twitter_text=twitter_text,
        generic_text=generic_text,
        expires_at=_now() + timedelta(days=_SHARE_EXPIRY_DAYS),
    )
    db.add(share)
    db.commit()

    share_url = f"https://app.hedgesparkhq.com/proof/{token}"

    return {
        "share_token": token,
        "share_url": share_url,
        "headline": headline,
        "twitter_text": twitter_text,
        "generic_text": generic_text,
        "og_title": headline,
        "og_description": generic_text,
        "expires_at": share.expires_at.isoformat() if share.expires_at else None,
    }


def get_public_proof(db: Session, token: str) -> dict | None:
    """
    Retrieve a public proof share by token. Increments view count.
    Returns the proof snapshot for rendering, or None if expired/not found.
    """
    share = (
        db.query(PublicProofShare)
        .filter(PublicProofShare.share_token == token)
        .first()
    )

    if not share:
        return None

    if share.expires_at and share.expires_at < _now():
        return None

    # Increment view count
    share.view_count = (share.view_count or 0) + 1
    db.commit()

    # Track view event
    _track_event(db, token, "view")

    return {
        "headline": share.headline,
        "proof": share.proof_snapshot,
        "proof_type": share.proof_type,
        "created_at": share.created_at.isoformat() if share.created_at else None,
        "view_count": share.view_count,
    }


def track_share_action(db: Session, token: str, event_type: str, channel: str | None = None, referrer: str | None = None) -> None:
    """Track a share event (share, click_cta, install)."""
    _track_event(db, token, event_type, channel, referrer)

    # Update counters on the share record
    if event_type == "click_cta":
        db.execute(
            text("UPDATE public_proof_shares SET click_cta_count = click_cta_count + 1 WHERE share_token = :t"),
            {"t": token},
        )
    elif event_type == "install":
        db.execute(
            text("UPDATE public_proof_shares SET installs_attributed = installs_attributed + 1 WHERE share_token = :t"),
            {"t": token},
        )
    db.commit()


def get_viral_metrics(db: Session) -> dict:
    """Compute viral coefficient and share funnel metrics."""
    row = db.execute(text("""
        SELECT
            COUNT(DISTINCT s.share_token) AS total_shares,
            COALESCE(SUM(s.view_count), 0) AS total_views,
            COALESCE(SUM(s.click_cta_count), 0) AS total_clicks,
            COALESCE(SUM(s.installs_attributed), 0) AS total_installs
        FROM public_proof_shares s
        WHERE s.created_at > NOW() - INTERVAL '30 days'
    """)).fetchone()

    shares = row[0] or 0
    views = row[1] or 0
    clicks = row[2] or 0
    installs = row[3] or 0

    return {
        "period": "30d",
        "shares_created": shares,
        "proof_page_views": views,
        "cta_clicks": clicks,
        "installs_from_shares": installs,
        "view_to_click_rate": round(clicks / views, 4) if views > 0 else 0,
        "click_to_install_rate": round(installs / clicks, 4) if clicks > 0 else 0,
        "viral_coefficient": round(installs / shares, 4) if shares > 0 else 0,
    }


# ══════════════════════════════════════════════════════════════════════════
# Internal
# ══════════════════════════════════════════════════════════════════════════

def _get_proof_data(db: Session, shop_domain: str, nudge_id: int | None, window_hours: int) -> dict | None:
    """Get proof data from the proof engine."""
    try:
        from app.services.proof_engine import get_proof_report
        report = get_proof_report(db, shop_domain, window_hours)
        if not report or not report.get("has_proof"):
            return None

        # If specific nudge requested, find it in the report
        if nudge_id and report.get("holdout_proof", {}).get("nudges"):
            for n in report["holdout_proof"]["nudges"]:
                if n.get("nudge_id") == nudge_id:
                    return {
                        "type": "nudge",
                        "nudge_id": nudge_id,
                        "lift_pct": n.get("lift_pct"),
                        "exposed_cvr": n.get("exposed_cvr"),
                        "holdout_cvr": n.get("holdout_cvr"),
                        "exposed_count": n.get("exposed_count"),
                        "holdout_count": n.get("holdout_count"),
                        "p_value": n.get("p_value"),
                        "incremental_revenue": n.get("incremental_revenue"),
                        "currency": report.get("currency", "USD"),
                        "confidence": report.get("confidence", {}).get("label", ""),
                    }

        # Store-level proof
        hp = report.get("holdout_proof", {})
        return {
            "type": "store",
            "lift_pct": hp.get("lift_pct"),
            "exposed_cvr": hp.get("pooled_exposed_cvr"),
            "holdout_cvr": hp.get("pooled_holdout_cvr"),
            "nudges_measured": hp.get("nudges_measured", 0),
            "total_exposed": hp.get("total_exposed", 0),
            "total_holdout": hp.get("total_holdout", 0),
            "incremental_revenue": report.get("total_incremental_revenue"),
            "currency": report.get("currency", "USD"),
            "confidence": report.get("confidence", {}).get("label", ""),
            "headline": report.get("headline", ""),
        }
    except Exception as exc:
        log.warning("share_engine: proof data fetch failed for %s: %s", shop_domain, exc)
        return None


def _sanitize_snapshot(proof: dict) -> dict:
    """Remove any merchant-identifiable data from the proof snapshot."""
    safe = dict(proof)
    # Remove fields that could identify the merchant
    for key in ("shop_domain", "product_url", "product_name"):
        safe.pop(key, None)
    return safe


def _build_headline(proof: dict) -> str:
    lift = proof.get("lift_pct")
    if lift and lift > 0:
        return f"+{lift:.0f}% conversion lift, measured with holdout testing"
    revenue = proof.get("incremental_revenue")
    currency = proof.get("currency")
    if revenue and revenue > 0:
        from app.core.currency import format_money
        return f"{format_money(revenue, currency)} incremental revenue recovered"
    return "Conversion improvement measured with holdout testing"


def _build_twitter_text(proof: dict, token: str) -> str:
    lift = proof.get("lift_pct")
    url = f"https://app.hedgesparkhq.com/proof/{token}"
    confidence = proof.get("confidence", "")

    if lift and lift > 0:
        parts = [f"Just measured a +{lift:.0f}% conversion lift on a Shopify product."]
        parts.append("Holdout-tested. Not vibes.")
        if proof.get("p_value") and proof["p_value"] < 0.05:
            parts.append(f"p < 0.05.")
        parts.append(f"\n{url}")
        return " ".join(parts)

    return f"Measured real conversion impact on my Shopify store using holdout testing.\n{url}"


def _build_generic_text(proof: dict) -> str:
    lift = proof.get("lift_pct")
    total = (proof.get("total_exposed") or proof.get("exposed_count") or 0) + (proof.get("total_holdout") or proof.get("holdout_count") or 0)

    if lift and lift > 0:
        return f"HedgeSpark detected a revenue leak, deployed a fix, and proved +{lift:.0f}% conversion lift with a control group. {total:,} visitors measured."

    return "HedgeSpark measured real conversion impact using holdout testing — no guessing."


def _track_event(db: Session, token: str, event_type: str, channel: str | None = None, referrer: str | None = None) -> None:
    event = ShareEvent(
        share_token=token,
        event_type=event_type,
        channel=channel,
        referrer=referrer[:512] if referrer else None,
    )
    db.add(event)
    db.commit()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
