import logging
import os
from contextlib import asynccontextmanager

from app.core.logging_config import configure_logging
configure_logging()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import Response as StarletteResponse
from app.core.rate_limit import RateLimitMiddleware
from app.core.request_id import RequestIDMiddleware

# ---------------------------------------------------------------------------
# Sentry error tracking — optional, graceful fallback when not installed
#
# Set SENTRY_DSN in backend/.env to enable.  When absent the server runs
# normally without any error tracking.  When present all unhandled exceptions
# are captured with full stack traces and shop_domain context attached.
#
# Install: pip install sentry-sdk[fastapi]
# ---------------------------------------------------------------------------
_sentry_enabled = False
try:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    _SENTRY_DSN = os.getenv("SENTRY_DSN", "")
    _SENTRY_ENV = os.getenv("SENTRY_ENVIRONMENT", "production")
    _SENTRY_RATE = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05"))

    if _SENTRY_DSN:
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            environment=_SENTRY_ENV,
            traces_sample_rate=_SENTRY_RATE,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                SqlalchemyIntegration(),
            ],
            # Don't send PII — visitor_id is pseudonymous but we play it safe
            send_default_pii=False,
        )
        _sentry_enabled = True
except ImportError:
    pass  # sentry-sdk not installed — no-op

from app.api.decision_engine import router as decision_engine_router
from app.api.market_lookup import router as market_lookup_router
from app.core.database import engine
from app.core.database import Base
from app.api.opportunities import router as opportunities_router
from app.api.conversion_probability import router as conversion_probability_router
from app.models.visitor import Visitor
from app.models.product_opportunity import ProductOpportunity
from app.models.product import Product
from app.models.wishlist_item import WishlistItem
from app.models.event import Event
from app.models.visitor_product_state import VisitorProductState
from app.api.dashboard import router as dashboard_router
from app.api.events import router as events_router
from app.api.intent import router as intent_router
from app.api.track import router as track_router
from app.models.price_intelligence import PriceIntelligence
from app.models.market_lookup import MarketLookup
from app.api.price_intelligence import router as price_intelligence_router
from app.api.revenue_radar import router as revenue_radar_router
from app.models.price_watch import PriceWatch
from app.models.opportunity_signal import OpportunitySignal
from app.models.product_metrics import ProductMetrics
from app.models.worker_state import WorkerState
from app.models.worker_log import WorkerLog
from app.models.daily_brief import DailyBrief
from app.api.agent import router as agent_router
from app.api.brief import router as brief_router
from app.api.merchant import router as merchant_router
from app.api.merchant_export import router as merchant_export_router
from app.api.merchant_privacy import router as merchant_privacy_router
from app.api.tracker import router as tracker_router
from app.api.live_visitors import router as live_visitors_router
from app.api.top_pages import router as top_pages_router
from app.api.live_opportunities import router as live_opportunities_router
from app.api.visitor_scores import router as visitor_scores_router
from app.api.live_alerts import router as live_alerts_router
from app.api.ai_actions import router as ai_actions_router
from app.api.weekly_trend import router as weekly_trend_router
# auth.py had its own /auth/callback with broken HMAC verification
# (hardcoded 3 params instead of reading the full query string).
# shopify_oauth.py is the canonical OAuth implementation.
from app.api.product_metrics import router as product_metrics_router
from app.api.store_intelligence import router as store_intelligence_router
from app.api.execution_actions import router as execution_actions_router
from app.api.product_trend import router as product_trend_router
from app.api.session_replay import router as session_replay_router
from app.api.funnel import router as funnel_router
from app.api.click_insights import router as click_insights_router
from app.api.source_quality import router as source_quality_router
from app.api.actions import router as actions_router
from app.api.action_tasks import router as action_tasks_router
from app.api.webhooks import router as webhooks_router
from app.api.shopify_refunds import router as shopify_refunds_router
from app.api.shopify_flow_schema import router as shopify_flow_schema_router
from app.api.track_purchase import router as track_purchase_router
from app.api.segments import router as segments_router
from app.models.shop_order import ShopOrder  # noqa: F401 — ensures table is created
from app.models.visitor_purchase_session import VisitorPurchaseSession  # noqa: F401 — ensures table is created
from app.models.shop_conversion_calibration import ShopConversionCalibration  # noqa: F401 — ensures table is created
from app.models.active_nudge import ActiveNudge  # noqa: F401 — ensures table is created
from app.models.nudge_event import NudgeEvent              # noqa: F401 — ensures table is created
from app.models.nudge_impression_daily import NudgeImpressionDaily  # noqa: F401 — ensures table is created
from app.models.action_snapshot import ActionSnapshot  # noqa: F401 — ensures table is created
from app.models.audit_log import AuditLog             # noqa: F401 — ensures table is created
from app.models.ops_alert import OpsAlert             # noqa: F401 — ensures table is created
from app.models.action_outcome import ActionOutcome   # noqa: F401 — ensures table is created
from app.models.action_approval import ActionApproval # noqa: F401 — ensures table is created
from app.models.autofix_promotion import AutoFixPromotion # noqa: F401 — ensures table is created
from app.models.merge_outcome import MergeOutcome       # noqa: F401 — ensures table is created
from app.models.evolution_proposal import EvolutionProposal # noqa: F401 — ensures table is created
from app.models.model_upgrade import ModelUpgradeProposal  # noqa: F401 — ensures table is created
from app.models.active_model_config import ActiveModelConfig  # noqa: F401 — ensures table is created
from app.models.support_incident import SupportIncident       # noqa: F401 — ensures table is created
from app.models.meta_review import MetaReview                 # noqa: F401 — ensures table is created
from app.models.system_snapshot import SystemSnapshot         # noqa: F401 — ensures table is created
from app.models.scaling_recommendation import ScalingRecommendation  # noqa: F401 — ensures table is created
from app.models.bugfix_candidate import BugFixCandidate # noqa: F401 — ensures table is created
from app.models.gdpr_request import GdprRequest                   # noqa: F401 — ensures table is created
from app.models.merchant import Merchant                           # noqa: F401 — ensures table is created
from app.models.action_task import ActionTask                      # noqa: F401 — ensures table is created
from app.models.store_metrics import StoreMetrics                  # noqa: F401 — ensures table is created
from app.models.unique_product_detection import UniqueProductDetection  # noqa: F401 — ensures table is created
from app.models.execution import (                                 # noqa: F401 — ensures execution tables created
    ExecutionOpportunity, ExecutionAudience, ExecutionTracking, ExecutionBaseline,
)
from app.api.nudges import router as nudges_router
from app.api.nudge_script import router as nudge_script_router
from app.api.nudge_events import router as nudge_events_router
from app.api.shopify_admin_api import router as shopify_admin_router
from app.api.klaviyo import router as klaviyo_router
from app.api.attribution import router as attribution_router
from app.api.lift import router as lift_router
from app.api.proof_report import router as proof_report_router
from app.api.public_proofs import router as public_proofs_router
from app.api.heatmap import router as heatmap_router
from app.api.cohorts import router as cohorts_router
from app.api.shopify_oauth import router as shopify_oauth_router
from app.api.billing import router as billing_router
from app.api.setup import router as setup_router
from app.api.onboarding import router as onboarding_router
from app.api.ops import router as ops_router
from app.api.frontend_errors import router as frontend_errors_router
from app.api.benchmarks import router as benchmarks_router
from app.api.benchmarks_vertical import router as benchmarks_vertical_router
from app.api.knowledge_graph import router as knowledge_graph_router
from app.api.outbound_webhooks import router as outbound_webhooks_router
from app.api.ads import router as ads_router
from app.api.anomaly_fusion import router as anomaly_fusion_router
from app.api.causal_explainer import router as causal_explainer_router
from app.api.public_status import router as public_status_router
from app.api.merchant_groups import router as merchant_groups_router
from app.api.agency import router as agency_router
from app.api.storefront_preview import router as storefront_preview_router
from app.api.tracker_error import router as tracker_error_router
from app.api.community_marketplace import router as community_marketplace_router
from app.api.realtime_stream import router as realtime_stream_router
from app.api.night_shift import router as night_shift_router
from app.api.public_roi_counter import router as public_roi_counter_router
from app.api.feature_flags_admin import router as feature_flags_admin_router
from app.api.slo_api import router as slo_api_router
from app.api.auth_posture import router as auth_posture_router
from app.api.feature_usage_api import router as feature_usage_router
from app.api.anomaly_replay import router as anomaly_replay_router
from app.api.counterfactual import router as counterfactual_router
from app.api.playbook import router as playbook_router
from app.models.community_template import CommunityTemplate, CommunityTemplateClone  # noqa: F401
from app.models.night_shift_report import NightShiftReport as NightShiftReportModel  # noqa: F401
from app.models.merchant_group import MerchantGroup, MerchantGroupMember  # noqa: F401
from app.models.agency import Agency, AgencyClient  # noqa: F401
from app.models.outbound_webhook import OutboundWebhookSubscription, OutboundWebhookDelivery  # noqa: F401
from app.models.ad_spend import AdSpendDaily, AdConnection  # noqa: F401
from app.api.refund_loss import router as refund_loss_router
from app.api.revenue_autopsy import router as revenue_autopsy_router
from app.api.abandoned_intent import router as abandoned_intent_router
from app.api.price_sensitivity import router as price_sensitivity_router
from app.api.causal_lift import router as causal_lift_router
from app.api.merchant_churn import router as merchant_churn_router
from app.api.revenue_genome import router as revenue_genome_router
from app.api.goals import router as goals_router
from app.api.revenue_at_risk import router as rars_router
from app.api.risk_forecast import router as risk_forecast_router
from app.api.annotations import router as annotations_router
from app.api.segment_compare import router as segment_compare_router
from app.api.roi_report import router as roi_report_router
from app.api.signal_webhooks import router as signal_webhooks_router
from app.api.team import router as team_router
from app.api.health import router as health_router
from app.api.orders import router as orders_router
from app.api.pnl import router as pnl_router
from app.api.cost_config import router as cost_config_router
from app.api.trust_contracts import router as trust_contracts_router
from app.api.roi_hero import router as roi_hero_router
from app.api.instant_intelligence import router as instant_intelligence_router
from app.api.daily_narrative import router as daily_narrative_router
from app.api.cac_ltv import router as cac_ltv_router
from app.api.mta import router as mta_router
from app.api.margin_guard_api import router as margin_guard_router
from app.api.visitor_journeys import router as visitor_journeys_router
from app.api.forecasts import router as forecasts_router
from app.api.compliance_evidence import router as compliance_evidence_router
from app.api.merchant_rules import router as merchant_rules_router
from app.api.public_events import router as public_events_router
from app.api.customer_churn import router as customer_churn_router
from app.api.nudge_dna import router as nudge_dna_router
from app.api.integrations import router as integrations_router
from app.api.telegram_webhook import router as telegram_webhook_router
from app.api.chat_support import router as chat_support_router
from app.api.resend_webhooks import router as resend_webhooks_router
from app.api.sentry_webhooks import router as sentry_webhooks_router
from app.api.legal_pages import router as legal_pages_router
from app.api.consent_banner import router as consent_banner_router

