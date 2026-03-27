# WishSpark Server Context
Auto-generated: 2026-03-27T18:00:01.727885 UTC

## Base Path
/opt/wishspark

## Stack
Backend: FastAPI
Frontend: Next.js
Process Manager: PM2

## Backend Structure

### API Modules
/backend/app/api/__init__.py
/backend/app/api/action_tasks.py
/backend/app/api/actions.py
/backend/app/api/agent.py
/backend/app/api/ai_actions.py
/backend/app/api/attribution.py
/backend/app/api/auth.py
/backend/app/api/billing.py
/backend/app/api/brief.py
/backend/app/api/click_insights.py
/backend/app/api/cohorts.py
/backend/app/api/conversion_probability.py
/backend/app/api/dashboard.py
/backend/app/api/decision_engine.py
/backend/app/api/events.py
/backend/app/api/execution_actions.py
/backend/app/api/funnel.py
/backend/app/api/health.py
/backend/app/api/heatmap.py
/backend/app/api/integrations.py
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
/backend/app/api/ops.py
/backend/app/api/orders.py
/backend/app/api/price_intelligence.py
/backend/app/api/product_metrics.py
/backend/app/api/product_trend.py
/backend/app/api/revenue_actions.py
/backend/app/api/revenue_radar.py
/backend/app/api/segments.py
/backend/app/api/session_replay.py
/backend/app/api/setup.py
/backend/app/api/shopify_admin_api.py
/backend/app/api/shopify_oauth.py
/backend/app/api/source_quality.py
/backend/app/api/store_intelligence.py
/backend/app/api/top_pages.py
/backend/app/api/track.py
/backend/app/api/track_purchase.py
/backend/app/api/tracker.py
/backend/app/api/visitor_scores.py
/backend/app/api/webhooks.py
/backend/app/api/weekly_trend.py

### Services
/backend/app/services/__init__.py
/backend/app/services/action_candidates_engine.py
/backend/app/services/action_executor.py
/backend/app/services/action_proof.py
/backend/app/services/activation.py
/backend/app/services/alerting.py
/backend/app/services/attribution.py
/backend/app/services/audience_segments.py
/backend/app/services/audit.py
/backend/app/services/brief_engine.py
/backend/app/services/bugfix_pipeline.py
/backend/app/services/cohort_engine.py
/backend/app/services/conversion_metrics.py
/backend/app/services/conversion_service.py
/backend/app/services/digest_formatter.py
/backend/app/services/empirical_calibration.py
/backend/app/services/execution_engine.py
/backend/app/services/external_lookup_service.py
/backend/app/services/gdpr_processor.py
/backend/app/services/intent_engine.py
/backend/app/services/klaviyo_connection.py
/backend/app/services/klaviyo_export.py
/backend/app/services/market_lookup_engine.py
/backend/app/services/nudge_composer.py
/backend/app/services/nudge_engine.py
/backend/app/services/nudge_gating.py
/backend/app/services/nudge_measurement.py
/backend/app/services/nudge_optimizer.py
/backend/app/services/nudge_rank.py
/backend/app/services/onboarding.py
/backend/app/services/opportunity_engine.py
/backend/app/services/orchestrator.py
/backend/app/services/orchestrator_context.py
/backend/app/services/orchestrator_llm.py
/backend/app/services/order_ingestion.py
/backend/app/services/outcome_evaluator.py
/backend/app/services/price_intelligence_engine.py
/backend/app/services/price_radar_service.py
/backend/app/services/product_intelligence_engine.py
/backend/app/services/promotion_pipeline.py
/backend/app/services/revenue_loss.py
/backend/app/services/revenue_metrics.py
/backend/app/services/revenue_recovery_engine.py
/backend/app/services/setup_audit.py
/backend/app/services/shopify_admin.py
/backend/app/services/shopify_auth.py
/backend/app/services/signal_text.py
/backend/app/services/unique_product_engine.py
/backend/app/services/utm_attribution.py
/backend/app/services/webhook_health.py
/backend/app/services/weekly_digest.py

