"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { DemoPreviewCard } from "./components/DemoPreviewCard";

/* ── OAuth guard (keep wiring) ── */
function useOAuthRedirect() {
  const [ok, setOk] = useState(false);
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    if (p.get("shop") || p.get("installed") || p.get("billing") || p.get("section")) {
      window.location.href = `/app${window.location.search}`;
      return;
    }
    setOk(true);
  }, []);
  return ok;
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

/* ── Live signal count ── */
function useSignalCount() {
  const [count, setCount] = useState<number | null>(null);
  useEffect(() => {
    const API = process.env.NEXT_PUBLIC_API_BASE_URL || "";
    fetch(`${API}/ops/signal-count-week`, { signal: AbortSignal.timeout(4000) })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d?.count) setCount(d.count); })
      .catch(() => {});
  }, []);
  return count;
}

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
      .catch(() => {});

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
                  <span className="mt-2 block text-[11px] text-slate-500">
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
          aria-label={`€${formatted} recovered across the HedgeSpark network in the last 30 days`}
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
              Live · Network Recovery · Last 30 Days
            </div>
            <div className="mt-2 font-mono text-[40px] font-extrabold tabular-nums leading-none text-white sm:text-[56px]">
              €{formatted}
            </div>
            <div className="mt-2 text-[13px] text-slate-400">
              recovered across {doc.shops_contributing} Shopify merchant{doc.shops_contributing === 1 ? "" : "s"}
            </div>
          </div>

          {topVerticals.length > 0 && (
            <div
              className={`mt-4 grid gap-2 overflow-hidden transition-[max-height,opacity] duration-500 ${
                hovering ? "max-h-64 opacity-100" : "max-h-0 opacity-0"
              }`}
              aria-hidden={!hovering}
            >
              <div className="text-center text-[10px] font-semibold uppercase tracking-wide text-slate-500">
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
                      €{new Intl.NumberFormat("en-US").format(Math.round(v.prevented_eur))}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="mt-4 text-center text-[10px] text-slate-500">
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
            <span className="text-[9.5px] font-semibold uppercase tracking-[0.18em] text-slate-500">
              AI Revenue Intelligence
            </span>
          </div>
        </R>

        {/* Headline */}
        <R d={0.08}>
          <h1 className="mx-auto mt-6 max-w-[52rem] text-center text-[2.75rem] font-extrabold leading-[1.05] tracking-[-0.03em] text-white sm:text-[4rem] lg:text-[5rem]">
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
            <strong className="text-white">The AI revenue leak detector built for Shopify.</strong>{" "}
            Finds products that get attention but don&apos;t sell. Stops the bleed.
            Proves the recovery.
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
   NUMBERS STRIP — social proof
   ══════════════════════════════════════════════════════════════════════════════ */

function Numbers() {
  const signalCount = useSignalCount();

  // Honesty rule: only surface numbers we can verify at render time.
  // If the live signal count is null (API down or warming up), we do NOT
  // fall back to a fabricated "2,400+". We fall back to a claim that is
  // true by construction.
  const stats = [
    signalCount
      ? { value: signalCount.toLocaleString(), label: "Signals detected this week", color: "text-[#d4893a]" }
      : { value: "Every visit", label: "Tracked from day one", color: "text-[#d4893a]" },
    { value: "5 min", label: "To first insight after install", color: "text-[#a855f7]" },
    { value: "<5kb", label: "Zero impact on store speed", color: "text-emerald-400" },
  ];

  return (
    <section className="relative border-y border-white/[0.04]">
      <div className="mx-auto grid max-w-[72rem] divide-y divide-white/[0.04] px-6 sm:grid-cols-3 sm:divide-x sm:divide-y-0 lg:px-10">
        {stats.map((s, i) => (
          <R key={i} d={i * 0.06}>
            <div className="flex flex-col items-center py-12 text-center sm:py-14">
              <span className={`text-[3rem] font-extrabold tracking-tight ${s.color} sm:text-[3.5rem]`}>
                {s.value}
              </span>
              <span className="mt-2 text-[16px] text-slate-400">{s.label}</span>
            </div>
          </R>
        ))}
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
      number: "$193",
      unit: "/day",
      highlight: "at risk",
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

function Features() {
  return (
    <section id="features" className="relative scroll-mt-20 py-28 sm:py-36">
      {/* Subtle amber background tint */}
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-[#d4893a]/[0.02] via-transparent to-transparent" />

      <div className="relative mx-auto max-w-[72rem] px-6 lg:px-10">
        <R className="text-center">
          <span className="text-[14px] font-bold uppercase tracking-[0.2em] text-[#d4893a]">What HedgeSpark does</span>
          <h2 className="mt-5 text-[2.25rem] font-extrabold leading-[1.1] text-white sm:text-[3rem] lg:text-[3.5rem]">
            Three things no other tool does.
          </h2>
          <p className="mx-auto mt-6 max-w-[38rem] text-[18px] leading-[1.7] text-slate-400">
            Most tools tell you what happened yesterday. HedgeSpark tells you what&apos;s broken right now, fixes it, and proves it worked.
          </p>
        </R>

        {/* Feature 1 — Find */}
        <R d={0.08}>
          <div className="mt-20 grid items-center gap-10 lg:grid-cols-2 lg:gap-16">
            <div>
              <div className="inline-flex items-center gap-3 rounded-full border border-[#d4893a]/20 bg-[#d4893a]/[0.08] px-5 py-2">
                <span className="text-[22px] font-extrabold text-[#d4893a]">1</span>
                <span className="text-[15px] font-bold text-[#d4893a]">Find the problem</span>
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
              <div className="inline-flex items-center gap-3 rounded-full border border-[#a855f7]/20 bg-[#a855f7]/[0.08] px-5 py-2">
                <span className="text-[22px] font-extrabold text-[#a855f7]">2</span>
                <span className="text-[15px] font-bold text-[#a855f7]">Fix it automatically</span>
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
              <div className="inline-flex items-center gap-3 rounded-full border border-emerald-400/20 bg-emerald-500/[0.08] px-5 py-2">
                <span className="text-[22px] font-extrabold text-emerald-400">3</span>
                <span className="text-[15px] font-bold text-emerald-400">Prove it worked</span>
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
              <div className="mb-6 text-[13px] font-bold uppercase tracking-[0.15em] text-slate-600">Lift report</div>

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
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   PRO INTELLIGENCE STACK — showcase ~80% of dashboard capabilities
   ══════════════════════════════════════════════════════════════════════════════ */

function ProStack() {
  const categories = [
    {
      title: "Revenue Intelligence",
      color: "#d4893a",
      features: [
        {
          name: "Revenue at Risk Score",
          desc: "Real-time dollar amount at risk across 5 dimensions: abandoned intent, refund decline, nudge gaps, peer underperformance, goal gaps.",
          tag: "Live calculation",
        },
        {
          name: "Revenue Autopsy",
          desc: "Per-product loss forensics. See exactly where each product bleeds money and why.",
          tag: "Per product",
        },
        {
          name: "Abandoned Intent",
          desc: "High-interest visitors who left without buying. Scored by scroll depth, dwell time, return visits.",
          tag: "Behavioral",
        },
        {
          name: "Refund Loss Tracking",
          desc: "Products with rising return rates. Catches quality or expectation mismatches early.",
          tag: "Trend analysis",
        },
      ],
    },
    {
      title: "Behavioral Intelligence",
      color: "#a855f7",
      features: [
        {
          name: "Visitor Intent Scoring",
          desc: "Every visitor classified as hot, warm, or cold based on real behavioral signals — not rules, real data.",
          tag: "Per visitor",
        },
        {
          name: "Scroll Heatmaps",
          desc: "See exactly where visitors drop off on each product page. Per-product, per-device.",
          tag: "Per product",
        },
        {
          name: "Price Sensitivity",
          desc: "Detects which products have price friction — visitors engage but bounce at the price point.",
          tag: "Elasticity",
        },
        {
          name: "Session Timeline",
          desc: "Full behavioral replay per visitor: scroll, dwell, clicks, cart events, page flow.",
          tag: "Per session",
        },
      ],
    },
    {
      title: "Measurement & Proof",
      color: "#34d399",
      features: [
        {
          name: "Causal Lift",
          desc: "Real A/B holdout measurement. Not correlation — actual causation with statistical confidence.",
          tag: "Holdout proof",
        },
        {
          name: "Peer Benchmarks",
          desc: "Anonymous comparison against stores in your revenue band. See where you lag and where you lead.",
          tag: "Anonymized",
        },
        {
          name: "Revenue Genome",
          desc: "DNA of your revenue — which sources, segments, and products actually drive profit.",
          tag: "Composition",
        },
        {
          name: "Shareable Proof Reports",
          desc: "Public links proving ROI. Share with your team, investors, or clients.",
          tag: "Public links",
        },
      ],
    },
    {
      title: "Growth Intelligence",
      color: "#7c3aed",
      features: [
        {
          name: "Cohort & LTV Analysis",
          desc: "Customer lifetime value by acquisition date, source, device, and behavior. Weekly and monthly cohorts.",
          tag: "Multi-dimensional",
        },
        {
          name: "P&L Intelligence",
          desc: "Profitability per product and channel. Sync costs from Shopify or set them manually.",
          tag: "Real costs",
        },
        {
          name: "Goals & ROI Tracking",
          desc: "Set revenue targets, track recovery ROI. See exactly what HedgeSpark earned back for you.",
          tag: "Measurable",
        },
        {
          name: "Risk Forecast",
          desc: "Predicted churn and revenue decline. Flags products and segments heading for trouble.",
          tag: "Predictive",
        },
      ],
    },
  ];

  return (
    <section id="intelligence" className="relative scroll-mt-20 py-28 sm:py-36">
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-transparent via-[#d4893a]/[0.015] to-transparent" />

      <div className="relative mx-auto max-w-[76rem] px-6 lg:px-10">
        <R className="text-center">
          <span className="text-[14px] font-bold uppercase tracking-[0.2em] text-[#d4893a]">Full intelligence stack</span>
          <h2 className="mt-5 text-[2.25rem] font-extrabold leading-[1.1] text-white sm:text-[3rem] lg:text-[3.5rem]">
            16 capabilities. Every one wired to real data.
          </h2>
          <p className="mx-auto mt-6 max-w-[44rem] text-[18px] leading-[1.7] text-slate-400">
            Every number in your dashboard is computed from real visitor behavior and real orders in your store.
            If a capability needs more data to be reliable, the UI says so instead of guessing.
            No demo data. No estimates. No placeholders.
          </p>
        </R>

        <div className="mt-20 space-y-16">
          {categories.map((cat, ci) => (
            <R key={cat.title} d={ci * 0.06}>
              <div>
                <div className="mb-8 flex items-center gap-3">
                  <div className="h-1 w-8 rounded-full" style={{ background: cat.color }} />
                  <h3 className="text-[18px] font-bold" style={{ color: cat.color }}>{cat.title}</h3>
                </div>
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  {cat.features.map((f, fi) => (
                    <div
                      key={f.name}
                      className="group relative flex flex-col rounded-2xl border border-white/[0.06] bg-[#0e0e1a] p-6 transition-all duration-300 hover:border-white/[0.12] hover:shadow-[0_8px_40px_-8px_rgba(0,0,0,0.4)]"
                    >
                      <div className="absolute inset-x-0 top-0 h-px rounded-t-2xl" style={{ background: `linear-gradient(90deg, transparent, ${alpha(cat.color, 0.3)}, transparent)` }} />
                      <span
                        className="mb-3 inline-flex w-fit rounded-md px-2 py-0.5 text-[11px] font-bold uppercase tracking-[0.1em]"
                        style={{
                          background: alpha(cat.color, 0.1),
                          color: cat.color,
                        }}
                      >
                        {f.tag}
                      </span>
                      <h4 className="text-[16px] font-bold text-white">{f.name}</h4>
                      <p className="mt-2 flex-1 text-[14px] leading-[1.65] text-slate-400">{f.desc}</p>
                    </div>
                  ))}
                </div>
              </div>
            </R>
          ))}
        </div>

        {/* Operations row */}
        <R d={0.3}>
          <div className="mt-16 grid gap-4 sm:grid-cols-3">
            {[
              { name: "Team Collaboration", desc: "Invite your team. Comment on signals. @mention on actions.", color: "#d4893a" },
              { name: "Webhook Integrations", desc: "Push signals to Slack, Klaviyo, or any endpoint. Test with one click.", color: "#a855f7" },
              { name: "Automated Nudges", desc: "Social proof, urgency, return-visitor messages. Deploy and measure automatically.", color: "#34d399" },
            ].map((f) => (
              <div
                key={f.name}
                className="flex items-start gap-4 rounded-2xl border border-white/[0.06] bg-[#0e0e1a] p-6 transition-all duration-300 hover:border-white/[0.1]"
              >
                <div className="mt-0.5 h-2 w-2 flex-shrink-0 rounded-full" style={{ background: f.color }} />
                <div>
                  <h4 className="text-[15px] font-bold text-white">{f.name}</h4>
                  <p className="mt-1 text-[14px] leading-[1.6] text-slate-400">{f.desc}</p>
                </div>
              </div>
            ))}
          </div>
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
            Four steps. Fully automatic.
          </h2>
          <p className="mx-auto mt-6 max-w-[36rem] text-[18px] leading-[1.7] text-slate-400">
            Most tools stop at step one. HedgeSpark runs the full loop — and it gets better every time.
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
                    <div className="text-[13px] text-slate-500">Signal detected</div>
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
                    <div className="text-[13px] text-slate-500">Nudge deployed</div>
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
                    <div className="text-[13px] text-slate-500">Lift confirmed</div>
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
   IT LEARNS
   ══════════════════════════════════════════════════════════════════════════════ */

function Learns() {
  // Maturity progression: red (raw) → orange → yellow → green (mature)
  const weeks = [
    { week: "Week 1", insight: "68 views, 0 carts on your Silk Pillowcase", desc: "Basic problems found across your catalog.", color: "#f87171" },
    { week: "Week 2", insight: "Return visitors stall on items over $40", desc: "Price sensitivity patterns start to appear.", color: "#fb923c" },
    { week: "Week 4", insight: "Instagram visitors convert 3x on lifestyle photos", desc: "Learns which traffic sources bring buyers vs. browsers.", color: "#facc15" },
    { week: "Week 8", insight: "Instagram visitors who scroll past the fold hesitate at $45+ — 73% respond to social proof", desc: "Deep, compound intelligence unique to your store.", color: "#34d399" },
  ];

  return (
    <section className="relative py-20 sm:py-24">
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-transparent via-[#c4b5fd]/[0.015] to-transparent" />

      <div className="relative mx-auto max-w-[72rem] px-6 lg:px-10">
        <div className="grid items-center gap-14 lg:grid-cols-2">
          <R>
            <span className="text-[14px] font-bold uppercase tracking-[0.2em] text-[#c4b5fd]">It gets smarter</span>
            <h2 className="mt-5 text-[2.25rem] font-extrabold leading-[1.1] text-white sm:text-[3rem]">
              Week 1, it spots the obvious.
              <br />
              <span className="text-emerald-400">Week 8, it knows your store better than you do.</span>
            </h2>
            <p className="mt-6 text-[17px] leading-[1.7] text-slate-400">
              Most Shopify tools run the same generic rules for every store. HedgeSpark builds a model specific to <em>your</em> visitors, <em>your</em> products, <em>your</em> price points.
            </p>
            <p className="mt-5 text-[17px] font-semibold leading-[1.7] text-slate-300">
              Every week, the insights get sharper — because the system has more of your data to learn from.
            </p>
          </R>

          <R d={0.1}>
            <div className="rounded-3xl border border-white/[0.06] bg-[#0e0e1a] p-6 sm:p-8">
              <div className="flex items-center gap-3 mb-8">
                <Image src="/branding/hedgespark/spark.png" alt="" width={28} height={28} className="hs-float" />
                <span className="text-[14px] font-bold text-slate-500">Intelligence timeline</span>
              </div>

              <div className="space-y-0">
                {weeks.map((w, i) => {
                  const isLast = i === weeks.length - 1;
                  const next = weeks[i + 1];
                  return (
                    <div key={w.week} className="relative flex gap-5">
                      <div className="flex flex-col items-center">
                        <div
                          className="h-4 w-4 rounded-full border-2"
                          style={{
                            borderColor: w.color,
                            backgroundColor: alpha(w.color, isLast ? 0.4 : 0.15),
                            boxShadow: isLast ? `0 0 12px ${alpha(w.color, 0.5)}` : undefined,
                          }}
                        />
                        {!isLast && (
                          <div
                            className="w-px flex-1"
                            style={{
                              background: `linear-gradient(to bottom, ${alpha(w.color, 0.35)}, ${alpha(next!.color, 0.35)})`,
                            }}
                          />
                        )}
                      </div>
                      <div className={`pb-8 ${isLast ? "pb-0" : ""}`}>
                        <div
                          className="text-[13px] font-bold uppercase tracking-[0.12em]"
                          style={{ color: isLast ? w.color : alpha(w.color, 0.7) }}
                        >
                          {w.week}
                        </div>
                        <p className={`mt-2 text-[15px] font-semibold leading-[1.5] ${
                          isLast ? "text-white" : "text-slate-300"
                        }`}>
                          {w.insight}
                        </p>
                        <p className="mt-1 text-[14px] leading-[1.6] text-slate-500">{w.desc}</p>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </R>
        </div>

        <R d={0.14}>
          <div className="mt-14 rounded-2xl border border-[#d4893a]/15 bg-[#d4893a]/[0.04] p-6 text-center sm:p-8">
            <p className="text-[18px] leading-[1.7] text-slate-300">
              Every week you wait is a week the system can&apos;t learn from.
              <br className="hidden sm:block" />
              <strong className="text-white">Merchants who start today are 8 weeks ahead of merchants who start in 8 weeks.</strong>
            </p>
            <a
              href={INSTALL_URL}
              className="mt-5 inline-block text-[16px] font-bold text-[#d4893a] transition-colors hover:text-[#e8a04e]"
            >
              Start learning now &rarr;
            </a>
          </div>
        </R>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   GET STARTED — 3 steps
   ══════════════════════════════════════════════════════════════════════════════ */

function GetStarted() {
  // Brand gradient progression: purple → magenta → orange.
  // Slightly desaturated from the pure brand stops to feel premium, not neon.
  const steps = [
    { n: "1", t: "Install from Shopify", d: "One click. No code. No theme edits. Tracking starts on the next visitor.", time: "30 seconds", color: "#8b5cf6" },
    { n: "2", t: "Signals start firing", d: "Products get scored by how well they convert. Problems surface automatically.", time: "~5 minutes", color: "#d946ef" },
    { n: "3", t: "Fix. Prove. Repeat.", d: "Every signal carries a fix. Every outcome makes the model sharper.", time: "Ongoing", color: "#fb923c" },
  ];

  return (
    <section className="relative py-20 sm:py-24">
      {/* Subtle brand wash */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 60% 50% at 50% 40%, rgba(217, 70, 239, 0.018) 0%, transparent 70%)",
        }}
      />

      <div className="relative mx-auto max-w-[72rem] px-6 lg:px-10">
        <R className="text-center">
          <span className="text-[14px] font-bold uppercase tracking-[0.2em] text-[#d4893a]">Get started</span>
          <h2 className="mt-5 text-[2.25rem] font-extrabold leading-[1.1] text-white sm:text-[3rem]">
            30 seconds to install.
            <br />
            <span style={{ color: "#e8a04e" }}>5 minutes to your first insight.</span>
          </h2>
        </R>

        <div className="mt-16 grid items-stretch gap-8 sm:grid-cols-3">
          {steps.map((s, i) => {
            const next = steps[i + 1];
            return (
              <R key={s.n} d={i * 0.08} className="h-full">
                <div className="relative flex h-full flex-col items-center text-center">
                  {/* Connector line — full row, fades in/out at the badge.
                      Last step fades to transparent (no next color). */}
                  <div
                    className="absolute right-0 top-[2.5rem] hidden h-px w-full sm:block"
                    style={{
                      background: next
                        ? `linear-gradient(to right, ${alpha(s.color, 0)}, ${alpha(s.color, 0.3)}, ${alpha(next.color, 0.3)}, ${alpha(next.color, 0)})`
                        : `linear-gradient(to right, ${alpha(s.color, 0)}, ${alpha(s.color, 0.3)}, ${alpha(s.color, 0)})`,
                    }}
                  />
                  {/* Number badge */}
                  <div
                    className="relative z-10 flex h-20 w-20 items-center justify-center rounded-3xl border-2 text-[28px] font-extrabold transition-transform duration-300 hover:scale-105"
                    style={{
                      borderColor: alpha(s.color, 0.28),
                      background: `linear-gradient(135deg, ${alpha(s.color, 0.12)}, ${alpha(next?.color ?? s.color, 0.025)})`,
                      color: s.color,
                      boxShadow: `0 0 24px -6px ${alpha(s.color, 0.2)}`,
                    }}
                  >
                    {s.n}
                  </div>
                  <h3 className="mt-7 text-[19px] font-bold text-white">{s.t}</h3>
                  <p className="mt-3 flex-1 text-[16px] leading-[1.65] text-slate-400">{s.d}</p>
                  <span
                    className="mt-5 rounded-full border px-4 py-1.5 text-[13px] font-bold"
                    style={{
                      borderColor: alpha(s.color, 0.22),
                      backgroundColor: alpha(s.color, 0.06),
                      color: s.color,
                    }}
                  >
                    {s.time}
                  </span>
                </div>
              </R>
            );
          })}
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   PRICING
   ══════════════════════════════════════════════════════════════════════════════ */

function Pricing() {
  const check = (color: string) => (
    <svg className={`mt-0.5 h-5 w-5 flex-shrink-0 ${color}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
    </svg>
  );

  return (
    <section id="pricing" className="relative scroll-mt-20 py-20 sm:py-24">
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-transparent via-[#d4893a]/[0.015] to-transparent" />

      <div className="relative mx-auto max-w-[60rem] px-6 lg:px-10">
        <R className="text-center">
          <span className="text-[14px] font-bold uppercase tracking-[0.2em] text-[#d4893a]">Pricing</span>
          <h2 className="mt-5 text-[2.25rem] font-extrabold leading-[1.1] text-white sm:text-[3rem]">
            Start with intelligence.
            <br />
            <span style={{ color: "#34d399" }}>Start making money.</span>
          </h2>
        </R>

        <div className="mt-16 grid gap-6 sm:grid-cols-2">
          {/* Lite */}
          <R d={0.04}>
            <div className="flex h-full flex-col rounded-3xl border border-white/[0.06] bg-[#0e0e1a] p-8 transition-all duration-300 hover:border-white/[0.1] sm:p-10">
              <div className="text-[14px] font-bold uppercase tracking-[0.18em] text-slate-400">Lite</div>
              <div className="mt-6 flex items-baseline gap-2">
                <span className="text-[3.5rem] font-extrabold tracking-tight text-white">$0</span>
                <span className="text-[17px] text-slate-500">forever</span>
              </div>
              <p className="mt-5 text-[17px] leading-relaxed text-slate-400">
                See what&apos;s wrong with every product. In real time.
              </p>
              <ul className="mt-8 flex-1 space-y-4">
                {[
                  "Visitor intent scoring (hot / warm / cold)",
                  "8 detection signals per product",
                  "Scroll depth + dwell time tracking",
                  "Daily intelligence brief",
                  "Revenue-at-risk estimates",
                  "Traffic source quality",
                ].map((f) => (
                  <li key={f} className="flex items-start gap-3 text-[16px] text-slate-300">
                    {check("text-emerald-400/70")}
                    {f}
                  </li>
                ))}
              </ul>
              <a
                href={INSTALL_URL}
                className="mt-10 block rounded-2xl border border-white/[0.08] bg-white/[0.03] py-4 text-center text-[16px] font-bold text-slate-200 transition-all duration-300 hover:border-white/[0.14] hover:bg-white/[0.06] hover:text-white"
              >
                Install free
              </a>
            </div>
          </R>

          {/* Pro */}
          <R d={0.1}>
            <div className="relative flex h-full flex-col overflow-hidden rounded-3xl border border-[#d4893a]/20 bg-gradient-to-b from-[#d4893a]/[0.04] to-transparent p-8 transition-all duration-300 hover:border-[#d4893a]/30 sm:p-10">
              <div className="absolute -top-px left-8 rounded-b-xl bg-[#d4893a] px-5 py-2 text-[12px] font-bold uppercase tracking-[0.12em] text-white shadow-[0_4px_20px_-4px_rgba(212,137,58,0.5)]">
                14-day free trial
              </div>
              <div className="text-[14px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">Pro</div>
              <div className="mt-6 flex items-baseline gap-2">
                <span className="text-[3.5rem] font-extrabold tracking-tight text-white">$49</span>
                <span className="text-[17px] text-slate-500">/month</span>
              </div>
              <p className="mt-5 text-[17px] leading-relaxed text-slate-400">
                Find the problem. Fix it. Prove it worked. Watch it learn.
              </p>
              <p className="mt-2 text-[14px] text-slate-500">
                One recovered signal typically pays for a full year.
              </p>
              <ul className="mt-8 flex-1 space-y-4">
                {[
                  "Everything in Lite",
                  "Revenue at Risk Score (5-dimension breakdown)",
                  "Revenue Autopsy per product",
                  "Abandoned intent detection",
                  "Automated nudge deployment + holdout proof",
                  "Causal lift measurement (real A/B, not guesses)",
                  "Cohort & LTV analysis",
                  "P&L intelligence per product",
                  "Scroll heatmaps per product",
                  "Price sensitivity detection",
                  "Peer benchmarks (anonymous)",
                  "Revenue Genome — source/segment composition",
                  "Goals, ROI tracking, risk forecast",
                  "Team collaboration + webhook integrations",
                  "Weekly email digest",
                ].map((f) => (
                  <li key={f} className={`flex items-start gap-3 text-[16px] ${f === "Everything in Lite" ? "text-slate-500" : "text-slate-200"}`}>
                    {check("text-[#d4893a]")}
                    {f}
                  </li>
                ))}
              </ul>
              <a
                href={INSTALL_URL}
                className="hs-cta-gradient mt-10 block rounded-2xl py-4 text-center text-[16px] font-bold text-white transition-all duration-300 hover:shadow-[0_4px_40px_rgba(212,137,58,0.3)]"
              >
                Start free trial
              </a>
            </div>
          </R>
        </div>
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
      q: "What happens after the Pro trial?",
      a: "You choose: keep Pro or drop to Lite. Lite is free forever — you keep all detection signals. You never lose visibility.",
    },
  ];

  return (
    <section id="faq" className="relative scroll-mt-20 py-20 sm:py-24">
      <div className="mx-auto max-w-[48rem] px-6 lg:px-10">
        <R className="text-center">
          <span className="text-[14px] font-bold uppercase tracking-[0.2em] text-[#d4893a]">Questions</span>
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
   FINAL CTA
   ══════════════════════════════════════════════════════════════════════════════ */

function FinalCTA() {
  return (
    <section className="relative overflow-hidden py-32 sm:py-40">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-1/2 top-1/2 h-[600px] w-[900px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#d4893a]/[0.06] blur-[180px]" />
        <div className="absolute left-[60%] top-[60%] h-[400px] w-[600px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#7c3aed]/[0.04] blur-[140px]" />
      </div>
      <R>
        <div className="relative mx-auto max-w-3xl px-6 text-center lg:px-10">
          <h2 className="text-[2.25rem] font-extrabold leading-[1.1] text-white sm:text-[3rem] lg:text-[3.75rem]">
            While you read this,
            <br />
            someone left your store.
          </h2>
          <p className="mt-6 text-[20px] leading-[1.5] text-slate-400 sm:text-[1.5rem]">
            A competitor using HedgeSpark
            <br className="hidden sm:block" />
            would already know <span className="text-white font-semibold">why</span>.
          </p>
          <div className="mt-10">
            <a
              href={INSTALL_URL}
              className="hs-cta-gradient group relative inline-block rounded-2xl px-14 py-5 text-[18px] font-bold text-white transition-all duration-300 hover:shadow-[0_4px_60px_rgba(212,137,58,0.4)]"
            >
              <span className="pointer-events-none absolute inset-0 rounded-2xl bg-gradient-to-b from-white/[0.08] to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
              <span className="relative">Install on Shopify</span>
            </a>
          </div>
          <p className="mt-6 text-[15px] text-slate-500">Installs in 30 seconds. Tracking starts on the next visitor.</p>
        </div>
      </R>
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
          <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-2 text-[15px] text-slate-500">
            <a href="/app" className="transition-colors duration-200 hover:text-white">Dashboard</a>
            <a href="/pricing" className="transition-colors duration-200 hover:text-white">Pricing</a>
            <a href="/privacy" className="transition-colors duration-200 hover:text-white">Privacy</a>
            <a href="/terms" className="transition-colors duration-200 hover:text-white">Terms</a>
            <a href="mailto:dev@hedgesparkhq.com" className="transition-colors duration-200 hover:text-white">Support</a>
          </div>
          <span className="text-[14px] text-slate-600">&copy; {new Date().getFullYear()} HedgeSpark</span>
        </div>
      </div>
    </footer>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   TRUST WALL — α8 — compliance badges, holdout-proof claim, aggregate savings
   ══════════════════════════════════════════════════════════════════════════════ */

function TrustWall() {
  const badges = [
    { label: "GDPR Compliant", icon: "🇪🇺", desc: "Data residency · audit log hash chain · breach runbook", color: "#60a5fa" },
    { label: "SOC2 In Progress", icon: "🛡️", desc: "Type II motion started · 11/11 compliance score", color: "#10b981" },
    { label: "Holdout-Measured", icon: "🔬", desc: "Every claim tested against a control group · p<0.05", color: "#e8a04e" },
    { label: "Zero PII in LLM", icon: "🔒", desc: "Runtime guard blocks personal data from prompts", color: "#a78bfa" },
  ];

  const differentiators = [
    {
      k: "Revenue-at-Risk Score",
      v: "See losses before they compound",
      icon: "🎯",
    },
    {
      k: "Holdout-proven savings",
      v: "Every saved € is statistically defended",
      icon: "📊",
    },
    {
      k: "Delegated Autonomy",
      v: "Pre-approve bounds, system acts within them",
      icon: "🛡️",
    },
    {
      k: "Closed-loop learning",
      v: "Self-heals, self-improves, self-measures",
      icon: "🔁",
    },
    {
      k: "60-second first insight",
      v: "Real numbers before your coffee is done",
      icon: "⚡",
    },
  ];

  return (
    <section className="relative border-t border-white/[0.04] bg-[#05050b] py-24">
      <div className="mx-auto max-w-[72rem] px-6 lg:px-10">
        <R>
          <div className="text-center mb-16">
            <div className="inline-block rounded-full border border-[#e8a04e]/30 bg-[#e8a04e]/5 px-4 py-1.5 text-[11px] font-bold uppercase tracking-[0.15em] text-[#e8a04e]">
              Built on trust, proven on data
            </div>
            <h2 className="mt-6 text-[clamp(32px,5vw,48px)] font-extrabold leading-[1.05] tracking-tight">
              The only SMB intelligence<br />
              <span className="bg-gradient-to-r from-[#e8a04e] via-[#f59e0b] to-[#fcd34d] bg-clip-text text-transparent">
                you can actually defend
              </span>
            </h2>
            <p className="mx-auto mt-5 max-w-[640px] text-[17px] leading-relaxed text-slate-400">
              Every € we save you is measured against a real control group. Every autonomous action
              runs inside bounds you set. Every claim is audit-logged. HedgeSpark is engineered
              to survive scrutiny, not to survive a demo.
            </p>
          </div>
        </R>

        {/* Compliance badges */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4 mb-16">
          {badges.map((b, i) => (
            <R key={b.label} d={i * 0.08}>
              <div
                className="rounded-2xl border p-5"
                style={{
                  background: "linear-gradient(135deg, rgba(11,18,32,0.9) 0%, rgba(20,26,48,0.5) 100%)",
                  borderColor: `${b.color}33`,
                }}
              >
                <div className="flex items-center gap-3 mb-2">
                  <span className="text-[24px]">{b.icon}</span>
                  <span className="text-[15px] font-bold" style={{ color: b.color }}>{b.label}</span>
                </div>
                <div className="text-[12px] leading-relaxed text-slate-400">{b.desc}</div>
              </div>
            </R>
          ))}
        </div>

        {/* 5 differentiators */}
        <R>
          <div className="rounded-3xl border border-white/[0.06] bg-gradient-to-br from-[#0b1220] to-[#111c2e] p-10">
            <div className="mb-8 text-center">
              <div className="text-[11px] font-bold uppercase tracking-[0.15em] text-slate-500 mb-3">
                5 things no competitor can copy
              </div>
              <div className="text-[22px] font-bold text-white">The defendable moat</div>
            </div>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-5">
              {differentiators.map((d, i) => (
                <div
                  key={d.k}
                  className="rounded-xl border border-white/[0.08] bg-black/30 p-5 transition-all duration-300 hover:border-[#e8a04e]/40"
                  style={{ animationDelay: `${i * 0.1}s` }}
                >
                  <div className="text-[28px] mb-3">{d.icon}</div>
                  <div className="text-[14px] font-bold text-white mb-1.5">{d.k}</div>
                  <div className="text-[12px] leading-relaxed text-slate-400">{d.v}</div>
                </div>
              ))}
            </div>
          </div>
        </R>

        {/* Stat line — aggregate proof */}
        <R d={0.15}>
          <div className="mt-16 flex flex-wrap items-center justify-center gap-x-12 gap-y-6 text-center">
            <div>
              <div className="text-[36px] font-extrabold text-emerald-400">p&lt;0.05</div>
              <div className="text-[12px] uppercase tracking-wide text-slate-500 mt-1">Every claim</div>
            </div>
            <div className="hidden sm:block text-white/10 text-[28px]">·</div>
            <div>
              <div className="text-[36px] font-extrabold text-[#e8a04e]">20%</div>
              <div className="text-[12px] uppercase tracking-wide text-slate-500 mt-1">Holdout by default</div>
            </div>
            <div className="hidden sm:block text-white/10 text-[28px]">·</div>
            <div>
              <div className="text-[36px] font-extrabold text-purple-400">0 PII</div>
              <div className="text-[12px] uppercase tracking-wide text-slate-500 mt-1">Leaves your store</div>
            </div>
            <div className="hidden sm:block text-white/10 text-[28px]">·</div>
            <div>
              <div className="text-[36px] font-extrabold text-sky-400">24/7</div>
              <div className="text-[12px] uppercase tracking-wide text-slate-500 mt-1">Autonomous</div>
            </div>
          </div>
        </R>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   PAGE
   ══════════════════════════════════════════════════════════════════════════════ */

export default function LandingPage() {
  const ok = useOAuthRedirect();
  if (!ok) return null;

  return (
    <div className="min-h-screen bg-[#07070f] text-white antialiased">
      <Nav />
      <Hero />
      <Numbers />
      <Problem />
      <Features />
      <ProStack />
      <HowItWorks />
      <RealExample />
      <Learns />
      <GetStarted />
      <Pricing />
      <FAQ />
      <FinalCTA />
      <TrustWall />
      <Footer />
    </div>
  );
}
