"use client";

import Image from "next/image";

export function DashboardHero({ shop }: { shop: string }) {
  const shopName = shop.replace(".myshopify.com", "");

  return (
    <div className="relative overflow-hidden rounded-3xl border border-[#d4893a]/15 bg-[#0e0e1a]">
      {/* Ambient gradient */}
      <div className="pointer-events-none absolute inset-0">
        <div
          className="absolute -left-24 -top-24 h-[280px] w-[400px] rounded-full blur-[120px]"
          style={{ background: "radial-gradient(circle, rgba(212,137,58,0.14) 0%, transparent 70%)" }}
        />
        <div
          className="absolute -right-16 -top-16 h-[240px] w-[320px] rounded-full blur-[100px]"
          style={{ background: "radial-gradient(circle, rgba(124,58,237,0.10) 0%, transparent 70%)" }}
        />
      </div>

      {/* Top accent bar */}
      <div className="absolute inset-x-0 top-0 h-[3px] rounded-t-3xl bg-gradient-to-r from-[#d4893a] via-[#a855f7] to-[#7c3aed]" />

      <div className="relative flex items-center gap-6 px-7 py-6 sm:px-8 sm:py-7">
        <Image
          src="/logo-beta-v2.png"
          alt="HedgeSpark"
          width={180}
          height={75}
          priority
          className="flex-shrink-0"
        />

        <div className="hidden h-14 w-px bg-gradient-to-b from-transparent via-white/[0.08] to-transparent sm:block" />

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-3">
            <h1 className="text-[20px] font-bold text-white">{shopName}</h1>
            <div className="flex items-center gap-1.5 rounded-full border border-emerald-400/20 bg-emerald-500/10 px-3 py-1">
              <div className="relative h-2 w-2">
                <div className="absolute inset-0 rounded-full bg-emerald-400" />
                <div className="absolute inset-0 animate-ping rounded-full bg-emerald-400/40" style={{ animationDuration: "2.5s" }} />
              </div>
              <span className="text-[13px] font-semibold text-emerald-300">Active</span>
            </div>
          </div>
          <p className="mt-1.5 text-[15px] text-slate-400">
            Watching <strong className="hs-brand-gradient">{shopName}</strong> — finding problems, proving fixes.
          </p>
        </div>
      </div>
    </div>
  );
}