### Models
/backend/app/models/__init__.py
/backend/app/models/action_approval.py
/backend/app/models/action_outcome.py
/backend/app/models/action_snapshot.py
/backend/app/models/action_task.py
/backend/app/models/active_nudge.py
/backend/app/models/audit_log.py
/backend/app/models/autofix_promotion.py
/backend/app/models/bugfix_candidate.py
/backend/app/models/daily_brief.py
/backend/app/models/event.py
/backend/app/models/execution.py
/backend/app/models/gdpr_request.py
/backend/app/models/market_lookup.py
/backend/app/models/merchant.py
/backend/app/models/nudge_event.py
/backend/app/models/nudge_impression_daily.py
/backend/app/models/opportunity_signal.py
/backend/app/models/ops_alert.py
/backend/app/models/price_intelligence.py
/backend/app/models/price_watch.py
/backend/app/models/product.py
/backend/app/models/product_metrics.py
/backend/app/models/product_opportunity.py
/backend/app/models/shop_conversion_calibration.py
/backend/app/models/shop_order.py
/backend/app/models/store_metrics.py
/backend/app/models/unique_product_detection.py
/backend/app/models/visitor.py
/backend/app/models/visitor_product_state.py
/backend/app/models/visitor_purchase_session.py
/backend/app/models/wishlist_item.py
/backend/app/models/worker_log.py
/backend/app/models/worker_state.py

