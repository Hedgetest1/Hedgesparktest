export default function CookiePolicyPage() {
  return (
    <div className="min-h-screen bg-[#080811] text-white antialiased">
      <div className="mx-auto max-w-[42rem] px-6 py-24">
        <a href="/" className="text-[13px] text-slate-400 transition-colors hover:text-slate-400">&larr; Back to home</a>

        <h1 className="mt-8 text-[2rem] font-bold tracking-tight">Cookie Policy</h1>
        <p className="mt-2 text-[13px] text-slate-400">Last updated: April 2026</p>

        <div className="mt-10 space-y-8 text-[14px] leading-[1.75] text-slate-400">
          <section>
            <h2 className="text-[16px] font-semibold text-white">What cookies we use</h2>
            <p className="mt-3">
              HedgeSpark uses a single first-party cookie on the merchant&apos;s storefront to maintain
              visitor session continuity. This cookie contains only a pseudonymous identifier (visitor_id).
              No cross-site tracking is performed. No third-party cookies are set.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Cookie details</h2>
            <div className="mt-3 space-y-3">
              <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4">
                <p className="text-[13px] font-semibold text-white">hs_vid</p>
                <p className="mt-1 text-[12px]">
                  <span className="text-slate-400">Purpose:</span> Anonymous visitor identification for behavioral analytics
                </p>
                <p className="text-[12px]">
                  <span className="text-slate-400">Type:</span> First-party, persistent &middot;{" "}
                  <span className="text-slate-400">Duration:</span> 90 days &middot;{" "}
                  <span className="text-slate-400">Data:</span> Pseudonymous visitor ID (UUID)
                </p>
              </div>
              <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4">
                <p className="text-[13px] font-semibold text-white">hs_session</p>
                <p className="mt-1 text-[12px]">
                  <span className="text-slate-400">Purpose:</span> Merchant dashboard authentication
                </p>
                <p className="text-[12px]">
                  <span className="text-slate-400">Type:</span> First-party, HttpOnly, Secure, SameSite=None &middot;{" "}
                  <span className="text-slate-400">Duration:</span> Session (24h) &middot;{" "}
                  <span className="text-slate-400">Data:</span> Encrypted session token (no PII)
                </p>
              </div>
            </div>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Local storage</h2>
            <p className="mt-3">
              The tracker script may read localStorage key &apos;hs_consent&apos; as a legacy consent
              signal (&apos;1&apos; = consented, &apos;0&apos; = denied). This is a fallback mechanism;
              the preferred integration is via <code className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[12px] text-violet-300">window.hsSetConsent(given, region)</code>.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">How to control cookies</h2>
            <p className="mt-3">
              Merchants can integrate their cookie consent banner with HedgeSpark by calling{" "}
              <code className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[12px] text-violet-300">window.hsSetConsent(true/false, &apos;EU&apos;)</code>{" "}
              from their consent management platform. When consent is denied, the tracker stops
              collecting data immediately. The backend also respects the browser&apos;s Global Privacy
              Control (GPC) and Do Not Track (DNT) signals. Visitors can clear cookies via their
              browser settings at any time.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Contact</h2>
            <p className="mt-3">
              For cookie-related inquiries:{" "}
              <a href="mailto:privacy@hedgesparkhq.com" className="text-violet-400 underline underline-offset-2 hover:text-violet-300">
                privacy@hedgesparkhq.com
              </a>
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
