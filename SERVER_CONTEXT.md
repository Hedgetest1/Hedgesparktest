# WishSpark Server Context
Auto-generated: 2026-04-07T12:00:01.972680 UTC

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
/backend/app/api/chat_support.py
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
/backend/app/api/onboarding.py
/backend/app/api/opportunities.py
/backend/app/api/ops.py
/backend/app/api/orders.py
/backend/app/api/price_intelligence.py
/backend/app/api/product_metrics.py
/backend/app/api/product_trend.py
/backend/app/api/resend_webhooks.py
/backend/app/api/revenue_actions.py
/backend/app/api/revenue_radar.py
/backend/app/api/segments.py
/backend/app/api/sentry_webhooks.py
/backend/app/api/session_replay.py
/backend/app/api/setup.py
/backend/app/api/shopify_admin_api.py
/backend/app/api/shopify_oauth.py
/backend/app/api/source_quality.py
/backend/app/api/store_intelligence.py
/backend/app/api/telegram_webhook.py
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
/backend/app/services/adaptive_governance.py
/backend/app/services/alerting.py
/backend/app/services/attribution.py
/backend/app/services/audience_segments.py
/backend/app/services/audit.py
/backend/app/services/behavioral_cohorts.py
/backend/app/services/brief_engine.py
/backend/app/services/bugfix_pipeline.py
/backend/app/services/candidate_scoring.py
/backend/app/services/chat_voice.py
/backend/app/services/cohort_engine.py
/backend/app/services/conversion_metrics.py
/backend/app/services/conversion_service.py
/backend/app/services/digest_formatter.py
/backend/app/services/email_templates.py
/backend/app/services/empirical_calibration.py
/backend/app/services/evolution_bet_governance.py
/backend/app/services/evolution_business_outcomes.py
/backend/app/services/evolution_causal_attribution.py
/backend/app/services/evolution_converter.py
/backend/app/services/evolution_decision_engine.py
/backend/app/services/evolution_engine.py
/backend/app/services/evolution_gc.py
/backend/app/services/evolution_outcomes.py
/backend/app/services/evolution_proposal_outcomes.py
/backend/app/services/evolution_reinforcement.py
/backend/app/services/evolution_strategy.py
/backend/app/services/execution_engine.py
/backend/app/services/external_lookup_service.py
/backend/app/services/gdpr_processor.py
/backend/app/services/intent_engine.py
/backend/app/services/klaviyo_connection.py
/backend/app/services/klaviyo_export.py
/backend/app/services/learning_isolation.py
/backend/app/services/lesson_gc.py
/backend/app/services/loop_health.py
/backend/app/services/ltv_engine.py
/backend/app/services/market_lookup_engine.py
/backend/app/services/merchant_chatbot.py
/backend/app/services/merchant_digest.py
/backend/app/services/merchant_email_service.py
/backend/app/services/merge_intelligence.py
/backend/app/services/meta_reviewer.py
/backend/app/services/model_config.py
/backend/app/services/model_upgrade_agent.py
/backend/app/services/monthly_evolution_audit.py
/backend/app/services/nudge_composer.py
/backend/app/services/nudge_engine.py
/backend/app/services/nudge_gating.py
/backend/app/services/nudge_measurement.py
/backend/app/services/nudge_optimizer.py
/backend/app/services/nudge_rank.py
/backend/app/services/onboarding.py
/backend/app/services/onboarding_funnel.py
/backend/app/services/onboarding_health.py
/backend/app/services/opportunity_engine.py
/backend/app/services/orchestrator.py
/backend/app/services/orchestrator_context.py
/backend/app/services/orchestrator_llm.py
/backend/app/services/order_ingestion.py
/backend/app/services/outcome_evaluator.py
/backend/app/services/price_intelligence_engine.py
/backend/app/services/price_radar_service.py
/backend/app/services/proactive_chat.py
/backend/app/services/product_intelligence_engine.py
/backend/app/services/project_brain.py
/backend/app/services/promotion_pipeline.py
/backend/app/services/revenue_forecast.py
/backend/app/services/revenue_loss.py
/backend/app/services/revenue_metrics.py
/backend/app/services/revenue_recovery_engine.py
/backend/app/services/reviewer_layer.py
/backend/app/services/scaling_intelligence.py
/backend/app/services/scoring_calibration.py
/backend/app/services/sentry_parser.py
/backend/app/services/sentry_triage.py
/backend/app/services/setup_audit.py
/backend/app/services/shopify_admin.py
/backend/app/services/shopify_auth.py
/backend/app/services/signal_text.py
/backend/app/services/simulation_engine.py
/backend/app/services/simulation_probe.py
/backend/app/services/store_context.py
/backend/app/services/store_insight_engine.py
/backend/app/services/system_diagnostic.py
/backend/app/services/system_health_synthesizer.py
/backend/app/services/system_summary.py
/backend/app/services/telegram_agent.py
/backend/app/services/unique_product_engine.py
/backend/app/services/utm_attribution.py
/backend/app/services/webhook_health.py
/backend/app/services/webhook_monitor.py
/backend/app/services/weekly_digest.py