_startup_log = logging.getLogger("wishspark.startup")
_middleware_log = logging.getLogger("wishspark.middleware")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager — replaces the deprecated
    `@app.on_event("startup")` / `@app.on_event("shutdown")` decorators.

    Startup hooks run in order before any request is served. The actual
    hook implementations live lower in this file (see `_startup_env_audit`
    and `_startup_telegram_warmup`) — Python resolves these globals at
    call time, not at module load, so the forward references work.

    No shutdown work is currently needed; the `yield` marks the handoff
    to the running server and everything after it would run on shutdown.
    """
    _startup_env_audit()
    _startup_telegram_warmup()
    yield


app = FastAPI(
    title="HedgeSpark API",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

# CORS — must allow:
#   1. https://app.hedgesparkhq.com       — dashboard (subdomain)
#   2. https://hedgesparkhq.com           — dashboard (root domain, same Next.js app)
#   3. https://admin.shopify.com           — Shopify new admin (embedded app iframe)
#   4. https://*.myshopify.com             — Shopify classic admin (embedded app iframe)
# allow_credentials=True is required because the dashboard sends the
# hs_session httpOnly cookie with every fetch (credentials: "include").
#
# CORS hardening (2026-04-11 audit):
#   * `allow_credentials=True` + wildcards is an anti-pattern. Explicit
#     allowlists force rejection of any header/method that wasn't
#     declared here, which is the whole point of CORS.
#   * The dashboard only sends `Content-Type: application/json`; session
#     state rides on the httpOnly `hs_session` cookie. No X-API-Key,
#     no custom auth headers. `Authorization` is kept on the list only
#     for future Shopify embed-app signed JWT flows.
#   * Methods are narrowed to the verbs actually used by the app.
#   * `expose_headers=[]` — the dashboard never reads response headers
#     (grep-confirmed), so nothing needs to be exposed to JS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://app.hedgesparkhq.com",
        "https://hedgesparkhq.com",
        "https://admin.shopify.com",
    ],
    allow_origin_regex=r"https://[a-zA-Z0-9\-]+\.myshopify\.com$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=[],
)

# Rate limiting — sliding window, in-process, zero dependencies.
# Applied before routing; 429 returned immediately on breach.
# /track and /nudge/event are fire-and-forget from storefronts — 60/min per IP
# is generous for legitimate traffic but stops obvious abuse and cost amplification.
app.add_middleware(
    RateLimitMiddleware,
    rules={
        ("POST", "/track"):                        (600, 60),  # 600 req / 60 s  (storefront traffic)
        ("POST", "/track/batch"):                  (120, 60),  # 120 req / 60 s  (batched events)
        ("POST", "/nudge/event"):                  (600, 60),  # 600 req / 60 s  (storefront traffic)
        ("POST", "/webhooks/shopify/orders"):          (20, 60),   # 20 req / 60 s
        ("POST", "/webhooks/shopify/orders-created"):  (20, 60),   # compat alias
        ("POST", "/webhooks/shopify/orders-paid"):     (20, 60),   # compat alias
        ("POST", "/pro/nudges"):                       (10, 60),   # 10 req / 60 s  (AI cost guard)
        ("POST", "/chat/support"):                     (30, 3600), # 30 req / 3600 s (merchant chat: 30/hour)
    },
)

# ---------------------------------------------------------------------------
# Storefront/pixel CORS preflight
#
# Shopify Custom Pixels and storefront scripts send cross-origin POST to
# /track and /track/batch with Content-Type: application/json, which triggers
# a browser preflight OPTIONS request.
#
# CORSMiddleware only allows app.hedgesparkhq.com (dashboard).  Storefront
# and pixel origins are unpredictable — we cannot enumerate them.  This
# middleware runs BEFORE CORSMiddleware (outermost = last added) and returns
# 204 with Access-Control-Allow-Origin: * for OPTIONS on /track paths.
#
# Safe: these endpoints accept no credentials (cookies/auth headers) and
# return no sensitive data.  The wildcard origin applies to preflight only.
# POST responses also carry these headers (set in track.py route handlers).
# ---------------------------------------------------------------------------
_TRACK_PREFLIGHT_PATHS = frozenset({"/track", "/track/batch"})
_TRACK_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
}


@app.middleware("http")
async def track_preflight_middleware(request: Request, call_next):
    if request.method == "OPTIONS" and request.url.path in _TRACK_PREFLIGHT_PATHS:
        return StarletteResponse(status_code=204, headers=_TRACK_CORS_HEADERS)
    return await call_next(request)


# ---------------------------------------------------------------------------
# CSRF protection for session-authenticated endpoints
#
# Cross-site form submissions cannot set custom headers. The dashboard's
# fetch() calls always include Content-Type: application/json (which is a
# custom header in CORS terms). This middleware rejects POST/PATCH/DELETE
# requests to session-authenticated paths that lack a non-simple Content-Type
# header — effectively blocking cross-site form POSTs while allowing the
# dashboard and API clients through.
#
# Excluded: /track, /track/batch, /nudge/event, /webhooks, /auth — these
# are either public/storefront endpoints or have their own auth (HMAC).
# ---------------------------------------------------------------------------
_CSRF_EXEMPT_PREFIXES = ("/track", "/webhooks", "/auth", "/nudge/event")


@app.middleware("http")
async def csrf_guard_middleware(request: Request, call_next):
    if request.method in ("POST", "PATCH", "DELETE"):
        path = request.url.path
        if not any(path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES):
            ct = (request.headers.get("content-type") or "").lower()
            if "application/json" not in ct and "multipart/form-data" not in ct:
                return JSONResponse(
                    {"detail": "Invalid content type for this endpoint."},
                    status_code=415,
                )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Security headers — applied to every response.
#
# Backend API is almost entirely JSON. The only HTML response path is the
# OAuth callback redirect (handled by Starlette's RedirectResponse).
# For JSON endpoints we set a strict "deny everything" CSP because any
# content executing in the context of an API response is an exploit.
# For the OAuth callback redirect we use a looser CSP that allows the
# Shopify admin origin to receive the redirect.
#
# X-Frame-Options: DENY everywhere — the API must never be framed.
# The dashboard (separate Next.js app) sets its own frame-ancestors.
#
# The `_TRACKER_HEADERS_EXEMPT` set is only for storefront tracker paths,
# which run on merchant domains; they need CORS and DO NOT need HSTS
# (their origin may not be HTTPS on dev shops).
# ---------------------------------------------------------------------------
_TRACKER_HEADERS_EXEMPT = frozenset({"/track", "/track/batch", "/nudge/event"})

# Strict CSP for JSON API responses — any rendered content is a sign
# of compromise. `default-src 'none'` blocks every content type, and
# `frame-ancestors 'none'` is defense-in-depth on top of X-Frame-Options.
_STRICT_API_CSP = (
    "default-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)


@app.middleware("http")
async def dashboard_rate_limit_middleware(request: Request, call_next):
    """Rate limit /pro/ and /merchant/ endpoints: 120 req/min per (shop, IP)."""
    path = request.url.path
    if path.startswith(("/pro/", "/merchant/")):
        try:
            from app.core.redis_client import _client
            rc = _client()
            if rc is not None:
                from app.core.merchant_session import SESSION_COOKIE_NAME
                shop = request.cookies.get(SESSION_COOKIE_NAME, "")[:64]
                ip = request.client.host if request.client else "anon"
                key = f"hs:rl:dash:{shop}:{ip}"
                count = rc.incr(key)
                if count == 1:
                    rc.expire(key, 60)
                if count > 120:
                    from fastapi.responses import JSONResponse
                    return JSONResponse({"detail": "Too many requests."}, status_code=429)
        except Exception as exc:
            _middleware_log.warning("dashboard_rate_limit: fail-open: %s", exc)
    return await call_next(request)


@app.middleware("http")
async def slo_timing_middleware(request: Request, call_next):
    """Record per-route timing into the SLO observability layer.

    Runs unconditionally. Never raises into the request path — any
    internal failure logs and continues.
    """
    import time as _time
    started = _time.monotonic()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        # Still record the timing for a thrown exception (status 500)
        dur_ms = (_time.monotonic() - started) * 1000
        try:
            from app.core.slo import record_timing
            record_timing(
                route=request.url.path,
                method=request.method,
                status=500,
                duration_ms=dur_ms,
            )
        except Exception as exc:
            _middleware_log.warning("slo_timing: record_timing (error path): %s", exc)
        raise

    try:
        from app.core.slo import record_timing
        dur_ms = (_time.monotonic() - started) * 1000
        record_timing(
            route=request.url.path,
            method=request.method,
            status=status,
            duration_ms=dur_ms,
        )
    except Exception as exc:
        _middleware_log.warning("slo_timing: record_timing (success path): %s", exc)
    return response


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path

    # Always-on baseline headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), interest-cohort=(), "
        "browsing-topics=(), payment=(), usb=(), midi=()"
    )

    # Storefront tracker paths run on merchant domains; they must not
    # set HSTS or frame-denial headers — those would break embedded
    # storefronts or force HTTPS upgrade on dev environments.
    if any(path.startswith(p) for p in _TRACKER_HEADERS_EXEMPT):
        return response

    # HSTS with preload eligibility — we're on our own domains here.
    response.headers["Strict-Transport-Security"] = (
        "max-age=63072000; includeSubDomains; preload"
    )

    # Frame protection: the API must never be frameable. The dashboard
    # is a separate Next.js app with its own frame-ancestors policy.
    response.headers["X-Frame-Options"] = "DENY"

    # CSP — strict by default. OAuth callback uses a redirect response
    # (3xx) whose body is ignored by browsers, so the strict CSP is
    # safe there too.
    response.headers["Content-Security-Policy"] = _STRICT_API_CSP

    # Cross-origin isolation headers — Spectre mitigations + tight
    # process isolation. `same-origin` on COOP prevents cross-origin
    # popups from sharing our browsing context.
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-site"

    return response


app.add_middleware(RequestIDMiddleware)

Base.metadata.create_all(bind=engine)

app.include_router(events_router)
app.include_router(conversion_probability_router)
app.include_router(revenue_radar_router)
app.include_router(intent_router)
app.include_router(track_router)
app.include_router(dashboard_router)
app.include_router(opportunities_router)
app.include_router(price_intelligence_router)
app.include_router(market_lookup_router)
app.include_router(decision_engine_router)
app.include_router(agent_router)
app.include_router(tracker_router)
app.include_router(live_visitors_router)
app.include_router(top_pages_router)
app.include_router(live_opportunities_router)
app.include_router(visitor_scores_router)
app.include_router(live_alerts_router)
app.include_router(ai_actions_router)
app.include_router(weekly_trend_router)
app.include_router(brief_router)
app.include_router(merchant_router)
app.include_router(merchant_export_router)
app.include_router(merchant_privacy_router)
app.include_router(product_metrics_router)
app.include_router(store_intelligence_router)
app.include_router(execution_actions_router)
app.include_router(product_trend_router)
app.include_router(session_replay_router)
app.include_router(funnel_router)
app.include_router(click_insights_router)
app.include_router(source_quality_router)
app.include_router(actions_router)
app.include_router(action_tasks_router)
app.include_router(webhooks_router)
app.include_router(shopify_refunds_router)
app.include_router(shopify_flow_schema_router)
app.include_router(track_purchase_router)
app.include_router(segments_router)
app.include_router(nudges_router)
app.include_router(nudge_script_router)
app.include_router(nudge_events_router)
app.include_router(shopify_admin_router)
app.include_router(klaviyo_router)
app.include_router(attribution_router)
app.include_router(lift_router)
app.include_router(proof_report_router)
app.include_router(public_proofs_router)
app.include_router(heatmap_router)
app.include_router(cohorts_router)
app.include_router(shopify_oauth_router)
app.include_router(billing_router)
app.include_router(setup_router)
app.include_router(onboarding_router)
app.include_router(health_router)
app.include_router(orders_router)
app.include_router(pnl_router)
app.include_router(cost_config_router)
app.include_router(trust_contracts_router)
app.include_router(roi_hero_router)
app.include_router(instant_intelligence_router)
app.include_router(daily_narrative_router)
app.include_router(cac_ltv_router)
app.include_router(mta_router)
app.include_router(margin_guard_router)
app.include_router(visitor_journeys_router)
app.include_router(forecasts_router)
app.include_router(compliance_evidence_router)
app.include_router(merchant_rules_router)
app.include_router(public_events_router)
app.include_router(customer_churn_router)
app.include_router(nudge_dna_router)
app.include_router(integrations_router)
app.include_router(telegram_webhook_router)
app.include_router(chat_support_router)
app.include_router(resend_webhooks_router)
app.include_router(sentry_webhooks_router)
app.include_router(ops_router)
app.include_router(legal_pages_router)
app.include_router(consent_banner_router)
app.include_router(frontend_errors_router)
app.include_router(benchmarks_router)
app.include_router(benchmarks_vertical_router)
app.include_router(knowledge_graph_router)
app.include_router(outbound_webhooks_router)
app.include_router(ads_router)
app.include_router(anomaly_fusion_router)
app.include_router(causal_explainer_router)
app.include_router(public_status_router)
app.include_router(merchant_groups_router)
app.include_router(agency_router)
app.include_router(storefront_preview_router)
app.include_router(tracker_error_router)
app.include_router(community_marketplace_router)
app.include_router(realtime_stream_router)
app.include_router(night_shift_router)
app.include_router(public_roi_counter_router)
app.include_router(feature_flags_admin_router)
app.include_router(slo_api_router)
app.include_router(auth_posture_router)
app.include_router(feature_usage_router)
app.include_router(anomaly_replay_router)
app.include_router(counterfactual_router)
app.include_router(playbook_router)
app.include_router(refund_loss_router)
app.include_router(goals_router)
app.include_router(rars_router)
app.include_router(risk_forecast_router)
app.include_router(annotations_router)
app.include_router(segment_compare_router)
app.include_router(roi_report_router)
app.include_router(signal_webhooks_router)
app.include_router(team_router)
app.include_router(revenue_autopsy_router)
app.include_router(abandoned_intent_router)
app.include_router(price_sensitivity_router)
app.include_router(causal_lift_router)
app.include_router(merchant_churn_router)
app.include_router(revenue_genome_router)


# ---------------------------------------------------------------------------
# Prometheus metrics endpoint + request tracking middleware
# ---------------------------------------------------------------------------

from fastapi.responses import PlainTextResponse
from app.core.metrics import track_request, render_metrics


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics():
    """Prometheus-compatible metrics endpoint."""
    return PlainTextResponse(render_metrics(), media_type="text/plain; version=0.0.4")


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Track request latency and status for Prometheus + opportunistic
    p95-snapshot flush.

    Intentional exclusions from `track_request` (per-route histograms):
      - `/metrics`      — Prometheus scrape, serializes the histograms
                          themselves; including it would create a
                          self-referential latency signal.
      - `/health`       — PM2 internal probe (localhost), not merchant
                          traffic. Would saturate histograms for a
                          synthetic endpoint.
      - `/system/health` — Traefik + external healthcheck probes.
                          Same reason.

    These three paths DO NOT contribute to p95 route stats. The p95
    slow-trend detector (observability_spikes.detect_p95_slow_trends)
    therefore excludes them by design — this is the correct behavior
    because route latency regressions that merchants feel live on
    other paths.

    However, we DO call `maybe_flush()` on these paths too — if a
    low-traffic dev period has 100% of traffic on health probes,
    skipping the flush call would stall p95 snapshotting entirely.
    The flush itself is rate-limited by a Redis lock to 1/5min.
    """
    path = request.url.path
    skip_tracking = path in ("/metrics", "/health", "/system/health")

    if skip_tracking:
        response = await call_next(request)
    else:
        ctx: dict = {}
        with track_request(request.method, path) as ctx:
            response = await call_next(request)
            ctx["status"] = response.status_code

    # Opportunistic p95 snapshot flush — called on EVERY request
    # (including skipped-tracking paths) so low-traffic windows still
    # flush accumulated histograms. Rate-limited by a 5-min Redis lock
    # so only ONE worker writes per window. Zero overhead when gate
    # is cold (one Redis GET). See app/services/p95_snapshot.py.
    try:
        from app.services.p95_snapshot import maybe_flush
        maybe_flush()
    except Exception:
        pass  # SILENT-EXCEPT-OK: snapshot flush failure must never abort a response

    return response


