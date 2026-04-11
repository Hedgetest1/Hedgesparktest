export default function PrivacyPage() {
  return (
    <div className="min-h-screen bg-[#080811] text-white antialiased">
      <div className="mx-auto max-w-[42rem] px-6 py-24">
        <a href="/" className="text-[13px] text-slate-600 transition-colors hover:text-slate-400">&larr; Back to home</a>

        <h1 className="mt-8 text-[2rem] font-bold tracking-tight">Privacy Policy</h1>
        <p className="mt-2 text-[13px] text-slate-600">Last updated: April 2025</p>

        <div className="mt-10 space-y-8 text-[14px] leading-[1.75] text-slate-400">
          <section>
            <h2 className="text-[16px] font-semibold text-white">What we collect</h2>
            <p className="mt-3">
              HedgeSpark collects anonymous behavioral data from your storefront visitors: page views, scroll depth,
              dwell time, click events, and cart interactions. We do not collect personal information such as names,
              emails, or payment details of your customers.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">How we use it</h2>
            <p className="mt-3">
              All data is used exclusively to generate product intelligence signals, measure conversion impact,
              and improve recommendations for your store. We do not sell, share, or transfer visitor data to
              third parties.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Data storage and security</h2>
            <p className="mt-3">
              All data is encrypted at rest and in transit. Data is stored on secure servers within the EU.
              We retain behavioral event data for a maximum of 90 days. Aggregated metrics are retained
              for the duration of your subscription.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">GDPR compliance</h2>
            <p className="mt-3">
              HedgeSpark is fully GDPR compliant. We act as a data processor on behalf of the merchant
              (data controller). We support data export and deletion requests. Merchants can request
              full data erasure at any time by contacting dev@hedgesparkhq.com.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Cookies</h2>
            <p className="mt-3">
              Our tracking script uses a first-party cookie to maintain visitor session continuity.
              This cookie contains an anonymous identifier only. No cross-site tracking is performed.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Contact</h2>
            <p className="mt-3">
              For privacy inquiries: <a href="mailto:dev@hedgesparkhq.com" className="text-violet-400 hover:text-violet-300">dev@hedgesparkhq.com</a>
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
