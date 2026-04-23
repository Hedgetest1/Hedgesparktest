# WishSpark Server Context
Auto-generated: 2026-04-23T23:00:01.227435 UTC

## Base Path
/opt/wishspark

## Stack
Backend: FastAPI
Frontend: Next.js
Process Manager: PM2

## Backend Structure

### API Modules
/backend/app/api/__init__.py
/backend/app/api/_types.py
/backend/app/api/abandoned_intent.py
/backend/app/api/action_tasks.py
/backend/app/api/actions.py
/backend/app/api/ads.py
/backend/app/api/agency.py
/backend/app/api/agent.py
/backend/app/api/ai_actions.py
/backend/app/api/analytics_assistant.py
/backend/app/api/annotations.py
/backend/app/api/anomaly_fusion.py
/backend/app/api/anomaly_replay.py
/backend/app/api/attribution.py
/backend/app/api/auth.py
/backend/app/api/auth_posture.py
/backend/app/api/benchmarks.py
/backend/app/api/benchmarks_vertical.py
/backend/app/api/billing.py
/backend/app/api/brief.py
/backend/app/api/cac_ltv.py
/backend/app/api/causal_explainer.py
/backend/app/api/causal_lift.py
/backend/app/api/chat_support.py
/backend/app/api/click_insights.py
/backend/app/api/cohorts.py
/backend/app/api/community_marketplace.py
/backend/app/api/compliance_evidence.py
/backend/app/api/consent_banner.py
/backend/app/api/conversion_probability.py
/backend/app/api/cost_config.py
/backend/app/api/counterfactual.py
/backend/app/api/customer_churn.py
/backend/app/api/daily_narrative.py
/backend/app/api/dashboard.py
/backend/app/api/decision_engine.py
/backend/app/api/events.py
/backend/app/api/execution_actions.py
/backend/app/api/feature_flags_admin.py
/backend/app/api/feature_usage_api.py
/backend/app/api/forecasts.py
/backend/app/api/frontend_errors.py
/backend/app/api/funnel.py
/backend/app/api/goals.py
/backend/app/api/health.py
/backend/app/api/heatmap.py
/backend/app/api/instant_intelligence.py
/backend/app/api/integrations.py
/backend/app/api/intent.py
/backend/app/api/klaviyo.py
/backend/app/api/knowledge_graph.py
/backend/app/api/legal_pages.py
/backend/app/api/lift.py
/backend/app/api/lite_export.py
/backend/app/api/live_alerts.py
/backend/app/api/live_opportunities.py
/backend/app/api/live_visitors.py
/backend/app/api/margin_guard_api.py
/backend/app/api/market_lookup.py
/backend/app/api/merchant.py
/backend/app/api/merchant_churn.py
/backend/app/api/merchant_export.py
/backend/app/api/merchant_groups.py
/backend/app/api/merchant_privacy.py
/backend/app/api/merchant_rules.py
/backend/app/api/merchant_slack.py
/backend/app/api/mta.py
/backend/app/api/night_shift.py
/backend/app/api/nudge_dna.py
/backend/app/api/nudge_events.py
/backend/app/api/nudge_script.py
/backend/app/api/nudges.py
/backend/app/api/onboarding.py
/backend/app/api/opportunities.py
/backend/app/api/ops.py
/backend/app/api/ops_email_preview.py
/backend/app/api/orders.py
/backend/app/api/outbound_webhooks.py
/backend/app/api/playbook.py
/backend/app/api/pnl.py
/backend/app/api/prediction_accuracy.py
/backend/app/api/price_intelligence.py
/backend/app/api/price_sensitivity.py
/backend/app/api/product_metrics.py
/backend/app/api/product_trend.py
/backend/app/api/proof_report.py
/backend/app/api/public_events.py
/backend/app/api/public_proofs.py
/backend/app/api/public_roi_counter.py
/backend/app/api/public_status.py
/backend/app/api/public_transparency.py
/backend/app/api/realtime_stream.py
/backend/app/api/refund_loss.py
/backend/app/api/resend_webhooks.py
/backend/app/api/revenue_at_risk.py
/backend/app/api/revenue_autopsy.py
/backend/app/api/revenue_genome.py
/backend/app/api/revenue_radar.py
/backend/app/api/risk_forecast.py
/backend/app/api/roi_hero.py
/backend/app/api/roi_report.py
/backend/app/api/rum.py
/backend/app/api/segment_compare.py
/backend/app/api/segments.py
/backend/app/api/sentry_webhooks.py
/backend/app/api/session_replay.py
/backend/app/api/setup.py
/backend/app/api/shopify_admin_api.py
/backend/app/api/shopify_flow_schema.py
/backend/app/api/shopify_oauth.py
/backend/app/api/shopify_refunds.py
/backend/app/api/signal_webhooks.py
/backend/app/api/slo_api.py
/backend/app/api/source_quality.py
/backend/app/api/store_intelligence.py
/backend/app/api/storefront_preview.py
/backend/app/api/team.py
/backend/app/api/telegram_webhook.py
/backend/app/api/top_pages.py
/backend/app/api/track.py
/backend/app/api/track_purchase.py
/backend/app/api/tracker.py
/backend/app/api/tracker_error.py
/backend/app/api/trust_contracts.py
/backend/app/api/visitor_journeys.py
/backend/app/api/visitor_scores.py
/backend/app/api/webhooks.py
/backend/app/api/weekly_trend.py

