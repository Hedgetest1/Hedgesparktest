"use client";

import { useEffect, useRef, useState } from "react";

/* ── OAuth guard ── */
function useOAuthRedirect() {
  const [ok, setOk] = useState(false);
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    if (p.get("shop") || p.get("installed") || p.get("billing") || p.get("section")) { window.location.href = `/app${window.location.search}`; return; }
    setOk(true);
  }, []);
  return ok;
}

/* ── Reveal ── */
function useReveal(threshold = 0.12) {
  const ref = useRef<HTMLDivElement>(null);
  const [v, setV] = useState(false);
  useEffect(() => { const el = ref.current; if (!el) return; const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setV(true); io.disconnect(); } }, { threshold }); io.observe(el); return () => io.disconnect(); }, [threshold]);
  return { ref, v };
}

function R({ children, className = "", d = 0 }: { children: React.ReactNode; className?: string; d?: number }) {
  const { ref, v } = useReveal();
  return <div ref={ref} className={className} style={{ opacity: v ? 1 : 0, transform: v ? "none" : "translateY(24px)", transition: `opacity 0.6s cubic-bezier(0.16,1,0.3,1) ${d}s, transform 0.6s cubic-bezier(0.16,1,0.3,1) ${d}s` }}>{children}</div>;
}

/* ══════════════════════════════════════════════════════════════════════════════
   NAV
   ══════════════════════════════════════════════════════════════════════════════ */

