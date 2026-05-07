"""
OnboardingEvent — append-only log of merchant onboarding funnel events.

Each row represents a single onboarding interaction or milestone.
Events are idempotent per (shop_domain, event_type) for milestone
events but allow duplicates for interaction events (clicks, views).

Funnel milestones (ordered):
    install_completed       — OAuth callback finished
    setup_completed         — store connected (webhook + tracker OK)
    pixel_viewed            — pixel setup instructions shown
    pixel_copy_clicked      — merchant copied pixel code
    pixel_confirmed         — merchant clicked "I've connected the pixel"
    pixel_skipped           — merchant clicked "I'll do this later"
    pixel_detected          — purchase pixel active (server-detected)
    first_visitor_detected  — first event from tracker arrived
    first_insight_generated — first opportunity signal created
    onboarding_complete     — all milestones reached

Interaction events (non-milestone, allow duplicates):
    welcome_dismissed       — welcome banner closed
    onboarding_dismissed    — tracking-active banner dismissed
    repair_triggered        — manual reconnect clicked
    setup_retry             — retry after fetch error
    upgrade_clicked         — Pro upgrade CTA clicked
    session_start           — new onboarding session started
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text, text

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OnboardingEvent(Base):
    __tablename__ = "onboarding_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default=text("now()"))

    shop_domain = Column(String, nullable=False)
    event_type = Column(String(64), nullable=False)

    # Seconds since the previous milestone for this shop (NULL for first event)
    elapsed_seconds = Column(Float, nullable=True)

    # Session counter — how many separate sessions this merchant has had
    session_number = Column(Integer, nullable=True)

    # Optional context (JSON string, ≤512 chars, for event-specific metadata)
    context = Column(String(512), nullable=True)

    __table_args__ = (
        Index("ix_onboarding_events_shop", "shop_domain"),
        Index("ix_onboarding_events_type", "event_type"),
        Index("ix_onboarding_events_shop_type", "shop_domain", "event_type"),
        Index("ix_onboarding_events_created", "created_at"),
    )
