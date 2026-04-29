from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB

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

    plan           = Column(String,   nullable=False, default="starter",   server_default="starter")
    installed_at   = Column(DateTime, default=_now_utc, nullable=False, server_default="now()")
    billing_active = Column(Boolean,  default=False,   nullable=False,    server_default="false")

    # ---------------------------------------------------------------------------
    # Install lifecycle
    # "active"      — app is installed and operational
    # "uninstalled" — merchant removed the app; access_token is nullified
    # ---------------------------------------------------------------------------
    install_status  = Column(String,   nullable=False, default="active",  server_default="active")
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
    # Slack integration (Strada 3.5, 2026-04-20)
    # slack_webhook_encrypted:  AES-256-GCM encrypted Slack incoming webhook URL.
    #                           Encrypted because the URL itself is the secret —
    #                           anyone holding it can post to the merchant's channel.
    # slack_status:             'connected' | 'error' | 'not_connected'
    # slack_last_error:         Sanitized error from last failed post.
    # ---------------------------------------------------------------------------
    slack_webhook_encrypted    = Column(String,       nullable=True)
    slack_status               = Column(String(32),   nullable=False, default="not_connected", server_default="not_connected")
    slack_last_error           = Column(String(255),  nullable=True)

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

    # Onboarding retry backoff — prevents infinite 15-min retry loops
    # retry_count: how many times onboarding has been attempted
    # next_retry_at: earliest time next retry is allowed (exponential backoff)
    onboarding_retry_count  = Column(Integer, nullable=True, default=0, server_default="0")
    onboarding_next_retry_at = Column(DateTime, nullable=True)

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

    # ---------------------------------------------------------------------------
    # Synthetic merchant flag — simulation isolation
    #
    # True = this merchant was created by the simulation engine for operational
    # hardening. Synthetic merchants are permanently excluded from:
    #   - real_merchant evidence classification
    #   - production reinforcement weights
    #   - strategic learning / monthly Opus context
    #   - merchant-facing business claims
    #
    # Synthetic merchants ARE included in:
    #   - technical diagnostics and pipeline exercise
    #   - patch engine hardening
    #   - failure taxonomy
    #   - operator visibility dashboards
    #
    # Once set to True, this flag must NEVER be changed to False.
    # ---------------------------------------------------------------------------
    is_synthetic = Column(Boolean, nullable=False, default=False, server_default="false")

    # ---------------------------------------------------------------------------
    # Shop locale from Shopify shop.json — used for currency-aware revenue
    # aggregation and timezone-correct daily breakdowns.
    # Populated at install/reinstall time from Shopify API.
    # NULL for merchants installed before this field was added (treated as
    # primary_currency="EUR", iana_timezone="UTC").
    # ---------------------------------------------------------------------------
    primary_currency = Column(String(8), nullable=True)
    iana_timezone    = Column(String(64), nullable=True)

    # Merchant-controlled communication pause — stops all non-critical emails
    # when True. Checked by email_orchestrator before any send.
    email_paused = Column(Boolean, nullable=False, default=False, server_default="false")

    # ---------------------------------------------------------------------------
    # Post-purchase survey config (Gap #7 of brutal $0-70 audit, 2026-04-28)
    #
    # The Shopify Checkout UI Extension fetches /survey/config?shop=<domain>
    # at render time; that endpoint reads these columns. Lite tier sees the
    # defaults; Pro tier can edit via /pro/settings/surveys.
    # ---------------------------------------------------------------------------
    survey_question = Column(
        String(160),
        nullable=False,
        default="How did you hear about us?",
        server_default=text("'How did you hear about us?'"),
    )
    survey_options = Column(
        JSONB,
        nullable=False,
        default=lambda: [
            {"label": "Instagram", "value": "instagram"},
            {"label": "TikTok", "value": "tiktok"},
            {"label": "Google", "value": "google"},
            {"label": "Friend", "value": "friend"},
            {"label": "Email", "value": "email"},
        ],
        server_default=text(
            "'["
            "{\"label\":\"Instagram\",\"value\":\"instagram\"},"
            "{\"label\":\"TikTok\",\"value\":\"tiktok\"},"
            "{\"label\":\"Google\",\"value\":\"google\"},"
            "{\"label\":\"Friend\",\"value\":\"friend\"},"
            "{\"label\":\"Email\",\"value\":\"email\"}"
            "]'::jsonb"
        ),
    )
    survey_allow_other = Column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    survey_show_on_order_status = Column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # G4 Lite parity (2026-04-29) — Google Sheets OAuth (drive.file scope).
    # encrypted_google_refresh_token stores the long-lived refresh_token
    # via existing token_crypto (AES-256-GCM, enc:v1: prefix). access_token
    # is short-lived (~1h) and refreshed in-memory on each Sheets API call.
    # NULL = merchant has not connected Google Sheets.
    # Text (vs String) because the encrypted+base64url payload can run
    # past 256 chars; matches the migration's TEXT type for alembic check.
    encrypted_google_refresh_token = Column(Text, nullable=True)
    # Display-only — the Google account the merchant authorized.
    google_oauth_email = Column(String(255), nullable=True)
    # When the OAuth handshake completed — used for staleness + audit log.
    google_oauth_connected_at = Column(DateTime, nullable=True)

    # G3 Lite parity (2026-04-29) — multi-question survey support.
    # When NULL, the legacy single-question fields above apply unchanged
    # (backward compatibility). When set, this is the canonical question
    # list — the survey config endpoint returns this array; the legacy
    # single-question fields are ignored.
    #
    # Shape per element:
    #   {
    #     "question_key": str,            # <=64 chars, unique per merchant
    #     "question": str,                # <=160 chars
    #     "type": "single_choice"|"multi_choice"|"text"|"nps",
    #     "options": [{"label": str, "value": str}, ...],  # required for *_choice
    #     "allow_other": bool,
    #     "position": int,                # 0-based ordering
    #   }
    survey_questions = Column(JSONB, nullable=True)

    # Per-shop override for the inventory reorder lead-time (Gap #4,
    # 2026-04-28). NULL → use the 14-day industry-median default.
    # Future Settings page (`/app/settings/inventory`) lets the merchant
    # tune this. Read by the days-of-cover / reorder-hint logic.
    inventory_lead_time_days = Column(Integer, nullable=True)