### Models
/backend/app/models/__init__.py
/backend/app/models/action_approval.py
/backend/app/models/action_outcome.py
/backend/app/models/action_snapshot.py
/backend/app/models/action_task.py
/backend/app/models/active_model_config.py
/backend/app/models/active_nudge.py
/backend/app/models/audit_log.py
/backend/app/models/autofix_promotion.py
/backend/app/models/bugfix_candidate.py
/backend/app/models/daily_brief.py
/backend/app/models/event.py
/backend/app/models/evolution_proposal.py
/backend/app/models/execution.py
/backend/app/models/gdpr_request.py
/backend/app/models/market_lookup.py
/backend/app/models/merchant.py
/backend/app/models/merchant_email.py
/backend/app/models/merge_outcome.py
/backend/app/models/meta_review.py
/backend/app/models/model_upgrade.py
/backend/app/models/nudge_event.py
/backend/app/models/nudge_impression_daily.py
/backend/app/models/onboarding_event.py
/backend/app/models/opportunity_signal.py
/backend/app/models/ops_alert.py
/backend/app/models/patch_fingerprint.py
/backend/app/models/price_intelligence.py
/backend/app/models/price_watch.py
/backend/app/models/product.py
/backend/app/models/product_metrics.py
/backend/app/models/product_opportunity.py
/backend/app/models/project_brain_snapshot.py
/backend/app/models/reviewer_assessment.py
/backend/app/models/scaling_recommendation.py
/backend/app/models/sentry_incident.py
/backend/app/models/shop_conversion_calibration.py
/backend/app/models/shop_order.py
/backend/app/models/store_metrics.py
/backend/app/models/support_incident.py
/backend/app/models/system_lesson.py
/backend/app/models/system_snapshot.py
/backend/app/models/unique_product_detection.py
/backend/app/models/visitor.py
/backend/app/models/visitor_product_state.py
/backend/app/models/visitor_purchase_session.py
/backend/app/models/wishlist_item.py
/backend/app/models/worker_log.py
/backend/app/models/worker_state.py

