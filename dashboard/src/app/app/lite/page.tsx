// Lite floor — re-exports the /app content at /app/lite for canonical
// tier-named URL. `/app` redirects here via next.config.ts rewrites.
// Physical file move deferred to a follow-up commit to avoid touching
// the 3500-line /app/page.tsx in this naming sprint.
export { default } from "../page";
