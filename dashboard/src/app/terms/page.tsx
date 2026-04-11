export default function TermsPage() {
  return (
    <div className="min-h-screen bg-[#080811] text-white antialiased">
      <div className="mx-auto max-w-[42rem] px-6 py-24">
        <a href="/" className="text-[13px] text-slate-600 transition-colors hover:text-slate-400">&larr; Back to home</a>

        <h1 className="mt-8 text-[2rem] font-bold tracking-tight">Terms of Service</h1>
        <p className="mt-2 text-[13px] text-slate-600">Last updated: April 2025</p>

        <div className="mt-10 space-y-8 text-[14px] leading-[1.75] text-slate-400">
          <section>
            <h2 className="text-[16px] font-semibold text-white">Service</h2>
            <p className="mt-3">
              HedgeSpark provides conversion intelligence and revenue optimization tools for Shopify merchants.
              The service includes behavioral tracking, signal detection, nudge deployment, and impact measurement.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Plans and billing</h2>
            <p className="mt-3">
              Lite plan is free with no time limit. Pro plan is billed monthly through Shopify&apos;s billing system.
              Pro includes a 14-day free trial. You can cancel at any time — your account will revert to Lite
              at the end of the billing period.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Your data</h2>
            <p className="mt-3">
              You retain full ownership of all data collected through your store. Upon cancellation or request,
              we will delete all associated data within 30 days. See our{" "}
              <a href="/privacy" className="text-violet-400 hover:text-violet-300">Privacy Policy</a> for details.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Acceptable use</h2>
            <p className="mt-3">
              The service is intended for legitimate Shopify merchants. Automated abuse, scraping, or
              attempts to manipulate the measurement system are prohibited.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Availability</h2>
            <p className="mt-3">
              We aim for high availability but do not guarantee 100% uptime. The service depends on
              Shopify&apos;s platform availability and third-party infrastructure.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Contact</h2>
            <p className="mt-3">
              Questions: <a href="mailto:dev@hedgesparkhq.com" className="text-violet-400 hover:text-violet-300">dev@hedgesparkhq.com</a>
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