## FastAPI Routes
/backend/app/api/resend_webhooks.py :: @router.post("/inbound")
/backend/app/api/attribution.py :: @router.get("/sources")
/backend/app/api/attribution.py :: @router.get("/sources/pro")
/backend/app/api/attribution.py :: @router.get("/products")
/backend/app/api/attribution.py :: @router.get("/summary/pro")
/backend/app/api/tracker.py :: @router.get("/tracker.js")
/backend/app/api/tracker.py :: @router.get("/attribution.js")
/backend/app/api/session_replay.py :: @router.get("/sessions")
/backend/app/api/source_quality.py :: @router.get("/source-quality")
/backend/app/api/source_quality.py :: @router.get("/source-quality/pro")
/backend/app/api/segments.py :: @router.get("/segments")
/backend/app/api/opportunities.py :: @router.get("/opportunities")
/backend/app/api/opportunities.py :: @router.get("/opportunities/pro")
/backend/app/api/opportunities.py :: @router.get("/opportunities/top")
/backend/app/api/product_metrics.py :: @router.get("/metrics", response_model=ProductMetricsResponse)
/backend/app/api/store_intelligence.py :: @router.get("/store-intelligence", response_model=StoreIntelligenceResponse)
/backend/app/api/onboarding.py :: @router.post("/event")
/backend/app/api/click_insights.py :: @router.get("/clicks")
/backend/app/api/nudge_events.py :: @router.post("/nudge/event")
/backend/app/api/ops.py :: @router.get("/readiness/orchestrator")
/backend/app/api/ops.py :: @router.get("/llm-budget")
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
/backend/app/api/ops.py :: @router.get("/promotions/{promo_id}/remote-ci")
/backend/app/api/ops.py :: @router.post("/promotions/{promo_id}/pr")
/backend/app/api/ops.py :: @router.post("/promotions/{promo_id}/merge")
/backend/app/api/ops.py :: @router.get("/evolution")
/backend/app/api/ops.py :: @router.post("/evolution/{proposal_id}/accept")
/backend/app/api/ops.py :: @router.post("/evolution/{proposal_id}/reject")
/backend/app/api/ops.py :: @router.post("/evolution/{proposal_id}/revalidate")
/backend/app/api/ops.py :: @router.get("/model-upgrades")
/backend/app/api/ops.py :: @router.get("/model-upgrades/{upgrade_id}")
/backend/app/api/ops.py :: @router.post("/model-upgrades/{upgrade_id}/evaluate")
/backend/app/api/ops.py :: @router.post("/model-upgrades/{upgrade_id}/approve")
/backend/app/api/ops.py :: @router.post("/model-upgrades/{upgrade_id}/reject")
/backend/app/api/ops.py :: @router.post("/model-upgrades/{upgrade_id}/activate")
/backend/app/api/ops.py :: @router.post("/model-config/{module}/rollback")
/backend/app/api/ops.py :: @router.get("/model-config")
/backend/app/api/ops.py :: @router.get("/scaling/snapshots")
/backend/app/api/ops.py :: @router.get("/scaling/forecast")
/backend/app/api/ops.py :: @router.get("/scaling/recommendations")
/backend/app/api/ops.py :: @router.get("/project-brain/summary")
/backend/app/api/ops.py :: @router.post("/project-brain/refresh")
/backend/app/api/ops.py :: @router.get("/project-brain/constitution")
/backend/app/api/ops.py :: @router.post("/reviewer/assess")
/backend/app/api/ops.py :: @router.get("/incidents")
/backend/app/api/ops.py :: @router.get("/meta-review")
/backend/app/api/ops.py :: @router.get("/governance")
/backend/app/api/ops.py :: @router.post("/lessons/{lesson_id}/promote")
/backend/app/api/ops.py :: @router.post("/lessons/{lesson_id}/reject")
/backend/app/api/ops.py :: @router.get("/diagnostic")
/backend/app/api/ops.py :: @router.get("/system-health")
/backend/app/api/ops.py :: @router.get("/attribution/health")
/backend/app/api/ops.py :: @router.get("/tracker/status")
/backend/app/api/ops.py :: @router.get("/digest/status")
/backend/app/api/ops.py :: @router.get("/webhooks/status")
/backend/app/api/ops.py :: @router.get("/webhooks/status/{shop_domain}")
/backend/app/api/ops.py :: @router.get("/loop-health")
/backend/app/api/ops.py :: @router.get("/onboarding-health")
/backend/app/api/ops.py :: @router.get("/onboarding-funnel")
/backend/app/api/ops.py :: @router.get("/onboarding-funnel/{shop_domain}")
/backend/app/api/ops.py :: @router.get("/onboarding-friction")
/backend/app/api/ops.py :: @router.get("/weakness")
/backend/app/api/ops.py :: @router.get("/tier-check")
/backend/app/api/ops.py :: @router.get("/file-locks")
/backend/app/api/ops.py :: @router.get("/sentry-intake/health")
/backend/app/api/ops.py :: @router.post("/sentry-test")
/backend/app/api/ops.py :: @router.get("/emails")
/backend/app/api/ops.py :: @router.get("/incidents")
/backend/app/api/ops.py :: @router.get("/incidents/{incident_id}")
/backend/app/api/ops.py :: @router.get("/incidents/{incident_id}/family")
/backend/app/api/ops.py :: @router.get("/incidents/triage/queue")
/backend/app/api/ops.py :: @router.get("/incidents/parse-errors")
/backend/app/api/ops.py :: @router.get("/incidents/consumer/stats")
/backend/app/api/ops.py :: @router.get("/simulation/status")
/backend/app/api/live_opportunities.py :: @router.get("/live-opportunities")
/backend/app/api/telegram_webhook.py :: @router.post("/webhook")
/backend/app/api/actions.py :: @router.get("/candidates/pro")
/backend/app/api/live_visitors.py :: @router.get("/visitors")
/backend/app/api/market_lookup.py :: @router.get("/market-lookup/top")
/backend/app/api/conversion_probability.py :: @router.get("/top")
/backend/app/api/brief.py :: @router.get("/today")
/backend/app/api/brief.py :: @router.get("/today/pro")
/backend/app/api/cohorts.py :: @router.get("")
/backend/app/api/cohorts.py :: @router.get("/summary")
/backend/app/api/cohorts.py :: @router.get("/monthly")
/backend/app/api/cohorts.py :: @router.get("/ltv")
/backend/app/api/cohorts.py :: @router.get("/ltv/products")
/backend/app/api/cohorts.py :: @router.get("/ltv/customers")
/backend/app/api/cohorts.py :: @router.get("/behavioral")
/backend/app/api/billing.py :: @router.post("/subscribe")
/backend/app/api/billing.py :: @router.get("/callback")
/backend/app/api/track_purchase.py :: @router.post("/track/purchase-confirmed")
/backend/app/api/live_alerts.py :: @router.get("/alerts")
/backend/app/api/live_alerts.py :: @router.get("/alerts/pro")
/backend/app/api/decision_engine.py :: @router.post("/infer")
/backend/app/api/sentry_webhooks.py :: @router.post("/inbound")
/backend/app/api/top_pages.py :: @router.get("/top-pages")
/backend/app/api/klaviyo.py :: @router.get("/segment")
/backend/app/api/klaviyo.py :: @router.post("/push")
/backend/app/api/funnel.py :: @router.get("/funnel")
/backend/app/api/chat_support.py :: @router.post("/support", response_model=ChatResponseSchema)
/backend/app/api/chat_support.py :: @router.get("/support/history")
/backend/app/api/chat_support.py :: @router.patch("/support/incidents/{incident_id}/resolve")
/backend/app/api/chat_support.py :: @router.get("/support/resolutions")
/backend/app/api/chat_support.py :: @router.get("/support/proactive")
/backend/app/api/chat_support.py :: @router.post("/support/proactive/{message_id}/ack")
/backend/app/api/chat_support.py :: @router.post("/support/resolutions/{incident_id}/ack")
/backend/app/api/nudge_script.py :: @router.get("/nudge.js")
/backend/app/api/nudge_script.py :: @router.get("/tracker.js")
/backend/app/api/shopify_oauth.py :: @router.get("/install")
/backend/app/api/shopify_oauth.py :: @router.get("/callback")
/backend/app/api/shopify_oauth.py :: @router.get("/session")
/backend/app/api/dashboard.py :: @router.get("/overview")
/backend/app/api/dashboard.py :: @router.get("/intelligence")
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
/backend/app/api/orders.py :: @router.get("/forecast/pro")
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
/backend/app/api/setup.py :: @router.get("/attribution-snippet")
/backend/app/api/setup.py :: @router.get("/pixel-status")
/backend/app/api/setup.py :: @router.post("/repair/webhook")
/backend/app/api/setup.py :: @router.post("/repair/tracker")
/backend/app/api/health.py :: @router.get("/system/health")

## Dashboard Routes
/
/app
/insights
/pricing

## Architecture Documents
/docs/AUTO_CONTEXT.md

## Notes
This file is automatically generated by context_builder.py
Used by AI agents to understand server architecture.
