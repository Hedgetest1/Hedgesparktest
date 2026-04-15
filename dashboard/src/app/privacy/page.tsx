export default function PrivacyPage() {
  return (
    <div className="min-h-screen bg-[#080811] text-white antialiased">
      <div className="mx-auto max-w-[42rem] px-6 py-24">
        <a href="/" className="text-[13px] text-slate-400 transition-colors hover:text-slate-400">&larr; Back to home</a>

        <h1 className="mt-8 text-[2rem] font-bold tracking-tight">Privacy Policy</h1>
        <p className="mt-2 text-[13px] text-slate-400">Last updated: April 2026</p>

        <div className="mt-10 space-y-8 text-[14px] leading-[1.75] text-slate-400">
          <section>
            <h2 className="text-[16px] font-semibold text-white">Data Controller</h2>
            <p className="mt-3">
              HedgeSpark operates as a data processor under GDPR Article 28. The merchant who installs
              HedgeSpark on their Shopify store is the data controller. HedgeSpark processes data
              exclusively on behalf of and under the instructions of the merchant.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">What we collect</h2>
            <p className="mt-3">
              HedgeSpark collects pseudonymous behavioral data from storefront visitors: page views,
              scroll depth, dwell time, click events, and cart interactions. We assign an anonymous
              visitor identifier via a first-party cookie. We do NOT collect personal information
              such as names, email addresses, phone numbers, or payment details of storefront customers.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Legal basis for processing</h2>
            <p className="mt-3">
              Processing is based on the merchant&apos;s legitimate interest in understanding storefront
              engagement (GDPR Article 6(1)(f)). Where applicable law requires visitor consent
              (e.g. ePrivacy Directive, CCPA/CPRA), the merchant must obtain consent before loading
              the HedgeSpark tracker script. Our tracker respects the Global Privacy Control (GPC)
              signal, the Do Not Track (DNT) header, and an explicit consent API.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">How we use data</h2>
            <p className="mt-3">
              All data is used exclusively to generate product intelligence signals, measure conversion
              impact, score revenue at risk, and improve recommendations for the merchant&apos;s store.
              We do not sell, share, or transfer visitor data to third parties for advertising or
              marketing purposes.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Sub-processors</h2>
            <p className="mt-3">
              HedgeSpark uses the following sub-processors: Shopify (merchant platform integration),
              Resend (transactional email delivery), Anthropic and OpenAI (AI analysis &mdash; no raw
              PII is sent to LLMs, enforced by a runtime PII guard), Sentry (error tracking with
              send_default_pii disabled). All sub-processors have signed Data Processing Agreements.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Data storage and security</h2>
            <p className="mt-3">
              All data is encrypted at rest and in transit (TLS 1.2+). Merchant access tokens are
              encrypted with AES-256. Data is stored on secure servers within the EU. Behavioral
              event data is retained for a maximum of 395 days; visitor purchase sessions for 730
              days. Automated retention sweeps run daily. An audit log with hash-chain integrity
              verification ensures tamper evidence.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Your rights</h2>
            <p className="mt-3">
              Under GDPR, UK DPA 2018, CCPA/CPRA, LGPD, and other applicable laws, data subjects
              have the right to: access their data (Art. 15), rectify inaccurate data (Art. 16),
              request erasure (Art. 17), data portability (Art. 20), object to processing (Art. 21),
              and not be subject to solely automated decision-making (Art. 22). Merchants can
              exercise these rights via their dashboard (Settings &gt; Privacy) or by contacting us.
              Storefront visitors should contact the merchant (data controller) directly.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">International data transfers</h2>
            <p className="mt-3">
              When data is transferred outside the EEA, we rely on Standard Contractual Clauses (SCCs)
              as approved by the European Commission. We honor the Global Privacy Control signal for
              California residents (CCPA/CPRA) and support opt-out requests under applicable US state
              privacy laws.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Breach notification</h2>
            <p className="mt-3">
              In the event of a personal data breach, HedgeSpark will notify the relevant supervisory
              authority within 72 hours (GDPR Art. 33) and affected data subjects without undue delay
              when required (Art. 34). An automated breach classifier monitors for security incidents
              continuously.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Children&apos;s privacy</h2>
            <p className="mt-3">
              HedgeSpark does not knowingly collect data from children under 16. Merchants are
              responsible for complying with COPPA, EU age of consent requirements, and other child
              protection laws on their storefronts.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Cookies</h2>
            <p className="mt-3">
              Our tracking script uses a single first-party cookie to maintain visitor session
              continuity. This cookie contains only a pseudonymous identifier. No cross-site tracking
              is performed. See our{" "}
              <a href="/cookies" className="text-violet-400 underline underline-offset-2 hover:text-violet-300">Cookie Policy</a>{" "}
              for full details.
            </p>
          </section>

          <section>
            <h2 className="text-[16px] font-semibold text-white">Contact</h2>
            <p className="mt-3">
              For privacy inquiries:{" "}
              <a href="mailto:privacy@hedgesparkhq.com" className="text-violet-400 hover:text-violet-300">
                privacy@hedgesparkhq.com
              </a>
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
