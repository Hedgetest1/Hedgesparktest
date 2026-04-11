import logging
import os

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
from app.api.tracker import router as tracker_router
from app.api.live_visitors import router as live_visitors_router
from app.api.top_pages import router as top_pages_router
from app.api.live_opportunities import router as live_opportunities_router
from app.api.visitor_scores import router as visitor_scores_router
from app.api.live_alerts import router as live_alerts_router
from app.api.ai_actions import router as ai_actions_router
# revenue_actions_router removed — was a dead placeholder returning empty data
from app.api.weekly_trend import router as weekly_trend_router
# auth_router removed — legacy duplicate of shopify_oauth_router.
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
from app.api.health import router as health_router
from app.api.orders import router as orders_router
from app.api.pnl import router as pnl_router
from app.api.cost_config import router as cost_config_router
from app.api.integrations import router as integrations_router
from app.api.telegram_webhook import router as telegram_webhook_router
from app.api.chat_support import router as chat_support_router
from app.api.resend_webhooks import router as resend_webhooks_router
from app.api.sentry_webhooks import router as sentry_webhooks_router

_startup_log = logging.getLogger("wishspark.startup")

app = FastAPI(title="HedgeSpark API", docs_url=None, redoc_url=None)

# CORS — must allow:
#   1. https://app.hedgesparkhq.com       — dashboard (subdomain)
#   2. https://hedgesparkhq.com           — dashboard (root domain, same Next.js app)
#   3. https://admin.shopify.com           — Shopify new admin (embedded app iframe)
#   4. https://*.myshopify.com             — Shopify classic admin (embedded app iframe)
# allow_credentials=True is required because the dashboard sends the
# hs_session httpOnly cookie with every fetch (credentials: "include").
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://app.hedgesparkhq.com",
        "https://hedgesparkhq.com",
        "https://admin.shopify.com",
    ],
    allow_origin_regex=r"https://[a-zA-Z0-9\-]+\.myshopify\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
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
# Omitted: Content-Security-Policy (requires careful audit of all inline
# styles/scripts in Shopify embedded context before enabling).
#
# X-Frame-Options is SAMEORIGIN — safe because the dashboard runs on our
# own domain.  Shopify embeds use their own App Bridge framing; the API
# responses going to the embedded iframe don't need to be frameable.
# ---------------------------------------------------------------------------
_SECURITY_HEADERS_EXEMPT = frozenset({"/track", "/track/batch", "/nudge/event"})


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # HSTS — only on non-storefront paths (storefront scripts load over merchant's domain)
    if not any(request.url.path.startswith(p) for p in _SECURITY_HEADERS_EXEMPT):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
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
# revenue_actions_router removed
app.include_router(visitor_scores_router)
app.include_router(live_alerts_router)
app.include_router(ai_actions_router)
app.include_router(weekly_trend_router)
# auth_router removed — see shopify_oauth_router
app.include_router(brief_router)
app.include_router(merchant_router)
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
app.include_router(integrations_router)
app.include_router(telegram_webhook_router)
app.include_router(chat_support_router)
app.include_router(resend_webhooks_router)
app.include_router(sentry_webhooks_router)
app.include_router(ops_router)
app.include_router(frontend_errors_router)


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
    """Track request latency and status for Prometheus."""
    path = request.url.path
    # Skip metrics endpoint itself and health checks
    if path in ("/metrics", "/health", "/system/health"):
        return await call_next(request)
    ctx: dict = {}
    with track_request(request.method, path) as ctx:
        response = await call_next(request)
        ctx["status"] = response.status_code
        return response


@app.on_event("startup")
def _startup_env_audit() -> None:
    """
    Log the status of every production secret at server startup.
    Operators can see this immediately in: pm2 logs wishspark-backend
    This fires once — not on every request.
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


@app.on_event("startup")
def _startup_telegram_warmup() -> None:
    """Pre-establish Telegram TLS connection in a background thread so the
    first operator command doesn't pay the 5-10s handshake cost."""
    import threading
    try:
        from app.services.telegram_agent import is_configured, warmup_connection
        if is_configured():
            threading.Thread(target=warmup_connection, daemon=True).start()
    except Exception:
        pass  # Non-fatal — connection will be established on first use


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
