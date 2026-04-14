/**
 * Spark Action Engine v3 — Prescription + Proof Loop
 *
 * Deterministic decision layer. No LLM, no randomness, no fake data.
 *
 * v3 upgrades over v2:
 * - PRESCRIPTIVE actions: exact what/where/how (not diagnostic)
 * - PROOF LOOP: detect improvements via localStorage baseline comparison
 * - PATTERN DIAGNOSIS: patterns explain themselves using dominant metric
 * - PRIORITY: improving metrics deprioritize, worsening metrics boost
 *
 * Revenue formulas (same base as revenue_loss.py):
 *   daily_loss = views_24h × cvr × aov
 *   weekly     = daily_loss × 7
 *
 * CVR constants (documented):
 *   baseline     = 0.02  (Shopify industry average)
 *   returning    = 0.03  (1.5× — return visitors convert higher)
 *   engaged      = 0.025 (1.25× — high engagement = warmer traffic)
 *   abandon_recv = 0.20  (20% of abandoned carts recoverable)
 *   low_dwell    = 0.01  (0.5× — bouncing traffic converts poorly)
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
export type ExecutionPayload = {
  type: "upsell" | "bundle";
  productA: string;
  productB: string;
  audienceSize: number;
  suggestedMessage: string;
  timing: string;
  expectedImpact: string;
};

export type SparkAction = {
  id: string;
  title: string;
  context: string;
  action: string;
  impact: string;
  impactValue: number;
  priority: "CRITICAL" | "HIGH" | "MEDIUM";
  evidence: number;
  isPattern: boolean;
  /** Proof loop state — detected from localStorage baseline comparison */
  proofStatus?: "improving" | "worsening" | "new";
  proofDetail?: string;
  /** Temporal trend: traffic direction for this product */
  trend?: "rising" | "falling" | "stable";
  /** Segment tag for UI badges (e.g. "Returning visitors", "High-traffic") */
  segment?: string;
  targetProduct?: string;
  targetSection: string;
  /** Execution payload for upsell/bundle actions — Klaviyo-ready */
  executionPayload?: ExecutionPayload;
};

export type ProductInput = {
  product_url: string;
  views_24h?: number;
  views_7d?: number;
  unique_visitors_24h?: number;
  unique_visitors_7d?: number;
  return_visitor_count_7d?: number;
  cart_conversions_24h?: number;
  cart_conversions_7d?: number;
  cart_abandonment_rate?: number | null;
  engagement_score?: number | null;
  avg_dwell_24h?: number | null;
  avg_scroll_24h?: number | null;
  estimated_loss: number | null;
  priority: "HIGH" | "MED" | "LOW";
  attention_score: number;
  insight: string | null;
  // Device segmentation
  views_mobile?: number;
  views_desktop?: number;
  carts_mobile?: number;
  carts_desktop?: number;
  // Source segmentation
  views_paid?: number;
  views_organic?: number;
  views_direct?: number;
  carts_paid?: number;
  carts_organic?: number;
  carts_direct?: number;
  // Temporal trend (computed by API)
  cart_rate_24h?: number | null;
  cart_rate_7d?: number | null;
  cart_rate_trend?: string | null;
  // Purchase attribution
  purchases_24h?: number;
  purchases_mobile?: number;
  purchases_desktop?: number;
  purchases_paid?: number;
  purchases_organic?: number;
  purchases_direct?: number;
  revenue_24h?: number;
  // Time-of-day
  peak_hour_views?: number;
  peak_hour_carts?: number;
  off_peak_hour_views?: number;
  off_peak_hour_carts?: number;
  peak_conversion_label?: string | null;
  // Session context
  landing_views_24h?: number;
  browsing_views_24h?: number;
  landing_carts_24h?: number;
  browsing_carts_24h?: number;
  landing_cart_rate?: number | null;
  browsing_cart_rate?: number | null;
};

type SignalInput = {
  product_url?: string;
  signal_type?: string;
  signal_strength?: number;
  explanation?: string;
  human_label?: string;
  human_action?: string;
};

export type RevenueContext = {
  aov: number;
  currency: string;
  aovIsReal: boolean;
  baselineCvr: number;
};

// Store-level intelligence input (from GET /products/store-intelligence)
export type StoreIntelligence = {
  co_viewed?: { product_a: string; product_b: string; shared_visitors: number; a_views: number; b_views: number }[];
  revenue_concentration?: { top_product_url: string | null; top_product_revenue_pct: number | null; top_3_revenue_pct: number | null; is_concentrated: boolean };
  device_split?: { views_mobile_pct: number; purchases_mobile_pct: number; mobile_conversion_gap: boolean };
  source_split?: { views_paid_pct: number; purchases_paid_pct: number; paid_revenue_gap: boolean };
  cohort_snapshot?: { new_visitors_7d: number; returning_visitors_7d: number; new_visitor_cart_rate: number | null; returning_visitor_cart_rate: number | null };
  execution_opportunities?: {
    execution_id: string; type: string; product_a: string; product_b: string;
    audience_size: number; suggested_message: string | null; timing: string | null;
    expected_impact: string | null;
    execution_status: string; executed_at: string | null;
    return_rate: number | null; view_rate: number | null; purchase_rate: number | null;
    tracked_count: number;
    baseline_return_rate: number | null; baseline_view_rate: number | null;
    baseline_purchase_rate: number | null;
    delta_return_rate: number | null; delta_view_rate: number | null;
    delta_purchase_rate: number | null;
    post_sample_size: number;
    exposed_sample_size: number; holdout_sample_size: number;
    view_rate_exposed: number | null; view_rate_holdout: number | null;
    purchase_rate_exposed: number | null; purchase_rate_holdout: number | null;
    lift_view_rate: number | null; lift_purchase_rate: number | null;
    confidence_label: string | null;
    enforcement_mode: string;
  }[];
};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const RETURNING_CVR = 1.5;
const ENGAGED_CVR = 1.25;
const ABANDON_RECOVERY = 0.20;
const LOW_DWELL_S = 12;
const HIGH_DWELL_S = 30;

// ---------------------------------------------------------------------------
// Proof Loop — localStorage baseline
// ---------------------------------------------------------------------------
const BASELINE_KEY = "hs_action_baselines";

type Baseline = {
  cart_rate: number;      // carts / views (0 if no carts)
  abandon_rate: number;
  return_rate: number;
  dwell: number;
  ts: number;             // timestamp when baseline was saved
};