def _startup_env_audit() -> None:
    """
    Log the status of every production secret at server startup.
    Operators can see this immediately in: pm2 logs wishspark-backend
    This fires once — not on every request.

    Invoked from the `lifespan` context manager at module top.
    """
    allow_insecure_dev = os.getenv("ALLOW_INSECURE_DEV", "").lower() == "true"

    if allow_insecure_dev:
        _startup_log.warning(
            "SECURITY WARNING: ALLOW_INSECURE_DEV=true — production secret enforcement is "
            "RELAXED. Webhook HMAC and API key checks will bypass validation when secrets are "
            "absent. This is acceptable ONLY in a private development environment. "
            "NEVER deploy to production with ALLOW_INSECURE_DEV=true."
        )

    checks = [
        ("DATABASE_URL",           os.getenv("DATABASE_URL"),           True),
        ("SHOPIFY_API_KEY",        os.getenv("SHOPIFY_API_KEY"),        True),
        ("SHOPIFY_API_SECRET",     os.getenv("SHOPIFY_API_SECRET"),     True),
        ("SHOPIFY_WEBHOOK_SECRET", os.getenv("SHOPIFY_WEBHOOK_SECRET"), True),
        ("DASHBOARD_API_KEY",      os.getenv("DASHBOARD_API_KEY"),      True),
        ("OPENAI_API_KEY",         os.getenv("OPENAI_API_KEY"),         False),  # degrades gracefully
        ("REDIS_URL",              os.getenv("REDIS_URL"),              False),
        ("RESEND_API_KEY",         os.getenv("RESEND_API_KEY"),         False),
        ("SHOPIFY_PRO_PLAN_PRICE", os.getenv("SHOPIFY_PRO_PLAN_PRICE"), False),
        ("APP_URL",                os.getenv("APP_URL"),                True),
        ("DASHBOARD_URL",          os.getenv("DASHBOARD_URL"),          True),
        ("MERCHANT_TOKEN_ENCRYPTION_KEY", os.getenv("MERCHANT_TOKEN_ENCRYPTION_KEY"), True),
    ]

    missing_required: list[str] = []
    missing_optional: list[str] = []

    for name, value, required in checks:
        if value:
            _startup_log.info("ENV  OK       %s", name)
        elif required:
            _startup_log.warning("ENV  MISSING  %s  [REQUIRED — production insecure without this]", name)
            missing_required.append(name)
        else:
            _startup_log.warning("ENV  MISSING  %s  [optional — feature will degrade]", name)
            missing_optional.append(name)

    # Hard enforcement: MERCHANT_TOKEN_ENCRYPTION_KEY must be set in production.
    # Without it, Shopify access tokens are stored as plaintext — a single DB
    # breach exposes full admin API access to every merchant's store.
    if not os.getenv("MERCHANT_TOKEN_ENCRYPTION_KEY") and not allow_insecure_dev:
        raise RuntimeError(
            "FATAL: MERCHANT_TOKEN_ENCRYPTION_KEY is not set and ALLOW_INSECURE_DEV is not enabled. "
            "Refusing to start — Shopify tokens would be stored as plaintext. "
            "Generate a key: python3 -c \"import os; print(os.urandom(32).hex())\" "
            "and add it to backend/.env"
        )

    # Hard enforcement: MERCHANT_SESSION_SECRET must be set in production.
    # Without it, session JWTs cannot be signed and all merchant auth fails.
    # NO fallback to SHOPIFY_API_SECRET — that would silently share the webhook
    # verification key with the session signing key, a security isolation failure.
    if not os.getenv("MERCHANT_SESSION_SECRET", "").strip() and not allow_insecure_dev:
        raise RuntimeError(
            "FATAL: MERCHANT_SESSION_SECRET is not set and ALLOW_INSECURE_DEV is not enabled. "
            "Refusing to start — merchant sessions cannot be signed securely. "
            "Generate a key: python3 -c \"import os; print(os.urandom(32).hex())\" "
            "and add MERCHANT_SESSION_SECRET=<key> to backend/.env"
        )

    if missing_required:
        if allow_insecure_dev:
            _startup_log.warning(
                "STARTUP DEGRADED (dev mode): %d required env var(s) not set: %s — "
                "security enforcement for these is relaxed because ALLOW_INSECURE_DEV=true. "
                "Fill them in backend/.env before production exposure.",
                len(missing_required), ", ".join(missing_required),
            )
        else:
            _startup_log.warning(
                "STARTUP INCOMPLETE: %d required env var(s) not set: %s — "
                "affected endpoints will reject requests until these are configured. "
                "Fill them in backend/.env and run: pm2 reload ecosystem.config.js",
                len(missing_required), ", ".join(missing_required),
            )
    else:
        _startup_log.info("STARTUP OK: all required env vars are set.")

    # Observability posture
    if _sentry_enabled:
        _startup_log.info("OBSERVABILITY: Sentry error tracking ENABLED (env=%s rate=%.2f)",
                          _SENTRY_ENV, _SENTRY_RATE)
    else:
        _startup_log.warning(
            "OBSERVABILITY: Sentry NOT enabled — set SENTRY_DSN in backend/.env and "
            "install sentry-sdk[fastapi] to enable production error tracking."
        )


def _startup_telegram_warmup() -> None:
    """Pre-establish Telegram TLS connection in a background thread so the
    first operator command doesn't pay the 5-10s handshake cost.

    Invoked from the `lifespan` context manager at module top.
    """
    import threading
    try:
        from app.services.telegram_agent import is_configured, warmup_connection
        if is_configured():
            threading.Thread(target=warmup_connection, daemon=True).start()
    except Exception as exc:
        _startup_log.warning("telegram_warmup: %s", exc)


@app.get("/")
def root():
    return {"service": "wishspark", "status": "running"}


@app.get("/health")
def health():
    """
    Health check endpoint.
    Verifies DB connectivity in addition to process liveness.
    Returns 200 {"status": "ok"} when healthy.
    Returns 503 {"status": "degraded", "detail": "..."} on DB failure.
    """
    from fastapi.responses import JSONResponse
    from sqlalchemy import text as _text
    try:
        with engine.connect() as conn:
            conn.execute(_text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:
        _startup_log.error("Health check: DB unreachable — %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "detail": "database unreachable"},
        )
