from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
from app.api.agent import router as agent_router
from app.api.tracker import router as tracker_router
from app.api.live_visitors import router as live_visitors_router
from app.api.top_pages import router as top_pages_router
from app.api.live_opportunities import router as live_opportunities_router
from app.api.visitor_scores import router as visitor_scores_router
from app.api.live_alerts import router as live_alerts_router
from app.api.ai_actions import router as ai_actions_router
from app.api.revenue_actions import router as revenue_actions_router
from app.api.weekly_trend import router as weekly_trend_router

app = FastAPI(title="WishSpark API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


@app.get("/")
def root():
    return {"service": "wishspark", "status": "running"}


@app.get("/health")
def health():
    return {"status": "ok"}
