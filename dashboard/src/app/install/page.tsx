"use client";

import Image from "next/image";
import { useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "";

export default function InstallPage() {
  const [shop, setShop] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleInstall = () => {
    setError("");
    let domain = shop.trim().toLowerCase();

    domain = domain.replace(/^https?:\/\//, "").replace(/\/$/, "");
    if (!domain.includes(".myshopify.com")) {
      domain = domain.replace(/\.myshopify\.com$/, "") + ".myshopify.com";
    }

    if (!domain || !domain.match(/^[a-z0-9-]+\.myshopify\.com$/)) {
      setError("Enter a valid Shopify store URL (e.g., yourstore.myshopify.com)");
      return;
    }

    setLoading(true);
    window.location.href = `${API}/auth/install?shop=${encodeURIComponent(domain)}`;
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#07070f] text-white antialiased">
      <div className="mx-auto max-w-[30rem] px-6 text-center">
        {/* Spark — dominant brand presence */}
        <div className="flex justify-center">
          <div className="hs-float">
            <Image
              src="/branding/hedgespark/spark.png"
              alt="Spark"
              width={80}
              height={80}
              priority
            />
          </div>
        </div>

        <h1 className="mt-6 text-[1.75rem] font-bold tracking-tight">
          Connect your store
        </h1>

        <p className="mt-4 text-[14px] leading-[1.7] text-slate-400">
          HedgeSpark will start watching your visitors immediately.
          <br />
          First signals in 10 minutes. No code changes needed.
        </p>

        <div className="mt-8">
          <div className="flex overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.02] focus-within:border-[#d4893a]/30 transition-colors">
            <input
              type="text"
              value={shop}
              onChange={(e) => setShop(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleInstall()}
              placeholder="yourstore.myshopify.com"
              className="flex-1 bg-transparent px-4 py-3.5 text-[14px] text-white placeholder-slate-600 outline-none"
              disabled={loading}
            />
            <button
              onClick={handleInstall}
              disabled={loading}
              className="bg-[#d4893a] hover:bg-[#e8a04e] px-6 text-[13px] font-semibold text-white transition-all hover:shadow-[0_0_20px_rgba(212,137,58,0.2)] disabled:opacity-50"
            >
              {loading ? "Connecting..." : "Install"}
            </button>
          </div>
          {error && (
            <p className="mt-3 text-[12px] text-rose-400">{error}</p>
          )}
        </div>

        {/* Trust signals */}
        <div className="mt-8 flex flex-wrap justify-center gap-x-6 gap-y-2 text-[11px] text-slate-400">
          <span>30-second install</span>
          <span>&middot;</span>
          <span>Read-only access</span>
          <span>&middot;</span>
          <span>GDPR compliant</span>
        </div>

        <p className="mt-6 text-[12px] leading-[1.6] text-slate-400">
          You&apos;ll be redirected to Shopify to approve the connection.
          We only request read access to products and orders.
          Your data stays encrypted and is never shared.
        </p>

        <a href="/" className="mt-10 inline-block text-[13px] text-slate-400 transition-colors hover:text-white">
          &larr; Back to home
        </a>
      </div>
    </div>
  );
}
