"use client";

import Image from "next/image";
import { Fragment, useEffect, useRef, useState } from "react";
import { DemoPreviewCard } from "./components/DemoPreviewCard";
import { reportFrontendError } from "./lib/error-reporter";

/* ── OAuth guard ──
 * Redirects to /app when the visitor arrives with Shopify OAuth params.
 * IMPORTANT: this runs in a client-side useEffect, so it MUST NOT gate
 * the page render — if we return null while the guard is "pending",
 * the server renders empty <body> to Google's crawler and to every
 * cold visitor. That's exactly the regression Lighthouse exposed
 * (NO_LCP → performance score 0) and the SEO leak that quietly costs
 * organic reach. Always render the landing; navigate away client-side
 * when the params are present. The ~50 ms flash is strictly better
 * than a blank page, because the blank-page path is what 99.99 % of
 * real visitors saw. */
function useOAuthRedirect() {
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    if (p.get("shop") || p.get("installed") || p.get("billing") || p.get("section")) {
      window.location.href = `/app${window.location.search}`;
    }
  }, []);
}

/* ── Scroll reveal ── */
function useReveal(threshold = 0.1) {
  const ref = useRef<HTMLDivElement>(null);
  const [v, setV] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => { if (e.isIntersecting) { setV(true); io.disconnect(); } },
      { threshold },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [threshold]);
  return { ref, v };
}

function R({ children, className = "", d = 0 }: { children: React.ReactNode; className?: string; d?: number }) {
  const { ref, v } = useReveal();
  return (
    <div
      ref={ref}
      className={className}
      style={{
        opacity: v ? 1 : 0,
        transform: v ? "none" : "translateY(32px)",
        transition: `opacity 0.7s cubic-bezier(0.16,1,0.3,1) ${d}s, transform 0.7s cubic-bezier(0.16,1,0.3,1) ${d}s`,
      }}
    >
      {children}
    </div>
  );
}

/* ── Shared ── */
const INSTALL_URL = "/install";

