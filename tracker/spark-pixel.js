// ---------------------------------------------------------------------------
// Hedge Spark — Shopify Custom Pixel (purchase tracking)
//
// WHY THIS EXISTS
// ---------------
// Script tags (spark-tracker.js) do NOT run on checkout or thank-you pages.
// Shopify webhook topics (orders/paid, orders/create, orders/updated) ALL
// require Protected Customer Data approval, blocking MVP validation.
//
// The Web Pixels API is the ONLY way to capture checkout_completed on Shopify
// without prior approval.  Custom Pixels run in a sandboxed iframe that
// Shopify injects on every page, including checkout.
//
// INSTALLATION
// ------------
// Shopify Admin → Settings → Customer events → Add custom pixel
// Paste this entire file.  Click Save.  Click Connect.
//
// IMPORTANT: Do NOT wrap in (function(){...})() — Shopify Custom Pixels
// inject `analytics` and `browser` into the top-level scope.  An IIFE
// can shadow or lose access to these globals.
//
// PAYLOAD CONTRACT
// ----------------
// POST https://api.hedgesparkhq.com/track
// {
//   shop_domain:  "hedgespark-dev.myshopify.com",
//   visitor_id:   "<shopify_client_id>",
//   event_type:   "purchase",
//   page_url:     "<checkout_url>",
//   timestamp:    <epoch_ms>,
//   order_id:     "<shopify_order_id>",
//   order_total:  <float>,
//   currency:     "EUR",
//   // Class D base-analytics enrichment (v14, 2026-04-26).
//   // All optional — backend falls back to NULL columns.
//   discount_amount:    <float|null>,         // sum of discounts applied
//   discount_codes:     ["SUMMER10", ...],    // codes used (best-effort)
//   tax_amount:         <float|null>,         // total tax
//   payment_method:     "shopify_payments",   // gateway name when known
//   financial_status:   "paid",               // pixel-time default
//   fulfillment_status: "unfulfilled"         // pixel-time default
// }
// ---------------------------------------------------------------------------

var API_URL      = "https://api.hedgesparkhq.com/track";
var SHOP_DOMAIN  = "hedgespark-dev.myshopify.com";
var PIXEL_SECRET = "2b6e9710a2726322ddce9ba51b3cb543";

// Minimal error reporter — Shopify Custom Pixel sandbox has `fetch` but
// limited browser APIs. Posts to /public/tracker-error keepalive; silent
// on any failure of its own so it can never affect the pixel's main work.
function _hsReportErr(source, err) {
  try {
    if (!SHOP_DOMAIN || !err) return;
    var endpoint = "https://api.hedgesparkhq.com/public/tracker-error";
    var body = JSON.stringify({
      shop: SHOP_DOMAIN,
      source: source,
      message: String((err && err.message) || err).slice(0, 1500),
      stack: String((err && err.stack) || "").slice(0, 3500),
      url: "",  // pixel sandbox doesn't reliably expose window.location
      tracker_version: 15,
      user_agent: "",
    });
    if (typeof fetch !== "undefined") {
      fetch(endpoint, {
        method: "POST", keepalive: true,
        headers: {"Content-Type": "application/json"}, body: body,
      }).catch(function () {});
    }
  } catch (_) {}
}

