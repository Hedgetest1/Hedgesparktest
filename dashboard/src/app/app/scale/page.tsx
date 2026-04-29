// Scale floor — re-exports the main /app/page.tsx so Scale-tier
// merchants see the FULL dashboard (including the 12 Northbeam-class
// moats migrated 2026-04-29: Causal Lift+Why, MTA Compare, Anomaly
// Fusion+Replay, Counterfactual, Competitor Playbook, Revenue
// Autopsy+Genome, Nudge DNA, Lift Report, Night Shift+Timeline) on
// the Scale floor. The shared page.tsx detects `pathname === "/app/scale"`
// via `isScaleFloor` and gates those moats with `isScaleUser`.
//
// Pre-2026-04-29 this re-exported a static "operations" feature list
// from `../operations/page.tsx`. That bypassed the merchant's real
// dashboard and bounced sessions through FloorLayout's stale auth
// path. Re-exporting page.tsx keeps the Scale floor on the same
// session + render pipeline as Lite/Pro — single source of truth.
export { default } from "../page";
