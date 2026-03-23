import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.rate_limit import RateLimitMiddleware

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
from app.api.revenue_actions import router as revenue_actions_router
from app.api.weekly_trend import router as weekly_trend_router
from app.api.auth import router as auth_router
from app.api.product_metrics import router as product_metrics_router
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
from app.models.nudge_event import NudgeEvent    # noqa: F401 — ensures table is created
from app.api.nudges import router as nudges_router
from app.api.nudge_script import router as nudge_script_router
from app.api.nudge_events import router as nudge_events_router
from app.api.shopify_admin_api import router as shopify_admin_router
from app.api.klaviyo import router as klaviyo_router
from app.api.attribution import router as attribution_router
from app.api.lift import router as lift_router
from app.api.heatmap import router as heatmap_router
from app.api.cohorts import router as cohorts_router

_startup_log = logging.getLogger("wishspark.startup")

app = FastAPI(title="WishSpark API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://app.hedgesparkhq.com",
    ],
    allow_credentials=True,   # required: dashboard fetches use credentials: "include"
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
        ("POST", "/track"):                        (60, 60),   # 60 req / 60 s
        ("POST", "/nudge/event"):                  (60, 60),   # 60 req / 60 s
        ("POST", "/webhooks/shopify/orders-paid"): (20, 60),   # 20 req / 60 s
        ("POST", "/pro/nudges"):                   (10, 60),   # 10 req / 60 s  (AI cost guard)
    },
)

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
app.include_router(revenue_actions_router)
app.include_router(visitor_scores_router)
app.include_router(live_alerts_router)
app.include_router(ai_actions_router)
app.include_router(weekly_trend_router)
app.include_router(auth_router)
app.include_router(brief_router)
app.include_router(merchant_router)
app.include_router(product_metrics_router)
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
app.include_router(heatmap_router)
app.include_router(cohorts_router)


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
        ("APP_URL",                os.getenv("APP_URL"),                True),
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
