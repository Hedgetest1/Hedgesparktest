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

    # ---------------------------------------------------------------------------
    # Session management
    #
    # session_version:  Monotonically increasing integer.  Included in JWT as "sv".
    #                   Bumping this value invalidates all existing session tokens
    #                   for this merchant without rotating the global signing secret.
    #                   Default 0 — matches tokens created before this field existed.
    # ---------------------------------------------------------------------------
    session_version = Column(Integer, nullable=False, default=0, server_default="0")

    # Shop owner email from Shopify's shop.json — used for digest emails.
    # Populated at install/reinstall time.  NULL for merchants installed
    # before this field was added (backfill via admin script if needed).
    contact_email = Column(String, nullable=True)

    # Per-merchant secret embedded in the Shopify Custom Pixel code.
    # Validated server-side on purchase events to prevent spoofed revenue injection.
    # Generated at install time.  NULL for merchants installed before this field.
    pixel_secret = Column(String(64), nullable=True)

    # ---------------------------------------------------------------------------
    # Klaviyo integration — per-merchant connection
    #
    # encrypted_klaviyo_key:       AES-256-GCM encrypted private key (enc:v1:...)
    #                              Uses same encryption as access_token via token_crypto.
    #                              NULL = no key saved.
    #
    # klaviyo_connection_status:   Structured state for AI inspection:
    #   "not_connected"  — no key saved (default)
    #   "connected"      — key saved and last verification passed
    #   "unverified"     — key saved but never verified / verification expired
    #   "invalid_key"    — last verification failed (auth error)
    #   "error"          — last verification failed (network/other)
    #
    # klaviyo_last_verified_at:    When the key was last successfully verified
    # klaviyo_last_error:          Sanitized error from last failed verification
    # klaviyo_last_sync_at:        When last execution sync was attempted
    # klaviyo_last_sync_error:     Sanitized error from last failed sync
    # ---------------------------------------------------------------------------
    encrypted_klaviyo_key      = Column(String,      nullable=True)
    klaviyo_connection_status  = Column(String(32),   nullable=False, default="not_connected", server_default="not_connected")
    klaviyo_last_verified_at   = Column(DateTime,     nullable=True)
    klaviyo_last_error         = Column(String(255),  nullable=True)
    klaviyo_last_sync_at       = Column(DateTime,     nullable=True)
    klaviyo_last_sync_error    = Column(String(255),  nullable=True)

    # ---------------------------------------------------------------------------
    # Automated onboarding state machine
    #
    # onboarding_status:
    #   "pending"     — newly installed, onboarding not started
    #   "configuring" — onboarding in progress
    #   "ready"       — fully operational (webhook + tracker confirmed)
    #   "failed"      — onboarding failed (see onboarding_error)
    #
    # onboarding_error: human/agent-readable error from last failure
    # ---------------------------------------------------------------------------
    onboarding_status = Column(String(32), nullable=False, default="pending", server_default="pending")
    onboarding_error  = Column(String(512), nullable=True)

    # ---------------------------------------------------------------------------
    # Tracker delivery method
    #
    # "script_tag"       — delivered via Shopify Script Tags API (current default)
    # "theme_extension"  — delivered via Shopify Theme App Extension (future)
    # "manual"           — manually injected by merchant (rare)
    #
    # Used for migration tracking from Script Tags → Theme App Extensions.
    # ---------------------------------------------------------------------------
    tracker_delivery_method = Column(String(32), nullable=False, default="script_tag", server_default="script_tag")

    # ---------------------------------------------------------------------------
    # GDPR consent tracking
    #
    # Set at OAuth install/reinstall time — records when the merchant consented
    # to data processing by installing the app from the Shopify App Store.
    # NULL for merchants installed before this field was added.
    # ---------------------------------------------------------------------------
    gdpr_consent_at = Column(DateTime, nullable=True)
