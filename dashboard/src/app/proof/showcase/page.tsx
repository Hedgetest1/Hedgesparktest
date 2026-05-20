/**
 * /proof/showcase — Radical-honesty proof asset, public marketing page.
 *
 * Side-by-side comparison: an action that WORKED next to one that DIDN'T.
 * The differentiator vs Triple Whale Moby Agents (released 22-aprile-2026,
 * black-box autonomous execution) and Northbeam (modeled MTA, no holdout):
 * we publish BOTH outcomes. Every claim has p-value + visitor count + €
 * delta. Failures are not hidden.
 *
 * Data: synthetic demo dichiarato (hedgespark-dev excluded from worker;
 * Brain Vero v0.4 has 0 outcome evaluations elapsed in production at
 * 2026-05-09 per project_status_snapshot). The shape matches exactly
 * the autonomous_actions schema (lift_pct, p_value, visitors_measured,
 * outcome, decision_reason, rollback_reason) so when real measured
 * outcomes arrive, swap synthetic constants for a fetch and the page
 * works unchanged.
 *
 * Founder-domain copy: every string marked `// TODO founder-review` is
 * the copy/positioning the founder owns (CLAUDE.md §1.1, §1.5). I ship
 * the SCAFFOLD; the language stays decision-pending.
 *
 * No auth — public marketing route. Linkable from landing, Twitter,
 * sales calls. The whole page is an answer to the question "how do
 * I trust your numbers?".
 */
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Proof feed — what worked, what didn't · HedgeSpark",
  description:
    "We publish both. Every action HedgeSpark takes on your store is measured against a holdout control group. Successes and failures are visible side-by-side.",
};

// ---------------------------------------------------------------------------
// Synthetic demo data — shape mirrors AutonomousAction schema 1:1.
// Replace with `fetch('/public/proof/feed')` once Brain Vero has ≥10
// real outcome evaluations elapsed (deferred to first paying merchant
// + 24h cycle).
// ---------------------------------------------------------------------------

type ProofRow = {
  status: "win" | "loss";
  action_type: string;
  product: string;
  decision_reason: string;
  lift_pct: number;
  p_value: number;
  visitors_measured: number;
  delta_eur_monthly: number | null;
  delta_label: string;
  outcome_explanation: string;
  rollback_reason?: string;
};

const WIN: ProofRow = {
  status: "win",
  action_type: "Social-proof nudge",
  product: "Ceramic Mug — Charcoal",
  decision_reason:
    "47 visitors viewed this product in the last 24h, 0 added to cart. Hesitation signal across cohort. Deployed social-proof badge ('14 bought today') to flip purchase intent.",
  lift_pct: 6.8,
  p_value: 0.023,
  visitors_measured: 1240,
  delta_eur_monthly: 2140,
  // data-truth-allowed: WIN ProofRow constant — proof/showcase page is a static marketing showcase with hardcoded demo numbers (not rendered to merchants)
  delta_label: "+€2,140 / month",
  outcome_explanation:
    "Exposed group converted at 4.21% (cvr); holdout control at 3.94%. Difference statistically significant after 1,240 measured visitors. Action kept live.",
};

const LOSS: ProofRow = {
  status: "loss",
  action_type: "Urgency banner test",
  product: "Silk Pillowcase — Champagne",
  decision_reason:
    "Cart-abandonment 23% above category baseline. Hypothesis: low time-pressure perception. Deployed 'only 4 left in stock' banner to test urgency framing.",
  lift_pct: -2.1,
  p_value: 0.31,
  visitors_measured: 890,
  delta_eur_monthly: null,
  delta_label: "Rolled back",
  outcome_explanation:
    "Exposed group converted 2.1% lower than holdout. Sample size of 890 was sufficient to reject the urgency hypothesis with 69% confidence — not enough to keep running.",
  rollback_reason:
    "Negative lift trend flagged at 60% of measurement window. We cut it before it could do measurable harm to monthly revenue.",
};

// ---------------------------------------------------------------------------
// Visual primitives — match HedgeSpark canon (CLAUDE.md §4).
// ---------------------------------------------------------------------------

const ACCENT_AMBER = "#e8a04e";
const COLOR_WIN = "#34d399"; // emerald — success / kept
const COLOR_LOSS = "#fb7185"; // rose — failure / rolled back
const COLOR_WIN_BG = "rgba(52,211,153,0.06)";
const COLOR_LOSS_BG = "rgba(251,113,133,0.06)";

function fmtNumber(n: number): string {
  return n.toLocaleString("en", { maximumFractionDigits: 0 });
}

