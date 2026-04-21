# /app/lite — Light palette direction (boutique/colonna)

**Status:** founder-locked 2026-04-21 ("B" — bg card chiaro crema su
canvas dark). Applies to Lite v4 sections ONLY. Cassettoni grid stays
dark (founder: "mantieni la visual dei cassettoni grandi con numeri").
Pro/Scale surfaces unaffected.

## Mood reference

Colonna photo (`/opt/wishspark/screenshots/WhatsApp Image 2026-04-21 at
19.49.12.jpeg`) — soft-ceramic pastel boutique object. Cream dominant,
lilac accent, peach decoration. No saturation urgency, no cartoon, no
mascot.

## Tokens

Because cards are now LIGHT on a DARK canvas, the pastels must be
saturated enough to coexist with cream, and text flips to dark slate-
purple for readability.

| Token | Hex | Role |
|---|---|---|
| `--lite-card-bg` | `#FAF5EB` | dominant light card background |
| `--lite-card-bg-soft` | `#F5EDD8` | subtle alt row / inner region |
| `--lite-card-border` | `#BCA8D9` | lilac saturated card border |
| `--lite-ink-primary` | `#2A2438` | dark purple-slate headlines/numbers |
| `--lite-ink-secondary` | `#5C5478` | body copy |
| `--lite-ink-muted` | `#8B85A0` | metadata |
| `--lite-lilac` | `#9177B8` | accent strokes, eyebrows, peer-median |
| `--lite-lilac-soft` | `#C5B5DB` | rings tracks, secondary strokes |
| `--lite-peach` | `#E88A6E` | warm highlights, "you" fills, ornaments |
| `--lite-good` | `#6FC5A0` | semantic positive (top_decile/top_quartile) |
| `--lite-problem` | `#E88888` | semantic problem (below_median) |
| `--lite-stall` | `#D4B342` | semantic stall/waiting (above_median) |

## Rules

1. **Cards: light bg on dark canvas.** Lite main canvas stays
   `#07070f`; Lite sections become cream islands.
2. **Cassettoni grid (LiteCassettoniGrid) stays DARK.** Does not
   convert — founder explicit.
3. **Charts are ADDED next to copy, not replacing it.** No rewrite of
   structure/data/behavior. Augmentation only.
4. **No radar — LiveRadarMap (geo-radar) already exists in v4.** Pick
   different chart primitives per section.
5. **Pastels saturated, not faded.** On a cream bg they must read;
   don't use 3-8% opacity tricks (those were dark-bg hacks).

## Implementation order

1. **Pilot — PeerBenchmarksCard** (commit α): full light-card rebuild
   + concentric rings chart (Apple-Watch-style but boutique). Founder
   review before propagation.
2. Pareto/P&L waterfall → light-palette + mini area chart as caption
3. ChannelAttribution → light-palette + horizontal bar chart
4. CohortSummary → light-palette + mini sparklines per cohort

Each step is one atomic commit with founder review before the next.
