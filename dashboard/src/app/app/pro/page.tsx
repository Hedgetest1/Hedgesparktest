// Pro floor — canonical URL /app/pro.
//
// Re-exports /app/page.tsx (the main dashboard) so the Pro floor
// shares the same shell (Sidebar + TopBar + auth) and data-fetching
// as the Lite floor. The page-body filters sections by pathname:
//   - On /app/lite → 7 Lite features + Live Radar only
//   - On /app/pro  → 5 migrated Intelligence cards (RecommendationImpact,
//     ChurnForecast, RiskForecast, CohortSummary, NudgeActionQueue) at
//     the top, then the rich Pro sections (AudienceSegments,
//     NudgePerformance, LiftReport, ProIntelligenceSection / Deep
//     analytics, BehavioralIntelligenceSection / Behavioral DNA, etc).
//
// Legacy `/app/intelligence/page.tsx` is retained but no longer
// linked — its only surviving callers are the 308 redirect in
// next.config.ts which now routes to /app/pro. Cat-5 deletion when
// the redirect window closes (bookmarks stop resolving).
export { default } from "../page";