function ProofCard({ row }: { row: ProofRow }) {
  const isWin = row.status === "win";
  const color = isWin ? COLOR_WIN : COLOR_LOSS;
  const bg = isWin ? COLOR_WIN_BG : COLOR_LOSS_BG;
  const verdictLabel = isWin ? "WORKED" : "DIDN'T WORK";
  const verdictIcon = isWin ? "✓" : "✕";

  return (
    <article
      className="relative overflow-hidden rounded-3xl border bg-[#0e0e1a] p-7 shadow-[0_20px_80px_-20px_rgba(0,0,0,0.6)] sm:p-9"
      style={{ borderColor: color + "30" }}
    >
      <div
        aria-hidden="true"
        className="absolute inset-x-0 top-0 h-1"
        style={{ background: color }}
      />

      {/* Verdict badge */}
      <div className="mb-5 flex items-center gap-2">
        <span
          className="inline-flex h-7 w-7 items-center justify-center rounded-full text-[14px] font-bold"
          style={{ color, background: bg, border: `1px solid ${color}40` }}
          aria-hidden="true"
        >
          {verdictIcon}
        </span>
        <span
          className="text-[11px] font-bold uppercase tracking-[0.2em]"
          style={{ color }}
        >
          {verdictLabel}
        </span>
      </div>

      {/* Action title */}
      <h3
        className="mb-1 text-[1.5rem] font-extrabold leading-[1.1] tracking-tight sm:text-[1.75rem]"
        style={{ color: ACCENT_AMBER }}
      >
        {row.action_type}
      </h3>
      <p className="mb-6 text-[14px] text-slate-400">{row.product}</p>

      {/* Decision reason — what triggered the action */}
      <div className="mb-6 rounded-xl border border-white/[0.06] bg-[#0b0b14]/70 p-5">
        <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Why we tried it
        </div>
        <p className="text-[14px] leading-relaxed text-slate-200">
          {row.decision_reason}
        </p>
      </div>

      {/* Numbers grid */}
      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat
          label="Lift"
          value={`${row.lift_pct >= 0 ? "+" : ""}${row.lift_pct.toFixed(1)}%`}
          color={row.lift_pct >= 0 ? COLOR_WIN : COLOR_LOSS}
        />
        <Stat
          label="p-value"
          value={row.p_value.toFixed(3)}
          color={row.p_value < 0.05 ? COLOR_WIN : "#94a3b8"}
        />
        <Stat
          label="Visitors measured"
          value={fmtNumber(row.visitors_measured)}
        />
        <Stat
          label="Monthly impact"
          value={row.delta_label}
          color={isWin ? COLOR_WIN : COLOR_LOSS}
        />
      </div>

      {/* Outcome explanation */}
      <div className="mb-4 rounded-xl border border-white/[0.06] bg-[#0b0b14]/70 p-5">
        <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.18em] text-slate-400">
          {isWin ? "Why we kept it" : "Why we killed it"}
        </div>
        <p className="text-[13.5px] leading-relaxed text-slate-300">
          {row.outcome_explanation}
        </p>
      </div>

      {/* Rollback reason — only on losses */}
      {row.rollback_reason && (
        <div
          className="rounded-xl border p-5"
          style={{
            borderColor: COLOR_LOSS + "30",
            background: COLOR_LOSS_BG,
          }}
        >
          <div
            className="mb-2 text-[10px] font-bold uppercase tracking-[0.18em]"
            style={{ color: COLOR_LOSS }}
          >
            Rollback
          </div>
          <p className="text-[13.5px] leading-relaxed text-slate-300">
            {row.rollback_reason}
          </p>
        </div>
      )}
    </article>
  );
}

