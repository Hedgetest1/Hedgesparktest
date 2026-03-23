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
    # MERCHANT_TOKEN_ENCRYPTION_KEY is configured.  Legacy plaintext values
    # (rows written before encryption was added) are transparently handled
    # by token_crypto.decrypt_token().
    access_token   = Column(String,   nullable=True)

    plan           = Column(String,   nullable=False, default="starter")
    installed_at   = Column(DateTime, default=_now_utc, nullable=False)
    billing_active = Column(Boolean,  default=False,   nullable=False)

    # ---------------------------------------------------------------------------
    # Install-time auto-registration tracking
    # Populated during the OAuth callback by _register_webhook() and
    # _register_script_tag() in shopify_oauth.py.
    # Null = not yet attempted / failed on last attempt.
    # ---------------------------------------------------------------------------

    # Shopify webhook ID for the orders/paid webhook we registered.
    # Stored as string (Shopify IDs can exceed int32 range).
    webhook_id             = Column(String,   nullable=True)
    webhook_registered_at  = Column(DateTime, nullable=True)

    # Shopify script tag ID for spark-tracker.js injection.
    script_tag_id          = Column(String,   nullable=True)
    script_tag_installed_at = Column(DateTime, nullable=True)
