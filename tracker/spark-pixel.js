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
//   currency:     "EUR"
// }
// ---------------------------------------------------------------------------

var API_URL      = "https://api.hedgesparkhq.com/track";
var SHOP_DOMAIN  = "hedgespark-dev.myshopify.com";
var PIXEL_SECRET = "2b6e9710a2726322ddce9ba51b3cb543";

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
      pixel_secret:        PIXEL_SECRET
    });

    fetch(API_URL, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    payload,
      mode:    "cors"
    }).catch(function () {});

  } catch (_) {}
});
