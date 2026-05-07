"""baseline 22-model drift consolidation

Revision ID: c4e5520c
Revises: zzzg_plan_lite_canonical
Create Date: 2026-05-07

Closes the bootstrap-drift class identified by stress-test #2 (recipe
migration-drift-resolver Step 0). All 22 tables already exist in prod
(legacy `Base.metadata.create_all` bootstrap); column-drift introspection
(Step 1.5) confirmed 0 db_only / 0 model_only across all 22 — schemas
match exactly. This migration is therefore IDEMPOTENT on prod (every
CREATE uses IF NOT EXISTS) and constructive on fresh deploys.

FK-chain ordering (Step 1.6): parent tables (Bucket A) created first;
FK-holder tables (Bucket C: agency_clients, community_template_clones,
outbound_webhook_deliveries, trust_execution_log, wishlist_items)
created last. Downgrade reverses the order with CASCADE for FK safety.

Tables (in upgrade order):
  Bucket A (17): agencies, community_templates, outbound_webhook_subscriptions,
    trust_contracts, visitors, products, ad_connections, ad_spend_daily,
    analytics_events, events, market_lookup, merchant_rules, price_intelligence,
    price_watch, product_opportunities, unique_product_detection,
    visitor_product_state.
  Bucket C (5): agency_clients, community_template_clones,
    outbound_webhook_deliveries, trust_execution_log, wishlist_items.
"""
from alembic import op
import sqlalchemy as sa  # noqa: F401  (kept for downstream migrations using op.execute + sa types)


