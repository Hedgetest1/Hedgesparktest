# WishSpark Auto Context
Auto-generated: 2026-03-17T14:00:01.181302 UTC

## Project Root
/opt/wishspark

## Technical Summary
- Backend: FastAPI
- Frontend: Next.js dashboard
- Database: Postgres (docker)
- Cache: Redis (docker)
- Proxy: Traefik
- Process manager: PM2

## Backend API Modules
/backend/app/api/__init__.py
/backend/app/api/agent.py
/backend/app/api/ai_actions.py
/backend/app/api/conversion_probability.py
/backend/app/api/dashboard.py
/backend/app/api/decision_engine.py
/backend/app/api/events.py
/backend/app/api/intent.py
/backend/app/api/live_alerts.py
/backend/app/api/live_opportunities.py
/backend/app/api/live_visitors.py
/backend/app/api/market_lookup.py
/backend/app/api/opportunities.py
/backend/app/api/price_intelligence.py
/backend/app/api/revenue_actions.py
/backend/app/api/revenue_radar.py
/backend/app/api/top_pages.py
/backend/app/api/track.py
/backend/app/api/tracker.py
/backend/app/api/visitor_scores.py
/backend/app/api/weekly_trend.py

## Backend Services
/backend/app/services/__init__.py
/backend/app/services/intent_engine.py
/backend/app/services/market_lookup_engine.py
/backend/app/services/opportunity_engine.py
/backend/app/services/price_intelligence_engine.py
/backend/app/services/product_intelligence_engine.py
/backend/app/services/revenue_recovery_engine.py
/backend/app/services/unique_product_engine.py

## Backend Models
/backend/app/models/__init__.py
/backend/app/models/event.py
/backend/app/models/market_lookup.py
/backend/app/models/price_intelligence.py
/backend/app/models/price_watch.py
/backend/app/models/product.py
/backend/app/models/product_opportunity.py
/backend/app/models/unique_product_detection.py
/backend/app/models/visitor.py
/backend/app/models/visitor_product_state.py
/backend/app/models/wishlist_item.py

## Dashboard Routes
/
/insights

## Agent Notes
- Read AGENTS.md first
- Use docs/CURRENT_STATE.md for stable project state
- Use docs/NEXT_STEPS.md for roadmap
- Use SERVER_CONTEXT.md and this file for auto-generated technical context
