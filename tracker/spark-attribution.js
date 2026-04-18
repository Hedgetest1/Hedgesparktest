(function () {
  "use strict";

  // Minimal error reporter — posts to /public/tracker-error. Never throws.
  // Fires at most once per page-load since attribution runs once.
  function _hsReportErr(source, err) {
    try {
      if (!err || !window.__wsAttrShop || !window.__wsAttrEndpoint) return;
      var body = JSON.stringify({
        shop: window.__wsAttrShop,
        source: source,
        message: String((err && err.message) || err).slice(0, 1500),
        stack: String((err && err.stack) || "").slice(0, 3500),
        url: String(window.location && window.location.href || "").slice(0, 500),
        tracker_version: 13,
        user_agent: String(navigator.userAgent || "").slice(0, 300),
      });
      if (navigator.sendBeacon) {
        try { navigator.sendBeacon(window.__wsAttrEndpoint, new Blob([body], {type:"application/json"})); return; } catch(_){}
      }
      fetch(window.__wsAttrEndpoint, {method:"POST", keepalive:true, headers:{"Content-Type":"application/json"}, body:body}).catch(function(){});
    } catch (_) {}
  }

  try { _hsAttrMain(); } catch (err) {
    _hsReportErr("spark-attribution.boot", err);
  }

  function _hsAttrMain() {

  // ---------------------------------------------------------------------------
  // spark-attribution.js — Visitor-to-order attribution for Hedge Spark
  //
  // PURPOSE
  // -------
  // This script runs on the Shopify Order Status page (the /thank_you page)
  // after a successful checkout.  It reads the persistent visitor identifier
  // from localStorage — written by spark-tracker.js on every product page
  // visit — and fires a single attribution event to the Hedge Spark backend.
  //
  // Once received, the backend joins the visitor_id to the shopify_order_id,
  // enabling:
  //   - Empirical per-product conversion rate computation
  //   - Behavioral profiling of converting vs non-converting visitors
  //   - Real retargeting intelligence (which behavioral patterns predict purchase)
  //   - Feedback measurement for agent-executed actions
  //
  // INSTALL INSTRUCTIONS FOR MERCHANTS
  // -----------------------------------
  // In your Shopify Admin:
  //   Settings → Checkout → Order status page → Additional scripts
  //
  // Add ONE line:
  //   <script src="https://<your-server>/spark-attribution.js?shop={{ shop.permanent_domain }}"></script>
  //
  // Replace <your-server> with your Hedge Spark backend hostname.
  // Shopify automatically resolves {{ shop.permanent_domain }} to the shop's
  // permanent myshopify.com domain — no manual configuration needed.
  //
  // SAFETY PROPERTIES
  // ------------------
  // - Never modifies any page state (read-only localStorage access)
  // - Fires at most ONE event per page load, guarded by idempotency on the server
  // - Falls back completely silently if localStorage is unavailable (Safari ITP
  //   in strict mode, incognito, or browsers that block storage access)
  // - Falls back silently if order_id cannot be resolved from any source
  // - Falls back silently if the network request fails
  // - Does NOT call sendBeacon — the page is not being unloaded, so fetch is safe
  //   and its success can be logged for debugging
  //
  // DOMAIN COMPATIBILITY
  // ---------------------
  // localStorage is shared across all pages on the same origin.  Shopify serves
  // all storefront pages (product pages, cart, checkout, thank-you) on the same
  // origin ({store}.myshopify.com or the merchant's custom domain).  This means
  // the visitor_id written by spark-tracker.js on a product page is readable here.
  //
  // Edge case: if a merchant has checkout on a SEPARATE subdomain (rare; requires
  // Shopify Plus custom checkout.liquid configuration), localStorage may not be
  // shared.  In that case, no visitor_id is found and the script exits silently.
  //
  // IDEMPOTENCY
  // -----------
  // The backend stores at most one attribution row per shopify_order_id.
  // If the Order Status page is refreshed or revisited, the duplicate event
  // is silently ignored on the server side.
  // ---------------------------------------------------------------------------

  // ---------------------------------------------------------------------------
  // 1. Resolve shop_domain from the script src ?shop= parameter
  //    (injected by Shopify Liquid as {{ shop.permanent_domain }})
  // ---------------------------------------------------------------------------
  var SHOP_DOMAIN = "";
  var API_BASE    = "";

  try {
    // document.currentScript is available during synchronous script execution.
    // The merchant adds this script without `async` or `defer`, so it is reliable.
    var scriptEl = document.currentScript;
    if (!scriptEl) {
      // Fallback: scan all script tags for the one pointing at spark-attribution.js
      var scripts = document.querySelectorAll("script[src]");
      for (var i = 0; i < scripts.length; i++) {
        if (scripts[i].src && scripts[i].src.indexOf("spark-attribution.js") !== -1) {
          scriptEl = scripts[i];
          break;
        }
      }
    }

    if (scriptEl && scriptEl.src) {
      var srcUrl = new URL(scriptEl.src);
      SHOP_DOMAIN = srcUrl.searchParams.get("shop") || "";
      // Derive the API base from the script's origin so the attribution event
      // goes to the same server that served the script — no hardcoded URL.
      API_BASE = srcUrl.origin;
    }
  } catch (_) {}

  if (!SHOP_DOMAIN) {
    // shop param is required — without it we cannot scope the attribution row.
    // Log a clear warning so the merchant can debug their script installation.
    console.warn("[HedgeSpark] spark-attribution.js: no ?shop= parameter found in script URL. Attribution skipped.");
    return;
  }

  // Wire the error reporter now that we know shop + API origin.
  try {
    window.__wsAttrShop = SHOP_DOMAIN;
    window.__wsAttrEndpoint = (API_BASE || window.location.origin) + "/public/tracker-error";
  } catch (_) {}

  // ---------------------------------------------------------------------------
  // 2. Resolve visitor_id from localStorage
  //    Written by spark-tracker.js on every product page visit.
  //    Key name must stay in sync with spark-tracker.js ("hedgespark_visitor_id").
  // ---------------------------------------------------------------------------
  var visitorId = null;
  try {
    visitorId = localStorage.getItem("hedgespark_visitor_id") || null;
  } catch (_) {
    // localStorage blocked (Safari ITP strict mode, incognito on some browsers).
    // No visitor identity → attribution is impossible → exit silently.
  }

  if (!visitorId) {
    // No visitor_id means the visitor did not browse a product page on this shop
    // before checkout (direct link to cart, returning customer who cleared storage,
    // or storage blocked).  Do not invent data — exit silently.
    return;
  }

  // ---------------------------------------------------------------------------
  // 3. Resolve Shopify order_id
  //
  // Priority order:
  //   A. window.Shopify.checkout.order_id  — always available on the thank-you
  //      page for all Shopify plan levels (Basic through Plus).
  //   B. window.Shopify.checkout.order_token — fallback identifier, used only
  //      when order_id is absent (should not happen in practice).
  //   C. URL params ?order_id= — for merchants who manually inject the order_id
  //      via Liquid in the script tag src.
  //
  // We do NOT parse it from document.URL path segments because the thank-you
  // URL format (/orders/{token}/thank_you) contains the order TOKEN, not the
  // order ID.  They are different — the token is a random string, the ID is
  // the numeric Shopify order identifier that appears in the admin and webhook.
  // ---------------------------------------------------------------------------
  var orderId = null;
  try {
    var checkout = window.Shopify && window.Shopify.checkout;
    if (checkout) {
      orderId = checkout.order_id
             || checkout.order_token
             || null;
    }
    if (orderId !== null) {
      orderId = String(orderId);
    }
  } catch (_) {}

  // Fallback C: ?order_id= on the current page URL
  if (!orderId) {
    try {
      orderId = new URL(window.location.href).searchParams.get("order_id") || null;
    } catch (_) {}
  }

  if (!orderId) {
    console.warn("[HedgeSpark] spark-attribution.js: could not resolve order_id from Shopify.checkout or URL params. Attribution skipped.");
    return;
  }

  // ---------------------------------------------------------------------------
  // 4. Fire the attribution event — single POST, fire-and-forget
  //
  // The backend endpoint POST /track/purchase-confirmed:
  //   - Validates the payload
  //   - Upserts to visitor_purchase_sessions (idempotent on shopify_order_id)
  //   - Returns {"status": "ok"} or {"status": "duplicate"}
  //
  // We log the result at debug level for merchant debugging convenience
  // (visible in browser DevTools Network tab).  Not required for correctness.
  // ---------------------------------------------------------------------------
  var payload = {
    shop_domain:      SHOP_DOMAIN,
    visitor_id:       visitorId,
    shopify_order_id: orderId,
    timestamp:        Date.now(),
  };

  var body = JSON.stringify(payload);
  var endpoint = API_BASE + "/track/purchase-confirmed";

  try {
    fetch(endpoint, {
      method:      "POST",
      headers:     { "Content-Type": "application/json" },
      body:        body,
      credentials: "omit",    // CORS safe: server uses allow_origins wildcard
      keepalive:   true,       // survives page unload if user navigates away
    })
      .then(function (resp) {
        if (!resp.ok) {
          console.warn("[HedgeSpark] Attribution endpoint returned HTTP " + resp.status);
        }
      })
      .catch(function (err) {
        // Network failure — attribution is best-effort (order data is already
        // in shop_orders via Shopify webhook), but the failure itself is
        // observability-relevant if it spikes.
        _hsReportErr("spark-attribution.fetch", err);
      });
  } catch (err) {
    _hsReportErr("spark-attribution.send", err);
  }

  }  // end _hsAttrMain
})();
