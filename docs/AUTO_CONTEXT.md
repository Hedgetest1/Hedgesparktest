# WishSpark Auto Context
Auto-generated: 2026-03-23T19:00:01.573773 UTC

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
/backend/app/api/action_tasks.py
/backend/app/api/actions.py
/backend/app/api/agent.py
/backend/app/api/ai_actions.py
/backend/app/api/attribution.py
/backend/app/api/auth.py
/backend/app/api/brief.py
/backend/app/api/click_insights.py
/backend/app/api/cohorts.py
/backend/app/api/conversion_probability.py
/backend/app/api/dashboard.py
/backend/app/api/decision_engine.py
/backend/app/api/events.py
/backend/app/api/funnel.py
/backend/app/api/heatmap.py
/backend/app/api/intent.py
/backend/app/api/klaviyo.py
/backend/app/api/lift.py
/backend/app/api/live_alerts.py
/backend/app/api/live_opportunities.py
/backend/app/api/live_visitors.py
/backend/app/api/market_lookup.py
/backend/app/api/merchant.py
/backend/app/api/nudge_events.py
/backend/app/api/nudge_script.py
/backend/app/api/nudges.py
/backend/app/api/opportunities.py
/backend/app/api/price_intelligence.py
/backend/app/api/product_metrics.py
/backend/app/api/product_trend.py
/backend/app/api/revenue_actions.py
/backend/app/api/revenue_radar.py
/backend/app/api/segments.py
/backend/app/api/session_replay.py
/backend/app/api/shopify_admin_api.py
/backend/app/api/source_quality.py
/backend/app/api/top_pages.py
/backend/app/api/track.py
/backend/app/api/track_purchase.py
/backend/app/api/tracker.py
/backend/app/api/visitor_scores.py
/backend/app/api/webhooks.py
/backend/app/api/weekly_trend.py

## Backend Services
/backend/app/services/__init__.py
/backend/app/services/action_candidates_engine.py
/backend/app/services/action_executor.py
/backend/app/services/attribution.py
/backend/app/services/audience_segments.py
/backend/app/services/brief_engine.py
/backend/app/services/cohort_engine.py
/backend/app/services/conversion_metrics.py
/backend/app/services/conversion_service.py
/backend/app/services/empirical_calibration.py
/backend/app/services/external_lookup_service.py
/backend/app/services/intent_engine.py
/backend/app/services/klaviyo_export.py
/backend/app/services/market_lookup_engine.py
/backend/app/services/nudge_composer.py
/backend/app/services/nudge_engine.py
/backend/app/services/nudge_gating.py
/backend/app/services/nudge_measurement.py
/backend/app/services/nudge_rank.py
/backend/app/services/opportunity_engine.py
/backend/app/services/order_ingestion.py
/backend/app/services/price_intelligence_engine.py
/backend/app/services/price_radar_service.py
/backend/app/services/product_intelligence_engine.py
/backend/app/services/revenue_loss.py
/backend/app/services/revenue_metrics.py
/backend/app/services/revenue_recovery_engine.py
/backend/app/services/shopify_admin.py
/backend/app/services/shopify_auth.py
/backend/app/services/signal_text.py
/backend/app/services/unique_product_engine.py
/backend/app/services/utm_attribution.py

## Backend Models
/backend/app/models/__init__.py
/backend/app/models/action_task.py
/backend/app/models/active_nudge.py
/backend/app/models/daily_brief.py
/backend/app/models/event.py
/backend/app/models/market_lookup.py
/backend/app/models/merchant.py
/backend/app/models/nudge_event.py
/backend/app/models/opportunity_signal.py
/backend/app/models/price_intelligence.py
/backend/app/models/price_watch.py
/backend/app/models/product.py
/backend/app/models/product_metrics.py
/backend/app/models/product_opportunity.py
/backend/app/models/shop_conversion_calibration.py
/backend/app/models/shop_order.py
/backend/app/models/unique_product_detection.py
/backend/app/models/visitor.py
/backend/app/models/visitor_product_state.py
/backend/app/models/visitor_purchase_session.py
/backend/app/models/wishlist_item.py
/backend/app/models/worker_log.py
/backend/app/models/worker_state.py

## Dashboard Routes
/
/insights
/pricing

## Agent Notes
- Read AGENTS.md first
- Use docs/CURRENT_STATE.md for stable project state
- Use docs/NEXT_STEPS.md for roadmap
- Use SERVER_CONTEXT.md and this file for auto-generated technical context
