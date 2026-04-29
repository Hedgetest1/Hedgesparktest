"use client";

/**
 * VisitorIntentExplorerCard — Pro-floor moat exploration.
 *
 * Restores the rich exploration pattern that lived on the Lite floor
 * pre-2026-04-29 (mechanics + stakes + donut + key metrics + methodology
 * + primary action + supporting actions). When Visitor Intent moved to
 * Pro per the strict $0-70 parity rule (no $0-60 competitor ships per-
 * visitor classification — Glew $79 is the closest analog), the
 * intermediate Pro UI was a simpler 3-pill card. Founder mandate
 * 2026-04-29: Pro must be top-1-in-the-world deep, not a downgrade
 * from the old Lite cassettone. This is the restoration.
 *
 * Three sections:
 *   1. What you're seeing — title + subtitle + mechanics + stakes
 *   2. The data — hero stat (dominant segment) + donut Hot/Warm/Cold
 *      + key metrics + methodology (formula + thresholds + note)
 *   3. Your next moves — primary action (data-driven, 5 cases) +
 *      supporting actions
 *
 * All copy + thresholds + segment math identical to the original Lite
 * cassettone PANEL_CONFIG entry. Donut + EmptyPreview helpers are
 * inlined so the component is self-contained (no shared-state import
 * from LiteCassettoniGrid).
 */

import { CardSkeleton, CardError, useCardFetch } from "./_CardStates";
import type { components } from "../lib/api-types";

type VisitorIntentCounts = components["schemas"]["VisitorIntentCounts"];

const ACCENT = {
  eyebrow: "#f87171",
  hero: "#fb7185",
  bg: "rgba(248,113,113,0.08)",
  border: "rgba(248,113,113,0.25)",
};

type DonutSegment = { label: string; value: number; color: string };

type SupportingAction = {
  label: string;
  description: string;
  href?: string;
  hrefLabel?: string;
};

type PrimaryAction = {
  headline: string;
  label: string;
  description: string;
  href?: string;
  hrefLabel?: string;
};