revision = "c4e5520c"
down_revision = "zzzg_plan_lite_canonical"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE IF NOT EXISTS agencies (
	id SERIAL NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	contact_email VARCHAR NOT NULL, 
	brand_color VARCHAR(8), 
	logo_url VARCHAR(500), 
	custom_subdomain VARCHAR(120), 
	default_revshare_pct FLOAT DEFAULT '20.0' NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (custom_subdomain)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS community_templates (
	id SERIAL NOT NULL, 
	template_type VARCHAR(16) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	description VARCHAR(500), 
	author_shop VARCHAR NOT NULL, 
	author_label VARCHAR(120), 
	vertical VARCHAR(32) DEFAULT 'other' NOT NULL, 
	payload JSONB NOT NULL, 
	upvotes INTEGER DEFAULT '0' NOT NULL, 
	clone_count INTEGER DEFAULT '0' NOT NULL, 
	status VARCHAR(16) DEFAULT 'published' NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS outbound_webhook_subscriptions (
	id SERIAL NOT NULL, 
	shop_domain VARCHAR NOT NULL, 
	target_url VARCHAR(1024) NOT NULL, 
	secret VARCHAR(128) NOT NULL, 
	event_types JSONB DEFAULT '[]' NOT NULL, 
	status VARCHAR(16) DEFAULT 'active' NOT NULL, 
	last_success_at TIMESTAMP WITHOUT TIME ZONE, 
	last_failure_at TIMESTAMP WITHOUT TIME ZONE, 
	consecutive_failures INTEGER DEFAULT '0' NOT NULL, 
	auto_disabled BOOLEAN DEFAULT 'false' NOT NULL, 
	description VARCHAR(200), 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	created_by VARCHAR, 
	PRIMARY KEY (id)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS trust_contracts (
	id SERIAL NOT NULL, 
	shop_domain VARCHAR NOT NULL, 
	action_type VARCHAR NOT NULL, 
	max_per_day INTEGER DEFAULT '3' NOT NULL, 
	max_per_week INTEGER DEFAULT '10' NOT NULL, 
	discount_floor_pct FLOAT DEFAULT '-5.0' NOT NULL, 
	discount_ceiling_pct FLOAT DEFAULT '0.0' NOT NULL, 
	confidence_threshold FLOAT DEFAULT '0.80' NOT NULL, 
	auto_pause_on_drop_pct FLOAT DEFAULT '15.0' NOT NULL, 
	require_holdout BOOLEAN DEFAULT 'true' NOT NULL, 
	scope_type VARCHAR DEFAULT 'all' NOT NULL, 
	scope_values TEXT, 
	status VARCHAR DEFAULT 'active' NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	revoked_at TIMESTAMP WITHOUT TIME ZONE, 
	revoked_reason VARCHAR, 
	created_by VARCHAR, 
	note VARCHAR, 
	PRIMARY KEY (id)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS visitors (
	id SERIAL NOT NULL, 
	visitor_id VARCHAR NOT NULL, 
	email VARCHAR, 
	first_seen TIMESTAMP WITHOUT TIME ZONE, 
	last_seen TIMESTAMP WITHOUT TIME ZONE, 
	shop_domain VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_visitor_shop UNIQUE (visitor_id, shop_domain)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS products (
	id SERIAL NOT NULL, 
	shopify_product_id VARCHAR, 
	title VARCHAR, 
	price FLOAT, 
	currency VARCHAR, 
	product_url VARCHAR, 
	image_url VARCHAR, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	shop_domain VARCHAR NOT NULL, 
	PRIMARY KEY (id)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS ad_connections (
	id SERIAL NOT NULL, 
	shop_domain VARCHAR NOT NULL, 
	network VARCHAR(16) NOT NULL, 
	credential_ref VARCHAR(128), 
	account_id VARCHAR(128), 
	account_name VARCHAR(200), 
	status VARCHAR(16) DEFAULT 'connected' NOT NULL, 
	last_synced_at TIMESTAMP WITHOUT TIME ZONE, 
	last_error VARCHAR(500), 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_ad_conn_shop_network UNIQUE (shop_domain, network)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS ad_spend_daily (
	id SERIAL NOT NULL, 
	shop_domain VARCHAR NOT NULL, 
	date DATE NOT NULL, 
	network VARCHAR(16) NOT NULL, 
	campaign_id VARCHAR(64) NOT NULL, 
	campaign_name VARCHAR(200), 
	spend_eur NUMERIC(18, 2) DEFAULT '0' NOT NULL, 
	impressions INTEGER DEFAULT '0' NOT NULL, 
	clicks INTEGER DEFAULT '0' NOT NULL, 
	conversions INTEGER DEFAULT '0' NOT NULL, 
	revenue_attributed_eur NUMERIC(18, 2) DEFAULT '0' NOT NULL, 
	ingested_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_ad_spend_shop_date_net_camp UNIQUE (shop_domain, date, network, campaign_id)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS analytics_events (
	id BIGSERIAL NOT NULL, 
	ts_ms BIGINT NOT NULL, 
	event_name VARCHAR(64) NOT NULL, 
	shop_domain VARCHAR NOT NULL, 
	visitor_id VARCHAR, 
	session_id VARCHAR, 
	source VARCHAR(64), 
	campaign VARCHAR(256), 
	product_url VARCHAR(512), 
	revenue_eur NUMERIC(18, 2), 
	props JSONB, 
	PRIMARY KEY (id)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS events (
	id SERIAL NOT NULL, 
	visitor_id VARCHAR, 
	event_type VARCHAR, 
	url VARCHAR, 
	product_url VARCHAR, 
	timestamp BIGINT NOT NULL, 
	dwell_seconds INTEGER, 
	max_scroll_depth INTEGER, 
	shop_domain VARCHAR NOT NULL, 
	source_type VARCHAR, 
	referrer VARCHAR, 
	utm_medium VARCHAR(128), 
	utm_source VARCHAR(128), 
	utm_campaign VARCHAR(256), 
	utm_content VARCHAR(256), 
	utm_term VARCHAR(256), 
	click_id VARCHAR(256), 
	landing_page VARCHAR(512), 
	device_type VARCHAR(16), 
	product_id VARCHAR(64), 
	PRIMARY KEY (id)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS market_lookup (
	id SERIAL NOT NULL, 
	shop_domain VARCHAR NOT NULL, 
	product_url TEXT NOT NULL, 
	lookup_status TEXT, 
	comparable_presence TEXT, 
	uniqueness_hint TEXT, 
	lookup_confidence INTEGER, 
	market_summary TEXT, 
	recommended_next_step TEXT, 
	plan_required TEXT, 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_market_lookup_shop_product UNIQUE (shop_domain, product_url)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS merchant_rules (
	id SERIAL NOT NULL, 
	shop_domain VARCHAR NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	trigger_signal VARCHAR(64) NOT NULL, 
	conditions JSONB DEFAULT '[]' NOT NULL, 
	action JSONB NOT NULL, 
	status VARCHAR(16) DEFAULT 'draft' NOT NULL, 
	max_per_hour INTEGER DEFAULT '30' NOT NULL, 
	fired_count INTEGER DEFAULT '0' NOT NULL, 
	last_fired_at TIMESTAMP WITHOUT TIME ZONE, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	created_by VARCHAR, 
	PRIMARY KEY (id)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS price_intelligence (
	id SERIAL NOT NULL, 
	shop_domain VARCHAR NOT NULL, 
	product_url TEXT NOT NULL, 
	market_status TEXT, 
	price_position TEXT, 
	price_opportunity TEXT, 
	recommended_price_action TEXT, 
	intelligence_explanation TEXT, 
	confidence_score INTEGER, 
	plan_required TEXT, 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_price_intelligence_shop_product UNIQUE (shop_domain, product_url)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS price_watch (
	id SERIAL NOT NULL, 
	product_id VARCHAR, 
	product_name VARCHAR, 
	competitor_url VARCHAR, 
	last_seen_price NUMERIC(18, 2), 
	previous_price NUMERIC(18, 2), 
	price_drop_detected INTEGER, 
	last_checked TIMESTAMP WITHOUT TIME ZONE, 
	shop_domain VARCHAR NOT NULL, 
	PRIMARY KEY (id)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS product_opportunities (
	id SERIAL NOT NULL, 
	shop_domain VARCHAR NOT NULL, 
	product_url TEXT NOT NULL, 
	records INTEGER, 
	avg_intent_score FLOAT, 
	hot_count INTEGER, 
	wishlist_count INTEGER, 
	avg_dwell_seconds FLOAT, 
	avg_scroll_depth FLOAT, 
	opportunity_type TEXT, 
	priority_score INTEGER, 
	recommended_action TEXT, 
	opportunity_explanation TEXT, 
	plan_required TEXT, 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_product_opportunities_shop_product UNIQUE (shop_domain, product_url)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS unique_product_detection (
	id SERIAL NOT NULL, 
	shop_domain VARCHAR NOT NULL, 
	product_url TEXT NOT NULL, 
	uniqueness_status TEXT, 
	uniqueness_score INTEGER, 
	evidence_summary TEXT, 
	recommended_strategy TEXT, 
	plan_required TEXT, 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_unique_product_detection_shop_product UNIQUE (shop_domain, product_url)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS visitor_product_state (
	id SERIAL NOT NULL, 
	visitor_id TEXT, 
	product_url TEXT, 
	total_views INTEGER, 
	total_dwell_seconds INTEGER, 
	max_scroll_depth INTEGER, 
	wishlist_added BOOLEAN, 
	first_seen TIMESTAMP WITHOUT TIME ZONE, 
	last_seen TIMESTAMP WITHOUT TIME ZONE, 
	intent_score INTEGER, 
	intent_level TEXT, 
	recommended_action TEXT, 
	intent_explanation TEXT, 
	shop_domain VARCHAR NOT NULL, 
	PRIMARY KEY (id)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS agency_clients (
	id SERIAL NOT NULL, 
	agency_id INTEGER NOT NULL, 
	shop_domain VARCHAR NOT NULL, 
	nickname VARCHAR(200), 
	revshare_pct FLOAT DEFAULT '20.0' NOT NULL, 
	status VARCHAR(16) DEFAULT 'active' NOT NULL, 
	onboarded_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_agency_client_agency_shop UNIQUE (agency_id, shop_domain), 
	FOREIGN KEY(agency_id) REFERENCES agencies (id)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS community_template_clones (
	id SERIAL NOT NULL, 
	template_id INTEGER NOT NULL, 
	shop_domain VARCHAR NOT NULL, 
	cloned_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_community_clones_template_shop UNIQUE (template_id, shop_domain), 
	FOREIGN KEY(template_id) REFERENCES community_templates (id)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS outbound_webhook_deliveries (
	id SERIAL NOT NULL, 
	subscription_id INTEGER NOT NULL, 
	shop_domain VARCHAR NOT NULL, 
	event_type VARCHAR(64) NOT NULL, 
	event_id VARCHAR(64) NOT NULL, 
	payload JSONB NOT NULL, 
	status VARCHAR(16) DEFAULT 'pending' NOT NULL, 
	attempts INTEGER DEFAULT '0' NOT NULL, 
	response_status INTEGER, 
	response_body TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	last_attempted_at TIMESTAMP WITHOUT TIME ZONE, 
	delivered_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(subscription_id) REFERENCES outbound_webhook_subscriptions (id)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS trust_execution_log (
	id SERIAL NOT NULL, 
	contract_id INTEGER NOT NULL, 
	shop_domain VARCHAR NOT NULL, 
	action_type VARCHAR NOT NULL, 
	target_url VARCHAR, 
	executed_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	confidence FLOAT, 
	discount_pct_applied FLOAT, 
	holdout_pct_applied INTEGER, 
	params_json TEXT, 
	outcome VARCHAR, 
	revenue_delta_eur NUMERIC(18, 2), 
	measured_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id)
)
""")
    op.execute("""
CREATE TABLE IF NOT EXISTS wishlist_items (
	id SERIAL NOT NULL, 
	visitor_id INTEGER, 
	product_id INTEGER, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	shop_domain VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(visitor_id) REFERENCES visitors (id), 
	FOREIGN KEY(product_id) REFERENCES products (id)
)
""")


def downgrade():
    op.execute("DROP TABLE IF EXISTS wishlist_items CASCADE")
    op.execute("DROP TABLE IF EXISTS trust_execution_log CASCADE")
    op.execute("DROP TABLE IF EXISTS outbound_webhook_deliveries CASCADE")
    op.execute("DROP TABLE IF EXISTS community_template_clones CASCADE")
    op.execute("DROP TABLE IF EXISTS agency_clients CASCADE")
    op.execute("DROP TABLE IF EXISTS visitor_product_state CASCADE")
    op.execute("DROP TABLE IF EXISTS unique_product_detection CASCADE")
    op.execute("DROP TABLE IF EXISTS product_opportunities CASCADE")
    op.execute("DROP TABLE IF EXISTS price_watch CASCADE")
    op.execute("DROP TABLE IF EXISTS price_intelligence CASCADE")
    op.execute("DROP TABLE IF EXISTS merchant_rules CASCADE")
    op.execute("DROP TABLE IF EXISTS market_lookup CASCADE")
    op.execute("DROP TABLE IF EXISTS events CASCADE")
    op.execute("DROP TABLE IF EXISTS analytics_events CASCADE")
    op.execute("DROP TABLE IF EXISTS ad_spend_daily CASCADE")
    op.execute("DROP TABLE IF EXISTS ad_connections CASCADE")
    op.execute("DROP TABLE IF EXISTS products CASCADE")
    op.execute("DROP TABLE IF EXISTS visitors CASCADE")
    op.execute("DROP TABLE IF EXISTS trust_contracts CASCADE")
    op.execute("DROP TABLE IF EXISTS outbound_webhook_subscriptions CASCADE")
    op.execute("DROP TABLE IF EXISTS community_templates CASCADE")
    op.execute("DROP TABLE IF EXISTS agencies CASCADE")