## FastAPI Routes
/backend/app/api/attribution.py :: @router.get("/sources")
/backend/app/api/attribution.py :: @router.get("/sources/pro")
/backend/app/api/attribution.py :: @router.get("/products")
/backend/app/api/tracker.py :: @router.get("/tracker.js")
/backend/app/api/session_replay.py :: @router.get("/sessions")
/backend/app/api/source_quality.py :: @router.get("/source-quality")
/backend/app/api/source_quality.py :: @router.get("/source-quality/pro")
/backend/app/api/segments.py :: @router.get("/segments")
/backend/app/api/opportunities.py :: @router.get("/opportunities")
/backend/app/api/opportunities.py :: @router.get("/opportunities/pro")
/backend/app/api/opportunities.py :: @router.get("/opportunities/top")
/backend/app/api/product_metrics.py :: @router.get("/metrics", response_model=ProductMetricsResponse)
/backend/app/api/store_intelligence.py :: @router.get("/store-intelligence", response_model=StoreIntelligenceResponse)
/backend/app/api/click_insights.py :: @router.get("/clicks")
/backend/app/api/nudge_events.py :: @router.post("/nudge/event")
/backend/app/api/ops.py :: @router.get("/readiness/orchestrator")
/backend/app/api/ops.py :: @router.get("/alerts")
/backend/app/api/ops.py :: @router.get("/alerts/recent")
/backend/app/api/ops.py :: @router.post("/alerts/{alert_id}/resolve")
/backend/app/api/ops.py :: @router.get("/gdpr/exports")
/backend/app/api/ops.py :: @router.get("/gdpr/exports/{request_id}")
/backend/app/api/ops.py :: @router.get("/approvals")
/backend/app/api/ops.py :: @router.post("/approvals/{approval_id}/approve")
/backend/app/api/ops.py :: @router.post("/approvals/{approval_id}/reject")
/backend/app/api/ops.py :: @router.get("/bugfixes")
/backend/app/api/ops.py :: @router.get("/bugfixes/{candidate_id}")
/backend/app/api/ops.py :: @router.post("/bugfixes/{candidate_id}/propose")
/backend/app/api/ops.py :: @router.post("/bugfixes/{candidate_id}/approve")
/backend/app/api/ops.py :: @router.post("/bugfixes/{candidate_id}/reject")
/backend/app/api/ops.py :: @router.post("/bugfixes/{candidate_id}/apply")
/backend/app/api/ops.py :: @router.get("/promotions")
/backend/app/api/ops.py :: @router.get("/promotions/{promo_id}")
/backend/app/api/ops.py :: @router.post("/promotions/{promo_id}/branch")
/backend/app/api/ops.py :: @router.post("/promotions/{promo_id}/ci")
/backend/app/api/ops.py :: @router.post("/promotions/{promo_id}/approve")
/backend/app/api/ops.py :: @router.post("/promotions/{promo_id}/reject")
/backend/app/api/ops.py :: @router.post("/promotions/{promo_id}/push")
/backend/app/api/live_opportunities.py :: @router.get("/live-opportunities")
/backend/app/api/actions.py :: @router.get("/candidates/pro")
/backend/app/api/live_visitors.py :: @router.get("/visitors")
/backend/app/api/market_lookup.py :: @router.get("/market-lookup/top")
/backend/app/api/conversion_probability.py :: @router.get("/top")
/backend/app/api/brief.py :: @router.get("/today")
/backend/app/api/brief.py :: @router.get("/today/pro")
/backend/app/api/cohorts.py :: @router.get("")
/backend/app/api/cohorts.py :: @router.get("/summary")
/backend/app/api/billing.py :: @router.post("/subscribe")
/backend/app/api/billing.py :: @router.get("/callback")
/backend/app/api/track_purchase.py :: @router.post("/track/purchase-confirmed")
/backend/app/api/live_alerts.py :: @router.get("/alerts")
/backend/app/api/live_alerts.py :: @router.get("/alerts/pro")
/backend/app/api/decision_engine.py :: @router.post("/infer")
/backend/app/api/top_pages.py :: @router.get("/top-pages")
/backend/app/api/klaviyo.py :: @router.get("/segment")
/backend/app/api/klaviyo.py :: @router.post("/push")
/backend/app/api/funnel.py :: @router.get("/funnel")
/backend/app/api/nudge_script.py :: @router.get("/nudge.js")
/backend/app/api/nudge_script.py :: @router.get("/tracker.js")
/backend/app/api/shopify_oauth.py :: @router.get("/install")
/backend/app/api/shopify_oauth.py :: @router.get("/callback")
/backend/app/api/dashboard.py :: @router.get("/overview")
/backend/app/api/dashboard.py :: @router.get("/overview/pro")
/backend/app/api/integrations.py :: @router.get("", response_model=IntegrationsResponse)
/backend/app/api/integrations.py :: @router.put("/klaviyo", response_model=KlaviyoConnectionResponse)
/backend/app/api/integrations.py :: @router.post("/klaviyo/test", response_model=KlaviyoTestResponse)
/backend/app/api/integrations.py :: @router.delete("/klaviyo", response_model=KlaviyoConnectionResponse)
/backend/app/api/revenue_radar.py :: @router.get("/top")
/backend/app/api/visitor_scores.py :: @router.get("/visitor-scores")
/backend/app/api/merchant.py :: @router.get("/me")
/backend/app/api/merchant.py :: @router.get("/plan")
/backend/app/api/merchant.py :: @router.get("/activation")
/backend/app/api/lift.py :: @router.get("")
/backend/app/api/webhooks.py :: @router.post("/shopify/orders")
/backend/app/api/webhooks.py :: @router.post("/shopify/orders-created")
/backend/app/api/webhooks.py :: @router.post("/shopify/orders-paid")
/backend/app/api/webhooks.py :: @router.post("/shopify/app-uninstalled")
/backend/app/api/webhooks.py :: @router.post("/shopify/customers-redact")
/backend/app/api/webhooks.py :: @router.post("/shopify/customers-data-request")
/backend/app/api/webhooks.py :: @router.post("/shopify/shop-redact")
/backend/app/api/price_intelligence.py :: @router.get("/price-intelligence/top")
/backend/app/api/price_intelligence.py :: @router.post("/price-radar")
/backend/app/api/product_trend.py :: @router.get("/trend", response_model=ProductTrendResponse)
/backend/app/api/orders.py :: @router.get("/summary")
/backend/app/api/orders.py :: @router.get("/daily-revenue")
/backend/app/api/orders.py :: @router.get("/product-conversions")
/backend/app/api/heatmap.py :: @router.get("")
/backend/app/api/heatmap.py :: @router.get("/top")
/backend/app/api/auth.py :: @router.get("/install")
/backend/app/api/auth.py :: @router.get("/auth/callback")
/backend/app/api/events.py :: @router.post("/track-event")
/backend/app/api/revenue_actions.py :: @router.get("/revenue-actions")
/backend/app/api/ai_actions.py :: @router.get("/actions")
/backend/app/api/weekly_trend.py :: @router.get("/weekly-trend")
/backend/app/api/agent.py :: @router.get("/daily-brief")
/backend/app/api/agent.py :: @router.get("/scan-project")
/backend/app/api/agent.py :: @router.get("/project-context")
/backend/app/api/agent.py :: @router.get("/analyze-backend")
/backend/app/api/agent.py :: @router.get("/implement-next-step")
/backend/app/api/agent.py :: @router.post("/sandbox/create")
/backend/app/api/agent.py :: @router.post("/sandbox/{run_id}/status")
/backend/app/api/agent.py :: @router.get("/sandbox/{run_id}")
/backend/app/api/agent.py :: @router.get("/sandbox-runs")
/backend/app/api/action_tasks.py :: @router.post("/execute")
/backend/app/api/action_tasks.py :: @router.get("/tasks")
/backend/app/api/action_tasks.py :: @router.patch("/tasks/{task_id}")
/backend/app/api/action_tasks.py :: @router.post("/tasks/{task_id}/release")
/backend/app/api/action_tasks.py :: @router.get("/tasks/{task_id}")
/backend/app/api/action_tasks.py :: @router.get("/proof")
/backend/app/api/shopify_admin_api.py :: @router.get("/inventory")
/backend/app/api/shopify_admin_api.py :: @router.post("/discount")
/backend/app/api/shopify_admin_api.py :: @router.post("/price")
/backend/app/api/shopify_admin_api.py :: @router.get("/products")
/backend/app/api/execution_actions.py :: @router.post("/{execution_id}/confirm", response_model=ExecutionConfirmResponse)
/backend/app/api/execution_actions.py :: @router.post("/{execution_id}/status")
/backend/app/api/execution_actions.py :: @router.get("/eligibility", response_model=EligibilityResponse)
/backend/app/api/execution_actions.py :: @router.get("/{execution_id}/audience", response_model=AudienceExportResponse)
/backend/app/api/execution_actions.py :: @router.post("/{execution_id}/sync-klaviyo", response_model=KlaviyoSyncResponse)
/backend/app/api/track.py :: @router.options("/track")
/backend/app/api/track.py :: @router.options("/track/batch")
/backend/app/api/track.py :: @router.post("/track")
/backend/app/api/track.py :: @router.post("/track/batch")
/backend/app/api/track.py :: @router.options("/track")
/backend/app/api/track.py :: @router.options("/track/batch")
/backend/app/api/nudges.py :: @router.get("/nudges/active")
/backend/app/api/nudges.py :: @router.get("/pro/nudges")
/backend/app/api/nudges.py :: @router.post("/pro/nudges")
/backend/app/api/nudges.py :: @router.get("/pro/nudges/rank")
/backend/app/api/nudges.py :: @router.get("/pro/nudges/{nudge_id}/stats")
/backend/app/api/nudges.py :: @router.patch("/pro/nudges/{nudge_id}/holdout")
/backend/app/api/nudges.py :: @router.delete("/pro/nudges/{nudge_id}")
/backend/app/api/intent.py :: @router.get("/intent/top-hot")
/backend/app/api/intent.py :: @router.get("/intent/visitor/{visitor_id}")
/backend/app/api/intent.py :: @router.get("/intent/summary")
/backend/app/api/intent.py :: @router.get("/intent/products/top")
/backend/app/api/intent.py :: @router.get("/intent/products/opportunities")
/backend/app/api/setup.py :: @router.get("/status")
/backend/app/api/setup.py :: @router.post("/repair/webhook")
/backend/app/api/setup.py :: @router.post("/repair/tracker")
/backend/app/api/health.py :: @router.get("/system/health")

## Dashboard Routes
/
/insights
/pricing

## Architecture Documents
/docs/AI_ENGINE_TYPES.md
/docs/AI_ROUTER.md
/docs/API_MAP.md
/docs/AUTO_CONTEXT.md
/docs/CURRENT_STATE.md
/docs/DATA_FLOW.md
/docs/EVENT_BACKBONE.md
/docs/NEXT_STEPS.md
/docs/PROJECT_ARCHITECTURE.md
/docs/README.md
/docs/SANDBOX_LAYER.md
/docs/SYSTEM_ARCHITECTURE.md

## Notes
This file is automatically generated by context_builder.py
Used by AI agents to understand server architecture.