export function VisitorIntentExplorerCard({
  apiBase,
  shop,
}: {
  apiBase: string;
  shop: string;
}) {
  const { data, state, retry } = useCardFetch<VisitorIntentCounts>({
    url: `${apiBase}/analytics/visitor-intent-classification`,
    enabled: !!apiBase && !!shop,
    isEmpty: () => false,
    component: "VisitorIntentExplorerCard",
  });

  if (state === "loading") {
    return <CardSkeleton label="Loading visitor intent" />;
  }

  if (state === "error") {
    return (
      <CardError
        label="Visitor intent unavailable"
        message="We couldn't load visitor intent right now. Your tracker is still capturing events — this card will recover on the next cycle."
        onRetry={retry}
      />
    );
  }

  const hot = data?.hot_visitors ?? 0;
  const warm = data?.warm_visitors ?? 0;
  const cold = data?.cold_visitors ?? 0;
  const total = data?.total_visitors ?? 0;

  const heroStat = computeHeroStat(hot, warm, cold, total);
  const donutSegments = total > 0
    ? [
        { label: "Hot", value: hot, color: "#f87171" },
        { label: "Warm", value: warm, color: "#a78bfa" },
        { label: "Cold", value: cold, color: "#94a3b8" },
      ] satisfies DonutSegment[]
    : null;
  const keyMetrics = [
    { label: "Hot visitors", value: `${hot.toLocaleString()}`, color: hot > 0 ? "#f87171" : undefined },
    { label: "Warm visitors", value: `${warm.toLocaleString()}`, color: warm > 0 ? "#a78bfa" : undefined },
    { label: "Cold visitors", value: `${cold.toLocaleString()}`, color: cold > 0 ? "#94a3b8" : undefined },
    { label: "Total scored", value: `${total.toLocaleString()}` },
  ];
  const methodologyInputs: Array<{ label: string; value: string }> = [];
  if (data?.hot_threshold !== undefined) {
    methodologyInputs.push({ label: "Hot threshold", value: `> ${data.hot_threshold}` });
  }
  if (data?.warm_threshold !== undefined) {
    methodologyInputs.push({ label: "Warm threshold", value: `> ${data.warm_threshold}` });
  }
  methodologyInputs.push({ label: "Total visitors scored", value: total.toLocaleString() });

  const primaryAction = computePrimaryAction(hot, warm, cold, total);
  const supportingActions = computeSupportingActions(hot);

  const subtitle = total === 0
    ? "No visitors scored yet. The classification kicks in with the first visitor."
    : `Hot ${hot} · Warm ${warm} · Cold ${cold} — out of ${total.toLocaleString()} visitors scored.`;

  return (
    <section
      role="region"
      aria-label="Visitor intent — Pro exploration"
      className="relative overflow-hidden rounded-3xl border border-white/[0.08] bg-[#0e0e1a] p-7 shadow-[0_20px_80px_-20px_rgba(0,0,0,0.6)] sm:p-9"
    >
      {/* Eyebrow */}
      <div
        className="mb-3 text-[10px] font-bold uppercase tracking-[0.18em]"
        style={{ color: ACCENT.eyebrow }}
      >
        Right now
      </div>

      {/* Title */}
      <h2
        className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight sm:text-[2rem]"
        style={{ color: ACCENT.hero }}
      >
        Visitor intent
      </h2>

      {/* Subtitle (data-driven) */}
      <p className="mt-2 text-[15px] font-semibold text-white">{subtitle}</p>

      {/* ── Section 1: mechanics + stakes ── */}
      <div className="mt-5 max-w-3xl space-y-5">
        <p className="text-[14px] leading-relaxed text-slate-300">
          I classify every live visitor into Hot (engaged and clicked),
          Warm (engaged but no click), and Cold (pass-through). A Hot
          visitor is roughly ten times more likely to buy than a Cold
          one — so the split tells you whether your traffic is the
          problem or your pages are.
        </p>
        <div>
          <div
            className="mb-1.5 text-[10.5px] font-bold uppercase tracking-[0.18em]"
            style={{ color: ACCENT.eyebrow }}
          >
            Why this matters
          </div>
          <p className="text-[14px] leading-relaxed text-slate-300">
            If your mix is mostly Cold, your traffic is wrong — fix the
            acquisition. If your mix is mostly Warm but low Hot, your
            product pages aren&apos;t earning the click — fix the
            conversion. Two very different costly mistakes, and mixing
            them up burns weeks.
          </p>
        </div>
      </div>

      {/* ── Section 2: the data (violet-accented) ── */}
      <div className="mt-8 rounded-2xl border border-violet-400/15 bg-violet-500/[0.025] p-5 sm:p-6">
        <div className="mb-5 flex items-center gap-2.5">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#c4b5fd"
            strokeWidth={1.8}
            className="h-4 w-4 flex-shrink-0"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75c0 .621-.504 1.125-1.125 1.125h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z"
            />
          </svg>
          <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-violet-300">
            The data · what you&apos;re looking at
          </div>
        </div>

        {/* Hero stat or empty preview */}
        {heroStat ? (
          <div className="mb-6 rounded-xl border border-white/[0.06] bg-[#0b0b14]/70 p-5">
            <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
              {heroStat.label}
            </div>
            <div
              className="mt-2 text-[2.25rem] font-extrabold leading-none tabular-nums"
              style={{ color: heroStat.color }}
            >
              {heroStat.value}
            </div>
            <div className="mt-2.5 text-[12.5px] leading-relaxed text-slate-400">
              {heroStat.sublabel}
            </div>
          </div>
        ) : (
          <EmptyPreview accentHero={ACCENT.hero} />
        )}

        {/* Donut */}
        {donutSegments && donutSegments.length > 0 && (
          <div className="mb-6 flex flex-col items-center gap-6 rounded-xl border border-white/[0.04] bg-[#0b0b14]/60 p-5 sm:flex-row sm:items-start sm:justify-start">
            <div className="flex-shrink-0">
              <Donut
                segments={donutSegments}
                hero={{ value: total.toLocaleString(), label: "visitors tracked" }}
              />
            </div>
            <div className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-slate-400">
              <div className="mb-1.5 text-[10px] font-bold uppercase tracking-[0.18em] text-slate-400">
                How to read it
              </div>
              <p>
                Each slice is one visitor segment sized by its share of
                the whole. The biggest slice is the merchant&apos;s
                biggest opportunity right now — its color tells you
                what kind.
              </p>
            </div>
          </div>
        )}

        {/* Key metrics */}
        {heroStat && keyMetrics.length > 0 && (
          <div className="mb-6">
            <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
              Key metrics
            </div>
            <div className="divide-y divide-white/[0.04] rounded-xl border border-white/[0.05] bg-[#0b0b14]/50">
              {keyMetrics.map((m, i) => (
                <div key={i} className="flex items-center justify-between gap-4 px-4 py-3">
                  <span className="text-[13px] text-slate-400">{m.label}</span>
                  <span
                    className="text-[14px] font-bold tabular-nums"
                    style={{ color: m.color ?? "#e2e8f0" }}
                  >
                    {m.value}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Methodology */}
        <div>
          <div className="mb-3 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
            How this is calculated
          </div>
          <div className="rounded-xl border border-white/[0.05] bg-[#0b0b14]/50 p-5">
            <p className="text-[13px] leading-relaxed text-slate-300">
              conversion_score = weighted sum of dwell time, scroll
              depth, and click count per visitor. Above the Hot
              threshold = Hot. Between Warm and Hot thresholds = Warm.
              At or below Warm threshold = Cold.
            </p>
            {methodologyInputs.length > 0 && (
              <ul className="mt-4 space-y-1.5 text-[12.5px]">
                {methodologyInputs.map((inp, i) => (
                  <li
                    key={i}
                    className="flex justify-between gap-3 border-b border-white/[0.03] pb-1.5 last:border-0 last:pb-0"
                  >
                    <span className="text-slate-500">{inp.label}</span>
                    <span className="tabular-nums text-slate-300">{inp.value}</span>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-4 text-[12.5px] leading-relaxed italic text-slate-400">
              Thresholds are published on purpose — we want you to
              audit the classification. Hot visitors are roughly 10×
              more likely to convert than Cold ones based on historical
              data across all shops.
            </p>
          </div>
        </div>
      </div>

      {/* ── Section 3: your next moves ── */}
      <div
        className="mt-6 rounded-2xl p-5 sm:p-6"
        style={{
          background: `linear-gradient(135deg, ${ACCENT.bg} 0%, transparent 80%)`,
          border: `1px solid ${ACCENT.border}`,
        }}
      >
        <div className="mb-4 flex items-center gap-2.5">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke={ACCENT.hero}
            strokeWidth={1.8}
            className="h-4 w-4 flex-shrink-0"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M13 10V3L4 14h7v7l9-11h-7z"
            />
          </svg>
          <div
            className="text-[11px] font-bold uppercase tracking-[0.18em]"
            style={{ color: ACCENT.hero }}
          >
            Your next moves
          </div>
        </div>

        {/* Primary action */}
        {primaryAction && (
          <div className="rounded-xl border border-white/[0.08] bg-[#0e0e1a]/80 p-5">
            <div
              className="text-[10px] font-bold uppercase tracking-[0.2em]"
              style={{ color: ACCENT.hero }}
            >
              {primaryAction.headline}
            </div>
            <div className="mt-2 text-[16px] font-bold leading-snug text-white">
              {primaryAction.label}
            </div>
            <p className="mt-2.5 max-w-3xl text-[13.5px] leading-relaxed text-slate-300">
              {primaryAction.description}
            </p>
          </div>
        )}

        {/* Supporting actions */}
        {supportingActions.length > 0 && (
          <ul className="mt-3 space-y-2">
            {supportingActions.map((s, i) => (
              <li
                key={i}
                className="flex items-start gap-3 rounded-xl border border-white/[0.05] bg-[#0e0e1a]/60 px-4 py-3"
              >
                <span
                  className="mt-1.5 inline-flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full text-[10px] font-bold tabular-nums"
                  style={{
                    color: ACCENT.hero,
                    background: ACCENT.bg,
                    border: `1px solid ${ACCENT.border}`,
                  }}
                  aria-hidden="true"
                >
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-[13.5px] font-semibold text-slate-200">
                    {s.label}
                  </div>
                  <p className="mt-1 text-[12.5px] leading-relaxed text-slate-400">
                    {s.description}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------

type HeroStat = {
  label: string;
  value: string;
  sublabel: string;
  color: string;
};

function computeHeroStat(
  hot: number,
  warm: number,
  cold: number,
  total: number,
): HeroStat | null {
  if (total === 0) return null;
  const segments = [
    { label: "Cold", value: cold, color: "#94a3b8" },
    { label: "Warm", value: warm, color: "#a78bfa" },
    { label: "Hot", value: hot, color: "#f87171" },
  ].sort((a, b) => b.value - a.value);
  const dominant = segments[0];
  const pct = total > 0 ? Math.round((dominant.value / total) * 100) : 0;
  return {
    label: "Dominant segment right now",
    value: `${dominant.label} ${pct}%`,
    sublabel: `${dominant.value.toLocaleString()} of ${total.toLocaleString()} visitors scored — the shape of your current traffic.`,
    color: dominant.color,
  };
}

function computePrimaryAction(
  hot: number,
  warm: number,
  cold: number,
  total: number,
): PrimaryAction {
  if (total === 0) {
    return {
      headline: "Tracker warming up",
      label: "No visitors scored yet",
      description:
        "Check that the HedgeSpark script is loaded on your storefront. As soon as the first visitor arrives, they'll be classified and this panel will populate.",
    };
  }
  if (cold > hot + warm) {
    return {
      headline: "Traffic problem",
      label: `Cold outnumbers engaged (${cold} vs ${hot + warm})`,
      description:
        "Your traffic quality is low. The fix is upstream — audit ad creative, landing pages, and channel mix. Fixing product pages won't help if Cold visitors dominate.",
    };
  }
  if (warm > 0 && hot < Math.max(1, Math.round(warm / 3))) {
    return {
      headline: "Conversion problem",
      label: `${warm} Warm but only ${hot} Hot`,
      description:
        "Your visitors are engaging but not clicking. The fix is on-page — tighten CTAs, add social proof, reduce friction above the fold. This is a page problem, not a traffic problem.",
    };
  }
  if (hot > 0) {
    return {
      headline: "Strike now",
      label: `${hot} Hot visitor${hot !== 1 ? "s" : ""} on your store right now`,
      description:
        "Hot visitors are 10× more likely to convert than Cold ones. Messaging them within the hour — email, retarget pixel, push — recovers meaningfully more than waiting until tomorrow.",
    };
  }
  return {
    headline: "Healthy mix",
    label: "Balanced intent distribution",
    description:
      "Your Hot/Warm/Cold split is healthy. Use the quiet period to invest in what's working — Hot Products is the right next panel to open.",
  };
}

function computeSupportingActions(hot: number): SupportingAction[] {
  const out: SupportingAction[] = [];
  if (hot > 0) {
    out.push({
      label: "Open Abandoned Intent",
      description:
        "Those Hot visitors leave traces on specific products. Abandoned Intent shows exactly which products they're looking at and where they drop off.",
    });
  } else {
    out.push({
      label: "Open Live Opportunities",
      description:
        "The pages with the biggest leaking intent right now are where to focus — especially if your mix shows a conversion problem rather than a traffic one.",
    });
  }
  out.push({
    label: "Re-check in an hour",
    description:
      "The mix shifts throughout the day as traffic sources change. A morning Cold-heavy mix can flip to Hot by afternoon if the right channel kicks in.",
  });
  return out;
}

// ----------------------------------------------------------------------
// EmptyPreview — day-1 cold-start affordance.
// ----------------------------------------------------------------------

function EmptyPreview({ accentHero }: { accentHero: string }) {
  return (
    <div className="mb-6 rounded-xl border border-dashed border-white/[0.12] bg-[#0b0b14]/40 p-5 sm:p-6">
      <div className="mb-3 flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
        <span
          className="inline-block h-1.5 w-1.5 animate-pulse rounded-full"
          style={{ background: accentHero }}
          aria-hidden="true"
        />
        Preview — what this card will show
      </div>
      <p className="mb-5 text-[13px] leading-relaxed text-slate-400">
        Every live visitor will be classified Hot / Warm / Cold within
        seconds of landing on your store. The split tells you in one
        glance whether to fix acquisition or conversion — two very
        different costly mistakes.
      </p>
      <div className="pointer-events-none mb-4 rounded-xl border border-white/[0.05] bg-[#0b0b14]/60 p-5 opacity-50">
        <div className="text-[10.5px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Dominant segment right now
        </div>
        <div
          className="mt-2 text-[2.25rem] font-extrabold leading-none tabular-nums"
          style={{ color: "#a78bfa" }}
        >
          Warm 54%
        </div>
        <div className="mt-2.5 text-[12.5px] leading-relaxed text-slate-400">
          48 of 89 visitors scored — your traffic is engaged but uncommitted.
        </div>
      </div>
      <div className="pointer-events-none mb-5 divide-y divide-white/[0.04] rounded-xl border border-white/[0.05] bg-[#0b0b14]/40 opacity-50">
        {[
          { label: "Hot visitors", value: "12", color: "#f87171" },
          { label: "Warm visitors", value: "48", color: "#a78bfa" },
          { label: "Cold visitors", value: "29", color: "#94a3b8" },
          { label: "Total scored", value: "89" },
        ].map((m, i) => (
          <div key={i} className="flex items-center justify-between gap-4 px-4 py-3">
            <span className="text-[13px] text-slate-400">{m.label}</span>
            <span
              className="text-[14px] font-bold tabular-nums"
              style={{ color: m.color ?? "#e2e8f0" }}
            >
              {m.value}
            </span>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2 rounded-lg bg-emerald-500/[0.05] border border-emerald-400/15 px-3 py-2 text-[11.5px] font-semibold text-emerald-300">
        <span
          className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400"
          aria-hidden="true"
        />
        Watching your storefront — real data will replace this preview within minutes.
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Donut — inline SVG, no library. 3 colored segments, center hero,
// legend below.
// ----------------------------------------------------------------------

function Donut({
  segments,
  hero,
  size = 180,
}: {
  segments: DonutSegment[];
  hero: { value: string; label: string };
  size?: number;
}) {
  const strokeWidth = 18;
  const radius = (size - strokeWidth) / 2;
  const center = size / 2;
  const circumference = 2 * Math.PI * radius;
  const total = segments.reduce((s, seg) => s + Math.max(0, seg.value), 0);
  const hasData = total > 0 && segments.length > 0;

  let cumulative = 0;

  return (
    <div className="flex flex-col items-center gap-3">
      <div className="relative" style={{ width: size, height: size }}>
        <svg
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          role="img"
          aria-label={`Distribution donut chart: ${segments
            .map((s) => `${s.label} ${s.value}`)
            .join(", ")}`}
        >
          <circle
            cx={center}
            cy={center}
            r={radius}
            fill="none"
            stroke="rgba(148, 163, 184, 0.08)"
            strokeWidth={strokeWidth}
          />
          {hasData &&
            segments.map((seg, i) => {
              const v = Math.max(0, seg.value);
              if (v <= 0) return null;
              const dashLength = (v / total) * circumference;
              const offset = -(cumulative / total) * circumference;
              cumulative += v;
              return (
                <circle
                  key={`${seg.label}-${i}`}
                  cx={center}
                  cy={center}
                  r={radius}
                  fill="none"
                  stroke={seg.color}
                  strokeWidth={strokeWidth}
                  strokeDasharray={`${dashLength} ${circumference - dashLength + 0.001}`}
                  strokeDashoffset={offset}
                  transform={`rotate(-90 ${center} ${center})`}
                  strokeLinecap="butt"
                />
              );
            })}
        </svg>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <div className="text-[22px] font-extrabold leading-none text-white tabular-nums">
            {hero.value}
          </div>
          {hero.label && (
            <div className="mt-1.5 max-w-[60%] text-center text-[9.5px] font-medium uppercase tracking-[0.12em] leading-tight text-slate-400">
              {hero.label}
            </div>
          )}
        </div>
      </div>

      <ul className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1.5 max-w-xs">
        {segments.map((seg, i) => (
          <li
            key={`leg-${seg.label}-${i}`}
            className="flex items-center gap-1.5 text-[11px]"
          >
            <span
              className="h-2 w-2 flex-shrink-0 rounded-full"
              style={{ background: seg.color }}
              aria-hidden="true"
            />
            <span className="font-medium text-slate-300">{seg.label}</span>
            <span className="tabular-nums text-slate-500">{seg.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