analytics.subscribe("checkout_completed", function (event) {
  try {
    var checkout = event.data.checkout;
    if (!checkout) return;

    // -- Extract order ID ---------------------------------------------------
    // Shopify exposes the order ID in multiple locations depending on
    // store type, checkout version, and dev vs production:
    //   1. checkout.order.id          — standard path
    //   2. checkout.orderId           — some SDK versions
    //   3. checkout.token             — fallback (checkout token, unique per order)
    var orderId = "";
    if (checkout.order && checkout.order.id) {
      orderId = String(checkout.order.id);
    } else if (checkout.orderId) {
      orderId = String(checkout.orderId);
    } else if (checkout.token) {
      orderId = "tok_" + String(checkout.token);
    }
    if (!orderId) return;

    // Strip GID prefix if present: "gid://shopify/OrderIdentity/12345" → "12345"
    var gidMatch = orderId.match(/\/(\d+)$/);
    if (gidMatch) orderId = gidMatch[1];

    // -- Extract total price ------------------------------------------------
    // Shopify exposes totalPrice as either:
    //   { amount: "123.45", currencyCode: "USD" }  — MoneyV2 object
    //   or checkout.totalPrice as a plain string    — older SDK versions
    var orderTotal = 0;
    var currency   = "EUR";

    if (checkout.totalPrice && typeof checkout.totalPrice === "object") {
      orderTotal = parseFloat(checkout.totalPrice.amount);
      currency   = (checkout.totalPrice.currencyCode || "EUR").toUpperCase();
    } else if (checkout.totalPrice) {
      orderTotal = parseFloat(checkout.totalPrice);
    }

    // Also try checkout.subtotalPrice as fallback
    if ((!orderTotal || isNaN(orderTotal)) && checkout.subtotalPrice) {
      if (typeof checkout.subtotalPrice === "object") {
        orderTotal = parseFloat(checkout.subtotalPrice.amount);
        currency   = (checkout.subtotalPrice.currencyCode || currency).toUpperCase();
      } else {
        orderTotal = parseFloat(checkout.subtotalPrice);
      }
    }

    // Try checkout.currencyCode as top-level fallback
    if (checkout.currencyCode) {
      currency = checkout.currencyCode.toUpperCase();
    }

    if (isNaN(orderTotal) || orderTotal <= 0) return;

    // -- Class D enrichment (best-effort, all fields optional) ---------------
    // Shopify exposes total discounts + tax + transactions on checkout.
    // Each block is wrapped so a missing field never breaks the pixel
    // (the order still posts with these as null). Common shapes covered:
    //   { totalTax: { amount, currencyCode } }   MoneyV2
    //   { totalTax: "1.23" }                     plain string
    //   { discountApplications: [{ value }] }    discount payload variant
    //   { discountCodes: [{ code }] }            checkout v2 codes
    //   { transactions: [{ gateway }] }          payment provider hint

    function _moneyV2OrString(v) {
      if (!v) return null;
      if (typeof v === "object" && v.amount != null) {
        var n = parseFloat(v.amount);
        return isNaN(n) ? null : n;
      }
      var n = parseFloat(v);
      return isNaN(n) ? null : n;
    }

    var discountAmount = null;
    try {
      discountAmount = _moneyV2OrString(checkout.totalDiscounts) || _moneyV2OrString(checkout.totalDiscount);
    } catch (_) {}

    var discountCodes = null;
    try {
      var rawCodes = checkout.discountCodes || checkout.discount_codes || [];
      if (Array.isArray(rawCodes) && rawCodes.length > 0) {
        discountCodes = rawCodes.map(function (c) {
          if (typeof c === "string") return c;
          return c && (c.code || c.title || c.name) || null;
        }).filter(function (c) { return c && typeof c === "string"; }).slice(0, 10);
        if (discountCodes.length === 0) discountCodes = null;
      }
    } catch (_) {}

    var taxAmount = null;
    try {
      taxAmount = _moneyV2OrString(checkout.totalTax) || _moneyV2OrString(checkout.taxAmount);
    } catch (_) {}

    // Line items with variant info (Class D follow-up — variant
    // performance was the last R-blocker on the audit). Shopify
    // checkout.lineItems exposes variant.id / variant.title /
    // variant.product.title / quantity / finalLinePrice. Each item
    // wrapped — partial data is preferable to no data.
    var lineItems = null;
    try {
      var rawItems = checkout.lineItems || [];
      if (Array.isArray(rawItems) && rawItems.length > 0) {
        lineItems = rawItems.map(function (it) {
          if (!it) return null;
          var variant = it.variant || {};
          var product = variant.product || {};
          var price = _moneyV2OrString(it.finalLinePrice) ||
                      _moneyV2OrString(variant.price) ||
                      _moneyV2OrString(it.price);
          return {
            variant_id:    variant.id ? String(variant.id).slice(0, 64) : null,
            variant_title: variant.title ? String(variant.title).slice(0, 200) : null,
            product_title: product.title ? String(product.title).slice(0, 200) :
                           (it.title ? String(it.title).slice(0, 200) : null),
            sku:           variant.sku ? String(variant.sku).slice(0, 80) : null,
            quantity:      typeof it.quantity === "number" ? it.quantity : null,
            price:         price,
          };
        }).filter(function (it) { return it !== null; }).slice(0, 50);
        if (lineItems.length === 0) lineItems = null;
      }
    } catch (_) {}

    var paymentMethod = null;
    try {
      // Try direct paymentMethod field, then transactions[0].gateway, then
      // checkout.transactions which some SDK versions expose.
      if (checkout.paymentMethod && typeof checkout.paymentMethod === "string") {
        paymentMethod = checkout.paymentMethod;
      } else if (checkout.payment && checkout.payment.gateway) {
        paymentMethod = String(checkout.payment.gateway);
      } else {
        var txns = checkout.transactions || (event.data && event.data.transactions) || [];
        if (Array.isArray(txns) && txns.length > 0) {
          var first = txns[0];
          if (first && first.gateway) paymentMethod = String(first.gateway);
        }
      }
      if (paymentMethod) paymentMethod = paymentMethod.slice(0, 64);
    } catch (_) {}

    // -- Dedup (pixel-scoped localStorage) ----------------------------------
    var dedupKey = "hs_purchase_" + orderId;
    try {
      if (browser && browser.localStorage) {
        var already = browser.localStorage.getItem(dedupKey);
        if (already) return;
        browser.localStorage.setItem(dedupKey, "1");
      }
    } catch (_) {}

    // -- Visitor identity ---------------------------------------------------
    // pixel visitor_id: Shopify's clientId (unique to this browser session)
    var visitorId = "";
    if (event.clientId) {
      visitorId = String(event.clientId);
    } else {
      visitorId = "pixel_" + orderId;
    }

    // tracker_visitor_id: read the storefront tracker's identity from the
    // first-party _hs_vid cookie.  spark-tracker.js writes this cookie on
    // every storefront page load.  The pixel sandbox can read first-party
    // cookies via browser.cookie.get().
    //
    // This is the identity bridge: it lets the backend link the purchase
    // (pixel identity) back to the browsing session (tracker identity).
    var trackerVisitorId = "";
    try {
      if (typeof browser !== "undefined" && browser.cookie) {
        var cookieVal = browser.cookie.get("_hs_vid");
        if (cookieVal) trackerVisitorId = decodeURIComponent(String(cookieVal));
      }
    } catch (_) {}

    // -- Page URL -----------------------------------------------------------
    var pageUrl = "";
    try {
      if (event.context && event.context.document && event.context.document.location) {
        pageUrl = event.context.document.location.href || "";
      }
    } catch (_) {}

    // -- Send to backend ----------------------------------------------------
    var payload = JSON.stringify({
      shop_domain:         SHOP_DOMAIN,
      visitor_id:          visitorId,
      event_type:          "purchase",
      page_url:            pageUrl,
      timestamp:           Date.now(),
      order_id:            orderId,
      order_total:         orderTotal,
      currency:            currency,
      tracker_visitor_id:  trackerVisitorId || undefined,
      pixel_secret:        PIXEL_SECRET,
      // Class D enrichment (v14, 2026-04-26). Each is OPTIONAL — sent as
      // null when Shopify checkout context didn't expose it.
      discount_amount:     discountAmount,
      discount_codes:      discountCodes,
      tax_amount:          taxAmount,
      payment_method:      paymentMethod,
      financial_status:    "paid",          // pixel-time default
      fulfillment_status:  "unfulfilled",   // pixel-time default
      // v15 (2026-04-26 Note-3 closure): line items with variant info.
      // Closes the "Variants performance" audit gap that previously
      // sat behind R-blocker:tier_2-approval. Capped at 50 items per
      // order (Shopify default cart cap is 100; 50 covers 99%+).
      line_items:          lineItems
    });

    fetch(API_URL, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    payload,
      mode:    "cors"
    }).catch(function () {});

  } catch (err) {
    // Surface pixel-side failures instead of swallowing them. Common
    // causes: Shopify sandbox API changes, unexpected checkout shape,
    // network transport issues.
    _hsReportErr("spark-pixel.checkout_completed", err);
  }
});
