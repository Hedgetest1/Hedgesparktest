"""email journey engine — journey states, email events, inbound emails

Revision ID: zzz2_email_journey_engine
Revises: zzz1_onboarding_funnel_events
Create Date: 2026-04-08
"""
from alembic import op
import sqlalchemy as sa

revision = "zzz2_email_journey_engine"
down_revision = "zzz1_onboarding_funnel_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- merchant_journey_states ---
    op.create_table(
        "merchant_journey_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("shop_domain", sa.String(), nullable=False, unique=True),
        # Invite stage
        sa.Column("beta_invite_sent_at", sa.DateTime(), nullable=True),
        sa.Column("beta_invite_resend_id", sa.String(128), nullable=True),
        sa.Column("beta_invite_opened_at", sa.DateTime(), nullable=True),
        sa.Column("beta_invite_clicked_at", sa.DateTime(), nullable=True),
        # Onboarding stage
        sa.Column("onboarding_started_at", sa.DateTime(), nullable=True),
        sa.Column("onboarding_completed_at", sa.DateTime(), nullable=True),
        # 48h follow-up
        sa.Column("followup_48h_sent_at", sa.DateTime(), nullable=True),
        sa.Column("followup_48h_variant", sa.String(64), nullable=True),
        sa.Column("followup_48h_resend_id", sa.String(128), nullable=True),
        sa.Column("followup_48h_opened_at", sa.DateTime(), nullable=True),
        sa.Column("followup_48h_clicked_at", sa.DateTime(), nullable=True),
        # Activation
        sa.Column("lite_activation_sent_at", sa.DateTime(), nullable=True),
        sa.Column("pro_activation_sent_at", sa.DateTime(), nullable=True),
        # Inbound
        sa.Column("inbound_reply_received_at", sa.DateTime(), nullable=True),
        # Email health (bounce/complaint suppression)
        sa.Column("email_suppressed", sa.String(32), nullable=True),
        sa.Column("email_suppressed_at", sa.DateTime(), nullable=True),
        # Derived
        sa.Column("current_stage", sa.String(32), nullable=False, server_default="new"),
        # Timestamps
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_journey_shop", "merchant_journey_states", ["shop_domain"])
    op.create_index("ix_journey_stage", "merchant_journey_states", ["current_stage"])
    op.create_index("ix_journey_invite_sent", "merchant_journey_states", ["beta_invite_sent_at"])
    # Composite index for followup eligibility query (prevents full table scan)
    op.create_index(
        "ix_journey_followup_eligible",
        "merchant_journey_states",
        ["beta_invite_sent_at", "followup_48h_sent_at"],
    )

    # --- email_events ---
    op.create_table(
        "email_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("resend_email_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("to_email", sa.String(), nullable=True),
        sa.Column("shop_domain", sa.String(), nullable=True),
        sa.Column("email_type", sa.String(64), nullable=True),
        sa.Column("event_timestamp", sa.DateTime(), nullable=True),
        sa.Column("resend_event_id", sa.String(128), nullable=True, unique=True),
        sa.Column("raw_payload", sa.Text(), nullable=True),
    )
    op.create_index("ix_email_events_resend_id", "email_events", ["resend_email_id"])
    op.create_index("ix_email_events_shop", "email_events", ["shop_domain"])
    op.create_index("ix_email_events_type", "email_events", ["event_type"])
    op.create_index("ix_email_events_created", "email_events", ["created_at"])

    # --- inbound_emails ---
    op.create_table(
        "inbound_emails",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("message_id", sa.String(256), nullable=True, unique=True),
        sa.Column("from_email", sa.String(), nullable=False),
        sa.Column("to_email", sa.String(), nullable=True),
        sa.Column("subject", sa.String(512), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("shop_domain", sa.String(), nullable=True),
        sa.Column("classification", sa.String(32), nullable=True),
        sa.Column("classification_confidence", sa.String(16), nullable=True),
        sa.Column("classification_method", sa.String(16), nullable=True),
        sa.Column("routing_action", sa.String(64), nullable=True),
        sa.Column("routing_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("routed_at", sa.DateTime(), nullable=True),
        sa.Column("agent_response_draft", sa.Text(), nullable=True),
        sa.Column("agent_response_sent_at", sa.DateTime(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_inbound_emails_shop", "inbound_emails", ["shop_domain"])
    op.create_index("ix_inbound_emails_classification", "inbound_emails", ["classification"])
    op.create_index("ix_inbound_emails_routing_status", "inbound_emails", ["routing_status"])
    op.create_index("ix_inbound_emails_created", "inbound_emails", ["created_at"])
    op.create_index("ix_inbound_emails_from", "inbound_emails", ["from_email"])

    # Add resend_id index to existing merchant_emails for event cross-referencing
    op.create_index("ix_merchant_emails_resend_id", "merchant_emails", ["resend_id"])

    # Scale-critical index: aggregation worker's hot query scans events by product + timestamp
    # Without this, DISTINCT + ORDER BY becomes O(n) at 10M+ events
    op.execute(sa.text(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_events_product_ts "
        "ON events (product_url, timestamp DESC) "
        "WHERE product_url IS NOT NULL"
    ))

    # Onboarding recovery: retry_count + next_retry_at columns on merchants table
    op.add_column("merchants", sa.Column("onboarding_retry_count", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("merchants", sa.Column("onboarding_next_retry_at", sa.DateTime(), nullable=True))

    # --- merchant_email_stats — self-improving email performance memory ---
    op.create_table(
        "merchant_email_stats",
        sa.Column("shop_domain", sa.String(), nullable=False),
        sa.Column("email_type", sa.String(64), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("opened_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicked_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("replied_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("complained_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_sent_at", sa.DateTime(), nullable=True),
        sa.Column("last_opened_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("shop_domain", "email_type"),
    )

    # Scale-critical: event retention cleanup needs (shop_domain, timestamp) composite
    op.execute(sa.text(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_events_shop_ts "
        "ON events (shop_domain, timestamp DESC)"
    ))

    # Scale-critical: shop_domain indexes on high-volume tables that lack them.
    # Without these, every tenant-scoped query does a full table scan.
    for table in [
        "events", "visitors", "products", "shop_orders",
        "visitor_purchase_sessions", "visitor_product_state",
        "active_nudges", "nudge_events", "nudge_impression_daily",
        "product_metrics", "product_opportunities",
        "daily_brief", "action_tasks",
    ]:
        idx_name = f"ix_{table}_shop_domain"
        op.execute(sa.text(
            f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} (shop_domain)"
        ))


def downgrade() -> None:
    for table in [
        "events", "visitors", "products", "shop_orders",
        "visitor_purchase_sessions", "visitor_product_state",
        "active_nudges", "nudge_events", "nudge_impression_daily",
        "product_metrics", "product_opportunities",
        "daily_brief", "action_tasks",
    ]:
        op.execute(sa.text(f"DROP INDEX IF EXISTS ix_{table}_shop_domain"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_events_shop_ts"))
    op.drop_table("merchant_email_stats")
    op.drop_column("merchants", "onboarding_next_retry_at")
    op.drop_column("merchants", "onboarding_retry_count")
    op.execute(sa.text("DROP INDEX IF EXISTS ix_events_product_ts"))
    op.drop_index("ix_merchant_emails_resend_id", table_name="merchant_emails")
    op.drop_table("inbound_emails")
    op.drop_table("email_events")
    op.drop_table("merchant_journey_states")