function Nav() {
  const [s, setS] = useState(false);
  useEffect(() => { const h = () => setS(window.scrollY > 24); window.addEventListener("scroll", h, { passive: true }); h(); return () => window.removeEventListener("scroll", h); }, []);
  return (
    <nav className={`fixed inset-x-0 top-0 z-50 transition-all duration-500 ${s ? "bg-[#080811]/80 shadow-[0_1px_60px_rgba(0,0,0,0.6)] backdrop-blur-2xl" : "bg-transparent"}`}
      style={{ borderBottom: s ? "1px solid rgba(255,255,255,0.04)" : "1px solid transparent" }}>
      <div className="mx-auto flex h-[4.25rem] max-w-[72rem] items-center justify-between px-6">
        <a href="/" className="group flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500/20 to-violet-600/10 transition-transform duration-300 group-hover:scale-110">
            <svg viewBox="0 0 20 20" className="h-4 w-4 text-violet-400" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M10 2L2 7l8 5 8-5-8-5zM2 13l8 5 8-5"/></svg>
          </div>
          <span className="text-[18px] font-bold tracking-tight text-white">Hedge<span className="text-violet-400">Spark</span></span>
        </a>
        <div className="flex items-center gap-1.5 sm:gap-4">
          {[["#signals","Signals"],["#how","How it works"],["#pricing","Pricing"]].map(([h,l])=>(
            <a key={h} href={h} className="hidden rounded-lg px-3 py-1.5 text-[13px] text-slate-400 transition-colors duration-200 hover:bg-white/[0.04] hover:text-white sm:block">{l}</a>
          ))}
          <a href="/app" className="rounded-lg px-3 py-1.5 text-[13px] text-slate-400 transition-colors duration-200 hover:bg-white/[0.04] hover:text-white">Dashboard</a>
          <a href="https://apps.shopify.com/" className="relative ml-1 overflow-hidden rounded-lg bg-violet-600 px-5 py-2 text-[13px] font-semibold text-white transition-all duration-300 hover:bg-violet-500 hover:shadow-[0_0_28px_rgba(124,58,237,0.4)]">
            Install free
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
  /* Mini sparkline bars (7 bars, descending pattern = "traffic but declining conversion") */
  const spark = (h: number[]) => (
    <svg viewBox="0 0 28 16" className="h-3 w-7" aria-hidden="true">
      {h.map((v, i) => <rect key={i} x={i * 4} y={16 - v} width="3" height={v} rx="0.5" className="fill-slate-600/60" />)}
    </svg>
  );

  return (
    <section className="relative overflow-hidden pb-20 pt-32 sm:pb-32 sm:pt-44">
      {/* Ambient */}
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-1/2 top-[-18%] h-[800px] w-[1100px] -translate-x-1/2 rounded-full bg-violet-600/[0.08] blur-[180px]" />
        <div className="absolute right-[-10%] top-[15%] h-[400px] w-[500px] rounded-full bg-cyan-400/[0.035] blur-[140px]" />
        <div className="absolute left-[-6%] bottom-[5%] h-[250px] w-[350px] rounded-full bg-violet-500/[0.04] blur-[100px]" />
      </div>
      <div className="pointer-events-none absolute inset-0 opacity-[0.018]" style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg width='48' height='48' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M48 0H0v48' fill='none' stroke='%23fff' stroke-width='.4'/%3E%3C/svg%3E\")", backgroundSize: "48px 48px" }} />

      <div className="relative mx-auto max-w-[68rem] px-6">
        <R className="flex justify-center">
          <div className="inline-flex items-center gap-2.5 rounded-full border border-violet-400/10 bg-violet-500/[0.05] py-1.5 pl-1.5 pr-5">
            <span className="rounded-full bg-violet-500/20 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-[0.15em] text-violet-300">Shopify</span>
            <span className="text-[13px] text-violet-200/60">Built for merchants who hate guessing</span>
          </div>
        </R>

        <R d={0.06}>
          <h1 className="mx-auto mt-8 max-w-[52rem] text-center text-[2.5rem] font-extrabold leading-[1.04] tracking-[-0.03em] text-white sm:text-[3.75rem] lg:text-[4.75rem]">
            Right now, a visitor is leaving
            <br />your best product.
            <br />
            <span className="bg-gradient-to-r from-violet-400 via-violet-300 to-fuchsia-400 bg-clip-text text-transparent">You don&apos;t know why.</span>
          </h1>
        </R>

        <R d={0.1}>
          <p className="mx-auto mt-7 max-w-[32rem] text-center text-[17px] leading-[1.75] text-slate-400">
            Hedge Spark tells you which products lose money, why, and what to change.
            <span className="text-slate-100"> Then it proves the fix worked.</span>
          </p>
        </R>

        <R d={0.14} className="mt-11 flex flex-col items-center gap-3.5 sm:flex-row sm:justify-center">
          <a href="https://apps.shopify.com/" className="group relative rounded-xl bg-violet-600 px-10 py-4 text-[15px] font-semibold text-white ring-1 ring-violet-500/30 transition-all duration-300 hover:bg-violet-500 hover:shadow-[0_4px_40px_rgba(124,58,237,0.4)]">
            <span className="pointer-events-none absolute inset-0 rounded-xl bg-gradient-to-b from-white/[0.1] to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
            <span className="relative">Install free on Shopify</span>
          </a>
          <a href="#signals" className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-9 py-4 text-[15px] font-semibold text-slate-300 transition-all duration-300 hover:border-white/[0.14] hover:bg-white/[0.05] hover:text-white">
            See real signals
          </a>
        </R>

        <R d={0.17} className="mt-5 text-center text-[13px] text-slate-600">Free forever. No credit card. 30-second install.</R>

        {/* ── Chevron hint ── */}
        <R d={0.2} className="mt-10 flex justify-center sm:mt-14">
          <svg viewBox="0 0 24 24" className="h-5 w-5 text-slate-700" style={{ animation: "hs-bob 2.5s ease-in-out infinite" }} fill="none" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" /></svg>
        </R>

        {/* ════════════════════════════════════════════════════════════════════
           PRODUCT PREVIEW — designed to feel like a real app window
           ════════════════════════════════════════════════════════════════════ */}
        <R d={0.24} className="mt-6 sm:mt-8">
          <div className="mx-auto max-w-[54rem]" style={{ perspective: "1200px" }}>
            <div style={{ transform: "rotateX(1.5deg)", transformOrigin: "bottom center" }}>
              {/* Outer glow shell */}
              <div className="rounded-2xl shadow-[0_20px_100px_-20px_rgba(124,58,237,0.2),0_8px_40px_-8px_rgba(0,0,0,0.5)]">
                <div className="rounded-2xl bg-gradient-to-b from-white/[0.07] via-white/[0.03] to-white/[0.01] p-px">
                  <div className="overflow-hidden rounded-[15px] bg-[#09091a]">

                    {/* ── Window chrome ── */}
                    <div className="flex items-center gap-2 border-b border-white/[0.04] bg-[#0c0c1c] px-4 py-2.5">
                      <div className="flex gap-1.5">
                        <div className="h-[9px] w-[9px] rounded-full bg-[#ff5f57]/80" />
                        <div className="h-[9px] w-[9px] rounded-full bg-[#febc2e]/80" />
                        <div className="h-[9px] w-[9px] rounded-full bg-[#28c840]/80" />
                      </div>
                      <div className="ml-3 flex-1 text-center">
                        <span className="text-[11px] text-slate-600">app.hedgesparkhq.com</span>
                      </div>
                      <div className="w-[52px]" /> {/* balance */}
                    </div>

                    {/* ── Tab bar ── */}
                    <div className="flex items-center gap-0 border-b border-white/[0.04] bg-[#0b0b19]">
                      {[
                        { label: "Signals", active: true, count: "3" },
                        { label: "Products", active: false, count: "12" },
                        { label: "Sources", active: false },
                      ].map((tab) => (
                        <div key={tab.label} className={`relative px-5 py-3 text-[12px] font-medium transition-colors ${tab.active ? "text-white" : "text-slate-500"}`}>
                          <span>{tab.label}</span>
                          {tab.count && (
                            <span className={`ml-1.5 rounded px-1 py-px text-[9px] font-bold tabular-nums ${tab.active ? "bg-violet-500/20 text-violet-300" : "bg-white/[0.04] text-slate-600"}`}>{tab.count}</span>
                          )}
                          {tab.active && <div className="absolute inset-x-0 bottom-0 h-[2px] bg-violet-500" />}
                        </div>
                      ))}
                    </div>

                    {/* ── Dashboard body ── */}
                    <div className="px-5 py-5 sm:px-7 sm:py-6">

                      {/* KPI strip */}
                      <div className="flex flex-wrap items-center gap-3">
                        <div className="flex items-center gap-2">
                          <div className="relative">
                            <div className="h-2 w-2 rounded-full bg-emerald-400" />
                            <div className="absolute inset-0 animate-ping rounded-full bg-emerald-400/40" style={{ animationDuration: "2.5s" }} />
                          </div>
                          <span className="text-[13px] font-semibold text-slate-100">Live</span>
                        </div>
                        <div className="h-4 w-px bg-white/[0.06]" />
                        <span className="rounded-md bg-rose-500/10 px-2 py-0.5 text-[11px] font-semibold tabular-nums text-rose-300 ring-1 ring-rose-500/15">3 flagged</span>
                        <span className="rounded-md bg-white/[0.04] px-2 py-0.5 text-[11px] tabular-nums text-slate-400">147 visitors</span>
                        <span className="ml-auto text-[12px] font-bold tabular-nums text-rose-400">$193/day at risk</span>
                      </div>

                      {/* Signal rows */}
                      <div className="mt-5 space-y-1.5">
                        {[
                          { name: "Silk Pillowcase Set", code: "HIGH_TRAFFIC_NO_CART", desc: "68 views today, 0 add-to-carts", tag: "Fix CTA", c: "rose" as const, loss: "$94", bars: [12,14,13,11,10,8,6], action: "Replace hero image with lifestyle shot. Add sticky Add to Cart bar visible on scroll." },
                          { name: "Ceramic Travel Mug", code: "HIGH_ENGAGEMENT_NO_ACTION", desc: "34s avg dwell, 72% scroll depth, 0 carts", tag: "Price issue", c: "amber" as const, loss: "$61", bars: [8,9,10,9,7,6,5], action: null },
                          { name: "Midnight Candle Trio", code: "HIGH_RETURN_LOW_CONVERSION", desc: "12 return visitors this week, 1 cart", tag: "Urgency", c: "violet" as const, loss: "$38", bars: [5,6,7,8,7,6,4], action: null },
                        ].map((r, idx) => {
                          const palette = {
                            rose: { bar: "bg-rose-400/70", dot: "bg-rose-400", glow: "shadow-[0_0_6px_rgba(251,113,133,0.5)]", badge: "bg-rose-500/10 text-rose-300 ring-1 ring-rose-500/20", actionBg: "border-rose-500/10 bg-rose-500/[0.04]", actionText: "text-rose-200/80" },
                            amber: { bar: "bg-amber-400/70", dot: "bg-amber-400", glow: "shadow-[0_0_6px_rgba(251,191,36,0.5)]", badge: "bg-amber-500/10 text-amber-300 ring-1 ring-amber-500/20", actionBg: "", actionText: "" },
                            violet: { bar: "bg-violet-400/70", dot: "bg-violet-400", glow: "shadow-[0_0_6px_rgba(167,139,250,0.5)]", badge: "bg-violet-500/10 text-violet-300 ring-1 ring-violet-500/20", actionBg: "", actionText: "" },
                          };
                          const p = palette[r.c];
                          return (
                            <div key={r.name} className="group rounded-xl border border-white/[0.03] bg-white/[0.008] transition-all duration-300 hover:-translate-y-px hover:border-white/[0.08] hover:bg-white/[0.02] hover:shadow-[0_4px_20px_-4px_rgba(0,0,0,0.4)]">
                              <div className="flex items-center gap-0">
                                {/* Severity bar */}
                                <div className={`w-[3px] self-stretch rounded-l-xl ${p.bar}`} />
                                {/* Content */}
                                <div className="flex flex-1 items-center gap-4 px-4 py-3.5">
                                  <div className={`h-2 w-2 flex-shrink-0 rounded-full ${p.dot} ${p.glow}`} />
                                  <div className="min-w-0 flex-1">
                                    <div className="flex flex-wrap items-center gap-2">
                                      <span className="text-[13px] font-semibold text-slate-100">{r.name}</span>
                                      <span className={`rounded-md px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider ${p.badge}`}>{r.tag}</span>
                                    </div>
                                    <div className="mt-0.5 flex flex-wrap items-center gap-2">
                                      <span className="font-mono text-[10px] text-slate-600">{r.code}</span>
                                      <span className="hidden text-[11px] text-slate-500 sm:inline">{r.desc}</span>
                                    </div>
                                  </div>
                                  {/* Right: loss + sparkline */}
                                  <div className="hidden flex-shrink-0 items-center gap-3 sm:flex">
                                    {spark(r.bars)}
                                    <div className="text-right">
                                      <div className="text-[14px] font-bold tabular-nums text-rose-400">{r.loss}</div>
                                      <div className="text-[9px] text-slate-600">/day lost</div>
                                    </div>
                                  </div>
                                </div>
                              </div>
                              {/* Expanded action (first row only) */}
                              {idx === 0 && r.action && (
                                <div className={`mx-4 mb-3 rounded-lg border px-3 py-2.5 ${p.actionBg}`}>
                                  <div className="flex items-start gap-2">
                                    <svg className="mt-0.5 h-3 w-3 flex-shrink-0 text-rose-400/60" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" /></svg>
                                    <div>
                                      <div className="text-[10px] font-bold uppercase tracking-wider text-rose-300/50">Recommended action</div>
                                      <div className={`mt-0.5 text-[12px] leading-relaxed ${p.actionText}`}>{r.action}</div>
                                    </div>
                                  </div>
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>

                      {/* Footer */}
                      <div className="mt-5 flex items-center justify-between border-t border-white/[0.04] pt-4">
                        <div className="flex items-center gap-1.5">
                          <svg className="h-3 w-3 text-slate-600" style={{ animation: "spin 4s linear infinite" }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" /></svg>
                          <span className="text-[10px] text-slate-600">Updated 8s ago</span>
                        </div>
                        <a href="/app" className="text-[11px] font-medium text-violet-400/70 transition-colors hover:text-violet-300">
                          View all 12 products &rarr;
                        </a>
                      </div>
                    </div>
                  </div>
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
   BLIND SPOTS
   ══════════════════════════════════════════════════════════════════════════════ */

function BlindSpots() {
  return (
    <section className="relative py-28">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />
      <div className="mx-auto max-w-[68rem] px-6">
        <R className="mx-auto max-w-2xl text-center">
          <span className="text-[11px] font-bold uppercase tracking-[0.2em] text-rose-400/60">The blind spot</span>
          <h2 className="mt-4 text-[1.75rem] font-bold leading-[1.15] text-white sm:text-[2.25rem]">
            You sell products.
            <br /><span className="text-slate-500">Your tools count pageviews.</span>
          </h2>
        </R>

        <div className="mt-16 grid gap-[1px] overflow-hidden rounded-2xl border border-white/[0.04] bg-white/[0.025] sm:grid-cols-3">
          {[
            { t: "Two visitors. Same product.", d: "One scrolled 80% and read for 40 seconds. The other bounced in 3. Your analytics says: 2 pageviews. Same thing, right?" },
            { t: "Products fail in silence.", d: "200 views. Zero add-to-carts. You won\u2019t notice for weeks. By then you\u2019ve spent another $500 driving traffic to a broken page." },
            { t: "You changed something. Did it work?", d: "New photos. New price. New CTA. Revenue went up. Was it the change, or was it Tuesday? Without a control group, you\u2019re guessing." },
          ].map((p, i) => (
            <R key={i} d={i * 0.06}>
              <div className="group h-full bg-[#080811] p-8 transition-colors duration-300 hover:bg-[#0a0a14]">
                <h3 className="text-[15px] font-semibold text-white">{p.t}</h3>
                <p className="mt-3 text-[13px] leading-[1.7] text-slate-500">{p.d}</p>
              </div>
            </R>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   SIGNALS
   ══════════════════════════════════════════════════════════════════════════════ */

function Signals() {
  const items = [
    { code: "HIGH_TRAFFIC_NO_CART", l: "Traffic but no carts", d: "20+ views, zero add-to-carts. Something on this page is broken.", c: "rose" },
    { code: "HIGH_ENGAGEMENT_NO_ACTION", l: "Interested but stuck", d: "They scroll deep and stay long. But don\u2019t buy. A specific friction is blocking them.", c: "amber" },
    { code: "DEAD_TRAFFIC", l: "Dead on arrival", d: "Gone in under 5 seconds. First impression failed. Above-the-fold needs work.", c: "slate" },
    { code: "HIGH_RETURN_LOW_CONVERSION", l: "Keeps coming back, won\u2019t buy", d: "5+ visits this week. Almost no carts. They want it. Something is stopping them.", c: "violet" },
    { code: "LOW_CONVERSION_ATTENTION", l: "Weak conversion rate", d: "Getting traffic. Sub-2% cart rate. Not broken, just underperforming.", c: "cyan" },
    { code: "TRAFFIC_SPIKE", l: "Sudden traffic spike", d: "Views jumped 1.5x. Something is driving traffic. Is the page ready?", c: "emerald" },
  ];
  const dotColor: Record<string,string> = { rose: "bg-rose-400", amber: "bg-amber-400", violet: "bg-violet-400", cyan: "bg-cyan-400", emerald: "bg-emerald-400", slate: "bg-slate-400" };
  const glowColor: Record<string,string> = { rose: "shadow-[0_0_6px_rgba(251,113,133,0.5)]", amber: "shadow-[0_0_6px_rgba(251,191,36,0.5)]", violet: "shadow-[0_0_6px_rgba(167,139,250,0.5)]", cyan: "shadow-[0_0_6px_rgba(34,211,238,0.4)]", emerald: "shadow-[0_0_6px_rgba(52,211,153,0.4)]", slate: "" };

  return (
    <section id="signals" className="relative scroll-mt-20 py-28">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />
      <div className="mx-auto max-w-[68rem] px-6">
        <R className="text-center">
          <span className="text-[11px] font-bold uppercase tracking-[0.2em] text-violet-400/60">Detection engine</span>
          <h2 className="mt-4 text-[1.75rem] font-bold text-white sm:text-[2.25rem]">
            Your products are talking.<br />Here&apos;s what they&apos;re saying.
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-slate-400">
            Every 5 minutes, Hedge Spark checks every product against 8 behavioral rules.
            When something is wrong, you get a signal with the problem and the fix.
          </p>
        </R>
        <div className="mt-16 grid gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((s, i) => (
            <R key={s.code} d={i * 0.04}>
              <div className="group rounded-2xl border border-white/[0.04] bg-white/[0.012] p-6 transition-all duration-300 hover:border-white/[0.09] hover:bg-white/[0.025] hover:shadow-[0_4px_30px_-8px_rgba(0,0,0,0.3)]">
                <div className="flex items-center gap-3">
                  <div className={`h-2 w-2 rounded-full transition-transform duration-300 group-hover:scale-150 ${dotColor[s.c]} ${glowColor[s.c]}`} />
                  <span className="text-[14px] font-semibold text-white">{s.l}</span>
                </div>
                <p className="mt-3 text-[12.5px] leading-[1.7] text-slate-500">{s.d}</p>
                <div className="mt-4 font-mono text-[9px] tracking-[0.08em] text-slate-700 transition-colors duration-300 group-hover:text-slate-600">{s.code}</div>
              </div>
            </R>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   BEHAVIORAL DEPTH
   ══════════════════════════════════════════════════════════════════════════════ */

function Depth() {
  return (
    <section className="relative py-28">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />
      <div className="pointer-events-none absolute inset-0"><div className="absolute left-1/2 top-1/2 h-[450px] w-[700px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-violet-600/[0.03] blur-[130px]" /></div>
      <div className="relative mx-auto max-w-[68rem] px-6">
        <R className="text-center">
          <span className="text-[11px] font-bold uppercase tracking-[0.2em] text-emerald-400/60">Behavioral layer</span>
          <h2 className="mt-4 text-[1.75rem] font-bold text-white sm:text-[2.25rem]">
            Most tools count clicks.<br />We read behavior.
          </h2>
        </R>
        <div className="mt-16 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[
            { v: "0\u2013100%", m: "Scroll depth", d: "Did they even see the CTA? Or did they leave before the fold?" },
            { v: "Per second", m: "Dwell time", d: "3 seconds = not interested. 40 seconds = ready to buy. Big difference." },
            { v: "HOT / WARM / COLD", m: "Visitor intent", d: "Scored on scroll + dwell + clicks. A HOT visitor at 70+ is money on the table." },
            { v: "7-day window", m: "Return visits", d: "Someone came back 5 times and didn\u2019t buy. That\u2019s not loyalty. That\u2019s indecision." },
          ].map((x, i) => (
            <R key={x.m} d={i * 0.05}>
              <div className="group rounded-2xl border border-white/[0.04] bg-white/[0.012] p-6 transition-all duration-300 hover:border-white/[0.09] hover:bg-white/[0.025]">
                <div className="text-[20px] font-bold tracking-tight text-white transition-transform duration-300 group-hover:translate-x-0.5">{x.v}</div>
                <div className="mt-1.5 text-[13px] font-semibold text-slate-300">{x.m}</div>
                <p className="mt-2.5 text-[11.5px] leading-[1.7] text-slate-500">{x.d}</p>
              </div>
            </R>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   HOW IT WORKS
   ══════════════════════════════════════════════════════════════════════════════ */

function How() {
  return (
    <section id="how" className="relative scroll-mt-20 py-28">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />
      <div className="mx-auto max-w-[60rem] px-6">
        <R className="text-center">
          <span className="text-[11px] font-bold uppercase tracking-[0.2em] text-violet-400/60">How it works</span>
          <h2 className="mt-4 text-[1.75rem] font-bold text-white sm:text-[2.25rem]">Install. Wait 10 minutes. See everything.</h2>
        </R>
        <div className="mt-16 grid gap-0 sm:grid-cols-3">
          {[
            { n: "1", t: "One-click install", d: "From the Shopify App Store. No code. No theme edits. Tracking starts on the next visitor.", tag: "30 sec" },
            { n: "2", t: "Signals fire automatically", d: "Products get scored by conversion health. The engine runs every 5 minutes. Problems surface on their own.", tag: "~10 min" },
            { n: "3", t: "Fix it. Prove it.", d: "Each signal comes with a specific action. After you act, the proof loop measures before vs. after. No more guessing.", tag: "Measured" },
          ].map((s, i) => (
            <R key={s.n} d={i * 0.08}>
              <div className="relative flex flex-col items-center px-6 py-10 text-center">
                {i < 2 && <div className="absolute right-0 top-[4rem] hidden h-px w-full bg-gradient-to-r from-transparent via-violet-500/15 to-transparent sm:block" />}
                <div className="relative z-10 flex h-14 w-14 items-center justify-center rounded-2xl border border-violet-400/15 bg-gradient-to-br from-violet-500/[0.12] to-violet-600/[0.04] text-[18px] font-bold text-violet-400 shadow-[0_0_20px_-4px_rgba(124,58,237,0.15)] transition-shadow duration-300 hover:shadow-[0_0_30px_-4px_rgba(124,58,237,0.25)]">
                  {s.n}
                </div>
                <h3 className="mt-6 text-[15px] font-semibold text-white">{s.t}</h3>
                <p className="mt-2.5 text-[13px] leading-[1.65] text-slate-500">{s.d}</p>
                <span className="mt-4 rounded-full border border-white/[0.05] bg-white/[0.02] px-3.5 py-1 text-[10px] font-medium text-slate-500">{s.tag}</span>
              </div>
            </R>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   PROOF
   ══════════════════════════════════════════════════════════════════════════════ */

function Proof() {
  return (
    <section className="relative py-28">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />
      <div className="mx-auto max-w-[68rem] px-6">
        <div className="grid items-center gap-14 lg:grid-cols-2">
          <R>
            <span className="text-[11px] font-bold uppercase tracking-[0.2em] text-amber-400/60">Proof engine</span>
            <h2 className="mt-4 text-[1.75rem] font-bold leading-[1.15] text-white sm:text-[2.25rem]">
              Did it actually work?
              <br /><span className="text-slate-500">We prove it.</span>
            </h2>
            <p className="mt-6 text-[15px] leading-[1.75] text-slate-400">
              When you act on a signal, Hedge Spark holds back a control group automatically.
              <span className="text-slate-200"> Then it compares: did the visitors who saw your change convert more than those who didn&apos;t?</span>
            </p>
            <p className="mt-3 text-[14px] text-slate-500">Real incremental lift. Not correlation. Not vibes.</p>
          </R>

          <R d={0.1}>
            <div className="rounded-2xl border border-white/[0.05] bg-gradient-to-b from-white/[0.02] to-transparent p-px shadow-[0_8px_60px_-12px_rgba(0,0,0,0.3)]">
              <div className="rounded-[15px] bg-[#0a0a17] p-7">
                <div className="text-[10px] font-bold uppercase tracking-[0.15em] text-slate-600">Lift report</div>
                <div className="mt-6 space-y-4">
                  <div className="flex items-center justify-between">
                    <span className="text-[13px] text-slate-400">Saw the change</span>
                    <span className="text-[16px] font-bold tabular-nums text-emerald-400">4.2% CVR</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-[13px] text-slate-400">Control group</span>
                    <span className="text-[16px] font-bold tabular-nums text-slate-500">3.1% CVR</span>
                  </div>
                  <div className="border-t border-white/[0.05] pt-5">
                    <div className="flex items-center justify-between">
                      <span className="text-[14px] font-semibold text-white">Your change drove</span>
                      <span className="bg-gradient-to-r from-emerald-400 to-emerald-300 bg-clip-text text-[22px] font-bold tabular-nums text-transparent">+35.5% lift</span>
                    </div>
                    <div className="mt-2 flex items-center gap-2.5">
                      <span className="rounded-md bg-emerald-500/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider text-emerald-300 ring-1 ring-emerald-500/20">Confident</span>
                      <span className="text-[10px] text-slate-600">p &lt; 0.05 &middot; 1,240 visitors</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </R>
        </div>
      </div>
    </section>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   PRICING
   ══════════════════════════════════════════════════════════════════════════════ */

function Pricing() {
  const check = <svg className="mt-0.5 h-4 w-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}><path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" /></svg>;

  return (
    <section id="pricing" className="relative scroll-mt-20 py-28">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />
      <div className="mx-auto max-w-[52rem] px-6">
        <R className="text-center">
          <span className="text-[11px] font-bold uppercase tracking-[0.2em] text-violet-400/60">Pricing</span>
          <h2 className="mt-4 text-[1.75rem] font-bold text-white sm:text-[2.25rem]">Lite sees the problems. Pro fixes them.</h2>
        </R>
        <div className="mt-16 grid gap-6 sm:grid-cols-2">
          {/* Lite */}
          <R d={0.04}>
            <div className="flex h-full flex-col rounded-2xl border border-white/[0.05] bg-white/[0.012] p-8 transition-all duration-300 hover:border-white/[0.08]">
              <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-500">Lite</div>
              <div className="mt-5 flex items-baseline gap-1.5">
                <span className="text-[2.75rem] font-extrabold tracking-tight text-white">$0</span>
                <span className="text-sm text-slate-600">forever</span>
              </div>
              <p className="mt-4 text-[14px] leading-relaxed text-slate-400">See what&apos;s wrong. Every product, every visitor, in real time.</p>
              <ul className="mt-8 flex-1 space-y-3.5">
                {["Visitor intent scoring (HOT / WARM / COLD)", "8 detection signals per product", "Scroll depth + dwell time data", "Daily intelligence brief", "Revenue at risk estimates", "Traffic source quality scores"].map((f) => (
                  <li key={f} className="flex items-start gap-3 text-[13px] text-slate-300"><span className="text-emerald-400/70">{check}</span>{f}</li>
                ))}
              </ul>
              <a href="https://apps.shopify.com/" className="mt-9 block rounded-xl border border-white/[0.07] bg-white/[0.02] py-3.5 text-center text-[14px] font-semibold text-slate-300 transition-all duration-300 hover:border-white/[0.12] hover:bg-white/[0.05] hover:text-white">Install free</a>
            </div>
          </R>

          {/* Pro */}
          <R d={0.1}>
            <div className="relative flex h-full flex-col overflow-hidden rounded-2xl border border-violet-400/15 bg-gradient-to-b from-violet-500/[0.04] to-transparent p-8 transition-all duration-300 hover:border-violet-400/25">
              {/* Badge */}
              <div className="absolute -top-px left-8 rounded-b-lg bg-violet-600 px-4 py-1.5 text-[10px] font-bold uppercase tracking-[0.12em] text-white shadow-[0_4px_20px_-4px_rgba(124,58,237,0.5)]">14-day free trial</div>
              <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-violet-400">Pro</div>
              <div className="mt-5 flex items-baseline gap-1.5">
                <span className="text-[2.75rem] font-extrabold tracking-tight text-white">$49</span>
                <span className="text-sm text-slate-500">/month</span>
              </div>
              <p className="mt-4 text-[14px] leading-relaxed text-slate-400">Know what to <em className="not-italic text-slate-200">do</em> about it. And prove it worked.</p>
              <ul className="mt-8 flex-1 space-y-3.5">
                {["Everything in Lite", "Per-product action plans", "Conversion probability engine", "Holdout-based lift proof", "Revenue attribution (visitor \u2192 order)", "Market & price intelligence", "Before/after measurement", "Weekly email digest"].map((f) => (
                  <li key={f} className="flex items-start gap-3 text-[13px] text-slate-200"><span className="text-violet-400">{check}</span>{f}</li>
                ))}
              </ul>
              <a href="https://apps.shopify.com/" className="mt-9 block rounded-xl bg-violet-600 py-3.5 text-center text-[14px] font-semibold text-white shadow-[0_0_0_1px_rgba(124,58,237,0.3)] transition-all duration-300 hover:bg-violet-500 hover:shadow-[0_4px_40px_rgba(124,58,237,0.3)]">Start free trial</a>
            </div>
          </R>
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
    <section className="relative overflow-hidden py-32">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-1/2 top-1/2 h-[500px] w-[800px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-violet-600/[0.06] blur-[160px]" />
      </div>
      <R>
        <div className="relative mx-auto max-w-2xl px-6 text-center">
          <h2 className="text-[1.75rem] font-bold leading-[1.15] text-white sm:text-[2.5rem]">
            While you read this,<br />someone left your store.
          </h2>
          <p className="mt-3 text-[1.75rem] font-bold leading-[1.15] text-slate-500 sm:text-[2.5rem]">You&apos;ll never know why.</p>
          <p className="mt-7 text-[16px] text-slate-400">Unless you install Hedge Spark.</p>
          <div className="mt-9">
            <a href="https://apps.shopify.com/" className="group relative inline-block rounded-xl bg-violet-600 px-12 py-4.5 text-[15px] font-semibold text-white shadow-[0_0_0_1px_rgba(124,58,237,0.3)] transition-all duration-300 hover:bg-violet-500 hover:shadow-[0_4px_50px_rgba(124,58,237,0.35)]">
              <span className="pointer-events-none absolute inset-0 rounded-xl bg-gradient-to-b from-white/[0.08] to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
              <span className="relative">Install free. First signal in 10 minutes.</span>
            </a>
          </div>
          <p className="mt-5 text-[12px] text-slate-600">Free Lite plan. No credit card. Cancel Pro anytime.</p>
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
    <footer className="py-12" style={{ borderTop: "1px solid rgba(255,255,255,0.03)" }}>
      <div className="mx-auto max-w-[72rem] px-6">
        <div className="flex flex-col items-center justify-between gap-5 sm:flex-row">
          <span className="text-[14px] font-semibold text-slate-700">Hedge<span className="text-violet-400/40">Spark</span></span>
          <div className="flex items-center gap-8 text-[13px] text-slate-600">
            <a href="/app" className="transition-colors duration-200 hover:text-slate-300">Dashboard</a>
            <a href="/pricing" className="transition-colors duration-200 hover:text-slate-300">Pricing</a>
            <a href="mailto:dev@hedgesparkhq.com" className="transition-colors duration-200 hover:text-slate-300">Support</a>
          </div>
          <span className="text-[12px] text-slate-700">&copy; {new Date().getFullYear()} Hedge Spark</span>
        </div>
      </div>
    </footer>
  );
}

/* ══════════════════════════════════════════════════════════════════════════════
   PAGE
   ══════════════════════════════════════════════════════════════════════════════ */

export default function LandingPage() {
  const ok = useOAuthRedirect();
  if (!ok) return null;
  return (
    <div className="min-h-screen bg-[#080811] text-white antialiased">
      <Nav />
      <Hero />
      <BlindSpots />
      <Signals />
      <Depth />
      <How />
      <Proof />
      <Pricing />
      <FinalCTA />
      <Footer />
    </div>
  );
}