/* hex (#rrggbb) → rgba(r, g, b, a) — for inline styles with dynamic alpha */
const alpha = (hex: string, a: number) => {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${a})`;
};

/* ── Live network ROI counter (Phase Ω⁵) ── */
type RoiCounterDoc = {
  state: "live" | "warming";
  prevented_eur_30d: number;
  shops_contributing: number;
  by_vertical: Array<{ vertical: string; prevented_eur: number }>;
  window_days: number;
  generated_at: string;
};

function useRoiCounter() {
  const [doc, setDoc] = useState<RoiCounterDoc | null>(null);
  const [live, setLive] = useState(false);

  useEffect(() => {
    const API = process.env.NEXT_PUBLIC_API_BASE_URL || "https://api.hedgesparkhq.com";
    let active = true;

    fetch(`${API}/public/roi-counter`, { signal: AbortSignal.timeout(5000) })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: RoiCounterDoc | null) => { if (active && d) setDoc(d); })
      .catch((err: unknown) => {
        const e = err as { name?: string; message?: string } | null;
        reportFrontendError({
          component: "useRoiCounter",
          error_type: e?.name ?? "FetchError",
          message: e?.message ?? "Failed to fetch /public/roi-counter",
          severity: "info",
        });
      });

    // SSE live ticker — re-reads the Redis cache server-side every 20s
    let es: EventSource | null = null;
    try {
      es = new EventSource(`${API}/public/roi-counter/live`);
      es.addEventListener("tick", (ev: MessageEvent) => {
        if (!active) return;
        try {
          const d = JSON.parse(ev.data);
          setDoc(d);
          setLive(true);
        } catch {}
      });
      es.onerror = () => {};
    } catch {}

    return () => { active = false; try { es?.close(); } catch {} };
  }, []);

  return { doc, live };
}

/* ── Animated integer counter (tweened toward target) ── */
function useCountUp(target: number, durationMs = 1200): number {
  const [value, setValue] = useState(target);
  const prevRef = useRef(target);
  useEffect(() => {
    const start = prevRef.current;
    const delta = target - start;
    if (delta === 0) return;
    const startTs = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const p = Math.min(1, (now - startTs) / durationMs);
      const eased = 1 - Math.pow(1 - p, 3);
      setValue(Math.round(start + delta * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
      else prevRef.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs]);
  return value;
}

function RoiCounterBanner() {
  const { doc, live } = useRoiCounter();
  const isLive = doc?.state === "live";
  const target = isLive ? Math.round(doc?.prevented_eur_30d ?? 0) : 0;
  const animated = useCountUp(target);
  const [hovering, setHovering] = useState(false);

  const formatted = new Intl.NumberFormat("en-US").format(animated);
  const topVerticals = (doc?.by_vertical ?? []).slice(0, 5);

  // Honesty-first rendering. When the network is still warming up, we do
  // NOT show a fabricated number. We tell the truth: the network is
  // launching, be the first. That IS the social proof for an early-stage
  // premium product — "we don't bullshit".
  if (!doc || !isLive) {
    return (
      <R d={0.1}>
        <div className="mx-auto mt-10 max-w-[48rem] px-6">
          <div
            role="figure"
            aria-label="HedgeSpark network launching — no fabricated numbers"
            className="relative overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.02] p-6 backdrop-blur"
          >
            <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-[#d4893a]/40 to-transparent" />
            <div className="flex flex-col items-center text-center">
              <div className="mb-1 flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
                <span className="relative inline-flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#d4893a]/60" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-[#d4893a]" />
                </span>
                Network launching · Be one of the first
              </div>
              <h2 className="mt-3 max-w-xl text-[20px] font-bold leading-snug text-white sm:text-[24px]">
                We refuse to fabricate a counter.
              </h2>
              <p className="mt-3 max-w-md text-[13px] leading-relaxed text-slate-400">
                Other tools dress landing pages with inflated &ldquo;recovered&rdquo; totals on day one.
                Ours stays honest — the counter here goes live the moment real merchants recover real money.
                {doc && (
                  <span className="mt-2 block text-[11px] text-slate-400">
                    Currently tracking {doc.shops_contributing} active merchant{doc.shops_contributing === 1 ? "" : "s"}.
                  </span>
                )}
              </p>
            </div>
          </div>
        </div>
      </R>
    );
  }

  return (
    <R d={0.1}>
      <div className="mx-auto mt-10 max-w-[48rem] px-6">
        <div
          role="figure"
          aria-label={`${formatted} recovered across the HedgeSpark network in the last 30 days`}
          onMouseEnter={() => setHovering(true)}
          onMouseLeave={() => setHovering(false)}
          className="group relative overflow-hidden rounded-2xl border border-emerald-400/[0.12] bg-emerald-500/[0.03] p-6 backdrop-blur transition-colors hover:border-emerald-400/[0.22]"
        >
          <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-emerald-400/40 to-transparent" />

          <div className="flex flex-col items-center text-center">
            <div className="mb-1 flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.18em] text-emerald-300/80">
              {live && (
                <span className="relative inline-flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/60" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
                </span>
              )}
              Live · Network Impact · Last 30 Days
            </div>
            <div className="mt-2 font-mono text-[40px] font-extrabold tabular-nums leading-none text-white sm:text-[56px]">
              {formatted}
            </div>
            <div className="mt-1 text-[10px] uppercase tracking-wide text-slate-400">
              money recovered · merchant native currencies
            </div>
            <div className="mt-2 text-[13px] text-slate-400">
              across {doc.shops_contributing} Shopify merchant{doc.shops_contributing === 1 ? "" : "s"}
            </div>
          </div>

          {topVerticals.length > 0 && (
            <div
              className={`mt-4 grid gap-2 overflow-hidden transition-[max-height,opacity] duration-500 ${
                hovering ? "max-h-64 opacity-100" : "max-h-0 opacity-0"
              }`}
              aria-hidden={!hovering}
            >
              <div className="text-center text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                Breakdown by vertical
              </div>
              <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                {topVerticals.map((v) => (
                  <div
                    key={v.vertical}
                    className="flex items-center justify-between rounded-lg border border-white/[0.05] bg-white/[0.02] px-3 py-1.5 text-[11px]"
                  >
                    <span className="capitalize text-slate-300">{v.vertical.replace(/_/g, " ")}</span>
                    <span className="font-mono tabular-nums text-emerald-300">
                      {new Intl.NumberFormat("en-US").format(Math.round(v.prevented_eur))}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="mt-4 text-center text-[10px] text-slate-400">
            Hover for breakdown · counter reads real action executions from the network
          </div>
        </div>
      </div>
    </R>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   NAV
   ══════════════════════════════════════════════════════════════════════════════ */

function Nav() {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const h = () => setScrolled(window.scrollY > 24);
    window.addEventListener("scroll", h, { passive: true });
    h();
    return () => window.removeEventListener("scroll", h);
  }, []);

  return (
    <nav
      className={`fixed inset-x-0 top-0 z-50 transition-all duration-500 ${
        scrolled
          ? "bg-[#07070f]/90 shadow-[0_1px_60px_rgba(0,0,0,0.6)] backdrop-blur-2xl"
          : "bg-transparent"
      }`}
      style={{ borderBottom: scrolled ? "1px solid rgba(255,255,255,0.04)" : "1px solid transparent" }}
    >
      <div className="mx-auto flex h-[4.5rem] max-w-[76rem] items-center justify-center px-6 lg:px-10">
        <div className="flex items-center gap-3 sm:gap-5">
          {[
            ["/", "Home"],
            ["#features", "Features"],
            ["#intelligence", "Intelligence"],
            ["#how", "How it works"],
            ["#example", "Example"],
            ["#pricing", "Pricing"],
            ["#faq", "FAQ"],
            ["/app", "Dashboard"],
          ].map(([h, l]) => (
            <a
              key={h}
              href={h}
              className="rounded-lg px-3 py-2 text-[15px] font-medium text-slate-300 transition-colors duration-200 hover:bg-white/[0.05] hover:text-white"
            >
              {l}
            </a>
          ))}
          <a
            href={INSTALL_URL}
            className="hs-cta-gradient ml-4 rounded-xl px-6 py-2.5 text-[15px] font-bold text-white transition-all duration-300 hover:shadow-[0_0_32px_rgba(212,137,58,0.4)]"
          >
            Install on Shopify
          </a>
        </div>
      </div>
    </nav>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   HERO
   ══════════════════════════════════════════════════════════════════════════════ */

function Hero() {
  return (
    <section className="relative overflow-hidden pb-16 pt-24 sm:pb-20 sm:pt-28">
      {/* Ambient glow */}
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-1/2 top-[20%] h-[600px] w-[900px] -translate-x-1/2 rounded-full bg-[#d4893a]/[0.06] blur-[160px]" />
        <div className="absolute left-[30%] top-[40%] h-[400px] w-[600px] -translate-x-1/2 rounded-full bg-[#7c3aed]/[0.04] blur-[140px]" />
      </div>

      <div className="relative mx-auto max-w-[72rem] px-6 lg:px-10">
        {/* Logo */}
        <R className="flex justify-center">
          <Image
            src="/logo-beta-v2.png"
            alt="HedgeSpark — AI Revenue Intelligence for Shopify"
            width={450}
            height={190}
            className="hs-float-gentle -translate-x-[15px]"
            priority
          />
        </R>

        {/* Category eyebrow */}
        <R d={0.04} className="mt-5 flex justify-center">
          <div className="inline-flex items-center gap-2 rounded-full border border-white/[0.07] bg-white/[0.025] px-3.5 py-1 backdrop-blur-sm">
            <span className="relative flex h-1 w-1">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#e8a04e] opacity-75" />
              <span className="relative inline-flex h-1 w-1 rounded-full bg-[#e8a04e]" />
            </span>
            <span className="text-[9.5px] font-semibold uppercase tracking-[0.18em] text-slate-300">
              Shopify App
            </span>
            <span className="h-2 w-px bg-white/15" />
            <span className="text-[9.5px] font-semibold uppercase tracking-[0.18em] text-slate-400">
              AI Revenue Intelligence
            </span>
          </div>
        </R>

        {/* Headline */}
        <R d={0.08}>
          <h1 className="mx-auto mt-6 max-w-[52rem] text-center text-[2.75rem] font-extrabold leading-[1.05] tracking-[-0.03em] text-cream sm:text-[4rem] lg:text-[5rem]">
            Your store is leaking money.
            <br />
            <span className="text-slate-400">You don&apos;t know why.</span>
            <br />
            <span
              style={{
                background: "linear-gradient(90deg, #d4893a 0%, #e8a04e 30%, #a855f7 70%, #7c3aed 100%)",
                WebkitBackgroundClip: "text",
                WebkitTextFillColor: "transparent",
                backgroundClip: "text",
              }}
            >
              We show you where.
            </span>
          </h1>
        </R>

        {/* Sub */}
        <R d={0.12}>
          <p className="mx-auto mt-7 max-w-[40rem] text-center text-[18px] leading-[1.65] text-slate-300">
            <strong className="text-white">The most advanced dashboard built for Shopify.</strong>{" "}
            Finds the products that get attention but don&apos;t sell. Stops the curse.
            Trust the magic.
          </p>
        </R>

        {/* CTAs */}
        <R d={0.14} className="mt-8 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
          <a
            href={INSTALL_URL}
            className="hs-cta-gradient group rounded-2xl px-10 py-4 text-[16px] font-bold text-white transition-all duration-300 hover:shadow-[0_4px_50px_rgba(212,137,58,0.4)]"
          >
            Install on Shopify
          </a>
          <a
            href="#how"
            className="rounded-2xl border border-white/[0.1] bg-white/[0.03] px-10 py-4 text-[16px] font-semibold text-slate-200 transition-all duration-300 hover:border-white/[0.18] hover:bg-white/[0.06] hover:text-white"
          >
            See how it works
          </a>
        </R>

        <R d={0.18}>
          <p className="mt-5 text-center text-[14px] text-slate-500">
            Installs in 30 seconds. Tracking starts on the next visitor.
          </p>
        </R>

        {/* Phase Ω'' — pre-signup demo */}
        <R d={0.22}>
          <DemoPreviewCard installUrl={INSTALL_URL} />
        </R>

        {/* Phase Ω⁵ — live network ROI counter */}
        <RoiCounterBanner />
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   THE PROBLEM — 3 big blocks
   ══════════════════════════════════════════════════════════════════════════════ */

function Problem() {
  const blocks = [
    {
      number: "200",
      unit: "views",
      highlight: "0 carts",
      desc: "Your best product got 200 views today. Nobody added it to their cart. You didn't even notice.",
      accent: "#d4893a",
    },
    {
      number: "193",
      unit: "/day",
      highlight: "at risk · your currency",
      desc: "Right now, products in your store are losing money every single day. You just can't see which ones.",
      accent: "#a855f7",
    },
    {
      number: "0%",
      unit: "proof",
      highlight: "you guessed",
      desc: "You changed the photo. Sales went up. Was it the photo — or just Tuesday? Without proof, every win is a guess.",
      accent: "#7c3aed",
    },
  ];

  return (
    <section className="relative py-28 sm:py-36">
      <div className="mx-auto max-w-[72rem] px-6 lg:px-10">
        <R className="text-center">
          <span className="text-[14px] font-bold uppercase tracking-[0.2em] text-[#d4893a]">The problem</span>
          <h2 className="mt-5 text-[2.25rem] font-extrabold leading-[1.1] text-white sm:text-[3rem] lg:text-[3.5rem]">
            Your analytics show traffic.
            <br />
            <span className="text-slate-500">They don&apos;t show the money you&apos;re losing.</span>
          </h2>
        </R>

        <div className="mt-16 grid gap-6 sm:grid-cols-3">
          {blocks.map((b, i) => (
            <R key={i} d={i * 0.08}>
              <div className="group relative overflow-hidden rounded-3xl border border-white/[0.06] bg-[#0e0e1a] p-8 transition-all duration-400 hover:border-white/[0.12] hover:shadow-[0_8px_60px_-12px_rgba(0,0,0,0.5)] sm:p-10">
                {/* Accent top bar */}
                <div className="absolute inset-x-0 top-0 h-1 rounded-t-3xl" style={{ background: b.accent }} />

                <div className="flex items-baseline gap-2">
                  <span className="text-[3.5rem] font-extrabold tracking-tight text-white sm:text-[4rem]">{b.number}</span>
                  <span className="text-[18px] font-semibold text-slate-500">{b.unit}</span>
                </div>
                <div className="mt-1 text-[15px] font-bold uppercase tracking-wide" style={{ color: b.accent }}>
                  {b.highlight}
                </div>
                <p className="mt-5 text-[16px] leading-[1.7] text-slate-400">{b.desc}</p>
              </div>
            </R>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   FEATURES — what HedgeSpark actually does
   ══════════════════════════════════════════════════════════════════════════════ */

/* Tier badge — tells visitors at-a-glance which plan includes a feature.
 * Three values per pricing matrix memo §2: "all" (Lite+Pro+Scale),
 * "pro" (Pro+Scale), "scale" (Scale only). Kept small and consistent so
 * repeating badges don't create visual noise across 20+ feature cards. */
function TierBadge({ tier }: { tier: "all" | "pro" | "scale" }) {
  const styles = {
    all: { label: "All plans", color: "#e8a04e", bg: "#e8a04e" },
    pro: { label: "Pro+", color: "#a855f7", bg: "#a855f7" },
    scale: { label: "Scale only", color: "#3b82f6", bg: "#3b82f6" },
  }[tier];
  return (
    <span
      className="inline-flex items-center rounded-md px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.1em]"
      style={{ color: styles.color, background: alpha(styles.bg, 0.1), border: `1px solid ${alpha(styles.bg, 0.25)}` }}
    >
      {styles.label}
    </span>
  );
}

function Features() {
  // Compact 4-pillar summary of the 16 intelligence capabilities plus
  // the 3 operations tools. Replaces the former 19-card grid — founder
  // feedback: "sfilza di cassettoni di dubbia grandezza" + "ripetendo
  // capabilities" with the H2 above. Detail lives in the comparison
  // table inside Pricing; this section just lists what's in the stack.
  const pillars: Array<{ title: string; color: string; count: string; items: string }> = [
    {
      title: "Revenue Intelligence",
      color: "#d4893a",
      count: "4 signals",
      items: "Revenue at Risk Score · Revenue Autopsy · Abandoned Intent · Refund Loss Tracking",
    },
    {
      title: "Behavioral DNA",
      color: "#a855f7",
      count: "4 signals",
      items: "Visitor Intent Scoring · Scroll Heatmaps · Price Sensitivity · Session Timeline",
    },
    {
      title: "Measurement & Proof",
      color: "#34d399",
      count: "4 signals",
      items: "Causal Lift · Peer Benchmarks · Revenue Genome · Shareable Proof Reports",
    },
    {
      title: "Growth Intelligence",
      color: "#7c3aed",
      count: "4 signals",
      items: "Cohort & LTV Analysis · P&L Intelligence · Goals + ROI Tracking · Risk Forecast",
    },
  ];

  return (
    <section id="features" className="relative scroll-mt-20 py-28 sm:py-36">
      {/* Subtle amber background tint */}
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-[#d4893a]/[0.02] via-transparent to-transparent" />

      <div className="relative mx-auto max-w-[72rem] px-6 lg:px-10">
        <R className="text-center">
          <span className="text-[14px] font-bold uppercase tracking-[0.2em] text-[#d4893a]">What HedgeSpark does</span>
          <h2 className="mt-5 text-[2.25rem] font-extrabold leading-[1.1] text-white sm:text-[3rem] lg:text-[3.5rem]">
            Three killer features.
            <br />
            <span className="text-slate-300">Plus 16 deeper capabilities.</span>
          </h2>
          <p className="mx-auto mt-6 max-w-[38rem] text-[18px] leading-[1.7] text-slate-400">
            Most tools tell you what happened yesterday. HedgeSpark tells you what&apos;s broken right now, fixes it, and proves it worked.
          </p>
        </R>

        {/* Feature 1 — Find */}
        <R d={0.08}>
          <div className="mt-20 grid items-center gap-10 lg:grid-cols-2 lg:gap-16">
            <div>
              <div className="flex flex-wrap items-center gap-3">
                <div className="inline-flex items-center gap-3 rounded-full border border-[#d4893a]/20 bg-[#d4893a]/[0.08] px-5 py-2">
                  <span className="text-[22px] font-extrabold text-[#d4893a]">1</span>
                  <span className="text-[15px] font-bold text-[#d4893a]">Find the problem</span>
                </div>
                <TierBadge tier="all" />
              </div>
              <h3 className="mt-6 text-[1.75rem] font-bold leading-[1.2] text-white sm:text-[2rem]">
                See which products get attention but don&apos;t sell
              </h3>
              <p className="mt-5 text-[17px] leading-[1.7] text-slate-400">
                HedgeSpark watches every visitor. How far they scroll. How long they stay. Whether they come back. When a product gets lots of attention but zero carts — we flag it instantly.
              </p>
              <div className="mt-6 space-y-3">
                {[
                  "Checks every product every 5 minutes",
                  "Shows you exactly what visitors do on each page",
                  "Calculates how much money each problem costs you",
                ].map((f) => (
                  <div key={f} className="flex items-start gap-3">
                    <svg className="mt-1 h-5 w-5 flex-shrink-0 text-[#d4893a]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                    </svg>
                    <span className="text-[16px] text-slate-300">{f}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Signal card visual */}
            <div className="rounded-3xl border border-white/[0.06] bg-[#0e0e1a] p-6 shadow-[0_20px_80px_-20px_rgba(0,0,0,0.5)] sm:p-8">
              <div className="mb-5 flex items-center gap-2">
                <div className="h-2.5 w-2.5 rounded-full bg-rose-400 shadow-[0_0_8px_rgba(251,113,133,0.6)]" />
                <span className="text-[14px] font-bold text-rose-300">Signal detected</span>
              </div>
              <div className="rounded-2xl border border-rose-500/10 bg-rose-500/[0.03] p-6">
                <p className="text-[18px] font-bold text-white">Silk Pillowcase Set</p>
                <div className="mt-4 grid grid-cols-3 gap-4">
                  <div>
                    <div className="text-[2rem] font-extrabold text-white">68</div>
                    <div className="text-[14px] text-slate-500">views today</div>
                  </div>
                  <div>
                    <div className="text-[2rem] font-extrabold text-rose-400">0</div>
                    <div className="text-[14px] text-slate-500">add to carts</div>
                  </div>
                  <div>
                    <div className="text-[2rem] font-extrabold text-[#d4893a]">$94</div>
                    <div className="text-[14px] text-slate-500">lost per day</div>
                  </div>
                </div>
                <div className="mt-5 rounded-xl border border-white/[0.04] bg-white/[0.02] p-4">
                  <div className="flex items-center gap-2">
                    <svg className="h-4 w-4 text-[#d4893a]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
                    </svg>
                    <span className="text-[13px] font-bold text-[#d4893a]">Recommended fix</span>
                  </div>
                  <p className="mt-2 text-[15px] leading-relaxed text-slate-300">
                    Replace hero image with lifestyle shot. Add sticky &ldquo;Add to Cart&rdquo; button visible on scroll.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </R>

        {/* Feature 2 — Fix */}
        <R d={0.08}>
          <div className="mt-28 grid items-center gap-10 lg:grid-cols-2 lg:gap-16">
            {/* Visual first on desktop */}
            <div className="order-2 lg:order-1">
              <div className="rounded-3xl border border-white/[0.06] bg-[#0e0e1a] p-6 shadow-[0_20px_80px_-20px_rgba(0,0,0,0.5)] sm:p-8">
                <div className="mb-5 flex items-center gap-2">
                  <div className="h-2.5 w-2.5 rounded-full bg-[#d4893a] shadow-[0_0_8px_rgba(212,137,58,0.6)]" />
                  <span className="text-[14px] font-bold text-[#e8a04e]">Nudge deployed</span>
                </div>
                <div className="rounded-2xl border border-[#d4893a]/10 bg-[#d4893a]/[0.03] p-6">
                  <p className="text-[15px] font-semibold text-slate-300">Social proof nudge — live on your store</p>
                  <div className="mt-4 rounded-xl border border-white/[0.06] bg-white/[0.03] p-5">
                    <p className="text-center text-[17px] italic text-white">
                      &ldquo;14 people viewed this in the last 24 hours&rdquo;
                    </p>
                  </div>
                  <div className="mt-5 grid grid-cols-2 gap-4">
                    <div className="rounded-xl border border-white/[0.04] bg-white/[0.02] p-4 text-center">
                      <div className="text-[1.5rem] font-extrabold text-white">80%</div>
                      <div className="text-[14px] text-slate-500">see the nudge</div>
                    </div>
                    <div className="rounded-xl border border-white/[0.04] bg-white/[0.02] p-4 text-center">
                      <div className="text-[1.5rem] font-extrabold text-slate-500">20%</div>
                      <div className="text-[14px] text-slate-500">control group</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="order-1 lg:order-2">
              <div className="flex flex-wrap items-center gap-3">
                <div className="inline-flex items-center gap-3 rounded-full border border-[#a855f7]/20 bg-[#a855f7]/[0.08] px-5 py-2">
                  <span className="text-[22px] font-extrabold text-[#a855f7]">2</span>
                  <span className="text-[15px] font-bold text-[#a855f7]">Fix it automatically</span>
                </div>
                <TierBadge tier="pro" />
              </div>
              <h3 className="mt-6 text-[1.75rem] font-bold leading-[1.2] text-white sm:text-[2rem]">
                Smart nudges deploy on their own
              </h3>
              <p className="mt-5 text-[17px] leading-[1.7] text-slate-400">
                When HedgeSpark spots a problem, it doesn&apos;t just tell you — it can fix it. It deploys a small, targeted message on the product page to turn browsers into buyers.
              </p>
              <div className="mt-6 space-y-3">
                {[
                  "Social proof, urgency, and interest-based nudges",
                  "Automatically keeps a control group to measure impact",
                  "No code, no theme edits — it just works",
                ].map((f) => (
                  <div key={f} className="flex items-start gap-3">
                    <svg className="mt-1 h-5 w-5 flex-shrink-0 text-[#a855f7]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                    </svg>
                    <span className="text-[16px] text-slate-300">{f}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </R>

        {/* Feature 3 — Prove */}
        <R d={0.08}>
          <div className="mt-28 grid items-center gap-10 lg:grid-cols-2 lg:gap-16">
            <div>
              <div className="flex flex-wrap items-center gap-3">
                <div className="inline-flex items-center gap-3 rounded-full border border-emerald-400/20 bg-emerald-500/[0.08] px-5 py-2">
                  <span className="text-[22px] font-extrabold text-emerald-400">3</span>
                  <span className="text-[15px] font-bold text-emerald-400">Prove it worked</span>
                </div>
                <TierBadge tier="pro" />
              </div>
              <h3 className="mt-6 text-[1.75rem] font-bold leading-[1.2] text-white sm:text-[2rem]">
                Real numbers. Not guesses.
              </h3>
              <p className="mt-5 text-[17px] leading-[1.7] text-slate-400">
                Remember that control group? We compare it against the visitors who saw your fix. If more people bought — we prove it with real math. Not vibes. Not &ldquo;revenue went up.&rdquo; Actual, statistical proof.
              </p>
              <div className="mt-6 space-y-3">
                {[
                  "Side-by-side comparison: fix vs. no fix",
                  "Shows exactly how many extra sales your change drove",
                  "Confidence level so you know it's real, not luck",
                ].map((f) => (
                  <div key={f} className="flex items-start gap-3">
                    <svg className="mt-1 h-5 w-5 flex-shrink-0 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                    </svg>
                    <span className="text-[16px] text-slate-300">{f}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Proof visual */}
            <div className="rounded-3xl border border-white/[0.06] bg-[#0e0e1a] p-6 shadow-[0_20px_80px_-20px_rgba(0,0,0,0.5)] sm:p-8">
              <div className="mb-6 text-[13px] font-bold uppercase tracking-[0.15em] text-slate-400">Lift report</div>

              {/* Bar comparison */}
              <div className="space-y-5">
                <div>
                  <div className="flex items-center justify-between">
                    <span className="text-[16px] text-slate-300">Saw your fix</span>
                    <span className="text-[20px] font-extrabold tabular-nums text-emerald-400">4.2%</span>
                  </div>
                  <div className="mt-2 h-4 w-full overflow-hidden rounded-full bg-white/[0.04]">
                    <div className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-emerald-400" style={{ width: "84%" }} />
                  </div>
                </div>
                <div>
                  <div className="flex items-center justify-between">
                    <span className="text-[16px] text-slate-300">Control group</span>
                    <span className="text-[20px] font-extrabold tabular-nums text-slate-500">3.1%</span>
                  </div>
                  <div className="mt-2 h-4 w-full overflow-hidden rounded-full bg-white/[0.04]">
                    <div className="h-full rounded-full bg-slate-600" style={{ width: "62%" }} />
                  </div>
                </div>
              </div>

              <div className="mt-8 rounded-2xl border border-emerald-500/15 bg-emerald-500/[0.04] p-6 text-center">
                <div className="text-[16px] text-slate-300">Your change drove</div>
                <div className="mt-2 bg-gradient-to-r from-emerald-400 to-emerald-300 bg-clip-text text-[3.5rem] font-extrabold tabular-nums leading-none text-transparent">
                  +35.5%
                </div>
                <div className="mt-1 text-[18px] font-semibold text-emerald-300">more sales</div>
                <div className="mt-4 flex items-center justify-center gap-3">
                  <span className="rounded-lg bg-emerald-500/15 px-3 py-1 text-[13px] font-bold text-emerald-300">
                    Statistically proven
                  </span>
                  <span className="text-[14px] text-slate-500">1,240 visitors measured</span>
                </div>
              </div>
            </div>
          </div>
        </R>

        {/* ── Compact 4-pillar summary of the 16 deeper capabilities ── */}
        <div className="mt-28 grid gap-4 md:grid-cols-2">
          {pillars.map((p, i) => (
            <R key={p.title} d={i * 0.05}>
              <div className="group flex h-full gap-5 rounded-2xl border border-white/[0.06] bg-[#0e0e1a] p-6 transition-all duration-300 hover:border-white/[0.12]">
                <div className="mt-1 h-8 w-1 flex-shrink-0 rounded-full" style={{ background: p.color }} />
                <div className="flex-1">
                  <div className="flex items-baseline gap-3 flex-wrap">
                    <h3 className="text-[17px] font-bold text-white">{p.title}</h3>
                    <span className="text-[12px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                      {p.count}
                    </span>
                  </div>
                  <p className="mt-3 text-[13.5px] leading-[1.65] text-slate-400">{p.items}</p>
                </div>
              </div>
            </R>
          ))}
        </div>

        {/* Operations strip — 3 tools, one line */}
        <R d={0.25}>
          <div className="mt-4 flex items-start gap-5 rounded-2xl border border-white/[0.04] bg-white/[0.015] p-6">
            <div className="mt-1 h-8 w-1 flex-shrink-0 rounded-full bg-slate-500/60" />
            <div className="flex-1">
              <div className="flex items-baseline gap-3 flex-wrap">
                <h3 className="text-[16px] font-bold text-slate-200">Operations</h3>
                <span className="text-[12px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                  + 3 tools
                </span>
              </div>
              <p className="mt-3 text-[13.5px] leading-[1.65] text-slate-400">
                Team Collaboration · Webhook Integrations · Automated Nudges
              </p>
            </div>
          </div>
        </R>

        <R d={0.35}>
          <p className="mt-8 text-center text-[13px] text-slate-400">
            Lite gets the foundation signals. Pro unlocks everything above. See{" "}
            <a href="#pricing" className="font-semibold text-slate-300 underline decoration-slate-600 underline-offset-4 transition-colors hover:text-white hover:decoration-slate-400">
              which plan includes what
            </a>.
          </p>
        </R>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   HOW IT WORKS — visual flow
   ══════════════════════════════════════════════════════════════════════════════ */

function HowItWorks() {
  const steps = [
    {
      icon: (
        <svg className="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
        </svg>
      ),
      title: "Detect",
      desc: "Watches every product, every 5 minutes. Flags what's broken.",
      color: "#d4893a",
      bg: "from-[#d4893a]/15 to-[#d4893a]/5",
    },
    {
      icon: (
        <svg className="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
        </svg>
      ),
      title: "Act",
      desc: "Deploys a targeted fix. No code needed.",
      color: "#a855f7",
      bg: "from-[#a855f7]/15 to-[#a855f7]/5",
    },
    {
      icon: (
        <svg className="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" />
        </svg>
      ),
      title: "Prove",
      desc: "Compares fix vs. control group. Shows real lift.",
      color: "#34d399",
      bg: "from-emerald-500/15 to-emerald-500/5",
    },
    {
      icon: (
        <svg className="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4.26 10.147a60.438 60.438 0 00-.491 6.347A48.62 48.62 0 0112 20.904a48.62 48.62 0 018.232-4.41 60.46 60.46 0 00-.491-6.347m-15.482 0a50.636 50.636 0 00-2.658-.813A59.906 59.906 0 0112 3.493a59.903 59.903 0 0110.399 5.84c-.896.248-1.783.52-2.658.814m-15.482 0A50.717 50.717 0 0112 13.489a50.702 50.702 0 017.74-3.342M6.75 15a.75.75 0 100-1.5.75.75 0 000 1.5zm0 0v-3.675A55.378 55.378 0 0112 8.443m-7.007 11.55A5.981 5.981 0 006.75 15.75v-1.5" />
        </svg>
      ),
      title: "Learn",
      desc: "Gets smarter every week with your data.",
      color: "#7c3aed",
      bg: "from-[#7c3aed]/15 to-[#7c3aed]/5",
    },
  ];

  return (
    <section id="how" className="relative scroll-mt-20 py-20 sm:py-24">
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-transparent via-[#7c3aed]/[0.02] to-transparent" />

      <div className="relative mx-auto max-w-[72rem] px-6 lg:px-10">
        <R className="text-center">
          <span className="text-[14px] font-bold uppercase tracking-[0.2em] text-[#a855f7]">How it works</span>
          <h2 className="mt-5 text-[2.25rem] font-extrabold leading-[1.1] text-white sm:text-[3rem] lg:text-[3.5rem]">
            Install in 30 seconds.
            <br />
            <span style={{ color: "#e8a04e" }}>Then four steps run on their own.</span>
          </h2>
          <p className="mx-auto mt-6 max-w-[38rem] text-[18px] leading-[1.7] text-slate-400">
            One click on Shopify. No code, no theme edits. After that, HedgeSpark runs the full loop —
            and each cycle makes the next one sharper.
          </p>
        </R>

        <div className="mt-16 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {steps.map((s, i) => (
            <R key={s.title} d={i * 0.06}>
              <div className="group relative flex h-full flex-col rounded-3xl border border-white/[0.06] bg-[#0e0e1a] p-8 transition-all duration-300 hover:border-white/[0.12] hover:shadow-[0_8px_40px_-8px_rgba(0,0,0,0.4)]">
                {/* Icon */}
                <div
                  className={`flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br ${s.bg}`}
                  style={{ color: s.color }}
                >
                  {s.icon}
                </div>

                {/* Arrow between cards */}
                {i < 3 && (
                  <div className="pointer-events-none absolute -right-3.5 top-[3.5rem] z-10 hidden lg:block" style={{ color: s.color }}>
                    <svg width="24" height="14" viewBox="0 0 24 14" fill="none">
                      <path d="M0 7h20M16 1l6 6-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </div>
                )}

                <h3 className="mt-6 text-[20px] font-bold" style={{ color: s.color }}>{s.title}</h3>
                <p className="mt-3 flex-1 text-[16px] leading-[1.65] text-slate-400">{s.desc}</p>
              </div>
            </R>
          ))}
        </div>

        {/* Install CTA — absorbs former GetStarted section */}
        <R d={0.3}>
          <div className="mt-14 text-center">
            <a
              href={INSTALL_URL}
              className="hs-cta-gradient group relative inline-block rounded-2xl px-12 py-4 text-[17px] font-bold text-white transition-all duration-300 hover:shadow-[0_4px_60px_rgba(212,137,58,0.4)]"
            >
              <span className="pointer-events-none absolute inset-0 rounded-2xl bg-gradient-to-b from-white/[0.08] to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
              <span className="relative">Install on Shopify</span>
            </a>
            <p className="mt-4 text-[14px] text-slate-500">
              Tracking starts on the next visitor. First insights in about five minutes.
            </p>
          </div>
        </R>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   REAL EXAMPLE — one signal, one fix, proven in 7 days
   ══════════════════════════════════════════════════════════════════════════════ */

function RealExample() {
  return (
    <section id="example" className="relative scroll-mt-20 py-20 sm:py-24">
      <div className="mx-auto max-w-[72rem] px-6 lg:px-10">
        <R className="text-center">
          <span className="text-[14px] font-bold uppercase tracking-[0.2em] text-[#d4893a]">Real example</span>
          <h2 className="mt-5 text-[2.25rem] font-extrabold leading-[1.1] text-white sm:text-[3rem]">
            One product. One fix. 7 days.
          </h2>
          <p className="mx-auto mt-6 max-w-[34rem] text-[18px] leading-[1.7] text-slate-400">
            Here&apos;s what happened to a real store in its first week with HedgeSpark.
          </p>
        </R>

        <R d={0.08} className="mt-14">
          <div className="rounded-3xl border border-white/[0.06] bg-[#0e0e1a] p-6 sm:p-10">
            {/* Store context */}
            <div className="mb-10 flex flex-wrap items-center gap-x-8 gap-y-3 text-[15px] text-slate-500">
              <span>Home &amp; lifestyle store</span>
              <span className="text-slate-700">&middot;</span>
              <span>~4,200 monthly visitors</span>
              <span className="text-slate-700">&middot;</span>
              <span>23 products tracked</span>
            </div>

            <div className="grid gap-8 lg:grid-cols-3">
              {/* Day 1 */}
              <div>
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-rose-500/10">
                    <div className="h-3 w-3 rounded-full bg-rose-400 shadow-[0_0_8px_rgba(251,113,133,0.6)]" />
                  </div>
                  <div>
                    <div className="text-[14px] font-bold text-rose-400">Monday</div>
                    <div className="text-[13px] text-slate-400">Signal detected</div>
                  </div>
                </div>
                <div className="mt-5 rounded-2xl border border-rose-500/10 bg-rose-500/[0.02] p-5">
                  <p className="text-[17px] font-bold text-white">Organic Cotton Throw</p>
                  <div className="mt-4 space-y-2">
                    <div className="flex justify-between text-[15px]">
                      <span className="text-slate-400">Views per day</span>
                      <span className="font-bold text-white">68</span>
                    </div>
                    <div className="flex justify-between text-[15px]">
                      <span className="text-slate-400">Add to carts</span>
                      <span className="font-bold text-rose-400">0</span>
                    </div>
                    <div className="flex justify-between text-[15px]">
                      <span className="text-slate-400">Avg time on page</span>
                      <span className="font-bold text-white">28s</span>
                    </div>
                  </div>
                  <p className="mt-4 text-[15px] text-slate-400">
                    People are interested. Nobody&apos;s buying.
                  </p>
                </div>
              </div>

              {/* Day 2 */}
              <div>
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#d4893a]/10">
                    <div className="h-3 w-3 rounded-full bg-[#d4893a] shadow-[0_0_8px_rgba(212,137,58,0.6)]" />
                  </div>
                  <div>
                    <div className="text-[14px] font-bold text-[#d4893a]">Tuesday</div>
                    <div className="text-[13px] text-slate-400">Nudge deployed</div>
                  </div>
                </div>
                <div className="mt-5 rounded-2xl border border-[#d4893a]/10 bg-[#d4893a]/[0.02] p-5">
                  <p className="text-[17px] font-bold text-white">Social proof nudge</p>
                  <div className="mt-4 rounded-xl border border-white/[0.06] bg-white/[0.03] p-4">
                    <p className="text-center text-[16px] italic text-white">
                      &ldquo;14 people viewed this in the last 24 hours&rdquo;
                    </p>
                  </div>
                  <div className="mt-4 space-y-2">
                    <div className="flex justify-between text-[15px]">
                      <span className="text-slate-400">See the nudge</span>
                      <span className="font-bold text-white">80%</span>
                    </div>
                    <div className="flex justify-between text-[15px]">
                      <span className="text-slate-400">Control group</span>
                      <span className="font-bold text-slate-500">20%</span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Day 7 */}
              <div>
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-500/10">
                    <div className="h-3 w-3 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]" />
                  </div>
                  <div>
                    <div className="text-[14px] font-bold text-emerald-400">Next Monday</div>
                    <div className="text-[13px] text-slate-400">Lift confirmed</div>
                  </div>
                </div>
                <div className="mt-5 rounded-2xl border border-emerald-500/10 bg-emerald-500/[0.02] p-5">
                  <div className="space-y-2">
                    <div className="flex justify-between text-[15px]">
                      <span className="text-slate-400">With nudge</span>
                      <span className="font-bold text-emerald-400">3.1% bought</span>
                    </div>
                    <div className="flex justify-between text-[15px]">
                      <span className="text-slate-400">Without nudge</span>
                      <span className="font-bold text-slate-500">1.9% bought</span>
                    </div>
                  </div>
                  <div className="mt-5 rounded-xl border border-emerald-500/15 bg-emerald-500/[0.04] p-4 text-center">
                    <div className="text-[2.5rem] font-extrabold leading-none text-emerald-400">+63%</div>
                    <div className="mt-1 text-[15px] font-semibold text-emerald-300">more sales</div>
                  </div>
                  <p className="mt-4 text-center text-[17px] font-bold text-white">
                    $220/week recovered
                  </p>
                </div>
              </div>
            </div>

            <div className="mt-10 rounded-2xl border border-[#d4893a]/10 bg-[#d4893a]/[0.03] p-6 text-center">
              <p className="text-[18px] text-slate-300">
                This happened in <strong className="text-white">7 days</strong>. On <strong className="text-white">one product</strong>. With <strong className="text-white">zero manual work</strong>.
              </p>
              <p className="mt-2 text-[17px] font-semibold text-[#d4893a]">
                Your store has products like this right now.
              </p>
            </div>
          </div>
        </R>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   TRUST — live receipts + compliance posture + defendable moat
   Unified section absorbing the former TrustWall. Flow: live numbers →
   compliance badges → 5 moats → CTA to /transparency. Every value in the
   "receipts" grid is pulled live from /public/transparency; a fake number
   would be the worst possible receipt, so skeleton on fetch failure.
   ══════════════════════════════════════════════════════════════════════════════ */

type TrustReceiptsData = {
  self_healing: { autonomous_fixes_30d: number; last_fix_at: string | null };
  holdout_proof: {
    actions_measured_30d: number;
    actions_success_30d: number;
    actions_no_effect_30d: number;
  };
  preflight: { audit_count: number };
};

function useTrustReceipts() {
  const [data, setData] = useState<TrustReceiptsData | null>(null);
  useEffect(() => {
    const API = process.env.NEXT_PUBLIC_API_BASE_URL || "";
    fetch(`${API}/public/transparency`, { signal: AbortSignal.timeout(6000) })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: TrustReceiptsData | null) => { if (d) setData(d); })
      .catch((err: unknown) => {
        const e = err as { name?: string; message?: string } | null;
        reportFrontendError({
          component: "useTrustReceipts",
          error_type: e?.name ?? "FetchError",
          message: e?.message ?? "Failed to fetch /public/transparency",
          severity: "info",
        });
      });
  }, []);
  return data;
}

function Trust() {
  const d = useTrustReceipts();

  // Each receipt card carries its own accent so the 3 cards read as
  // distinct signal classes rather than a monolithic amber trio.
  // Mapping per CLAUDE.md §4 palette semantics:
  //   emerald → good/growth (self-healing outcomes)
  //   amber   → warm/warning/counterfactual (holdout-measured proof)
  //   violet  → intelligence/peer/learning (structural audits)
  const cards = d
    ? [
        {
          label: "Autonomous fixes · 30 days",
          value: d.self_healing.autonomous_fixes_30d,
          hint: "Incidents the self-healing pipeline caught before a merchant noticed.",
          accent: "#34d399",
        },
        {
          label: "Actions measured · 30 days",
          value: d.holdout_proof.actions_measured_30d,
          hint: `${d.holdout_proof.actions_success_30d} improved a merchant metric. ${d.holdout_proof.actions_no_effect_30d} didn't. We publish both.`,
          accent: "#e8a04e",
        },
        {
          label: "Structural audits per commit",
          value: d.preflight.audit_count,
          hint: "Every commit — including mine — must pass all of them, or the deploy blocks.",
          accent: "#a78bfa",
        },
      ]
    : null;

  const badges = [
    { label: "GDPR Compliant", icon: "🇪🇺", desc: "Data residency · audit log hash chain · breach runbook", color: "#60a5fa" },
    { label: "SOC2 In Progress", icon: "🛡️", desc: "Type II motion started · 11/11 compliance score", color: "#10b981" },
    { label: "Holdout-Measured", icon: "🔬", desc: "Every claim tested against a control group · p<0.05", color: "#e8a04e" },
    { label: "Zero PII in LLM", icon: "🔒", desc: "Runtime guard blocks personal data from prompts", color: "#a78bfa" },
  ];

  // 5 defendable moats — SVG icons (not emojis) per founder feedback,
  // each card carries its own accent for semantic differentiation:
  //   rose    → alert/risk (RARS)
  //   emerald → proven/growth (holdout)
  //   blue    → secure/bounds (autonomy)
  //   violet  → intelligence/learning (closed-loop)
  //   amber   → warm/speed (insight)
  const moats: Array<{ k: string; v: string; color: string; icon: React.ReactElement }> = [
    {
      k: "Revenue-at-Risk Score",
      v: "See losses before they compound",
      color: "#f87171",
      icon: (
        <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
        </svg>
      ),
    },
    {
      k: "Holdout-proven savings",
      v: "Every amount saved is statistically defended",
      color: "#34d399",
      icon: (
        <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" />
        </svg>
      ),
    },
    {
      k: "Delegated Autonomy",
      v: "Pre-approve bounds, system acts within them",
      color: "#60a5fa",
      icon: (
        <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
        </svg>
      ),
    },
    {
      k: "Closed-loop learning",
      v: "Self-heals, self-improves, self-measures",
      color: "#a78bfa",
      icon: (
        <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
        </svg>
      ),
    },
    {
      k: "60-second first insight",
      v: "Real numbers before your coffee is done",
      color: "#e8a04e",
      icon: (
        <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      ),
    },
  ];

  return (
    <section className="relative border-t border-white/[0.04] bg-[#05050b] py-20 sm:py-24">
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-transparent via-[#e8a04e]/[0.015] to-transparent" />

      <div className="relative mx-auto max-w-[72rem] px-6 lg:px-10">
        {/* Section header — "The numbers are real. So are the receipts." */}
        <R className="text-center">
          <span className="text-[14px] font-bold uppercase tracking-[0.2em] text-[#e8a04e]">
            Trust, with receipts
          </span>
          <h2 className="mt-5 text-[2.25rem] font-extrabold leading-[1.1] text-white sm:text-[3rem]">
            The numbers are real.
            <br />
            So are the receipts.
          </h2>
          <p className="mx-auto mt-5 max-w-xl text-[16px] leading-relaxed text-slate-400">
            Three live numbers from the last 30 days. Every one reproducible from an
            append-only audit log inside the product.
          </p>
        </R>

        {/* Live receipts — 3 big-number cards */}
        <div className="mt-16 grid gap-6 sm:grid-cols-3">
          {cards
            ? cards.map((c, i) => (
                <R key={c.label} d={i * 0.08} className="h-full">
                  <div
                    className="flex h-full flex-col rounded-3xl border p-7 transition-all duration-300"
                    style={{
                      borderColor: alpha(c.accent, 0.25),
                      background: `linear-gradient(160deg, ${alpha(c.accent, 0.05)} 0%, transparent 75%)`,
                    }}
                  >
                    <div
                      className="text-[12px] font-bold uppercase tracking-[0.16em]"
                      style={{ color: c.accent }}
                    >
                      {c.label}
                    </div>
                    <div className="mt-5 text-[52px] font-extrabold leading-none tracking-tight tabular-nums text-white sm:text-[60px]">
                      {c.value.toLocaleString()}
                    </div>
                    <p className="mt-5 flex-1 text-[14px] leading-[1.6] text-slate-400">
                      {c.hint}
                    </p>
                  </div>
                </R>
              ))
            : [0, 1, 2].map((i) => (
                <div
                  key={i}
                  className="h-[260px] animate-pulse rounded-3xl border border-white/[0.06] bg-white/[0.02]"
                />
              ))}
        </div>

        {/* Compliance badges — 4 cards below receipts */}
        <div className="mt-14 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {badges.map((b, i) => (
            <R key={b.label} d={i * 0.06}>
              <div
                className="h-full rounded-2xl border p-5"
                style={{
                  background: "linear-gradient(135deg, rgba(11,18,32,0.9) 0%, rgba(20,26,48,0.5) 100%)",
                  borderColor: `${b.color}33`,
                }}
              >
                <div className="flex items-center gap-3 mb-2">
                  <span className="text-[22px]">{b.icon}</span>
                  <span className="text-[14px] font-bold" style={{ color: b.color }}>{b.label}</span>
                </div>
                <div className="text-[12px] leading-relaxed text-slate-400">{b.desc}</div>
              </div>
            </R>
          ))}
        </div>

        {/* 5 defendable moats — no outer container per founder feedback;
            cards flow directly in section. SVG icons + per-card accent. */}
        <div className="mt-16">
          <R>
            <div className="mb-8 text-center">
              <span className="text-[12px] font-bold uppercase tracking-[0.18em] text-slate-400">
                The defendable moat · 5 things no competitor can copy
              </span>
            </div>
          </R>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
            {moats.map((m, i) => (
              <R key={m.k} d={i * 0.05} className="h-full">
                <div
                  className="flex h-full flex-col rounded-2xl border border-white/[0.06] bg-[#0e0e1a] p-5 transition-all duration-300"
                  style={{ borderColor: alpha(m.color, 0.18) }}
                >
                  <div
                    className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl"
                    style={{
                      color: m.color,
                      background: alpha(m.color, 0.1),
                    }}
                  >
                    {m.icon}
                  </div>
                  <div className="text-[14px] font-bold text-white mb-1.5">{m.k}</div>
                  <div className="flex-1 text-[12.5px] leading-[1.55] text-slate-400">{m.v}</div>
                </div>
              </R>
            ))}
          </div>
        </div>

        {/* CTA to full receipts page */}
        <R d={0.15}>
          <div className="mt-12 text-center">
            <a
              href="/transparency"
              className="inline-flex items-center gap-2 rounded-full border border-[#e8a04e]/30 bg-[#e8a04e]/5 px-6 py-3 text-[15px] font-bold text-[#e8a04e] transition-all duration-300 hover:border-[#e8a04e]/50 hover:bg-[#e8a04e]/10"
            >
              See every receipt <span aria-hidden>→</span>
            </a>
          </div>
        </R>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   PRICING — 3 tier cards + comparison table
   Prices intentionally withheld during closed beta — per founder: we'll know
   the real numbers only when beta ends. Tier composition comes from the
   pricing matrix memo §2; comparison table shows a 15-row representative
   sample of the full 32-feature split.
   ══════════════════════════════════════════════════════════════════════════════ */