### Services
/backend/app/services/__init__.py
/backend/app/services/abandoned_intent.py
/backend/app/services/action_agent.py
/backend/app/services/action_candidates_engine.py
/backend/app/services/action_executor.py
/backend/app/services/action_learning.py
/backend/app/services/action_proof.py
/backend/app/services/activation.py
/backend/app/services/adaptive_governance.py
/backend/app/services/ads_connectors.py
/backend/app/services/adversarial_test_gen.py
/backend/app/services/agency.py
/backend/app/services/alerting.py
/backend/app/services/analytics_assistant.py
/backend/app/services/annotations.py
/backend/app/services/anomaly_fusion.py
/backend/app/services/audience_segments.py
/backend/app/services/audit.py
/backend/app/services/auto_responder.py
/backend/app/services/autonomous_loop.py
/backend/app/services/behavioral_cohorts.py
/backend/app/services/benchmarks.py
/backend/app/services/benchmarks_vertical.py
/backend/app/services/billing_sync.py
/backend/app/services/brand_voice.py
/backend/app/services/breach_notification.py
/backend/app/services/brief_engine.py
/backend/app/services/bugfix_pipeline.py
/backend/app/services/bugfix_prompt_grounding.py
/backend/app/services/candidate_scoring.py
/backend/app/services/causal_explainer.py
/backend/app/services/causal_intervention_engine.py
/backend/app/services/chat_voice.py
/backend/app/services/chatbot_llm_fallback.py
/backend/app/services/cig_engine.py
/backend/app/services/cohort_engine.py
/backend/app/services/community_marketplace.py
/backend/app/services/compliance_score.py
/backend/app/services/contextual_bandit.py
/backend/app/services/conversion_metrics.py
/backend/app/services/conversion_service.py
/backend/app/services/cross_pollination.py
/backend/app/services/customer_churn_scorer.py
/backend/app/services/dashboard_auto_remediation.py
/backend/app/services/dashboard_drift_scope.py
/backend/app/services/data_integrity_probe.py
/backend/app/services/data_retention.py
/backend/app/services/digest_formatter.py
/backend/app/services/email_deliverability.py
/backend/app/services/email_governance.py
/backend/app/services/email_journey.py
/backend/app/services/email_orchestrator.py
/backend/app/services/email_performance.py
/backend/app/services/email_templates.py
/backend/app/services/empirical_calibration.py
/backend/app/services/event_bus.py
/backend/app/services/event_emitter.py
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
/backend/app/services/feedback_intelligence.py
/backend/app/services/fix_holdout_measurement.py
/backend/app/services/followup_worker.py
/backend/app/services/gdpr_processor.py
/backend/app/services/gdpr_sla.py
/backend/app/services/goals.py
/backend/app/services/inbound_action_executor.py
/backend/app/services/inbound_email_processor.py
/backend/app/services/instant_onboarding.py
/backend/app/services/intelligence_report.py
/backend/app/services/invariant_monitor.py
/backend/app/services/klaviyo_connection.py
/backend/app/services/klaviyo_events.py
/backend/app/services/klaviyo_export.py
/backend/app/services/knowledge_graph.py
/backend/app/services/learning_isolation.py
/backend/app/services/lesson_gc.py
/backend/app/services/lighthouse_monitor.py
/backend/app/services/lite_morning_digest.py
/backend/app/services/llm_benchmark_monitor.py
/backend/app/services/llm_realmodel_drift.py
/backend/app/services/loop_health.py
/backend/app/services/ltv_engine.py
/backend/app/services/margin_guard.py
/backend/app/services/market_lookup_engine.py
/backend/app/services/measurement_health.py
/backend/app/services/merchant_chatbot.py
/backend/app/services/merchant_churn_predictor.py
/backend/app/services/merchant_digest.py
/backend/app/services/merchant_email_service.py
/backend/app/services/merchant_groups.py
/backend/app/services/merchant_privacy.py
/backend/app/services/merchant_scoring.py
/backend/app/services/merge_intelligence.py
/backend/app/services/meta_reviewer.py
/backend/app/services/model_config.py
/backend/app/services/model_upgrade_agent.py
/backend/app/services/monthly_evolution_audit.py
/backend/app/services/mta_engine.py
/backend/app/services/night_shift_agent.py
/backend/app/services/night_shift_calibration.py
/backend/app/services/nudge_composer.py
/backend/app/services/nudge_dna.py
/backend/app/services/nudge_engine.py
/backend/app/services/nudge_gating.py
/backend/app/services/nudge_measurement.py
/backend/app/services/nudge_optimizer.py
/backend/app/services/nudge_rank.py
/backend/app/services/observability_spikes.py
/backend/app/services/on_alert_responder.py
/backend/app/services/on_alert_triage_llm.py
/backend/app/services/onboarding.py
/backend/app/services/onboarding_funnel.py
/backend/app/services/onboarding_health.py
/backend/app/services/operator_prediction.py
/backend/app/services/opportunity_engine.py
/backend/app/services/orchestrator.py
/backend/app/services/orchestrator_context.py
/backend/app/services/orchestrator_llm.py
/backend/app/services/order_ingestion.py
/backend/app/services/outbound_webhooks.py
/backend/app/services/outcome_evaluator.py
/backend/app/services/p95_snapshot.py
/backend/app/services/pipeline_heartbeat.py
/backend/app/services/pipeline_self_upgrade.py
/backend/app/services/pnl_engine.py
/backend/app/services/prediction_log.py
/backend/app/services/price_intelligence_engine.py
/backend/app/services/price_radar_service.py
/backend/app/services/price_sensitivity.py
/backend/app/services/proactive_chat.py
/backend/app/services/probabilistic_forecast.py
/backend/app/services/product_intelligence_engine.py
/backend/app/services/project_brain.py
/backend/app/services/promotion_pipeline.py
/backend/app/services/proof_engine.py
/backend/app/services/refund_ingest.py
/backend/app/services/refund_loss.py
/backend/app/services/regulatory_feed_monitor.py
/backend/app/services/regulatory_watch.py
/backend/app/services/response_guardrails.py
/backend/app/services/revenue_at_risk.py
/backend/app/services/revenue_autopsy.py
/backend/app/services/revenue_forecast.py
/backend/app/services/revenue_genome.py
/backend/app/services/revenue_loss.py
/backend/app/services/revenue_metrics.py
/backend/app/services/reviewer_layer.py
/backend/app/services/risk_forecast.py
/backend/app/services/roi_report.py
/backend/app/services/rule_engine.py
/backend/app/services/rum_monitor.py
/backend/app/services/scaling_intelligence.py
/backend/app/services/scoring_calibration.py
/backend/app/services/security_heartbeat.py
/backend/app/services/security_preflight_guard.py
/backend/app/services/segment_compare.py
/backend/app/services/sentry_parser.py
/backend/app/services/sentry_triage.py
/backend/app/services/setup_audit.py
/backend/app/services/share_engine.py
/backend/app/services/shopify_admin.py
/backend/app/services/shopify_auth.py
/backend/app/services/shopify_cogs_sync.py
/backend/app/services/signal_text.py
/backend/app/services/signal_webhooks.py
/backend/app/services/silence_detector.py
/backend/app/services/simulation_engine.py
/backend/app/services/simulation_probe.py
/backend/app/services/sip_engine.py
/backend/app/services/slack_dispatcher.py
/backend/app/services/soc2_controls.py
/backend/app/services/spark_voice.py
/backend/app/services/store_context.py
/backend/app/services/store_insight_engine.py
/backend/app/services/storefront_preview.py
/backend/app/services/system_diagnostic.py
/backend/app/services/system_health_synthesizer.py
/backend/app/services/system_summary.py
/backend/app/services/team.py
/backend/app/services/telegram_agent.py
/backend/app/services/trust_contract.py
/backend/app/services/trust_outcome_measurement.py
/backend/app/services/uninstall_erasure.py
/backend/app/services/unique_product_engine.py
/backend/app/services/utm_attribution.py
/backend/app/services/vertical_classifier.py
/backend/app/services/vertical_prompt_pack.py
/backend/app/services/webhook_health.py
/backend/app/services/webhook_monitor.py
/backend/app/services/weekly_digest.py
/backend/app/services/worker_watchdog.py