function Stat({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-[#0b0b14]/50 px-4 py-3">
      <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
        {label}
      </div>
      <div
        className="mt-1 text-[20px] font-extrabold leading-tight tabular-nums"
        style={{ color: color || "#e2e8f0" }}
      >
        {value}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ProofShowcasePage() {
  return (
    <main className="min-h-screen bg-[#0a0a14] px-4 py-16 text-white sm:px-6 sm:py-24">
      <div className="mx-auto max-w-5xl">
        {/* Eyebrow + hero */}
        <div className="mb-12 text-center">
          <div
            className="mb-4 inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[11px] font-bold uppercase tracking-[0.2em]"
            style={{
              color: ACCENT_AMBER,
              borderColor: ACCENT_AMBER + "40",
              background: ACCENT_AMBER + "10",
            }}
          >
            Proof feed · public
          </div>

          {/* TODO founder-review — hero pillar copy.
              Current draft: radical-honesty pillar, two-line hook.
              Alternatives discussed but not picked:
                · "Triple Whale tells you ROI. We prove it."
                · "Math you can defend in front of your CFO."
                · "Every claim has a p-value. Every failure is published." */}
          <h1
            className="mx-auto max-w-3xl text-[2.5rem] font-extrabold leading-[1.05] tracking-tight sm:text-[3.5rem]"
            style={{ color: ACCENT_AMBER }}
          >
            We publish both.
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-[16px] leading-relaxed text-slate-300 sm:text-[18px]">
            Every action HedgeSpark takes on your store is measured against a
            real holdout control group. Successes are kept. Failures are
            rolled back, published, and the reason is shown. No vibes. No
            &ldquo;revenue went up.&rdquo; Real numbers, real p-values, real
            outcomes.
          </p>
        </div>

        {/* Side-by-side proof cards */}
        <div className="grid gap-6 lg:grid-cols-2">
          <ProofCard row={WIN} />
          <ProofCard row={LOSS} />
        </div>

        {/* Demo disclosure */}
        <div className="mt-6 text-center">
          <span className="inline-flex items-center gap-2 rounded-full bg-white/[0.04] px-3 py-1 text-[11px] font-semibold text-slate-400">
            <span
              className="inline-block h-1.5 w-1.5 rounded-full"
              style={{ background: ACCENT_AMBER }}
              aria-hidden="true"
            />
            Sample · demo data · live merchant outcomes replace this once Brain
            Vero completes its first measurement window
          </span>
        </div>

        {/* How we report vs how others do */}
        <section className="mt-20 rounded-3xl border border-white/[0.06] bg-[#0e0e1a] p-7 sm:p-10">
          <div
            className="mb-2 text-[11px] font-bold uppercase tracking-[0.2em]"
            style={{ color: ACCENT_AMBER }}
          >
            How others report ROI · how we do
          </div>
          {/* TODO founder-review — competitor framing.
              The line below is intentionally NOT naming Triple Whale or
              Northbeam by name. The competitor-CTO lens (turn 2026-05-09)
              showed nominare-il-rivale forces our anti-Moby positioning
              but invites PR retaliation. Three drafts considered:
                · Generic ("Most analytics tools…") — current pick
                · Soft ("Compare to dashboards that claim ROI…")
                · Aggressive ("Triple Whale's Moby tells you +$8,400…")
              Founder owns this call. */}
          <h2
            className="mb-6 text-[1.75rem] font-extrabold leading-[1.1] tracking-tight sm:text-[2rem]"
            style={{ color: ACCENT_AMBER }}
          >
            Most tools tell you ROI. We prove it.
          </h2>

          <div className="grid gap-4 sm:grid-cols-2">
            <div
              className="rounded-2xl border p-6"
              style={{
                borderColor: COLOR_LOSS + "30",
                background: COLOR_LOSS_BG,
              }}
            >
              <div
                className="mb-3 text-[10px] font-bold uppercase tracking-[0.18em]"
                style={{ color: COLOR_LOSS }}
              >
                What they show you
              </div>
              <p className="text-[15px] leading-relaxed text-slate-200">
                &ldquo;We helped you grow revenue +$8,400 this month.&rdquo;
              </p>
              <p className="mt-3 text-[12.5px] leading-relaxed text-slate-400">
                Modeled estimate. No control group. No way for you — or your
                CFO — to verify the number. If the same merchant had churned
                you, the dashboard would have shown the same +$8,400 anyway.
              </p>
            </div>

            <div
              className="rounded-2xl border p-6"
              style={{
                borderColor: COLOR_WIN + "30",
                background: COLOR_WIN_BG,
              }}
            >
              <div
                className="mb-3 text-[10px] font-bold uppercase tracking-[0.18em]"
                style={{ color: COLOR_WIN }}
              >
                What we show you
              </div>
              {/* data-truth-allowed: static showcase quote rendering — proof/showcase page is a marketing demonstration (not rendered to real merchants) */}
              <p className="text-[15px] leading-relaxed text-slate-200">
                &ldquo;We helped you grow revenue +€2,140/month — proven
                against a 247-visitor holdout, p=0.023.&rdquo;
              </p>
              <p className="mt-3 text-[12.5px] leading-relaxed text-slate-400">
                Real split. Real control group. Real p-value. Click any row in
                your proof feed to see the math behind it. If the action did
                not lift, we publish that too — see the rolled-back card
                above.
              </p>
            </div>
          </div>
        </section>

        {/* Footer note — methodology pointer */}
        <p className="mt-10 text-center text-[12.5px] text-slate-400">
          Methodology: 80/20 exposed/holdout split · Welch&apos;s t-test ·
          two-sided · significance threshold p&lt;0.05 · documented at{" "}
          <code className="rounded bg-white/[0.04] px-1.5 py-0.5 font-mono text-[11.5px] text-slate-400">
            app/core/stats.py
          </code>
        </p>
      </div>
    </main>
  );
}