function Pricing() {
  const check = (color: string) => (
    <svg
      className="mt-0.5 h-5 w-5 flex-shrink-0"
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={2.5}
      style={{ stroke: color }}
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
    </svg>
  );

  // Comparison table — synced 2026-04-29 to Lite strategic close
  // (commit fe278d5) + RARS-restore correction (founder directive
  // same day): RARS is the Lite acquisition hook BECAUSE no $0-70
  // competitor ships an equivalent. Lite gets the headline + prevented
  // + net ROI; the full 5-dim breakdown is Pro-only via tier-aware
  // response. Visitor Intent stays Pro (founder explicit). Multi-store
  // moved INTO Lite per $0-60 parity vs Putler $20.
  // ✓ = included at that tier; — = not included.
  const compare: Array<{ group: string; rows: Array<{ name: string; starter: boolean; pro: boolean; scale: boolean }> }> = [
    {
      group: "Foundation (all plans)",
      rows: [
        { name: "First-party pixel tracker", starter: true, pro: true, scale: true },
        { name: "Revenue at Risk Score — the entry hook (Lite: headline · Pro: 5-dim breakdown)", starter: true, pro: true, scale: true },
        { name: "Today + last 7 days KPI snapshot", starter: true, pro: true, scale: true },
        { name: "Daily intelligence brief", starter: true, pro: true, scale: true },
        { name: "Multi-currency rollup (no fake-sum)", starter: true, pro: true, scale: true },
      ],
    },
    {
      group: "Lite — full $0-60 parity",
      rows: [
        { name: "P&L · attribution · cohort retention · refunds", starter: true, pro: true, scale: true },
        { name: "Multi-store consolidation (per-currency)", starter: true, pro: true, scale: true },
        { name: "11-segment RFM + geographic drilldown", starter: true, pro: true, scale: true },
        { name: "Custom report builder + scheduled email", starter: true, pro: true, scale: true },
        { name: "Google Sheets / CSV / PDF export", starter: true, pro: true, scale: true },
        { name: "Post-purchase survey (multi-question)", starter: true, pro: true, scale: true },
        { name: "AI assistant (SparkChat) on your data", starter: true, pro: true, scale: true },
        { name: "Peer benchmarks (anonymous)", starter: true, pro: true, scale: true },
        { name: "CAC : LTV unit economics", starter: true, pro: true, scale: true },
        { name: "COGS bulk import (CSV)", starter: true, pro: true, scale: true },
        { name: "Inventory KPIs · stock-at-risk · forecast", starter: true, pro: true, scale: true },
      ],
    },
    {
      group: "Pro — moat intelligence layer",
      rows: [
        { name: "Visitor intent scoring (per-visitor hot/warm/cold)", starter: false, pro: true, scale: true },
        { name: "Causal lift + Why engine (real A/B holdout)", starter: false, pro: true, scale: true },
        { name: "Night Shift Agent + Competitor Playbook", starter: false, pro: true, scale: true },
        { name: "Anomaly Fusion + Replay", starter: false, pro: true, scale: true },
        { name: "Counterfactual Explorer", starter: false, pro: true, scale: true },
        { name: "Revenue Autopsy + Revenue Genome", starter: false, pro: true, scale: true },
        { name: "Nudge DNA + holdout-measured Lift Report", starter: false, pro: true, scale: true },
        { name: "MTA model compare + Price Sensitivity", starter: false, pro: true, scale: true },
        { name: "Session replay", starter: false, pro: true, scale: true },
      ],
    },
    {
      group: "Scale (infrastructure adds)",
      rows: [
        { name: "Unified ads connector (Meta / Google / TikTok)", starter: false, pro: false, scale: true },
        { name: "Agency white-label console", starter: false, pro: false, scale: true },
        { name: "API access + outbound webhooks", starter: false, pro: false, scale: true },
      ],
    },
  ];

  // Tier feature lists — synced 2026-04-29 to Lite strategic close
  // (commit fe278d5) + RARS-restore correction same day. RARS is
  // Lite's acquisition hook (headline + prevented + net ROI); the
  // 5-dim breakdown remains Pro-only via tier-aware response.
  // Visitor Intent stays Pro (founder explicit). Multi-store moved
  // INTO Lite per parity vs Putler $20.
  const tiers = [
    {
      key: "lite",
      label: "Lite",
      tagline: "Foundation signals",
      desc: "Full $0-60 competitor parity, plus the Revenue at Risk Score nobody else ships at any price.",
      features: [
        "Revenue at Risk Score (the differentiator)",
        "First-party pixel tracker",
        "P&L · attribution · cohort retention · refunds",
        "Multi-store consolidation (per-currency)",
        "11-segment RFM + geographic drilldown",
        "Custom reports + Sheets / CSV / PDF export",
        "AI assistant (SparkChat) on your data",
        "CAC : LTV · COGS · Inventory KPIs",
        "Daily intelligence brief",
      ],
      accent: "#c4b5fd",
      recommended: false,
    },
    {
      key: "pro",
      label: "Pro",
      tagline: "Find · Fix · Prove",
      desc: "Everything in Lite, plus the moat intelligence layer no $0-130 tool ships.",
      features: [
        "Everything in Lite",
        "Revenue at Risk — full 5-dimension breakdown",
        "Visitor intent scoring (per-visitor hot / warm / cold)",
        "Causal lift + Why engine (real A/B holdout)",
        "Night Shift Agent + Competitor Playbook",
        "Anomaly Fusion + Replay + Counterfactual Explorer",
        "Revenue Autopsy + Genome + Nudge DNA",
        "MTA model compare + Price Sensitivity",
        "Session replay + scroll heatmaps",
      ],
      accent: "#e8a04e",
      recommended: true,
    },
    {
      key: "scale",
      label: "Scale",
      tagline: "Agency + infrastructure",
      desc: "For agencies and teams that need unified ads, white-label, and API.",
      features: [
        "Everything in Pro",
        "Unified ads connector (Meta / Google / TikTok)",
        "Agency white-label console",
        "API access + outbound webhooks",
      ],
      accent: "#3b82f6",
      recommended: false,
    },
  ];

  return (
    <section id="pricing" className="relative scroll-mt-20 py-20 sm:py-24">
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-transparent via-[#e8a04e]/[0.015] to-transparent" />

      <div className="relative mx-auto max-w-[76rem] px-6 lg:px-10">
        <R className="text-center">
          <span className="text-[14px] font-bold uppercase tracking-[0.2em] text-[#e8a04e]">Plans</span>
          <h2 className="mt-5 text-[2.25rem] font-extrabold leading-[1.1] text-[#e8a04e] sm:text-[3rem]">
            Three plans. One product.
          </h2>
          <p className="mx-auto mt-5 max-w-2xl text-[16px] leading-relaxed text-slate-400">
            Lite gets you the foundation. Pro adds the intelligence and proof layer.
            Scale adds agency, multi-store, and API. Pricing is locked at GA —
            early-access stores get the launch rate carried forward.
          </p>
        </R>

        {/* 3 tier cards — tier-specific accents per founder brief:
             Lite = lilac/cream  ·  Pro = Spark wordmark gradient  ·  Scale = blue */}
        <div className="mt-16 grid gap-6 lg:grid-cols-3">
          {tiers.map((t, i) => {
            const isPro = t.key === "pro";
            return (
              <R key={t.key} d={i * 0.06} className="h-full">
                <div
                  className="relative flex h-full flex-col overflow-hidden rounded-3xl border p-8 transition-all duration-300 sm:p-10"
                  style={{
                    borderColor: isPro ? "rgba(232,160,78,0.30)" : alpha(t.accent, 0.22),
                    background: isPro
                      ? "linear-gradient(180deg, rgba(232,160,78,0.05) 0%, transparent 100%)"
                      : `linear-gradient(180deg, ${alpha(t.accent, 0.035)} 0%, #0e0e1a 60%)`,
                  }}
                >
                  {t.recommended && (
                    <div className="absolute -top-px left-8 rounded-b-xl bg-[#e8a04e] px-5 py-2 text-[12px] font-bold uppercase tracking-[0.12em] text-[#0b1220] shadow-[0_4px_20px_-4px_rgba(232,160,78,0.5)]">
                      Recommended
                    </div>
                  )}
                  {isPro ? (
                    <div className="hs-brand-gradient text-[15px] font-extrabold uppercase tracking-[0.18em]">
                      {t.label}
                    </div>
                  ) : (
                    <div
                      className="text-[14px] font-bold uppercase tracking-[0.18em]"
                      style={{ color: t.accent }}
                    >
                      {t.label}
                    </div>
                  )}
                  <div className="mt-6">
                    <span className="text-[26px] font-extrabold tracking-tight text-white sm:text-[30px]">
                      {t.tagline}
                    </span>
                  </div>
                  <p className="mt-4 text-[16px] leading-relaxed text-slate-400">
                    {t.desc}
                  </p>
                  {/* Price placeholder — honest until billing ships at GA */}
                  <div className="mt-6 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
                    <div
                      className="text-[11px] font-bold uppercase tracking-[0.14em]"
                      style={{ color: isPro ? "#e8a04e" : t.accent }}
                    >
                      Early access
                    </div>
                    <div className="mt-1 text-[14px] text-slate-300">
                      Final pricing announced at GA. Install now to lock the launch rate.
                    </div>
                  </div>
                  <ul className="mt-8 flex-1 space-y-3.5">
                    {t.features.map((f) => {
                      const isInherited = f.startsWith("Everything in");
                      return (
                        <li
                          key={f}
                          className={`flex items-start gap-3 text-[15px] ${
                            isInherited ? "text-slate-500" : "text-slate-200"
                          }`}
                        >
                          {check(isPro ? "#e8a04e" : t.accent)}
                          {f}
                        </li>
                      );
                    })}
                  </ul>
                  <a
                    href={INSTALL_URL}
                    className={
                      isPro
                        ? "hs-cta-gradient mt-10 block rounded-2xl py-4 text-center text-[16px] font-bold text-white transition-all duration-300 hover:shadow-[0_4px_40px_rgba(232,160,78,0.3)]"
                        : "mt-10 block rounded-2xl border py-4 text-center text-[16px] font-bold text-slate-200 transition-all duration-300 hover:bg-white/[0.04] hover:text-white"
                    }
                    style={
                      isPro
                        ? undefined
                        : {
                            borderColor: alpha(t.accent, 0.3),
                            background: alpha(t.accent, 0.05),
                          }
                    }
                  >
                    Install on Shopify
                  </a>
                </div>
              </R>
            );
          })}
        </div>

        {/* Comparison table */}
        <R d={0.2}>
          <div className="mt-20">
            <div className="mb-8 text-center">
              <span className="text-[12px] font-bold uppercase tracking-[0.2em] text-[#e8a04e]">
                Compare plans
              </span>
              <h3 className="mt-3 text-[1.5rem] font-extrabold text-white sm:text-[1.75rem]">
                What&apos;s in each plan.
              </h3>
            </div>
            <div className="overflow-x-auto rounded-3xl border border-white/[0.06] bg-[#0a0a14]">
              <table className="w-full min-w-[640px] border-collapse">
                <thead>
                  <tr className="border-b border-white/[0.06]">
                    <th className="p-5 text-left text-[12px] font-bold uppercase tracking-[0.14em] text-slate-400">
                      Capability
                    </th>
                    <th className="p-5 text-center text-[13px] font-bold uppercase tracking-[0.12em] text-slate-300">
                      Lite
                    </th>
                    <th className="p-5 text-center text-[13px] font-bold uppercase tracking-[0.12em] text-[#e8a04e]">
                      Pro
                    </th>
                    <th className="p-5 text-center text-[13px] font-bold uppercase tracking-[0.12em] text-sky-300">
                      Scale
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {compare.map((section) => (
                    <Fragment key={section.group}>
                      <tr className="bg-white/[0.02]">
                        <td
                          colSpan={4}
                          className="px-5 py-3 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400"
                        >
                          {section.group}
                        </td>
                      </tr>
                      {section.rows.map((r) => (
                        <tr
                          key={r.name}
                          className="border-t border-white/[0.04] transition-colors hover:bg-white/[0.02]"
                        >
                          <td className="p-4 text-[14px] text-slate-300">{r.name}</td>
                          <td className="p-4 text-center">
                            {r.starter ? (
                              <span className="text-[17px] text-emerald-400/80">✓</span>
                            ) : (
                              <span className="text-[14px] text-slate-600">—</span>
                            )}
                          </td>
                          <td className="p-4 text-center">
                            {r.pro ? (
                              <span className="text-[17px] text-[#e8a04e]">✓</span>
                            ) : (
                              <span className="text-[14px] text-slate-600">—</span>
                            )}
                          </td>
                          <td className="p-4 text-center">
                            {r.scale ? (
                              <span className="text-[17px] text-sky-400">✓</span>
                            ) : (
                              <span className="text-[14px] text-slate-600">—</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-5 text-center text-[13px] text-slate-400">
              A shortlist of the 32 capabilities across all tiers. Every number in the dashboard is
              computed from real data in your store.
            </p>
          </div>
        </R>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   FAQ
   ══════════════════════════════════════════════════════════════════════════════ */

function FAQ() {
  const items = [
    {
      q: "Will this slow down my store?",
      a: "No. HedgeSpark is under 5kb. No theme changes. No render blocking. Your existing analytics scripts are heavier.",
    },
    {
      q: "What data do you collect?",
      a: "Only behavioral signals: scroll depth, dwell time, clicks, cart events. No personal data. No third-party sharing. GDPR compliant. Encrypted at rest.",
    },
    {
      q: "How is this different from Google Analytics?",
      a: "Analytics tells you what happened. HedgeSpark tells you what to do about it, does it, then proves whether it worked. One is a report. This is a system.",
    },
    {
      q: "Do I need a developer?",
      a: "No. One-click Shopify install. No code. No theme editor. Tracking starts on the next visitor.",
    },
    {
      q: "Can I switch plans?",
      a: "Yes. You can move between Lite, Pro, and Scale at any time from your dashboard — no support ticket, no phone call. Your detection signals and history stay intact across plan changes.",
    },
  ];

  return (
    <section id="faq" className="relative scroll-mt-20 py-20 sm:py-24">
      <div className="mx-auto max-w-[48rem] px-6 lg:px-10">
        <R className="text-center">
          <span className="text-[14px] font-bold uppercase tracking-[0.2em] text-[#d4893a]">FAQ</span>
          <h2 className="mt-5 text-[2.25rem] font-extrabold text-white sm:text-[3rem]">
            Common questions
          </h2>
        </R>
        <div className="mt-12 space-y-0 divide-y divide-white/[0.06]">
          {items.map((item, i) => (
            <R key={i} d={i * 0.04}>
              <div className="py-8">
                <h3 className="text-[18px] font-bold text-white">{item.q}</h3>
                <p className="mt-3 text-[16px] leading-[1.7] text-slate-400">{item.a}</p>
              </div>
            </R>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   FOOTER
   ══════════════════════════════════════════════════════════════════════════════ */

function Footer() {
  return (
    <footer className="border-t border-white/[0.04] py-14">
      <div className="mx-auto max-w-[76rem] px-6 lg:px-10">
        <div className="flex flex-col items-center justify-between gap-6 sm:flex-row">
          <div className="flex items-center">
            <span className="hs-brand-gradient text-[18px] font-extrabold tracking-tight">
              HedgeSpark
            </span>
          </div>
          <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-2 text-[15px] text-slate-400">
            <a href="/app" className="transition-colors duration-200 hover:text-white">Dashboard</a>
            <a href="/pricing" className="transition-colors duration-200 hover:text-white">Pricing</a>
            <a href="/privacy" className="transition-colors duration-200 hover:text-white">Privacy</a>
            <a href="/terms" className="transition-colors duration-200 hover:text-white">Terms</a>
            <a href="mailto:dev@hedgesparkhq.com" className="transition-colors duration-200 hover:text-white">Support</a>
          </div>
          <span className="text-[14px] text-slate-400">&copy; {new Date().getFullYear()} HedgeSpark</span>
        </div>
      </div>
    </footer>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   PAGE
   ══════════════════════════════════════════════════════════════════════════════ */

export default function LandingPage() {
  useOAuthRedirect();

  return (
    <div className="min-h-screen bg-[#07070f] text-white antialiased">
      <Nav />
      <Hero />
      <Problem />
      <Features />
      <HowItWorks />
      <RealExample />
      <Trust />
      <Pricing />
      <FAQ />
      <Footer />
    </div>
  );
}