### Models
/backend/app/models/__init__.py
/backend/app/models/action_approval.py
/backend/app/models/action_outcome.py
/backend/app/models/action_snapshot.py
/backend/app/models/action_task.py
/backend/app/models/active_model_config.py
/backend/app/models/active_nudge.py
/backend/app/models/ad_spend.py
/backend/app/models/agency.py
/backend/app/models/analytics_event.py
/backend/app/models/audit_log.py
/backend/app/models/autofix_promotion.py
/backend/app/models/autonomous_action.py
/backend/app/models/bugfix_candidate.py
/backend/app/models/cig.py
/backend/app/models/community_template.py
/backend/app/models/daily_brief.py
/backend/app/models/email_event.py
/backend/app/models/event.py
/backend/app/models/evolution_proposal.py
/backend/app/models/execution.py
/backend/app/models/gdpr_request.py
/backend/app/models/inbound_email.py
/backend/app/models/market_lookup.py
/backend/app/models/merchant.py
/backend/app/models/merchant_email.py
/backend/app/models/merchant_group.py
/backend/app/models/merchant_journey_state.py
/backend/app/models/merchant_rule.py
/backend/app/models/merge_outcome.py
/backend/app/models/meta_review.py
/backend/app/models/model_upgrade.py
/backend/app/models/night_shift_report.py
/backend/app/models/nudge_event.py
/backend/app/models/nudge_impression_daily.py
/backend/app/models/onboarding_event.py
/backend/app/models/opportunity_signal.py
/backend/app/models/ops_alert.py
/backend/app/models/outbound_webhook.py
/backend/app/models/patch_fingerprint.py
/backend/app/models/prediction_log.py
/backend/app/models/price_intelligence.py
/backend/app/models/price_watch.py
/backend/app/models/product.py
/backend/app/models/product_cost.py
/backend/app/models/product_metrics.py
/backend/app/models/product_opportunity.py
/backend/app/models/project_brain_snapshot.py
/backend/app/models/reviewer_assessment.py
/backend/app/models/scaling_recommendation.py
/backend/app/models/sentry_incident.py
/backend/app/models/share_event.py
/backend/app/models/shop_conversion_calibration.py
/backend/app/models/shop_cost_defaults.py
/backend/app/models/shop_order.py
/backend/app/models/store_intelligence_profile.py
/backend/app/models/store_metrics.py
/backend/app/models/support_incident.py
/backend/app/models/system_lesson.py
/backend/app/models/system_snapshot.py
/backend/app/models/trust_contract.py
/backend/app/models/unique_product_detection.py
/backend/app/models/visitor.py
/backend/app/models/visitor_product_state.py
/backend/app/models/visitor_purchase_session.py
/backend/app/models/wishlist_item.py
/backend/app/models/worker_log.py
/backend/app/models/worker_state.py

