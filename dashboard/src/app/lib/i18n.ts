/**
 * i18n.ts — English-only shim.
 *
 * HedgeSpark ships in English natively. This file used to carry partial
 * translations for IT/ES/FR/DE (~32 keys each, against a dashboard of
 * thousands of strings — under 1% coverage). That was theater and
 * violated principle §2 rule 2 ("no half-truths, no hollow stubs"):
 * a merchant whose browser was set to Italian saw 32 Italian strings
 * surrounded by 3000 English ones, which felt broken.
 *
 * Every serious Shopify analytics competitor (Triple Whale, Peel, Varos,
 * Lifetimely, Northbeam) ships EN-only and merchants cope — so EN-only
 * is industry standard, not a downgrade. If real international coverage
 * ever becomes a paying-customer priority, reintroduce a proper i18n
 * library (next-intl, formatjs) and translate >95% of strings — never
 * accept partial coverage again.
 *
 * This file remains as a thin shim: `t(key)` returns the EN string for
 * the key if present, otherwise falls back to the key itself. Existing
 * call sites (CausalWhyCard, DemoPreviewCard, AskHedgeSparkCard) keep
 * working without code changes.
 */

export type Locale = "en";

type Dict = Record<string, string>;

const EN: Dict = {
  "hero.eyebrow_1": "Shopify App",
  "hero.eyebrow_2": "AI Revenue Intelligence",
  "hero.headline_1": "Your store is leaking money.",
  "hero.headline_2": "You don't know why.",
  "hero.headline_3": "We show you where.",
  "hero.sub":
    "The most advanced dashboard built for Shopify. Finds the products that get attention but don't sell. Stops the curse. Trust the magic.",
  "hero.cta_primary": "Install on Shopify",
  "hero.cta_secondary": "See how it works",
  "hero.cta_disclaimer":
    "Installs in 30 seconds. Tracking starts on the next visitor.",
  "demo.eyebrow": "See your numbers in 30 seconds",
  "demo.title": "No install. No OAuth. Just your Shopify URL.",
  "demo.sub":
    "We scan your public catalog and show you a real revenue estimate before you sign up.",
  "demo.placeholder": "yourstore.myshopify.com",
  "demo.button": "Run preview",
  "demo.button_loading": "Scanning…",
  "ask.eyebrow": "Ask Hedge Spark",
  "ask.title": "Ask any question about your store",
  "ask.sub": "Plain language, instant answer. No charts to dig through.",
  "ask.placeholder": "Why did revenue drop yesterday?",
  "ask.button": "Ask",
  "why.eyebrow": "The Why Engine",
  "why.title": "What's actually driving the numbers",
  "why.healthy": "All quiet — no causal anomalies detected.",
  "why.next_step": "Next step",
  "anomaly.eyebrow": "Anomaly Radar",
  "anomaly.title": "Cross-signal fusion alerts",
  "anomaly.healthy": "No correlated anomalies right now.",
  "common.confidence": "confident",
  "common.loading": "Loading…",
  "common.connect": "Connect",
  "common.connected": "connected",
  "common.not_connected": "not connected",
};

export function getLocale(): Locale {
  return "en";
}

export function setLocale(_locale: Locale): void {
  // No-op: EN-only by design. Kept for API compatibility with any
  // previous call sites that imported setLocale.
}

export function t(key: string, fallback?: string): string {
  return EN[key] || fallback || key;
}

export function supportedLocales(): Locale[] {
  return ["en"];
}