function loadBaselines(): Record<string, Baseline> {
  try {
    const raw = localStorage.getItem(BASELINE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function saveBaselines(baselines: Record<string, Baseline>): void {
  try { localStorage.setItem(BASELINE_KEY, JSON.stringify(baselines)); } catch { /* noop */ }
}

function getBaseline(baselines: Record<string, Baseline>, productUrl: string): Baseline | null {
  const b = baselines[productUrl];
  if (!b) return null;
  // Baselines older than 14 days are stale — treat as no baseline
  if (Date.now() - b.ts > 14 * 86_400_000) return null;
  return b;
}

function computeProof(
  p: ProductInput,
  actionId: string,
  baselines: Record<string, Baseline>,
): { proofStatus?: "improving" | "worsening" | "new"; proofDetail?: string } {
  const b = getBaseline(baselines, p.product_url);
  if (!b) return { proofStatus: "new" };

  const views = p.views_24h ?? 0;
  const carts = p.cart_conversions_24h ?? 0;
  const currentCartRate = views > 0 ? carts / views : 0;
  const abandon = p.cart_abandonment_rate ?? 0;

  if (actionId.startsWith("fix-cart-") || actionId.startsWith("fix-engage-")) {
    if (currentCartRate > b.cart_rate && currentCartRate > 0) {
      const delta = ((currentCartRate - b.cart_rate) * 100).toFixed(1);
      return { proofStatus: "improving", proofDetail: `Cart rate: ${(b.cart_rate * 100).toFixed(1)}% → ${(currentCartRate * 100).toFixed(1)}% (+${delta}pp)` };
    }
  }

  if (actionId.startsWith("fix-abandon-")) {
    if (abandon < b.abandon_rate && b.abandon_rate > 0) {
      const delta = ((b.abandon_rate - abandon) * 100).toFixed(0);
      return { proofStatus: "improving", proofDetail: `Abandonment: ${Math.round(b.abandon_rate * 100)}% → ${Math.round(abandon * 100)}% (−${delta}pp)` };
    }
    if (abandon > b.abandon_rate + 0.05) {
      return { proofStatus: "worsening", proofDetail: `Abandonment increased: ${Math.round(b.abandon_rate * 100)}% → ${Math.round(abandon * 100)}%` };
    }
  }

  if (actionId.startsWith("fix-dwell-")) {
    const dwell = p.avg_dwell_24h ?? 0;
    if (dwell > b.dwell + 3 && b.dwell > 0) {
      return { proofStatus: "improving", proofDetail: `Dwell time: ${b.dwell.toFixed(0)}s → ${dwell.toFixed(0)}s` };
    }
  }

  return {};
}

// ---------------------------------------------------------------------------
// Temporal intelligence — trend from existing 24h vs 7d data
// ---------------------------------------------------------------------------
function detectTrend(p: ProductInput): "rising" | "falling" | "stable" {
  const daily = p.views_24h ?? 0;
  const weeklyAvg = (p.views_7d ?? 0) / 7;
  if (weeklyAvg < 2) return "stable"; // not enough data
  const ratio = daily / weeklyAvg;
  if (ratio >= 1.3) return "rising";   // 30%+ above weekly average
  if (ratio <= 0.7) return "falling";  // 30%+ below weekly average
  return "stable";
}

/** Segment tag based on dominant visitor characteristic */
function detectSegment(p: ProductInput): string | undefined {
  // Device gap — check first, it's highly actionable
  const vm = p.views_mobile ?? 0;
  const vd = p.views_desktop ?? 0;
  const cm = p.carts_mobile ?? 0;
  const cd = p.carts_desktop ?? 0;
  if (vm >= 10 && vd >= 5) {
    const mRate = vm > 0 ? cm / vm : 0;
    const dRate = vd > 0 ? cd / vd : 0;
    if (dRate > 0 && mRate < dRate * 0.4) return "Mobile issue";
    if (mRate > 0 && dRate < mRate * 0.4 && vd >= 10) return "Desktop issue";
  }

  // Paid traffic problem
  const vp = p.views_paid ?? 0;
  const cp = p.carts_paid ?? 0;
  if (vp >= 10 && cp === 0) return "Paid traffic";

  // Landing page issue
  const lv = p.landing_views_24h ?? 0;
  const bv = p.browsing_views_24h ?? 0;
  const lcr = p.landing_cart_rate ?? 0;
  const bcr = p.browsing_cart_rate ?? 0;
  if (lv >= 10 && bv >= 5 && bcr > 0 && lcr < bcr * 0.3) return "Landing page issue";

  // Cart rate declining
  if (p.cart_rate_trend === "declining") return "Worsening";

  // Time mismatch
  if (p.peak_conversion_label === "off_peak_converts_better") return "Timing mismatch";

  const returns = p.return_visitor_count_7d ?? 0;
  const visitors = p.unique_visitors_7d ?? 0;
  const returnRate = visitors > 0 ? returns / visitors : 0;
  if (returnRate >= 0.25) return "Returning visitors";

  const eng = p.engagement_score ?? 0;
  if (eng >= 0.75) return "High engagement";

  const views = p.views_24h ?? 0;
  if (views >= 50) return "High traffic";

  if (p.cart_rate_trend === "improving") return "Improving";

  return undefined;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function sUrl(url: string): string {
  try {
    const path = new URL(url).pathname.replace(/\/$/, "");
    const parts = path.split("/").filter(Boolean);
    const last = parts.slice(-1)[0] ?? url;
    return last.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  } catch {
    return url.length > 30 ? "…" + url.slice(-28) : url;
  }
}

function fmt(value: number, ccy: string): string {
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency", currency: ccy,
      minimumFractionDigits: 0, maximumFractionDigits: 0,
    }).format(value);
  } catch {
    return `${ccy} ${Math.round(value)}`;
  }
}

function wk(dailyViews: number, cvr: number, aov: number): number {
  return Math.round(dailyViews * cvr * aov * 7);
}

/**
 * When real per-product revenue is available, use it to ground impact estimates.
 * Falls back to theoretical estimate (views × cvr × aov × 7) when no real data.
 */
function realWeeklyRevenue(p: ProductInput): number | null {
  const rev = p.revenue_24h ?? 0;
  if (rev > 0) return Math.round(rev * 7);
  return null;
}

// ---------------------------------------------------------------------------
// PRESCRIPTIVE PER-PRODUCT RULES
// ---------------------------------------------------------------------------
type RuleResult = SparkAction | null;

function ruleHighViewsLowCart(p: ProductInput, rev: RevenueContext): RuleResult {
  const views = p.views_24h ?? 0;
  const carts = p.cart_conversions_24h ?? 0;
  if (views < 10 || carts > 0) return null;

  const eng = p.engagement_score ?? 0;
  const dwell = p.avg_dwell_24h ?? 0;
  const scroll = p.avg_scroll_24h ?? 0;
  const effectiveCvr = eng > 0.6 ? rev.baselineCvr * ENGAGED_CVR : rev.baselineCvr;
  const impact = wk(views, effectiveCvr, rev.aov);
  if (impact < 5) return null;

  let action: string;
  let evidence = 1;

  if (dwell > 0 && dwell < LOW_DWELL_S) {
    action = [
      `1. Replace the hero image with a clear, lifestyle product shot — visitors decide in ${dwell.toFixed(0)}s.`,
      `2. Add a one-line benefit headline directly above the price (not just the product name).`,
      `3. Make the price and "Add to Cart" button visible without scrolling on mobile.`,
    ].join("\n");
    evidence += 1;
  } else if (eng > 0.7 && scroll > 50) {
    action = [
      `1. Add a sticky "Add to Cart" bar that stays visible as visitors scroll — they're reading ${Math.round(scroll)}% of the page but the CTA disappears.`,
      `2. Place the price in high contrast next to the CTA — visitors shouldn't have to look for it.`,
      `3. Add a trust element near the button: "Free returns" or "Ships in 24h."`,
    ].join("\n");
    evidence += 2;
  } else if (eng > 0.4) {
    action = [
      `1. Show the full price (including shipping estimate) near the Add to Cart button — hidden costs kill conversion.`,
      `2. Add 1-2 customer reviews or a trust badge next to the CTA area.`,
      `3. Test a "Buy now" button alongside "Add to Cart" to reduce decision steps.`,
    ].join("\n");
    evidence += 1;
  } else {
    action = [
      `1. Improve the first product image — use a white-background product shot, not a lifestyle image.`,
      `2. Write a specific headline: what the product does, not just its name.`,
      `3. Ensure the page loads in under 3 seconds — check with Google PageSpeed.`,
    ].join("\n");
  }

  return {
    id: `fix-cart-${p.product_url}`,
    title: `Fix cart conversion on ${sUrl(p.product_url)}`,
    context: `${views} views/day, zero add-to-carts${eng > 0 ? `, ${Math.round(eng * 100)}% engagement` : ""}${dwell > 0 ? `, ${dwell.toFixed(0)}s dwell` : ""}. Traffic exists but nothing converts.`,
    action,
    impact: `At ${Math.round(effectiveCvr * 100)}% conversion, this traffic is worth ~${fmt(impact, rev.currency)}/week.`,
    impactValue: impact,
    priority: views >= 50 ? "CRITICAL" : "HIGH",
    evidence,
    isPattern: false,
    targetProduct: p.product_url,
    targetSection: "product-performance",
  };
}

function ruleCartAbandonment(p: ProductInput, rev: RevenueContext): RuleResult {
  const abandon = p.cart_abandonment_rate ?? 0;
  const carts = p.cart_conversions_24h ?? 0;
  if (abandon < 0.5 || carts === 0) return null;

  const recoverable = Math.round(carts * abandon * rev.aov * ABANDON_RECOVERY * 7);
  if (recoverable < 10) return null;

  let action: string;
  let evidence = 1;

  if (abandon >= 0.85) {
    action = [
      `1. Check your checkout page for surprise costs — ${Math.round(abandon * 100)}% abandonment usually means shipping or tax appeared unexpectedly.`,
      `2. Add "Free shipping over $X" messaging on the product page before visitors reach checkout.`,
      `3. Verify all payment methods work — test a purchase yourself on mobile.`,
    ].join("\n");
    evidence += 1;
  } else if (abandon >= 0.7) {
    action = [
      `1. Show estimated shipping cost on the product page (before cart) so there's no surprise at checkout.`,
      `2. Add an exit-intent popup offering 10% off or free shipping for first-time buyers.`,
      `3. Enable guest checkout if it's not already active — forced account creation kills carts.`,
    ].join("\n");
    evidence += 1;
  } else {
    action = [
      `1. Set up an abandoned-cart email sequence (Shopify has this built in) — send within 1 hour.`,
      `2. Consider adding a cart-saver popup: "Still thinking? Your cart is saved for 24 hours."`,
      `3. Test a free-shipping threshold just above your AOV to incentivize completion.`,
    ].join("\n");
  }

  return {
    id: `fix-abandon-${p.product_url}`,
    title: `Recover abandoned carts on ${sUrl(p.product_url)}`,
    context: `${Math.round(abandon * 100)}% abandonment — ${carts} cart${carts !== 1 ? "s" : ""}/day, ${Math.round(carts * abandon)} lost before purchase.`,
    action,
    impact: `Recovering ${Math.round(ABANDON_RECOVERY * 100)}% of abandoned carts → ~${fmt(recoverable, rev.currency)}/week.`,
    impactValue: recoverable,
    priority: abandon >= 0.8 ? "CRITICAL" : "HIGH",
    evidence,
    isPattern: false,
    targetProduct: p.product_url,
    targetSection: "product-performance",
  };
}

function ruleReturnVisitorStall(p: ProductInput, rev: RevenueContext): RuleResult {
  const returns = p.return_visitor_count_7d ?? 0;
  const visitors = p.unique_visitors_7d ?? 0;
  const carts = p.cart_conversions_24h ?? 0;
  if (returns < 3 || visitors === 0) return null;

  const returnRate = returns / visitors;
  if (returnRate < 0.15 || carts > 2) return null;

  const views = p.views_24h ?? 0;
  const impact = wk(views, rev.baselineCvr * RETURNING_CVR, rev.aov);

  let action: string;
  if (returnRate >= 0.3) {
    action = [
      `1. Add a "Limited time" or "Only X left" message near the price — ${Math.round(returnRate * 100)}% return rate means high intent, just needs a push.`,
      `2. Set up a "You viewed this" email trigger for visitors who return 2+ times without buying.`,
      `3. Do NOT deep-discount — their intent is strong. A small nudge (free shipping, 5% off) is enough.`,
    ].join("\n");
  } else {
    action = [
      `1. Add a price comparison or "was/now" anchor if the product has ever been on sale.`,
      `2. Enable a "Notify me" option for price drops or restocks — capture their email for retargeting.`,
      `3. Add social proof near the CTA: "${Math.round(returns * 3)}+ people viewed this recently."`,
    ].join("\n");
  }

  return {
    id: `fix-return-${p.product_url}`,
    title: `Convert returning visitors on ${sUrl(p.product_url)}`,
    context: `${returns} visitors returned this week (${Math.round(returnRate * 100)}% return rate) with ${carts === 0 ? "zero" : "only " + carts} cart${carts !== 1 ? "s" : ""}. Interest is real, conversion is stalled.`,
    action,
    impact: `Returning visitors convert at ~${Math.round(rev.baselineCvr * RETURNING_CVR * 100)}%. Potential: ~${fmt(impact, rev.currency)}/week.`,
    impactValue: impact,
    priority: returns >= 8 ? "CRITICAL" : "HIGH",
    evidence: 2,
    isPattern: false,
    targetProduct: p.product_url,
    targetSection: "product-performance",
  };
}

function ruleHighEngagementNoAction(p: ProductInput, rev: RevenueContext): RuleResult {
  const eng = p.engagement_score ?? 0;
  const carts = p.cart_conversions_24h ?? 0;
  const views = p.views_24h ?? 0;
  const scroll = p.avg_scroll_24h ?? 0;
  const dwell = p.avg_dwell_24h ?? 0;
  if (eng < 0.65 || carts > 0 || views < 5) return null;

  const impact = wk(views, rev.baselineCvr * ENGAGED_CVR, rev.aov);

  let action: string;
  if (dwell > HIGH_DWELL_S && scroll > 60) {
    action = [
      `1. Add a sticky "Add to Cart" button — visitors spend ${dwell.toFixed(0)}s and scroll ${Math.round(scroll)}%, but the button disappears.`,
      `2. Use a contrasting color for the CTA that stands out from the page background.`,
      `3. Place "Add to Cart" and "Buy Now" side by side — reduce the decision to one click.`,
    ].join("\n");
  } else {
    action = [
      `1. Move the "Add to Cart" button higher on the page — make it visible without scrolling.`,
      `2. Add a product benefit summary (3 bullet points) directly above the CTA.`,
      `3. Test adding a size/variant selector that is pre-selected — removing selection friction lifts CVR.`,
    ].join("\n");
  }

  return {
    id: `fix-engage-${p.product_url}`,
    title: `Close the gap on ${sUrl(p.product_url)}`,
    context: `${Math.round(eng * 100)}% engagement, ${views} views/day, zero carts. Visitors read but don't act.`,
    action,
    impact: `Engaged traffic converts at ~${Math.round(rev.baselineCvr * ENGAGED_CVR * 100)}%. Potential: ~${fmt(impact, rev.currency)}/week.`,
    impactValue: impact,
    priority: eng >= 0.85 ? "CRITICAL" : "HIGH",
    evidence: 2,
    isPattern: false,
    targetProduct: p.product_url,
    targetSection: "product-performance",
  };
}

function ruleLowDwell(p: ProductInput, rev: RevenueContext): RuleResult {
  const dwell = p.avg_dwell_24h ?? 0;
  const views = p.views_24h ?? 0;
  if (dwell > LOW_DWELL_S || dwell === 0 || views < 15) return null;

  const impact = wk(views, rev.baselineCvr * 0.5, rev.aov);

  return {
    id: `fix-dwell-${p.product_url}`,
    title: `Visitors leave ${sUrl(p.product_url)} in ${dwell.toFixed(0)}s`,
    context: `${views} views/day, ${dwell.toFixed(1)}s avg dwell. Most leave before seeing the product.`,
    action: [
      `1. Test the page on your phone right now — if it takes more than 3s to load, that's the problem.`,
      `2. Use a clear product-on-white hero image (not a busy lifestyle shot) as the first thing visitors see.`,
      `3. Write the headline as a benefit, not a product name: "Keeps drinks cold for 24 hours" beats "Insulated Bottle."`,
    ].join("\n"),
    impact: `Doubling dwell lifts conversion 40-60%. Even at half baseline CVR → ~${fmt(impact, rev.currency)}/week potential.`,
    impactValue: impact,
    priority: dwell < 6 ? "HIGH" : "MEDIUM",
    evidence: 1,
    isPattern: false,
    targetProduct: p.product_url,
    targetSection: "product-performance",
  };
}

function ruleMobileConversionGap(p: ProductInput, rev: RevenueContext): RuleResult {
  const vm = p.views_mobile ?? 0;
  const vd = p.views_desktop ?? 0;
  const cm = p.carts_mobile ?? 0;
  const cd = p.carts_desktop ?? 0;
  if (vm < 10 || vd < 5) return null;

  const mRate = vm > 0 ? cm / vm : 0;
  const dRate = vd > 0 ? cd / vd : 0;

  // Mobile converts much worse than desktop
  if (dRate > 0 && mRate < dRate * 0.4) {
    const gapPct = Math.round((1 - mRate / Math.max(dRate, 0.001)) * 100);
    const impact = wk(vm, rev.baselineCvr * 0.5, rev.aov); // lost mobile revenue

    return {
      id: `fix-mobile-${p.product_url}`,
      title: `Mobile visitors aren't converting on ${sUrl(p.product_url)}`,
      context: `${vm} mobile views, ${cm} cart(s) (${(mRate * 100).toFixed(1)}%) vs ${vd} desktop views, ${cd} cart(s) (${(dRate * 100).toFixed(1)}%). Mobile converts ${gapPct}% worse.`,
      action: [
        `1. Open this product page on your phone — check if the "Add to Cart" button is visible without scrolling.`,
        `2. Test the page load speed on mobile — if it takes more than 3 seconds, that's the problem.`,
        `3. Make the product image swipeable and the price + CTA sticky at the bottom of the screen.`,
      ].join("\n"),
      impact: `Closing the mobile gap could recover ~${fmt(impact, rev.currency)}/week from mobile alone.`,
      impactValue: impact,
      priority: vm >= 30 ? "CRITICAL" : "HIGH",
      evidence: 3,
      isPattern: false,
      targetProduct: p.product_url,
      targetSection: "product-performance",
    };
  }

  return null;
}

function rulePaidTrafficWasted(p: ProductInput, rev: RevenueContext): RuleResult {
  const vp = p.views_paid ?? 0;
  const cp = p.carts_paid ?? 0;
  if (vp < 10 || cp > 0) return null;

  const organicCarts = (p.carts_organic ?? 0) + (p.carts_direct ?? 0);
  const impact = wk(vp, rev.baselineCvr, rev.aov);

  const organicProof = organicCarts > 0
    ? ` Organic/direct traffic generated ${organicCarts} cart(s) — the page works, the targeting may not.`
    : ` No source is converting — fix the product page before the ad spend.`;

  return {
    id: `fix-paid-${p.product_url}`,
    title: `Paid traffic to ${sUrl(p.product_url)} isn't converting`,
    context: `${vp} paid views, zero carts.${organicProof}`,
    action: organicCarts > 0
      ? [
          `1. Check your ad targeting — the audience clicking these ads may not match this product.`,
          `2. Align your ad creative with the landing page — if the ad promises something specific, it must be immediately visible.`,
          `3. Narrow your audience or test a different product for this campaign.`,
        ].join("\n")
      : [
          `1. Pause the ad spend until the product page converts organically.`,
          `2. Fix the product page first (see other actions above).`,
          `3. Once organic conversion is healthy, re-enable ads with tighter targeting.`,
        ].join("\n"),
    impact: `This paid traffic costs money. Potential value if converting: ~${fmt(impact, rev.currency)}/week.`,
    impactValue: impact,
    priority: "HIGH",
    evidence: 2,
    isPattern: false,
    targetProduct: p.product_url,
    targetSection: "product-performance",
  };
}

function ruleCartRateDeclining(p: ProductInput, rev: RevenueContext): RuleResult {
  const rate24h = p.cart_rate_24h ?? null;
  const rate7d = p.cart_rate_7d ?? null;
  if (rate24h === null || rate7d === null || rate7d < 0.005) return null;

  const ratio = rate24h / rate7d;
  if (ratio > 0.6) return null; // Not declining enough

  const dropPct = Math.round((1 - ratio) * 100);
  const views = p.views_24h ?? 0;
  const impact = wk(views, rate7d - rate24h, rev.aov); // lost conversion delta

  return {
    id: `fix-trend-${p.product_url}`,
    title: `Cart rate dropped ${dropPct}% on ${sUrl(p.product_url)}`,
    context: `Today's cart rate: ${(rate24h * 100).toFixed(1)}% vs 7-day average: ${(rate7d * 100).toFixed(1)}%. Something changed.`,
    action: [
      `1. Check if you or your team made recent changes to this product page (price, description, images, app conflicts).`,
      `2. Verify the page loads correctly on both mobile and desktop — broken elements can cause sudden drops.`,
      `3. Check if a competitor launched a promotion — visitors may be comparison shopping.`,
    ].join("\n"),
    impact: `Restoring the previous conversion rate would recover ~${fmt(impact, rev.currency)}/week.`,
    impactValue: impact,
    priority: dropPct >= 50 ? "CRITICAL" : "HIGH",
    evidence: 2,
    isPattern: false,
    targetProduct: p.product_url,
    targetSection: "product-performance",
  };
}

function ruleLandingPageFailure(p: ProductInput, rev: RevenueContext): RuleResult {
  const lv = p.landing_views_24h ?? 0;
  const bv = p.browsing_views_24h ?? 0;
  const lc = p.landing_carts_24h ?? 0;
  const bc = p.browsing_carts_24h ?? 0;
  if (lv < 10 || bv < 5) return null;

  const lRate = lv > 0 ? lc / lv : 0;
  const bRate = bv > 0 ? bc / bv : 0;
  if (bRate <= 0 || lRate >= bRate * 0.3) return null;

  const impact = wk(lv, rev.baselineCvr * 0.5, rev.aov);

  return {
    id: `fix-landing-${p.product_url}`,
    title: `${sUrl(p.product_url)} fails as a landing page`,
    context: `${lv} visitors landed directly on this page (${(lRate * 100).toFixed(1)}% cart rate) but visitors who browse to it convert at ${(bRate * 100).toFixed(1)}%. The first impression isn't working.`,
    action: [
      `1. Check the above-the-fold content on this page — is the product benefit immediately clear?`,
      `2. Add social proof (reviews, "X people bought this") visible without scrolling.`,
      `3. Ensure the price and "Add to Cart" button are visible within the first screen.`,
    ].join("\n"),
    impact: `Improving the landing experience could recover ~${fmt(impact, rev.currency)}/week from direct visitors.`,
    impactValue: impact,
    priority: lv >= 25 ? "CRITICAL" : "HIGH",
    evidence: 3,
    isPattern: false,
    targetProduct: p.product_url,
    targetSection: "product-performance",
  };
}

function ruleDevicePurchaseGap(p: ProductInput, rev: RevenueContext): RuleResult {
  const pm = p.purchases_mobile ?? 0;
  const pd = p.purchases_desktop ?? 0;
  const vm = p.views_mobile ?? 0;
  const total = pm + pd;
  if (total < 2) return null;

  // Mobile views but zero mobile purchases (desktop is buying)
  if (pd > 0 && pm === 0 && vm >= 10) {
    const impact = wk(vm, rev.baselineCvr, rev.aov);
    return {
      id: `fix-device-purchase-${p.product_url}`,
      title: `Mobile visitors view ${sUrl(p.product_url)} but never buy`,
      context: `${vm} mobile views, zero mobile purchases. Desktop generated ${pd} purchase(s). Mobile checkout may be broken or too slow.`,
      action: [
        `1. Complete a test purchase on your phone right now — check every step from product page to confirmation.`,
        `2. Verify all payment methods work on mobile (Apple Pay, Google Pay are critical).`,
        `3. Check mobile page speed — if checkout takes more than 3 seconds to load, that's the problem.`,
      ].join("\n"),
      impact: `Fixing mobile checkout could add ~${fmt(impact, rev.currency)}/week in mobile revenue.`,
      impactValue: impact,
      priority: vm >= 30 ? "CRITICAL" : "HIGH",
      evidence: 3,
      isPattern: false,
      targetProduct: p.product_url,
      targetSection: "product-performance",
    };
  }
  return null;
}

// ---------------------------------------------------------------------------
// PATTERN DETECTION — cross-product, self-diagnosing
// ---------------------------------------------------------------------------

function patternStoreWideCTA(products: ProductInput[], rev: RevenueContext): RuleResult {
  const affected = products.filter(
    p => (p.views_24h ?? 0) >= 10 && (p.cart_conversions_24h ?? 0) === 0
  );
  if (affected.length < 3) return null;

  const totalViews = affected.reduce((s, p) => s + (p.views_24h ?? 0), 0);
  const impact = wk(totalViews, rev.baselineCvr, rev.aov);
  const names = affected.slice(0, 3).map(p => sUrl(p.product_url));

  // Diagnose the dominant sub-pattern
  const lowDwellCount = affected.filter(p => (p.avg_dwell_24h ?? 99) < LOW_DWELL_S).length;
  const highEngCount = affected.filter(p => (p.engagement_score ?? 0) > 0.6).length;

  let diagnosis: string;
  let action: string;

  if (lowDwellCount >= affected.length * 0.5) {
    diagnosis = `Most of these products have low dwell time — visitors leave before reading. This points to a theme-level above-the-fold problem.`;
    action = [
      `1. Open your Shopify theme editor → Product page template. Check the hero section layout on mobile.`,
      `2. Ensure the product image loads instantly (lazy-load is fine for below-fold, not the hero).`,
      `3. Move the price and CTA above the fold — if visitors have to scroll to see the price, most won't.`,
    ].join("\n");
  } else if (highEngCount >= affected.length * 0.5) {
    diagnosis = `Most of these products have decent engagement — visitors stay and read, but never click "Add to Cart." This is a CTA problem across your template.`;
    action = [
      `1. Open your Shopify theme → Product page. Make the "Add to Cart" button larger, sticky on scroll, and in a contrasting color.`,
      `2. Check that the button isn't pushed below visible content by long descriptions or variant selectors.`,
      `3. Add a trust line directly below the button: "Free returns · Ships in 24h · Secure checkout."`,
    ].join("\n");
  } else {
    diagnosis = `Mixed signals across products — but the common thread is zero carts despite traffic. Your product page template likely has a structural conversion issue.`;
    action = [
      `1. Review your product page template in Shopify theme editor — focus on mobile layout.`,
      `2. Check: is the Add to Cart button visible without scrolling? Is the price clear? Are there trust signals?`,
      `3. Test one product page change (e.g., sticky CTA) — it will affect all ${affected.length} products simultaneously.`,
    ].join("\n");
  }

  return {
    id: "pattern-store-cta",
    title: `Store-wide conversion gap: ${affected.length} products with traffic but zero carts`,
    context: `${names.join(", ")}${affected.length > 3 ? ` and ${affected.length - 3} more` : ""} — all have views, none have carts. ${diagnosis}`,
    action,
    impact: `One template fix affects all ${affected.length} products. Combined potential: ~${fmt(impact, rev.currency)}/week.`,
    impactValue: impact,
    priority: "CRITICAL",
    evidence: affected.length,
    isPattern: true,
    targetSection: "product-performance",
  };
}

function patternCheckoutFriction(products: ProductInput[], rev: RevenueContext): RuleResult {
  const affected = products.filter(
    p => (p.cart_conversions_24h ?? 0) > 0 && (p.cart_abandonment_rate ?? 0) >= 0.6
  );
  if (affected.length < 2) return null;

  const avgAbandon = affected.reduce((s, p) => s + (p.cart_abandonment_rate ?? 0), 0) / affected.length;
  const totalCarts = affected.reduce((s, p) => s + (p.cart_conversions_24h ?? 0), 0);
  const recoverable = Math.round(totalCarts * avgAbandon * rev.aov * ABANDON_RECOVERY * 7);

  return {
    id: "pattern-checkout-friction",
    title: `Checkout friction: ${affected.length} products with ${Math.round(avgAbandon * 100)}% avg abandonment`,
    context: `${affected.length} products have visitors adding to cart but abandoning. Average abandonment: ${Math.round(avgAbandon * 100)}%. This is a checkout problem, not a product problem.`,
    action: [
      `1. Check your Shopify checkout settings → are shipping costs shown only at the last step? Move them earlier.`,
      `2. Enable all major payment methods (Apple Pay, Google Pay, PayPal) — limited options cause abandonment.`,
      `3. Ensure guest checkout is enabled — forcing account creation adds friction.`,
      `4. Test a purchase yourself on mobile — complete the full flow and note any friction points.`,
    ].join("\n"),
    impact: `Recovering ${Math.round(ABANDON_RECOVERY * 100)}% of ${totalCarts} daily abandoned carts → ~${fmt(recoverable, rev.currency)}/week.`,
    impactValue: recoverable,
    priority: "CRITICAL",
    evidence: affected.length,
    isPattern: true,
    targetSection: "funnel",
  };
}

function patternReturningCluster(products: ProductInput[], rev: RevenueContext): RuleResult {
  const affected = products.filter(p => {
    const returns = p.return_visitor_count_7d ?? 0;
    const visitors = p.unique_visitors_7d ?? 0;
    return returns >= 3 && visitors > 0 && (returns / visitors) >= 0.15 && (p.cart_conversions_24h ?? 0) <= 1;
  });
  if (affected.length < 2) return null;

  const totalReturns = affected.reduce((s, p) => s + (p.return_visitor_count_7d ?? 0), 0);
  const totalViews = affected.reduce((s, p) => s + (p.views_24h ?? 0), 0);
  const impact = Math.round(wk(totalViews, rev.baselineCvr * RETURNING_CVR, rev.aov) * 0.5);

  return {
    id: "pattern-return-cluster",
    title: `${totalReturns} returning visitors across ${affected.length} products — not buying`,
    context: `Multiple products have high return-visit rates with low conversion. These visitors want your products but something store-wide is blocking them — likely price confidence, unclear offers, or missing urgency.`,
    action: [
      `1. Add a site-wide banner: "Free shipping over $X" or "10% off first order" — this addresses hesitation across all products.`,
      `2. Enable a "Back in stock" or "Price drop alert" option on product pages — capture emails from hesitant visitors.`,
      `3. Add a visible return policy and satisfaction guarantee near every CTA — returning visitors need reassurance, not discounts.`,
    ].join("\n"),
    impact: `${totalReturns} returning visitors at ~${Math.round(rev.baselineCvr * RETURNING_CVR * 100)}% CVR. Capturing half → ~${fmt(impact, rev.currency)}/week.`,
    impactValue: impact,
    priority: "CRITICAL",
    evidence: affected.length + 1,
    isPattern: true,
    targetSection: "product-performance",
  };
}

// ---------------------------------------------------------------------------
// STORE-LEVEL STRATEGIC PATTERNS
// ---------------------------------------------------------------------------

function storeRevenueConcentration(products: ProductInput[], rev: RevenueContext, si?: StoreIntelligence): RuleResult {
  const conc = si?.revenue_concentration;
  if (!conc || !conc.is_concentrated || !conc.top_product_url) return null;

  const topUrl = conc.top_product_url;
  const topPct = conc.top_product_revenue_pct ?? 0;
  const top3Pct = conc.top_3_revenue_pct ?? 0;
  const totalRev = products.reduce((s, p) => s + (p.revenue_24h ?? 0), 0);
  const weeklyRev = Math.round(totalRev * 7);

  return {
    id: "strategic-revenue-concentration",
    title: `Revenue is concentrated: ${Math.round(topPct)}% comes from one product`,
    context: `${sUrl(topUrl)} generates ${Math.round(topPct)}% of your revenue. Your top 3 products account for ${Math.round(top3Pct)}%. If this product's traffic drops, your entire store is affected.`,
    action: [
      `1. Identify your second-tier products with decent traffic but low conversion — these are your growth levers.`,
      `2. Cross-link from ${sUrl(topUrl)} to related products (e.g., "Customers also bought…").`,
      `3. Run a promotion on one underperforming product to diversify your revenue base.`,
    ].join("\n"),
    impact: weeklyRev > 0 ? `Your store does ~${fmt(weeklyRev, rev.currency)}/week. Diversifying reduces single-product risk.` : `Diversifying revenue reduces your dependence on a single product.`,
    impactValue: weeklyRev > 0 ? Math.round(weeklyRev * 0.2) : 0,
    priority: topPct >= 70 ? "CRITICAL" : "HIGH",
    evidence: products.length,
    isPattern: true,
    targetSection: "strategic",
  };
}

function storeMobileGap(_products: ProductInput[], _rev: RevenueContext, si?: StoreIntelligence): RuleResult {
  const ds = si?.device_split;
  if (!ds || !ds.mobile_conversion_gap) return null;

  return {
    id: "strategic-mobile-gap",
    title: `Mobile drives ${Math.round(ds.views_mobile_pct)}% of traffic but only ${Math.round(ds.purchases_mobile_pct)}% of purchases`,
    context: `Store-wide, mobile visitors are browsing but not buying. This is a checkout or page speed problem, not a product problem — it affects your entire store.`,
    action: [
      `1. Test a complete mobile purchase yourself — from homepage to checkout confirmation.`,
      `2. Check if Apple Pay / Google Pay are enabled — mobile shoppers expect one-tap checkout.`,
      `3. Run Google PageSpeed Insights on your top 3 product pages using a mobile device.`,
    ].join("\n"),
    impact: `Closing the mobile gap could lift overall revenue by 20-40%.`,
    impactValue: 0,
    priority: "CRITICAL",
    evidence: 4,
    isPattern: true,
    targetSection: "strategic",
  };
}

function storePaidGap(_products: ProductInput[], _rev: RevenueContext, si?: StoreIntelligence): RuleResult {
  const ss = si?.source_split;
  if (!ss || !ss.paid_revenue_gap) return null;

  return {
    id: "strategic-paid-gap",
    title: `Paid traffic drives ${Math.round(ss.views_paid_pct)}% of visits but only ${Math.round(ss.purchases_paid_pct)}% of purchases`,
    context: `Store-wide, you're paying for traffic that doesn't convert to revenue. Organic and direct traffic converts better — your ad spend may be misallocated.`,
    action: [
      `1. Audit your top ad campaigns — are they sending traffic to the right products?`,
      `2. Compare your ad creative with what visitors see on the landing page — misalignment kills conversion.`,
      `3. Shift budget toward products with proven organic conversion, then retarget with ads.`,
    ].join("\n"),
    impact: `Reallocating paid budget to converting products could significantly improve ROAS.`,
    impactValue: 0,
    priority: "CRITICAL",
    evidence: 3,
    isPattern: true,
    targetSection: "strategic",
  };
}

function storeCohortDrift(_products: ProductInput[], _rev: RevenueContext, si?: StoreIntelligence): RuleResult {
  const cs = si?.cohort_snapshot;
  if (!cs) return null;
  const newRate = cs.new_visitor_cart_rate;
  const retRate = cs.returning_visitor_cart_rate;
  if (newRate === null || retRate === null) return null;
  if (cs.new_visitors_7d < 20 || cs.returning_visitors_7d < 10) return null;

  // New visitors convert much worse than returning
  if (retRate > 0 && newRate < retRate * 0.3) {
    return {
      id: "strategic-cohort-drift",
      title: `New visitors convert ${Math.round((1 - newRate / retRate) * 100)}% worse than returning visitors`,
      context: `Returning visitors cart at ${(retRate * 100).toFixed(1)}% but new visitors only at ${(newRate * 100).toFixed(1)}%. Your store's first impression isn't working — new traffic is being wasted.`,
      action: [
        `1. Add social proof visible on first visit: reviews, customer count, press mentions.`,
        `2. Consider a first-purchase incentive (10% off, free shipping) for new visitors only.`,
        `3. Check your homepage and top landing pages — are they clear about what you sell and why?`,
      ].join("\n"),
      impact: `${cs.new_visitors_7d} new visitors this week. Converting them at even half the returning visitor rate would meaningfully grow revenue.`,
      impactValue: 0,
      priority: "HIGH",
      evidence: 3,
      isPattern: true,
      targetSection: "strategic",
    };
  }

  return null;
}

export type ProofData = {
  returnRate: number | null;
  viewRate: number | null;
  purchaseRate: number | null;
  trackedCount: number;
  executionStatus: string;
  executedAt: string | null;
  baselineViewRate: number | null;
  baselinePurchaseRate: number | null;
  deltaViewRate: number | null;
  deltaPurchaseRate: number | null;
  postSampleSize: number;
  confidenceLabel: string | null;
  // Counterfactual (exposed vs holdout)
  exposedSampleSize: number;
  holdoutSampleSize: number;
  viewRateExposed: number | null;
  viewRateHoldout: number | null;
  purchaseRateExposed: number | null;
  purchaseRateHoldout: number | null;
  liftViewRate: number | null;
  liftPurchaseRate: number | null;
  enforcementMode: string;  // email | onsite | unknown
};

function _findExecOpp(si: StoreIntelligence | undefined, type: string, a: string, b: string): { exec: ExecutionPayload; proof: ProofData } | undefined {
  const opps = si?.execution_opportunities;
  if (!opps) return undefined;
  const match = opps.find(o => o.type === type && ((o.product_a === a && o.product_b === b) || (o.product_a === b && o.product_b === a)));
  if (!match) return undefined;
  return {
    exec: {
      type: match.type as "upsell" | "bundle",
      productA: match.product_a,
      productB: match.product_b,
      audienceSize: match.audience_size,
      suggestedMessage: match.suggested_message ?? "",
      timing: match.timing ?? "",
      expectedImpact: match.expected_impact ?? "",
    },
    proof: {
      returnRate: match.return_rate,
      viewRate: match.view_rate,
      purchaseRate: match.purchase_rate,
      trackedCount: match.tracked_count,
      executionStatus: match.execution_status,
      executedAt: match.executed_at,
      baselineViewRate: match.baseline_view_rate,
      baselinePurchaseRate: match.baseline_purchase_rate,
      deltaViewRate: match.delta_view_rate,
      deltaPurchaseRate: match.delta_purchase_rate,
      postSampleSize: match.post_sample_size,
      confidenceLabel: match.confidence_label,
      exposedSampleSize: match.exposed_sample_size,
      holdoutSampleSize: match.holdout_sample_size,
      viewRateExposed: match.view_rate_exposed,
      viewRateHoldout: match.view_rate_holdout,
      purchaseRateExposed: match.purchase_rate_exposed,
      purchaseRateHoldout: match.purchase_rate_holdout,
      liftViewRate: match.lift_view_rate,
      liftPurchaseRate: match.lift_purchase_rate,
      enforcementMode: match.enforcement_mode,
    },
  };
}

function _buildProofNote(proof: ProofData | undefined, productName: string): string {
  if (!proof || proof.trackedCount < 5) return "";

  // Post-execution counterfactual (strongest — exposed vs holdout)
  if (proof.executionStatus === "executed" && proof.exposedSampleSize >= 5 && proof.holdoutSampleSize >= 3) {
    const enforced = proof.enforcementMode === "email" ? " (controlled)" : proof.enforcementMode === "onsite" ? " (partial control)" : "";
    const conf = proof.confidenceLabel === "strong" ? `Strong signal${enforced}`
      : proof.confidenceLabel === "moderate" ? `Moderate confidence${enforced}`
      : "Early signal";

    if (proof.liftPurchaseRate !== null && proof.liftPurchaseRate > 0) {
      const expPct = proof.purchaseRateExposed !== null ? `${(proof.purchaseRateExposed * 100).toFixed(1)}%` : "?";
      const hldPct = proof.purchaseRateHoldout !== null ? `${(proof.purchaseRateHoldout * 100).toFixed(1)}%` : "?";
      return ` ${conf}: exposed purchased at ${expPct} vs ${hldPct} holdout → +${(proof.liftPurchaseRate * 100).toFixed(1)}pp lift (${proof.exposedSampleSize} exposed, ${proof.holdoutSampleSize} holdout).`;
    }

    if (proof.liftViewRate !== null && proof.liftViewRate > 0) {
      const expPct = proof.viewRateExposed !== null ? `${(proof.viewRateExposed * 100).toFixed(1)}%` : "?";
      const hldPct = proof.viewRateHoldout !== null ? `${(proof.viewRateHoldout * 100).toFixed(1)}%` : "?";
      return ` ${conf}: exposed viewed at ${expPct} vs ${hldPct} holdout → +${(proof.liftViewRate * 100).toFixed(1)}pp lift (${proof.exposedSampleSize} exposed, ${proof.holdoutSampleSize} holdout).`;
    }

    // Executed with holdout but no lift detected
    if (proof.liftViewRate !== null || proof.liftPurchaseRate !== null) {
      return ` No effect detected: exposed and holdout behave similarly (${proof.exposedSampleSize} exposed, ${proof.holdoutSampleSize} holdout).`;
    }

    return ` Executed — tracking outcomes (${proof.exposedSampleSize} exposed, ${proof.holdoutSampleSize} holdout).`;
  }

  // Post-execution before/after (moderate — no holdout yet)
  if (proof.executionStatus === "executed" && proof.confidenceLabel) {
    if (proof.deltaViewRate !== null && proof.deltaViewRate > 0) {
      return ` Since execution: view rate +${(proof.deltaViewRate * 100).toFixed(1)}pp (n=${proof.postSampleSize}).`;
    }
    return ` Executed — tracking outcomes (n=${proof.postSampleSize}).`;
  }

  // Pre-execution observational (weakest)
  if (proof.viewRate !== null && proof.viewRate > 0) {
    const viewPct = Math.round(proof.viewRate * 100);
    const purchaseNote = proof.purchaseRate !== null && proof.purchaseRate > 0
      ? `, ${Math.round(proof.purchaseRate * 100)}% purchased`
      : "";
    return ` ${viewPct}% of audience viewed ${productName}${purchaseNote}.`;
  }

  return "";
}

function storeCoViewUpsell(products: ProductInput[], rev: RevenueContext, si?: StoreIntelligence): RuleResult {
  const pairs = si?.co_viewed;
  if (!pairs || pairs.length === 0) return null;

  const productMap = new Map(products.map(p => [p.product_url, p]));

  for (const pair of pairs) {
    const a = productMap.get(pair.product_a);
    const b = productMap.get(pair.product_b);
    if (!a || !b) continue;

    const aPurchases = a.purchases_24h ?? 0;
    const bPurchases = b.purchases_24h ?? 0;
    const shared = pair.shared_visitors;

    if (aPurchases > 0 && bPurchases === 0 && (b.views_24h ?? 0) >= 5) {
      const found = _findExecOpp(si, "upsell", pair.product_a, pair.product_b);
      const ep = found?.exec;
      const proof = found?.proof;
      const audienceNote = ep ? ` ${ep.audienceSize} buyers of ${sUrl(pair.product_a)} also viewed ${sUrl(pair.product_b)} — ready to target.` : "";
      const proofNote = _buildProofNote(proof, sUrl(pair.product_b));
      return {
        id: `strategic-upsell-${pair.product_b}`,
        title: `${sUrl(pair.product_a)} buyers also view ${sUrl(pair.product_b)} — but don't buy it`,
        context: `${shared} visitors viewed both products this week. ${sUrl(pair.product_a)} converts but ${sUrl(pair.product_b)} doesn't.${audienceNote}${proofNote}`,
        action: ep
          ? [
              `1. Send a post-purchase email to ${sUrl(pair.product_a)} buyers: "${ep.suggestedMessage}"`,
              `2. Timing: ${ep.timing}`,
              `3. Add "Frequently viewed together" on the product page as a permanent cross-sell.`,
            ].join("\n")
          : [
              `1. Add a "Frequently viewed together" section on ${sUrl(pair.product_a)}'s page linking to ${sUrl(pair.product_b)}.`,
              `2. Create a bundle or "complete the look" offer combining both products.`,
              `3. Send a post-purchase email to ${sUrl(pair.product_a)} buyers promoting ${sUrl(pair.product_b)}.`,
            ].join("\n"),
        impact: ep?.expectedImpact ?? `${shared} shared visitors/week. Converting even 10% on the upsell adds incremental revenue.`,
        impactValue: Math.round(shared * rev.baselineCvr * rev.aov * 0.1 * 7),
        priority: shared >= 10 ? "HIGH" : "MEDIUM",
        evidence: shared,
        isPattern: true,
        segment: "Upsell opportunity",
        targetSection: "strategic",
        executionPayload: ep,
      };
    }
    if (bPurchases > 0 && aPurchases === 0 && (a.views_24h ?? 0) >= 5) {
      const found = _findExecOpp(si, "upsell", pair.product_b, pair.product_a);
      const ep = found?.exec;
      const proof = found?.proof;
      const audienceNote = ep ? ` ${ep.audienceSize} buyers of ${sUrl(pair.product_b)} also viewed ${sUrl(pair.product_a)} — ready to target.` : "";
      const proofNote = _buildProofNote(proof, sUrl(pair.product_a));
      return {
        id: `strategic-upsell-${pair.product_a}`,
        title: `${sUrl(pair.product_b)} buyers also view ${sUrl(pair.product_a)} — but don't buy it`,
        context: `${shared} visitors viewed both products this week. ${sUrl(pair.product_b)} converts but ${sUrl(pair.product_a)} doesn't.${audienceNote}${proofNote}`,
        action: ep
          ? [
              `1. Send a post-purchase email to ${sUrl(pair.product_b)} buyers: "${ep.suggestedMessage}"`,
              `2. Timing: ${ep.timing}`,
              `3. Add a cross-sell section on the product page.`,
            ].join("\n")
          : [
              `1. Add a "Frequently viewed together" section on ${sUrl(pair.product_b)}'s page linking to ${sUrl(pair.product_a)}.`,
              `2. Create a bundle discount combining both products.`,
              `3. Review ${sUrl(pair.product_a)}'s page — if visitors are already interested, what's stopping them?`,
            ].join("\n"),
        impact: ep?.expectedImpact ?? `${shared} shared visitors/week. Converting even 10% on the upsell adds incremental revenue.`,
        impactValue: Math.round(shared * rev.baselineCvr * rev.aov * 0.1 * 7),
        priority: shared >= 10 ? "HIGH" : "MEDIUM",
        evidence: shared,
        isPattern: true,
        segment: "Upsell opportunity",
        targetSection: "strategic",
      };
    }
  }

  // Fallback: if the top pair both sell, it's a bundle opportunity
  const topPair = pairs[0];
  const a = productMap.get(topPair.product_a);
  const b = productMap.get(topPair.product_b);
  if (a && b && (a.purchases_24h ?? 0) > 0 && (b.purchases_24h ?? 0) > 0) {
    const found = _findExecOpp(si, "bundle", topPair.product_a, topPair.product_b);
    const ep = found?.exec;
    return {
      id: "strategic-bundle",
      title: `Bundle opportunity: ${sUrl(topPair.product_a)} + ${sUrl(topPair.product_b)}`,
      context: `${topPair.shared_visitors} visitors viewed both products this week, and both sell independently. A bundle could increase AOV.`,
      action: ep
        ? [
            `1. Create a bundle: "${ep.suggestedMessage}"`,
            `2. ${ep.timing}`,
            `3. Add "Frequently bought together" on both product pages.`,
          ].join("\n")
        : [
            `1. Create a discounted bundle combining these two products.`,
            `2. Add "Frequently bought together" on both product pages.`,
            `3. Feature the bundle in email campaigns and homepage.`,
          ].join("\n"),
      impact: ep?.expectedImpact ?? `Bundles typically increase AOV 15-25%. ${topPair.shared_visitors} shared visitors/week is a strong signal.`,
      impactValue: Math.round(topPair.shared_visitors * rev.aov * 0.15),
      priority: "HIGH",
      evidence: topPair.shared_visitors,
      isPattern: true,
      segment: "Bundle opportunity",
      targetSection: "strategic",
      executionPayload: ep,
    };
  }

  return null;
}

// ---------------------------------------------------------------------------
// MAIN ENGINE v4 — Product + Store Intelligence
// ---------------------------------------------------------------------------
export function computeActions(
  products: ProductInput[],
  _signals: SignalInput[],
  rev: RevenueContext,
  storeIntel?: StoreIntelligence,
): SparkAction[] {
  const actions: SparkAction[] = [];
  const seen = new Set<string>();

  // Load proof baselines
  const baselines = loadBaselines();
  const newBaselines: Record<string, Baseline> = {};

  // 1. Strategic store-level patterns (highest value)
  for (const p of [
    storeRevenueConcentration(products, rev, storeIntel),
    storeMobileGap(products, rev, storeIntel),
    storePaidGap(products, rev, storeIntel),
    storeCohortDrift(products, rev, storeIntel),
    storeCoViewUpsell(products, rev, storeIntel),
  ]) {
    if (p && !seen.has(p.id)) {
      seen.add(p.id);
      actions.push(p);
    }
  }

  // 2. Cross-product patterns
  for (const p of [
    patternStoreWideCTA(products, rev),
    patternCheckoutFriction(products, rev),
    patternReturningCluster(products, rev),
  ]) {
    if (p && !seen.has(p.id)) {
      seen.add(p.id);
      actions.push(p);
    }
  }

  // 2. Per-product rules with proof loop
  for (const p of products) {
    // Save current metrics as baseline for future proof comparison
    const views = p.views_24h ?? 0;
    const carts = p.cart_conversions_24h ?? 0;
    if (views > 0) {
      newBaselines[p.product_url] = {
        cart_rate: views > 0 ? carts / views : 0,
        abandon_rate: p.cart_abandonment_rate ?? 0,
        return_rate: (p.unique_visitors_7d ?? 0) > 0
          ? (p.return_visitor_count_7d ?? 0) / (p.unique_visitors_7d ?? 1)
          : 0,
        dwell: p.avg_dwell_24h ?? 0,
        ts: Date.now(),
      };
    }

    const rules = [
      ruleHighViewsLowCart(p, rev),
      ruleCartAbandonment(p, rev),
      ruleReturnVisitorStall(p, rev),
      ruleHighEngagementNoAction(p, rev),
      ruleLowDwell(p, rev),
      ruleMobileConversionGap(p, rev),
      rulePaidTrafficWasted(p, rev),
      ruleCartRateDeclining(p, rev),
      ruleLandingPageFailure(p, rev),
      ruleDevicePurchaseGap(p, rev),
    ];
    // Compute temporal + segment for this product (once, shared across rules)
    const trend = detectTrend(p);
    const segment = detectSegment(p);

    for (const r of rules) {
      if (r && !seen.has(r.id)) {
        seen.add(r.id);
        // Attach proof status
        const proof = computeProof(p, r.id, baselines);
        r.proofStatus = proof.proofStatus;
        r.proofDetail = proof.proofDetail;
        // Attach temporal + segment
        r.trend = trend;
        r.segment = segment;
        // Boost priority for falling traffic (urgent) or high-returning products
        if (trend === "falling" && r.priority === "HIGH") {
          r.priority = "CRITICAL";
          r.context += ` Traffic is declining — today is 30%+ below the 7-day average.`;
        }
        // Enrich context with device split if available
        const vm = p.views_mobile ?? 0;
        const vd = p.views_desktop ?? 0;
        if (vm > 0 && vd > 0 && vm + vd >= 15) {
          const mobilePct = Math.round(vm / (vm + vd) * 100);
          if (mobilePct >= 60) {
            r.context += ` ${mobilePct}% of traffic is mobile.`;
          }
        }
        // Enrich with cart rate trend
        if (p.cart_rate_trend === "declining" && !r.id.startsWith("fix-trend-")) {
          r.context += ` Cart rate is also declining vs 7-day average.`;
        }
        // Ground impact in real revenue when available
        const realWk = realWeeklyRevenue(p);
        const p24h = p.purchases_24h ?? 0;
        if (realWk !== null && realWk > 0) {
          // Override theoretical impact with real revenue baseline
          r.impact = `Based on real revenue: ~${fmt(realWk, rev.currency)}/week from this product.`;
          if (r.impactValue < realWk) r.impactValue = realWk;
        }
        if (p24h > 0) {
          r.context += ` ${p24h} purchase(s) today.`;
        }
        // Enrich with landing page context
        const landV = p.landing_views_24h ?? 0;
        if (landV > 0 && (p.views_24h ?? 0) > 0) {
          const landPct = Math.round(landV / (p.views_24h ?? 1) * 100);
          if (landPct >= 50) {
            r.context += ` ${landPct}% of visitors land directly on this page.`;
          }
        }
        actions.push(r);
      }
    }
  }

  // Persist baselines for next comparison (only if we have real data)
  if (Object.keys(newBaselines).length > 0) {
    // Merge with existing (keep baselines for products not in current view)
    const merged = { ...baselines, ...newBaselines };
    saveBaselines(merged);
  }

  // 4. Rank: strategic first → patterns → per-product → evidence → impact
  // Strategic signals (targetSection=strategic) outrank everything
  const pOrder = { CRITICAL: 0, HIGH: 1, MEDIUM: 2 };
  actions.sort((a, b) => {
    // Strategic store-level insights rank above product-level
    const aStrat = a.targetSection === "strategic" ? 0 : 1;
    const bStrat = b.targetSection === "strategic" ? 0 : 1;
    if (aStrat !== bStrat) return aStrat - bStrat;
    if (a.isPattern !== b.isPattern) return a.isPattern ? -1 : 1;
    const pa = pOrder[a.priority] + (a.proofStatus === "improving" ? 0.5 : a.proofStatus === "worsening" ? -0.5 : 0);
    const pb = pOrder[b.priority] + (b.proofStatus === "improving" ? 0.5 : b.proofStatus === "worsening" ? -0.5 : 0);
    if (pa !== pb) return pa - pb;
    if (a.evidence !== b.evidence) return b.evidence - a.evidence;
    return b.impactValue - a.impactValue;
  });

  return actions;
}