## FastAPI Routes
/backend/app/api/resend_webhooks.py :: @router.post("/inbound")
/backend/app/api/resend_webhooks.py :: @router.post("/events")
/backend/app/api/resend_webhooks.py :: @router.post("/merchant-inbound")
/backend/app/api/attribution.py :: @router.get("/sources")
/backend/app/api/attribution.py :: @router.get("/sources/pro")
/backend/app/api/attribution.py :: @router.get("/products")
/backend/app/api/attribution.py :: @router.get(
/backend/app/api/attribution.py :: @router.get(
/backend/app/api/tracker.py :: @router.get("/tracker.js")
/backend/app/api/tracker.py :: @router.get("/attribution.js")
/backend/app/api/signal_webhooks.py :: @router.get(
/backend/app/api/signal_webhooks.py :: @router.post(
/backend/app/api/signal_webhooks.py :: @router.delete("/pro/signal-webhooks/{webhook_id}", response_model=OkResponse)
/backend/app/api/signal_webhooks.py :: @router.post("/pro/signal-webhooks/{webhook_id}/test", response_model=WebhookTestResponse)
/backend/app/api/session_replay.py :: @router.get(
/backend/app/api/source_quality.py :: @router.get("/source-quality")
/backend/app/api/source_quality.py :: @router.get("/source-quality/pro")
/backend/app/api/merchant_slack.py :: @router.get("/status", response_model=SlackStatusResponse)
/backend/app/api/merchant_slack.py :: @router.post("/connect", response_model=SlackConnectResponse)
/backend/app/api/merchant_slack.py :: @router.post("/test", response_model=SlackConnectResponse)
/backend/app/api/merchant_slack.py :: @router.delete("", response_model=SlackStatusResponse)
/backend/app/api/merchant_slack.py :: @router.get("/oauth/authorize", include_in_schema=False)
/backend/app/api/merchant_slack.py :: @router.get("/oauth/callback", include_in_schema=False)
/backend/app/api/segments.py :: @router.get(
/backend/app/api/nudge_dna.py :: @router.get("/nudge-dna", response_model=NudgeDnaResponse)
/backend/app/api/nudge_dna.py :: @router.post("/nudge-dna/refresh", response_model=NudgeDnaResponse)
/backend/app/api/refund_loss.py :: @router.get(
/backend/app/api/public_events.py :: @router.post("/events")
/backend/app/api/public_roi_counter.py :: @router.get("/public/roi-counter")
/backend/app/api/public_roi_counter.py :: @router.get("/public/roi-counter/live")
/backend/app/api/opportunities.py :: @router.get("/opportunities")
/backend/app/api/opportunities.py :: @router.get("/opportunities/pro")
/backend/app/api/opportunities.py :: @router.get("/opportunities/top")
/backend/app/api/outbound_webhooks.py :: @router.post("/pro/webhooks/subscriptions", response_model=SubscriptionOut)
/backend/app/api/outbound_webhooks.py :: @router.get("/pro/webhooks/subscriptions", response_model=SubscriptionListResponse)
/backend/app/api/outbound_webhooks.py :: @router.patch("/pro/webhooks/subscriptions/{sub_id}", response_model=SubscriptionOut)
/backend/app/api/outbound_webhooks.py :: @router.delete("/pro/webhooks/subscriptions/{sub_id}", response_model=OkResponse)
/backend/app/api/outbound_webhooks.py :: @router.get("/pro/webhooks/deliveries", response_model=DeliveriesListResponse)
/backend/app/api/outbound_webhooks.py :: @router.post("/pro/webhooks/deliveries/{delivery_id}/replay", response_model=ReplayResponse)
/backend/app/api/legal_pages.py :: @router.get("/legal/privacy")
/backend/app/api/legal_pages.py :: @router.get("/legal/cookies")
/backend/app/api/legal_pages.py :: @router.get("/privacy-policy", response_class=HTMLResponse)
/backend/app/api/legal_pages.py :: @router.get("/cookie-policy", response_class=HTMLResponse)
/backend/app/api/product_metrics.py :: @router.get("/metrics", response_model=ProductMetricsResponse)
/backend/app/api/revenue_autopsy.py :: @router.get("/pro/revenue-autopsy", response_model=RevenueAutopsyResponse)
/backend/app/api/frontend_errors.py :: @router.post(
/backend/app/api/roi_hero.py :: @router.get("/roi-hero", response_model=ROIHeroResponse)
/backend/app/api/store_intelligence.py :: @router.get("/store-intelligence", response_model=StoreIntelligenceResponse)
/backend/app/api/onboarding.py :: @router.post("/event")
/backend/app/api/counterfactual.py :: @router.get("/pro/counterfactual/signals", response_model=CounterfactualListResponse)
/backend/app/api/counterfactual.py :: @router.get("/pro/counterfactual/signals/{signal_id}", response_model=CounterfactualEntry)
/backend/app/api/public_proofs.py :: @router.get("/public/proof/{token}")
/backend/app/api/public_proofs.py :: @router.post("/public/proof/{token}/event")
/backend/app/api/public_proofs.py :: @router.post("/pro/shares", response_model=CreateShareResponse)
/backend/app/api/public_proofs.py :: @router.get("/pro/shares", response_model=list[ShareRow])
/backend/app/api/knowledge_graph.py :: @router.post("/pro/kg/query", response_model=KGQueryResponse)
/backend/app/api/knowledge_graph.py :: @router.get("/pro/kg/stats", response_model=KGStatsResponse)
/backend/app/api/click_insights.py :: @router.get(
/backend/app/api/nudge_events.py :: @router.post("/nudge/event")
/backend/app/api/public_status.py :: @router.get("/public/status")
/backend/app/api/ops.py :: @router.get("/readiness/orchestrator")
/backend/app/api/ops.py :: @router.get("/llm-budget")
/backend/app/api/ops.py :: @router.get("/dashboard-health")
/backend/app/api/ops.py :: @router.get("/email-health")
/backend/app/api/ops.py :: @router.get("/silent-fallback")
/backend/app/api/ops.py :: @router.get("/compliance")
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
/backend/app/api/ops.py :: @router.get("/journey")
/backend/app/api/ops.py :: @router.get("/journey/stats")
/backend/app/api/ops.py :: @router.get("/email-events")
/backend/app/api/ops.py :: @router.get("/email-events/stats")
/backend/app/api/ops.py :: @router.get("/merchant-scores")
/backend/app/api/ops.py :: @router.get("/merchant/{shop_domain}/score")
/backend/app/api/ops.py :: @router.get("/feedback/themes")
/backend/app/api/ops.py :: @router.get("/merchant/{shop_domain}/profile")
/backend/app/api/ops.py :: @router.get("/inbound-emails")
/backend/app/api/ops.py :: @router.get("/merchant/{shop_domain}/email-trace")
/backend/app/api/ops.py :: @router.get("/incidents")
/backend/app/api/ops.py :: @router.get("/incidents/{incident_id}")
/backend/app/api/ops.py :: @router.get("/incidents/{incident_id}/family")
/backend/app/api/ops.py :: @router.get("/incidents/triage/queue")
/backend/app/api/ops.py :: @router.get("/incidents/parse-errors")
/backend/app/api/ops.py :: @router.get("/incidents/consumer/stats")
/backend/app/api/ops.py :: @router.get("/simulation/status")
/backend/app/api/ops.py :: @router.get("/pipeline-health")
/backend/app/api/ops.py :: @router.post("/force-logout")
/backend/app/api/live_opportunities.py :: @router.get("/live-opportunities")
/backend/app/api/telegram_webhook.py :: @router.post("/webhook")
/backend/app/api/agency.py :: @router.post("/agency/register")
/backend/app/api/agency.py :: @router.post("/agency/clients")
/backend/app/api/agency.py :: @router.get("/agency/clients")
/backend/app/api/agency.py :: @router.delete("/agency/clients/{shop_domain}")
/backend/app/api/agency.py :: @router.get("/agency/dashboard")
/backend/app/api/pnl.py :: @router.get(
/backend/app/api/actions.py :: @router.get("/candidates/pro")
/backend/app/api/live_visitors.py :: @router.get("/visitors")
/backend/app/api/proof_report.py :: @router.get(
/backend/app/api/market_lookup.py :: @router.get("/market-lookup/top")
/backend/app/api/price_sensitivity.py :: @router.get("/pro/price-sensitivity", response_model=PriceSensitivityResponse)
/backend/app/api/conversion_probability.py :: @router.get("/top")
/backend/app/api/brief.py :: @router.get("/today")
/backend/app/api/brief.py :: @router.get("/today/pro")
/backend/app/api/cohorts.py :: @router.get(
/backend/app/api/cohorts.py :: @router.get(
/backend/app/api/cohorts.py :: @router.get(
/backend/app/api/cohorts.py :: @router.get(
/backend/app/api/cohorts.py :: @router.get(
/backend/app/api/cohorts.py :: @router.get(
/backend/app/api/cohorts.py :: @router.get(
/backend/app/api/billing.py :: @router.post("/subscribe")
/backend/app/api/billing.py :: @router.get("/callback")
/backend/app/api/rum.py :: @router.post("/metric", status_code=status.HTTP_202_ACCEPTED)
/backend/app/api/night_shift.py :: @router.get("/pro/night-shift/latest", response_model=NightShiftReport)
/backend/app/api/night_shift.py :: @router.post("/pro/night-shift/run", response_model=NightShiftReport)
/backend/app/api/night_shift.py :: @router.get("/pro/night-shift/timeline", response_model=TimelineResponse)
/backend/app/api/night_shift.py :: @router.post("/pro/night-shift/apply", response_model=ApplyActionResponse)
/backend/app/api/track_purchase.py :: @router.post("/track/purchase-confirmed")
/backend/app/api/live_alerts.py :: @router.get(
/backend/app/api/live_alerts.py :: @router.get(
/backend/app/api/trust_contracts.py :: @router.get("/contracts", response_model=list[TrustContractResponse])
/backend/app/api/trust_contracts.py :: @router.post("/contracts", response_model=TrustContractResponse, status_code=201)
/backend/app/api/trust_contracts.py :: @router.patch("/contracts/{contract_id}", response_model=TrustContractResponse)
/backend/app/api/trust_contracts.py :: @router.delete("/contracts/{contract_id}", response_model=TrustContractResponse)
/backend/app/api/trust_contracts.py :: @router.post("/autopilot", response_model=AutopilotResponse)
/backend/app/api/trust_contracts.py :: @router.post("/panic", response_model=PanicResponse)
/backend/app/api/trust_contracts.py :: @router.get("/executions", response_model=list[TrustExecutionResponse])
/backend/app/api/trust_contracts.py :: @router.get("/summary", response_model=TrustSummaryResponse)
/backend/app/api/causal_explainer.py :: @router.get("/pro/causal/explain", response_model=CausalExplainResponse)
/backend/app/api/decision_engine.py :: @router.post("/infer")
/backend/app/api/realtime_stream.py :: @router.get("/pro/stream/dashboard", include_in_schema=False)
/backend/app/api/sentry_webhooks.py :: @router.post("/inbound")
/backend/app/api/mta.py :: @router.get("/mta", response_model=MtaResponse)
/backend/app/api/mta.py :: @router.get("/mta/compare", response_model=MtaCompareResponse)
/backend/app/api/goals.py :: @router.get(
/backend/app/api/goals.py :: @router.post(
/backend/app/api/goals.py :: @router.delete("/pro/goals/{metric}", response_model=OkResponse)
/backend/app/api/goals.py :: @router.get(
/backend/app/api/customer_churn.py :: @router.get("/customer-churn")
/backend/app/api/anomaly_fusion.py :: @router.get("/pro/anomalies/fusion", response_model=AnomalyFusionResponse)
/backend/app/api/top_pages.py :: @router.get(
/backend/app/api/merchant_privacy.py :: @router.get("/privacy/preferences")
/backend/app/api/merchant_privacy.py :: @router.patch("/me")
/backend/app/api/merchant_privacy.py :: @router.post("/object")
/backend/app/api/merchant_privacy.py :: @router.post("/unobject")
/backend/app/api/public_transparency.py :: @router.get("/public/transparency")
/backend/app/api/revenue_genome.py :: @router.get("/pro/revenue-genome", response_model=RevenueGenomeResponse)
/backend/app/api/segment_compare.py :: @router.get(
/backend/app/api/slo_api.py :: @router.get("/ops/slo")
/backend/app/api/slo_api.py :: @router.get("/ops/slo/{name}")
/backend/app/api/slo_api.py :: @router.get("/ops/slo/routes/inspect")
/backend/app/api/klaviyo.py :: @router.get("/segment")
/backend/app/api/klaviyo.py :: @router.post("/push")
/backend/app/api/benchmarks.py :: @router.get(
/backend/app/api/benchmarks.py :: @router.get(
/backend/app/api/analytics_assistant.py :: @router.post("/chat/analytics", response_model=AnalyticsAskResponse)
/backend/app/api/funnel.py :: @router.get(
/backend/app/api/chat_support.py :: @router.post("/support", response_model=ChatResponseSchema)
/backend/app/api/chat_support.py :: @router.get("/support/history")
/backend/app/api/chat_support.py :: @router.patch("/support/incidents/{incident_id}/resolve")
/backend/app/api/chat_support.py :: @router.get("/support/resolutions")
/backend/app/api/chat_support.py :: @router.get("/support/proactive")
/backend/app/api/chat_support.py :: @router.post("/support/proactive/{message_id}/ack")
/backend/app/api/chat_support.py :: @router.post("/support/resolutions/{incident_id}/ack")
/backend/app/api/daily_narrative.py :: @router.get("/daily-narrative", response_model=DailyNarrativeResponse)
/backend/app/api/visitor_journeys.py :: @router.get("/visitor-journeys", response_model=VisitorJourneysResponse)
/backend/app/api/nudge_script.py :: @router.get("/nudge.js")
/backend/app/api/nudge_script.py :: @router.get("/tracker.js")
/backend/app/api/shopify_oauth.py :: @router.get("/install")
/backend/app/api/shopify_oauth.py :: @router.get("/callback")
/backend/app/api/shopify_oauth.py :: @router.get("/detect")
/backend/app/api/shopify_oauth.py :: @router.get("/session")
/backend/app/api/dashboard.py :: @router.get("/overview")
/backend/app/api/dashboard.py :: @router.get("/intelligence")
/backend/app/api/dashboard.py :: @router.get("/overview/pro")
/backend/app/api/merchant_export.py :: @router.get("/export")
/backend/app/api/team.py :: @router.get(
/backend/app/api/team.py :: @router.post(
/backend/app/api/team.py :: @router.delete("/pro/team/members/{member_id}", response_model=OkResponse)
/backend/app/api/integrations.py :: @router.get("", response_model=IntegrationsResponse)
/backend/app/api/integrations.py :: @router.put("/klaviyo", response_model=KlaviyoConnectionResponse)
/backend/app/api/integrations.py :: @router.post("/klaviyo/test", response_model=KlaviyoTestResponse)
/backend/app/api/integrations.py :: @router.delete("/klaviyo", response_model=KlaviyoConnectionResponse)
/backend/app/api/causal_lift.py :: @router.get("/pro/causal-lift", response_model=CausalLiftResponse)
/backend/app/api/causal_lift.py :: @router.get("/pro/recommendation-impact", response_model=RecommendationImpactResponse)
/backend/app/api/shopify_refunds.py :: @router.post("/shopify/refunds")
/backend/app/api/merchant_groups.py :: @router.post("/pro/groups", response_model=GroupCreateResponse)
/backend/app/api/merchant_groups.py :: @router.get("/pro/groups", response_model=GroupListResponse)
/backend/app/api/merchant_groups.py :: @router.post("/pro/groups/{group_id}/members", response_model=MemberAddResponse)
/backend/app/api/merchant_groups.py :: @router.delete("/pro/groups/{group_id}/members/{shop_domain}", response_model=OkResponse)
/backend/app/api/merchant_groups.py :: @router.get("/pro/groups/{group_id}/dashboard", response_model=GroupDashboardResponse)
/backend/app/api/revenue_radar.py :: @router.get("/top")
/backend/app/api/visitor_scores.py :: @router.get("/visitor-scores")
/backend/app/api/visitor_scores.py :: @router.get("/visitor-intent-classification", response_model=VisitorIntentCounts)
/backend/app/api/ads.py :: @router.get("/pro/ads/networks", response_model=AdsNetworksResponse)
/backend/app/api/ads.py :: @router.get("/pro/ads/connections", response_model=AdsConnectionsResponse)
/backend/app/api/ads.py :: @router.post("/pro/ads/connect", response_model=AdsConnectResponse)
/backend/app/api/ads.py :: @router.delete("/pro/ads/connect/{network}", response_model=OkResponse)
/backend/app/api/ads.py :: @router.post("/pro/ads/sync", response_model=AdsSyncResponse)
/backend/app/api/ads.py :: @router.get("/pro/ads/spend", response_model=AdsSpendResponse)
/backend/app/api/feature_usage_api.py :: @router.get("/ops/features")
/backend/app/api/feature_usage_api.py :: @router.get("/ops/features/dormant")
/backend/app/api/merchant.py :: @router.get(
/backend/app/api/merchant.py :: @router.get(
/backend/app/api/merchant.py :: @router.get("/activation")
/backend/app/api/lift.py :: @router.get(
/backend/app/api/playbook.py :: @router.get("/pro/playbook/{signal_type}", response_model=PlaybookResponse)
/backend/app/api/webhooks.py :: @router.post("/shopify/orders")
/backend/app/api/webhooks.py :: @router.post("/shopify/orders-created")
/backend/app/api/webhooks.py :: @router.post("/shopify/orders-paid")
/backend/app/api/webhooks.py :: @router.post("/shopify/app-uninstalled")
/backend/app/api/webhooks.py :: @router.post("/shopify/customers-redact")
/backend/app/api/webhooks.py :: @router.post("/shopify/customers-data-request")
/backend/app/api/webhooks.py :: @router.post("/shopify/shop-redact")
/backend/app/api/abandoned_intent.py :: @router.get("/pro/abandoned-intent", response_model=AbandonedIntentResponse)
/backend/app/api/price_intelligence.py :: @router.get("/price-intelligence/top")
/backend/app/api/price_intelligence.py :: @router.post("/price-radar")
/backend/app/api/product_trend.py :: @router.get("/trend", response_model=ProductTrendResponse)
/backend/app/api/merchant_churn.py :: @router.get("/ops/churn-report")
/backend/app/api/merchant_churn.py :: @router.get("/ops/churn-score/{shop_domain}")
/backend/app/api/consent_banner.py :: @router.get("/consent-banner.js")
/backend/app/api/orders.py :: @router.get(
/backend/app/api/orders.py :: @router.get(
/backend/app/api/orders.py :: @router.get(
/backend/app/api/orders.py :: @router.get(
/backend/app/api/anomaly_replay.py :: @router.get("/pro/anomalies/{pattern}/replay", response_model=AnomalyReplayResponse)
/backend/app/api/heatmap.py :: @router.get(
/backend/app/api/heatmap.py :: @router.get(
/backend/app/api/auth_posture.py :: @router.get("/ops/auth/posture")
/backend/app/api/compliance_evidence.py :: @router.get("/soc2")
/backend/app/api/compliance_evidence.py :: @router.get("/evidence")
/backend/app/api/auth.py :: @router.get("/install")
/backend/app/api/auth.py :: @router.get("/auth/callback")
/backend/app/api/events.py :: @router.post("/track-event")
/backend/app/api/community_marketplace.py :: @router.get("/pro/marketplace/templates", response_model=MarketplaceTemplatesListResponse)
/backend/app/api/community_marketplace.py :: @router.post("/pro/marketplace/templates", response_model=PublishResponse)
/backend/app/api/community_marketplace.py :: @router.post("/pro/marketplace/templates/{template_id}/clone", response_model=CloneResponse)
/backend/app/api/community_marketplace.py :: @router.post("/pro/marketplace/templates/{template_id}/upvote", response_model=OkResponse)
/backend/app/api/community_marketplace.py :: @router.delete("/pro/marketplace/templates/{template_id}", response_model=OkResponse)
/backend/app/api/cost_config.py :: @router.get(
/backend/app/api/cost_config.py :: @router.patch(
/backend/app/api/cost_config.py :: @router.get(
/backend/app/api/cost_config.py :: @router.post(
/backend/app/api/cost_config.py :: @router.post(
/backend/app/api/ai_actions.py :: @router.get("/actions")
/backend/app/api/instant_intelligence.py :: @router.get("/instant-intelligence", response_model=InstantIntelligenceResponse)
/backend/app/api/instant_intelligence.py :: @router.post("/instant-intelligence/refresh", response_model=InstantIntelligenceResponse)
/backend/app/api/shopify_flow_schema.py :: @router.get("/shopify-flow/schema")
/backend/app/api/roi_report.py :: @router.get(
/backend/app/api/weekly_trend.py :: @router.get(
/backend/app/api/cac_ltv.py :: @router.get("/cac-ltv", response_model=CacLtvResponse)
/backend/app/api/margin_guard_api.py :: @router.get("/snapshot", response_model=MarginSnapshot)
/backend/app/api/margin_guard_api.py :: @router.get("/check", response_model=MarginCheckResponse)
/backend/app/api/feature_flags_admin.py :: @router.get("/ops/flags")
/backend/app/api/feature_flags_admin.py :: @router.get("/ops/flags/{name}")
/backend/app/api/feature_flags_admin.py :: @router.post("/ops/flags/{name}")
/backend/app/api/benchmarks_vertical.py :: @router.get("/pro/benchmarks/vertical", response_model=VerticalBenchmarkResponse)
/backend/app/api/benchmarks_vertical.py :: @router.get(
/backend/app/api/benchmarks_vertical.py :: @router.get("/pro/vertical", response_model=VerticalSelfResponse)
/backend/app/api/benchmarks_vertical.py :: @router.get("/ops/benchmarks/pool")
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
/backend/app/api/action_tasks.py :: @router.get(
/backend/app/api/action_tasks.py :: @router.patch("/tasks/{task_id}")
/backend/app/api/action_tasks.py :: @router.post("/tasks/{task_id}/release")
/backend/app/api/action_tasks.py :: @router.get("/tasks/{task_id}")
/backend/app/api/action_tasks.py :: @router.get(
/backend/app/api/revenue_at_risk.py :: @router.get(
/backend/app/api/tracker_error.py :: @router.post("/public/tracker-error")
/backend/app/api/risk_forecast.py :: @router.get("/pro/risk-forecast", response_model=RiskForecastResponse)
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
/backend/app/api/nudges.py :: @router.get(
/backend/app/api/nudges.py :: @router.post("/pro/nudges", response_model=ComposeNudgeResponse)
/backend/app/api/nudges.py :: @router.get("/pro/nudges/rank", response_model=NudgeRankResponse)
/backend/app/api/nudges.py :: @router.get(
/backend/app/api/nudges.py :: @router.patch("/pro/nudges/{nudge_id}/holdout", response_model=HoldoutUpdateResponse)
/backend/app/api/nudges.py :: @router.delete("/pro/nudges/{nudge_id}", response_model=DeactivateNudgeResponse)
/backend/app/api/annotations.py :: @router.get(
/backend/app/api/annotations.py :: @router.post(
/backend/app/api/annotations.py :: @router.delete("/pro/annotations/{annotation_id}", response_model=OkResponse)
/backend/app/api/intent.py :: @router.get("/intent/top-hot")
/backend/app/api/intent.py :: @router.get("/intent/visitor/{visitor_id}")
/backend/app/api/intent.py :: @router.get("/intent/summary")
/backend/app/api/intent.py :: @router.get("/intent/products/top")
/backend/app/api/intent.py :: @router.get("/intent/products/opportunities")
/backend/app/api/merchant_rules.py :: @router.get("/catalog")
/backend/app/api/merchant_rules.py :: @router.get("", response_model=list[RuleResponse])
/backend/app/api/merchant_rules.py :: @router.post("", response_model=RuleResponse, status_code=201)
/backend/app/api/merchant_rules.py :: @router.patch("/{rule_id}", response_model=RuleResponse)
/backend/app/api/merchant_rules.py :: @router.delete("/{rule_id}")
/backend/app/api/prediction_accuracy.py :: @router.get("/prediction-accuracy", response_model=PredictionAccuracyResponse)
/backend/app/api/ops_email_preview.py :: @router.get("/preview", include_in_schema=False)
/backend/app/api/setup.py :: @router.get("/status")
/backend/app/api/setup.py :: @router.get("/attribution-snippet")
/backend/app/api/setup.py :: @router.get("/pixel-status")
/backend/app/api/setup.py :: @router.post("/repair/webhook")
/backend/app/api/setup.py :: @router.post("/repair/tracker")
/backend/app/api/forecasts.py :: @router.get("/revenue")
/backend/app/api/forecasts.py :: @router.get("/churn", response_model=ChurnForecastResponse)
/backend/app/api/health.py :: @router.get("/system/health")
/backend/app/api/health.py :: @router.get(
/backend/app/api/health.py :: @router.get("/ops/signal-count-week")
/backend/app/api/lite_export.py :: @router.get(
/backend/app/api/storefront_preview.py :: @router.post("/public/preview")

## Dashboard Routes
/
/agency
/app
/app/groups
/app/intelligence
/app/lite
/app/marketplace
/app/operations
/app/pro
/app/scale
/app/settings
/app/settings/cost-defaults
/app/settings/costs
/app/settings/currency
/app/settings/klaviyo
/app/settings/privacy
/app/settings/slack
/app/settings/team
/app/settings/webhooks
/cookies
/install
/pricing
/privacy
/proof
/status
/terms
/transparency

## Architecture Documents
/docs/AUTO_CONTEXT.md
/docs/BREACH_RESPONSE.md
/docs/DPIA.md
/docs/HARDENING_ROADMAP_POST_BACKEND.md
/docs/HEDGESPARK_MERCHANT_COHERENCE_SPEC.md
/docs/LITE_VISUAL_SPEC.md
/docs/RESEND_DNS_RUNBOOK.md
/docs/processors.md
/docs/reality_scheduled_jobs.md

## Notes
This file is automatically generated by context_builder.py
Used by AI agents to understand server architecture.
