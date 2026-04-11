"""
email_journey.py — Merchant email journey state engine.

Manages the per-merchant journey state machine that tracks every
significant touchpoint from beta invite through activation.

Public interface:
    get_or_create_journey(db, shop_domain) -> MerchantJourneyState
    get_journey(db, shop_domain) -> MerchantJourneyState | None
    record_invite_sent(db, shop_domain, resend_id) -> MerchantJourneyState
    record_event(db, shop_domain, event_type, resend_email_id) -> bool
    record_onboarding_started(db, shop_domain) -> None
    record_onboarding_completed(db, shop_domain) -> None
    record_followup_sent(db, shop_domain, variant, resend_id) -> None
    record_inbound_reply(db, shop_domain) -> None
    get_followup_eligible(db) -> list[MerchantJourneyState]
    get_journey_summary(db, shop_domain) -> dict

State transitions are deterministic — each function advances
the journey only if the precondition is met.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.merchant_journey_state import MerchantJourneyState

log = logging.getLogger("email_journey")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Core CRUD
# ---------------------------------------------------------------------------

def get_or_create_journey(db: Session, shop_domain: str) -> MerchantJourneyState:
    """Get existing journey state or create a new one."""
    journey = (
        db.query(MerchantJourneyState)
        .filter(MerchantJourneyState.shop_domain == shop_domain)
        .first()
    )
    if journey is None:
        journey = MerchantJourneyState(
            shop_domain=shop_domain,
            current_stage="new",
        )
        db.add(journey)
        db.flush()
        log.info("email_journey: created journey for %s", shop_domain)
    return journey


def get_journey(db: Session, shop_domain: str) -> MerchantJourneyState | None:
    """Get journey state without creating. Returns None if not found."""
    return (
        db.query(MerchantJourneyState)
        .filter(MerchantJourneyState.shop_domain == shop_domain)
        .first()
    )


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

def _recompute_stage(journey: MerchantJourneyState) -> str:
    """
    Derive current_stage from timestamps and suppression state.

    Priority order (highest stage wins, but suppression overrides):
        suppressed (bounced/complained) — terminal, overrides everything
        replied > activated_pro > activated_lite > active >
        followed_up > clicked > opened > invited > new
    """
    # Suppression is terminal — merchant has bounced or complained
    if journey.email_suppressed:
        return "suppressed"
    if journey.inbound_reply_received_at:
        return "replied"
    if journey.pro_activation_sent_at:
        return "activated_pro"
    if journey.lite_activation_sent_at:
        return "activated_lite"
    if journey.onboarding_completed_at:
        return "active"
    if journey.followup_48h_sent_at:
        return "followed_up"
    if journey.onboarding_started_at:
        return "onboarding"
    if journey.beta_invite_clicked_at:
        return "clicked"
    if journey.beta_invite_opened_at:
        return "opened"
    if journey.beta_invite_sent_at:
        return "invited"
    return "new"


def record_invite_sent(
    db: Session,
    shop_domain: str,
    resend_id: str | None = None,
) -> MerchantJourneyState:
    """Record that a beta invite was sent to this merchant."""
    journey = get_or_create_journey(db, shop_domain)
    if journey.beta_invite_sent_at is None:
        journey.beta_invite_sent_at = _now()
        journey.beta_invite_resend_id = resend_id
        journey.current_stage = _recompute_stage(journey)
        journey.updated_at = _now()
        db.flush()
        log.info("email_journey: invite sent shop=%s resend_id=%s", shop_domain, resend_id)
    return journey


def record_event(
    db: Session,
    shop_domain: str,
    event_type: str,
    resend_email_id: str | None = None,
    event_timestamp: datetime | None = None,
) -> bool:
    """
    Record a Resend delivery event (opened/clicked) on the journey.

    Matches resend_email_id against journey's stored resend IDs to determine
    which email (invite vs follow-up) was interacted with.

    event_timestamp: the actual Resend event time (preserves causality when
    events arrive out of order). Falls back to _now() if not provided.

    Returns True if the journey was updated, False if no match / already recorded.
    """
    journey = get_journey(db, shop_domain)
    if journey is None:
        return False

    updated = False
    ts = event_timestamp or _now()

    if event_type == "opened":
        # Check if this is the invite email
        if resend_email_id and resend_email_id == journey.beta_invite_resend_id:
            if journey.beta_invite_opened_at is None:
                journey.beta_invite_opened_at = ts
                updated = True
        # Check if this is the follow-up email
        elif resend_email_id and resend_email_id == journey.followup_48h_resend_id:
            if journey.followup_48h_opened_at is None:
                journey.followup_48h_opened_at = ts
                updated = True
        # Fallback: if we can't match, update invite opened (most common case)
        elif journey.beta_invite_opened_at is None:
            journey.beta_invite_opened_at = ts
            updated = True

    elif event_type == "clicked":
        if resend_email_id and resend_email_id == journey.beta_invite_resend_id:
            if journey.beta_invite_clicked_at is None:
                # Also mark opened — use earlier timestamp (opened must precede clicked)
                if journey.beta_invite_opened_at is None:
                    journey.beta_invite_opened_at = ts
                journey.beta_invite_clicked_at = ts
                updated = True
        elif resend_email_id and resend_email_id == journey.followup_48h_resend_id:
            if journey.followup_48h_clicked_at is None:
                if journey.followup_48h_opened_at is None:
                    journey.followup_48h_opened_at = ts
                journey.followup_48h_clicked_at = ts
                updated = True
        elif journey.beta_invite_clicked_at is None:
            if journey.beta_invite_opened_at is None:
                journey.beta_invite_opened_at = ts
            journey.beta_invite_clicked_at = ts
            updated = True

    if updated:
        journey.current_stage = _recompute_stage(journey)
        journey.updated_at = _now()  # updated_at is always wall-clock
        db.flush()
        log.info(
            "email_journey: event=%s shop=%s stage=%s ts=%s",
            event_type, shop_domain, journey.current_stage, ts.isoformat(),
        )

    return updated


def record_onboarding_started(db: Session, shop_domain: str) -> None:
    """Record that the merchant started onboarding (OAuth install)."""
    journey = get_or_create_journey(db, shop_domain)
    if journey.onboarding_started_at is None:
        journey.onboarding_started_at = _now()
        journey.current_stage = _recompute_stage(journey)
        journey.updated_at = _now()
        db.flush()


def record_onboarding_completed(db: Session, shop_domain: str) -> None:
    """Record that the merchant completed onboarding."""
    journey = get_or_create_journey(db, shop_domain)
    if journey.onboarding_completed_at is None:
        if journey.onboarding_started_at is None:
            journey.onboarding_started_at = _now()
        journey.onboarding_completed_at = _now()
        journey.current_stage = _recompute_stage(journey)
        journey.updated_at = _now()
        db.flush()


def record_followup_sent(
    db: Session,
    shop_domain: str,
    variant: str,
    resend_id: str | None = None,
) -> None:
    """Record that the 48h follow-up was sent."""
    journey = get_or_create_journey(db, shop_domain)
    if journey.followup_48h_sent_at is None:
        journey.followup_48h_sent_at = _now()
        journey.followup_48h_variant = variant
        journey.followup_48h_resend_id = resend_id
        journey.current_stage = _recompute_stage(journey)
        journey.updated_at = _now()
        db.flush()
        log.info("email_journey: followup sent shop=%s variant=%s", shop_domain, variant)


def suppress_email(db: Session, shop_domain: str, reason: str) -> None:
    """
    Mark a merchant's email as suppressed due to hard bounce or complaint.

    Durable DB-level flag that survives Redis flush. Checked by followup_worker
    and lifecycle email service before sending.
    """
    journey = get_or_create_journey(db, shop_domain)
    if journey.email_suppressed is None:
        journey.email_suppressed = reason
        journey.email_suppressed_at = _now()
        journey.updated_at = _now()
        db.flush()
        log.warning("email_journey: email suppressed shop=%s reason=%s", shop_domain, reason)


def record_inbound_reply(db: Session, shop_domain: str) -> None:
    """Record that the merchant sent an inbound reply."""
    journey = get_or_create_journey(db, shop_domain)
    # Always update the timestamp (most recent reply)
    journey.inbound_reply_received_at = _now()
    journey.current_stage = _recompute_stage(journey)
    journey.updated_at = _now()
    db.flush()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_followup_eligible(db: Session) -> list[MerchantJourneyState]:
    """
    Find merchants eligible for 48h follow-up.

    Criteria:
        - beta invite was sent
        - more than 48 hours ago
        - no follow-up sent yet
        - no onboarding completed (they haven't finished setup)

    Includes merchants who opened but didn't click — they showed intent
    but didn't convert. These are the highest-value re-engagement targets.
    The variant picker (_pick_variant) selects the right message based on
    their engagement level (noopen vs opened vs clicked).
    """
    cutoff = _now() - timedelta(hours=48)
    return (
        db.query(MerchantJourneyState)
        .filter(
            MerchantJourneyState.beta_invite_sent_at.isnot(None),
            MerchantJourneyState.beta_invite_sent_at <= cutoff,
            MerchantJourneyState.followup_48h_sent_at.is_(None),
            MerchantJourneyState.onboarding_completed_at.is_(None),
        )
        .all()
    )


def get_journey_summary(db: Session, shop_domain: str | None = None) -> list[dict] | dict:
    """
    Return journey state(s) as dict(s) for operator visibility.

    If shop_domain is provided, returns a single dict.
    Otherwise returns list of all journeys.
    """
    if shop_domain:
        j = get_journey(db, shop_domain)
        if j is None:
            return {}
        return _journey_to_dict(j)

    journeys = (
        db.query(MerchantJourneyState)
        .order_by(MerchantJourneyState.updated_at.desc())
        .limit(200)
        .all()
    )
    return [_journey_to_dict(j) for j in journeys]


def _journey_to_dict(j: MerchantJourneyState) -> dict:
    def _ts(dt):
        return dt.isoformat() + "Z" if dt else None

    return {
        "shop_domain": j.shop_domain,
        "current_stage": j.current_stage,
        "beta_invite_sent_at": _ts(j.beta_invite_sent_at),
        "beta_invite_opened_at": _ts(j.beta_invite_opened_at),
        "beta_invite_clicked_at": _ts(j.beta_invite_clicked_at),
        "onboarding_started_at": _ts(j.onboarding_started_at),
        "onboarding_completed_at": _ts(j.onboarding_completed_at),
        "followup_48h_sent_at": _ts(j.followup_48h_sent_at),
        "followup_48h_variant": j.followup_48h_variant,
        "followup_48h_opened_at": _ts(j.followup_48h_opened_at),
        "followup_48h_clicked_at": _ts(j.followup_48h_clicked_at),
        "lite_activation_sent_at": _ts(j.lite_activation_sent_at),
        "pro_activation_sent_at": _ts(j.pro_activation_sent_at),
        "inbound_reply_received_at": _ts(j.inbound_reply_received_at),
        "updated_at": _ts(j.updated_at),
    }
