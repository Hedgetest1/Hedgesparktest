# /app/_components — dashboard split target

The `app/page.tsx` file is 6400+ lines and has become a growth-debt
liability. This directory is the target for the incremental split.

## Split rules

1. **Extract pure components first.** Anything without app-state
   coupling (helpers, atoms, skeleton cards) is the safest first move.
2. **Extract by size × isolation.** The best candidates are big
   self-contained sections with few external props.
3. **Never batch extract.** One component per PR so reviewers can
   verify the build still green-paths.
4. **Type imports, not prop drilling.** Extracted components take
   typed props. No `any`, no stringly-typed selectors.
5. **Test the golden path** after each extraction — run
   `npx next build` and manually load `/app` in a browser before commit.

## Priority queue

1. ✅ `CountUp` — extracted 2026-04-13 (first step, proves pattern)
2. `KpiInsightModal` (1064-1418) — 355 lines, self-contained dialog
3. `ProductInsightPanel` (1582-1867) — 285 lines
4. `LiveRadarMap` (534-767) — 234 lines, uses props heavily but isolated
5. `FunnelVisualization` (961-1064) — 103 lines
6. `KpiCard` + `KpiSkeleton` — pairs of small atoms
7. `TrafficSourceBox` — isolated

After queue 1-7, `page.tsx` should drop below 5000 lines. Then reconsider
whether the remaining orchestration belongs in one file or should split
by tab (OverviewTab, IntelligenceTab, etc.).

## Anti-patterns

- Do NOT extract into nested `sections/intelligence/cards/causal/`
  hierarchies. Flat is fine — the page is a dashboard, not a library.
- Do NOT introduce barrel files (`index.ts` re-exports). They obscure
  what's where and slow the bundler.
- Do NOT change component APIs during extraction. First move, then
  refactor — in separate commits.
