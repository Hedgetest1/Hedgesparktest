(function () {
  "use strict";

  // ---------------------------------------------------------------------------
  // Configuration — read from the <script> tag attributes
  //
  // Usage on Shopify storefront:
  //   <script src="https://app.hedgespark.io/tracker.js"
  //           data-shop="example.myshopify.com"
  //           async></script>
  // ---------------------------------------------------------------------------
  var scriptEl = document.currentScript;
  var SHOP_DOMAIN = (scriptEl && scriptEl.getAttribute("data-shop")) || "";
  var API_ORIGIN =
    scriptEl && scriptEl.src
      ? new URL(scriptEl.src).origin
      : window.location.origin;
  var API_URL = API_ORIGIN + "/track";

  if (!SHOP_DOMAIN) {
    return; // cannot track without a shop identity
  }

  // ---------------------------------------------------------------------------
  // Visitor identity — persisted in localStorage across sessions
  // ---------------------------------------------------------------------------
  var visitorId =
    localStorage.getItem("hedgespark_visitor_id") ||
    (typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2));
  localStorage.setItem("hedgespark_visitor_id", visitorId);

  // ---------------------------------------------------------------------------
  // Page classification helpers
  // ---------------------------------------------------------------------------
  function currentPageUrl() {
    return window.location.href;
  }

  // On Shopify, product pages always have /products/ in the path.
  function detectProductUrl() {
    return /\/products\//.test(window.location.pathname)
      ? window.location.href
      : null;
  }

  // ---------------------------------------------------------------------------
  // Event sending
  //
  // fetch()       — used for all events except unload (async, no body limit)
  // sendBeacon()  — used on page unload; browser keeps the request alive even
  //                 after the page tears down
  // ---------------------------------------------------------------------------
  function buildPayload(eventType, extra) {
    var payload = {
      shop_domain: SHOP_DOMAIN,
      visitor_id: visitorId,
      event_type: eventType,
      page_url: currentPageUrl(),
      product_url: detectProductUrl(),
      timestamp: Date.now(),
    };
    if (extra) {
      for (var k in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, k)) {
          payload[k] = extra[k];
        }
      }
    }
    return payload;
  }

  function sendFetch(eventType, extra) {
    try {
      fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildPayload(eventType, extra)),
        keepalive: true,
      }).catch(function () {});
    } catch (_) {}
  }

  function sendBeacon(eventType, extra) {
    try {
      var data = JSON.stringify(buildPayload(eventType, extra));
      if (navigator.sendBeacon) {
        navigator.sendBeacon(
          API_URL,
          new Blob([data], { type: "application/json" })
        );
      } else {
        // Fallback for browsers without sendBeacon
        sendFetch(eventType, extra);
      }
    } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // 1. page_view — fired immediately on script load
  // ---------------------------------------------------------------------------
  sendFetch("page_view");

  // ---------------------------------------------------------------------------
  // 2. product_view — fired when the page is a Shopify product page
  // ---------------------------------------------------------------------------
  if (detectProductUrl()) {
    sendFetch("product_view");
  }

  // ---------------------------------------------------------------------------
  // 3. scroll_depth — tracks the maximum scroll percentage reached
  //    Reported as part of the dwell_time event on page leave.
  // ---------------------------------------------------------------------------
  var maxScrollDepth = 0;

  function updateScrollDepth() {
    var scrolled = window.scrollY + window.innerHeight;
    var total = document.body.scrollHeight;
    if (total > 0) {
      var pct = Math.round((scrolled / total) * 100);
      if (pct > maxScrollDepth) {
        maxScrollDepth = Math.min(pct, 100);
      }
    }
  }

  window.addEventListener("scroll", updateScrollDepth, { passive: true });

  // ---------------------------------------------------------------------------
  // 4. dwell_time — reported on page leave along with final scroll depth
  //    Uses sendBeacon so the request survives the page teardown.
  // ---------------------------------------------------------------------------
  var pageStartTime = Date.now();

  function onPageLeave() {
    var dwellSeconds = Math.round((Date.now() - pageStartTime) / 1000);
    sendBeacon("dwell_time", {
      dwell_seconds: dwellSeconds,
      scroll_depth: maxScrollDepth,
    });
  }

  // visibilitychange catches tab-switches and browser minimise in addition to
  // navigation; beforeunload catches close/reload on desktop.
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") {
      onPageLeave();
    }
  });

  window.addEventListener("beforeunload", onPageLeave);
})();
