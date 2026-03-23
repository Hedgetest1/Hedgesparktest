from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from app.core.database import Base


def _now_utc():
    """Timezone-aware UTC now, stored as naive datetime for DB compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Merchant(Base):
    __tablename__ = "merchants"

    id             = Column(Integer,  primary_key=True, autoincrement=True)
    shop_domain    = Column(String,   unique=True, nullable=False, index=True)

    # Shopify OAuth access token — stored encrypted (enc:v1:...) when
    # MERCHANT_TOKEN_ENCRYPTION_KEY is configured.  Nullified on uninstall.
    access_token   = Column(String,   nullable=True)

    plan           = Column(String,   nullable=False, default="starter")
    installed_at   = Column(DateTime, default=_now_utc, nullable=False)
    billing_active = Column(Boolean,  default=False,   nullable=False)

    # ---------------------------------------------------------------------------
    # Install lifecycle
    # "active"      — app is installed and operational
    # "uninstalled" — merchant removed the app; access_token is nullified
    # ---------------------------------------------------------------------------
    install_status  = Column(String,   nullable=False, default="active")
    uninstalled_at  = Column(DateTime, nullable=True)

    # ---------------------------------------------------------------------------
    # Install-time auto-registration tracking (Phase 1-2 of onboarding pass)
    # ---------------------------------------------------------------------------
    webhook_id              = Column(String,   nullable=True)
    webhook_registered_at   = Column(DateTime, nullable=True)
    script_tag_id           = Column(String,   nullable=True)
    script_tag_installed_at = Column(DateTime, nullable=True)

    # ---------------------------------------------------------------------------
    # Billing — Shopify RecurringApplicationCharge
    #
    # billing_charge_id:    Shopify's charge ID (stored as string; may exceed int32).
    #                       Set when a subscribe request is initiated (status pending).
    #                       Cleared if merchant declines.
    # billing_confirmed_at: When the charge was accepted AND activated.
    # plan:                 Updated to "pro" when billing is activated.
    # billing_active:       Set to True on activation, False on cancellation/uninstall.
    # ---------------------------------------------------------------------------
    billing_charge_id    = Column(String,   nullable=True)
    billing_confirmed_at = Column(DateTime, nullable=True)
