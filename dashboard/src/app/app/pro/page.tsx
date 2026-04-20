// Pro floor — re-exports /app/intelligence content at /app/pro for
// canonical tier-named URL. `/app/intelligence` redirects here via
// next.config.ts. Physical folder move deferred to avoid import-path
// churn in this naming sprint.
export { default } from "../intelligence/page";
