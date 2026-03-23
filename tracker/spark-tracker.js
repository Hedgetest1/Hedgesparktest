(function () {
  "use strict";

  // ---------------------------------------------------------------------------
  // Top-level error boundary
  //
  // The entire tracker is wrapped in a try/catch so ANY unhandled exception
  // — including ones in dependencies we don't control (Shopify API changes,
  // browser quirks, theme conflicts) — fails silently without affecting the
  // storefront.  An error here is logged to the console for debugging but
  // never rethrows.
  //
  // The boot guard (window.__wishsparkInit) lives OUTSIDE this boundary so
  // double-init is prevented even if the first load threw.
  // ---------------------------------------------------------------------------
  if (window.__wishsparkInit) return;
  window.__wishsparkInit = true;

  try { _wishsparkBoot(); } catch (bootErr) {
    try { console.warn("[WishSpark] tracker boot error (non-fatal):", bootErr); } catch (_) {}
  }

  function _wishsparkBoot() {

  // ---------------------------------------------------------------------------
  // Configuration
  //
  // shop_domain resolution order:
  //   1. ?shop= query param on the script src URL
  //      NOTE: document.currentScript is null when the script tag uses `async`,
  //      so we also scan all <script> tags for the one pointing at tracker.js.
  //   2. ?shop= query param on the current page URL (window.location.href)
  //   3. Neither found → warn and abort (no events sent)
  //
  // API endpoint is derived from the script src origin when available,
  // otherwise falls back to the current page origin.
  // ---------------------------------------------------------------------------

  // Resolve the <script> element even under async loading.
  var scriptEl = document.currentScript;
  if (!scriptEl) {
    try {
      var scripts = document.querySelectorAll("script[src]");
      for (var i = 0; i < scripts.length; i++) {
        if (scripts[i].src && scripts[i].src.indexOf("tracker.js") !== -1) {
          scriptEl = scripts[i];
          break;
        }
      }
    } catch (_) {}
  }

  var SHOP_DOMAIN = "";
  var API_URL = window.location.origin + "/track";

  // 1. Try script src ?shop=
  try {
    if (scriptEl && scriptEl.src) {
      var srcUrl = new URL(scriptEl.src);
      SHOP_DOMAIN = srcUrl.searchParams.get("shop") || "";
      API_URL = srcUrl.origin + "/track";
    }
  } catch (_) {}

  // 2. Fallback to page URL ?shop=
  if (!SHOP_DOMAIN) {
    try {
      SHOP_DOMAIN = new URL(window.location.href).searchParams.get("shop") || "";
    } catch (_) {}
  }

  // 3. Abort if still unresolved
  if (!SHOP_DOMAIN) {
    console.warn("[WishSpark] tracker loaded but no shop param found");
    return;
  }

  // ---------------------------------------------------------------------------
  // Visitor identity — persisted in localStorage across sessions
  // ---------------------------------------------------------------------------
  var visitorId;
  try {
    visitorId = localStorage.getItem("hedgespark_visitor_id");
    if (!visitorId) {
      visitorId =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : Math.random().toString(36).slice(2) + Date.now().toString(36);
      localStorage.setItem("hedgespark_visitor_id", visitorId);
    }
  } catch (_) {
    visitorId = Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

  // ---------------------------------------------------------------------------
  // Source attribution — multi-signal, priority-ordered
  //
  // Priority:
  //   1. UTM parameters on the current page URL  (utm_source)
  //   2. Referrer domain classification
  //   3. Direct (no UTM, no referrer)
  //
  // All values are lowercase. detectSourceType() is the single public entry
  // point; helpers below are internal.
  // ---------------------------------------------------------------------------

  function _rootDomain(hostname) {
    try {
      var parts = hostname.toLowerCase().replace(/^www\./, "").split(".");
      return parts.length >= 2 ? parts.slice(-2).join(".") : hostname.toLowerCase();
    } catch (_) {
      return hostname.toLowerCase();
    }
  }

  function _utmSource() {
    try {
      var params = new URL(window.location.href).searchParams;
      var src = params.get("utm_source");
      if (!src) return null;
      src = src.toLowerCase().trim();
      if (src === "newsletter" || src === "e-mail") src = "email";
      return src || null;
    } catch (_) {
      return null;
    }
  }

  var _REFERRER_MAP = [
    // Search engines
    { domain: "google.com",    source: "google"    },
    { domain: "bing.com",      source: "bing"      },
    { domain: "yahoo.com",     source: "yahoo"     },
    { domain: "duckduckgo.com",source: "duckduckgo"},
    { domain: "baidu.com",     source: "baidu"     },
    // Social networks
    { domain: "facebook.com",  source: "facebook"  },
    { domain: "instagram.com", source: "instagram" },
    { domain: "tiktok.com",    source: "tiktok"    },
    { domain: "twitter.com",   source: "twitter"   },
    { domain: "x.com",         source: "twitter"   },
    { domain: "pinterest.com", source: "pinterest" },
    { domain: "linkedin.com",  source: "linkedin"  },
    { domain: "youtube.com",   source: "youtube"   },
    { domain: "reddit.com",    source: "reddit"    },
    { domain: "snapchat.com",  source: "snapchat"  },
    // Marketplaces
    { domain: "amazon.com",    source: "amazon"    },
    { domain: "amazon.co.uk",  source: "amazon"    },
    { domain: "ebay.com",      source: "ebay"      },
    { domain: "ebay.co.uk",    source: "ebay"      },
    { domain: "etsy.com",      source: "etsy"      },
  ];

  function _classifyReferrer(ref) {
    if (!ref) return null;
    try {
      var refHost  = new URL(ref).hostname.toLowerCase();
      var refRoot  = _rootDomain(refHost);
      var selfRoot = _rootDomain(window.location.hostname);

      if (refRoot === selfRoot) return null;

      for (var j = 0; j < _REFERRER_MAP.length; j++) {
        if (refRoot === _REFERRER_MAP[j].domain ||
            refHost.slice(-(1 + _REFERRER_MAP[j].domain.length)) === ("." + _REFERRER_MAP[j].domain)) {
          return _REFERRER_MAP[j].source;
        }
      }
      return "referral";
    } catch (_) {
      return "referral";
    }
  }

  function detectSourceType() {
    try {
      var utm = _utmSource();
      if (utm) return utm;

      var fromRef = _classifyReferrer(document.referrer || "");
      if (fromRef) return fromRef;

      return "direct";
    } catch (_) {
      return "direct";
    }
  }

  // ---------------------------------------------------------------------------
  // Page helpers
  // ---------------------------------------------------------------------------
  function currentPageUrl() {
    return window.location.href;
  }

  // Resolve Shopify's numeric product ID using a multi-source fallback chain.
  //
  // Shopify does not guarantee a single stable API for product ID access, and
  // the primary source (ShopifyAnalytics.meta) is an undocumented internal
  // object that has changed in the past.  The fallback chain below is ordered
  // from most to least reliable:
  //
  //   1. ShopifyAnalytics.meta.product.id     — primary (undocumented, stable since 2016)
  //   2. window.__st.product                  — secondary Shopify analytics object
  //   3. data-product-id attribute on body/section  — theme-level opt-in
  //   4. meta[property="shopify:product_id"]  — some themes inject this
  //   5. Liquid JSON in page source            — __productJSON / ShopifyAnalytics.productGids
  //
  // Any failure in the chain falls through to the next option silently.
  // Returns null if no source provides an ID — the event is still sent without it.
  //
  // Stored as a string to survive JSON serialisation without precision loss
  // (Shopify product IDs can exceed 2^53 on high-volume stores).
  function detectProductId() {
    // 1. ShopifyAnalytics.meta.product.id (primary)
    try {
      var id1 =
        window.ShopifyAnalytics &&
        window.ShopifyAnalytics.meta &&
        window.ShopifyAnalytics.meta.product &&
        window.ShopifyAnalytics.meta.product.id;
      if (id1) return String(id1);
    } catch (_) {}

    // 2. window.__st.product (secondary Shopify analytics stub)
    try {
      var id2 =
        window.__st &&
        window.__st.product;
      if (id2) return String(id2);
    } catch (_) {}

    // 3. [data-product-id] attribute on common theme elements
    try {
      var el = (
        document.querySelector("[data-product-id]") ||
        document.querySelector("[data-productid]") ||
        document.querySelector(".product-single[data-product-id]") ||
        document.querySelector("product-form[data-product-id]")
      );
      if (el) {
        var attr = el.getAttribute("data-product-id") || el.getAttribute("data-productid");
        if (attr) return String(attr);
      }
    } catch (_) {}

    // 4. <meta property="shopify:product_id"> or <meta name="product_id">
    try {
      var metaEl = (
        document.querySelector("meta[property='shopify:product_id']") ||
        document.querySelector("meta[name='product_id']")
      );
      if (metaEl) {
        var content = metaEl.getAttribute("content");
        if (content) return String(content);
      }
    } catch (_) {}

    // 5. ShopifyAnalytics.productGids (newer PWA storefronts, base64 encoded)
    try {
      var gids = window.ShopifyAnalytics && window.ShopifyAnalytics.productGids;
      if (gids && Array.isArray(gids) && gids.length > 0) {
        // gids are "gid://shopify/Product/12345678" — extract the numeric tail
        var gid = String(gids[0]);
        var numeric = gid.replace(/.*\//, "");
        if (/^\d+$/.test(numeric)) return numeric;
      }
    } catch (_) {}

    return null;
  }

  function detectProductUrl() {
    // Canonical format: /products/{handle} — path-only, no query or fragment.
    // This ensures the value stored server-side is consistent across variants,
    // UTM params, and other query string noise.
    var pathname = window.location.pathname;

    // Primary: standard Shopify product page path
    if (/\/products\/[^/]/.test(pathname)) {
      // Trim to /products/{handle} — drop sub-paths and trailing slashes
      var m = pathname.match(/\/products\/([^/?#]+)/);
      return m ? "/products/" + m[1] : null;
    }

    // Secondary: any page with ?product=<slug> query param
    try {
      var productParam = new URL(window.location.href).searchParams.get("product");
      if (productParam) {
        return "/products/" + productParam;
      }
    } catch (_) {}

    return null;
  }

  // ---------------------------------------------------------------------------
  // Payload builder
  // ---------------------------------------------------------------------------
  function buildPayload(eventType, extra) {
    var productUrl = detectProductUrl();
    // Only capture product_id on product pages — null everywhere else so we
    // don't send a spurious field on page_view / dwell_time events from
    // non-product pages where ShopifyAnalytics.meta.product may be stale.
    var productId  = productUrl ? detectProductId() : null;
    var payload = {
      shop_domain: SHOP_DOMAIN,
      visitor_id:  visitorId,
      event_type:  eventType,
      page_url:    currentPageUrl(),
      product_url: productUrl || undefined,
      product_id:  productId  || undefined,
      timestamp:   Date.now(),
      source_type: detectSourceType(),
      referrer:    document.referrer || "",
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

  // ---------------------------------------------------------------------------
  // Transport layer
  //
  // fetchFallback        — ALL normal events (page_view, product_view, …).
  //                        credentials: "omit" avoids the credentialed-request
  //                        CORS error when the server uses allow_origins: *.
  //
  // sendBeaconOrFallback — ONLY used from onPageLeave() so it cannot fire
  //                        during normal browsing and produce "ping" requests.
  //
  // sendEvent()          — always routes to fetchFallback(). The call site
  //                        (onPageLeave), not the event name, decides transport.
  // ---------------------------------------------------------------------------
  // ---------------------------------------------------------------------------
  // Offline event buffer
  //
  // Events that fail to send (network error, offline) are queued in
  // sessionStorage under the key "hs_event_queue" and retried on the next
  // successful network request.  The queue is bounded to MAX_QUEUE_SIZE
  // events to prevent sessionStorage from growing without bound on persistent
  // offline sessions.
  //
  // Retry fires at most once per page load via _flushQueue() (called before
  // each successful send).  This is a best-effort retry — events older than
  // the current browser session are lost on tab close.  For the behavioral
  // analytics use case (scroll, dwell, product_view) this is acceptable:
  // stale events from a closed session would confuse recency-sensitive queries.
  // ---------------------------------------------------------------------------
  var _QUEUE_KEY    = "hs_event_queue";
  var _MAX_QUEUE    = 20;
  var _queueFlushed = false;

  function _readQueue() {
    try {
      var raw = sessionStorage.getItem(_QUEUE_KEY);
      if (!raw) return [];
      var q = JSON.parse(raw);
      return Array.isArray(q) ? q : [];
    } catch (_) { return []; }
  }

  function _writeQueue(q) {
    try {
      sessionStorage.setItem(_QUEUE_KEY, JSON.stringify(q.slice(-_MAX_QUEUE)));
    } catch (_) {}
  }

  function _enqueue(body) {
    try {
      var q = _readQueue();
      q.push(body);
      _writeQueue(q);
    } catch (_) {}
  }

  function _flushQueue() {
    if (_queueFlushed) return;
    _queueFlushed = true;
    try {
      var q = _readQueue();
      if (!q.length) return;
      _writeQueue([]);                   // clear before sending (idempotent ok)
      for (var i = 0; i < q.length; i++) {
        (function (body) {
          try {
            fetch(API_URL, {
              method:      "POST",
              headers:     { "Content-Type": "application/json" },
              body:        body,
              keepalive:   true,
              credentials: "omit",
            }).catch(function () { _enqueue(body); });
          } catch (_) { _enqueue(body); }
        })(q[i]);
      }
    } catch (_) {}
  }

  function fetchFallback(body) {
    _flushQueue();   // retry any previously-queued events on a successful connection
    try {
      fetch(API_URL, {
        method:      "POST",
        headers:     { "Content-Type": "application/json" },
        body:        body,
        keepalive:   true,
        credentials: "omit",
      }).catch(function () {
        _enqueue(body);   // buffer on network failure
      });
    } catch (_) {
      _enqueue(body);     // buffer if fetch itself throws (e.g. offline)
    }
  }

  function sendBeaconOrFallback(body) {
    try {
      if (navigator.sendBeacon) {
        var sent = navigator.sendBeacon(
          API_URL,
          new Blob([body], { type: "application/json" })
        );
        if (!sent) {
          fetchFallback(body);
        }
      } else {
        fetchFallback(body);
      }
    } catch (_) {
      fetchFallback(body);
    }
  }

  // All normal event sends go through fetch. sendBeacon is unreachable from here.
  function sendEvent(eventType, extra) {
    try {
      var payload = buildPayload(eventType, extra);
      var body = JSON.stringify(payload);
      fetchFallback(body);
    } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // 1. page_view — fired immediately on script load (fetch)
  // ---------------------------------------------------------------------------
  sendEvent("page_view");

  // ---------------------------------------------------------------------------
  // 2. product_view — fired on Shopify product pages (fetch)
  // ---------------------------------------------------------------------------
  if (detectProductUrl()) {
    sendEvent("product_view");
  }

  // ---------------------------------------------------------------------------
  // 3. Scroll depth tracking
  //
  // maxScrollDepth accumulates the highest scroll percentage reached this
  // session (0–100). It is reported in two ways:
  //
  //   a. As scroll_depth on the dwell_time event at page leave (existing).
  //   b. As a standalone event_type="scroll" event at page leave — this is
  //      what /analytics/source-quality reads for avg_scroll per source.
  //
  // The scroll listener is THROTTLED at 250 ms to avoid saturating the main
  // thread on large pages or fast scroll events. No network request is made
  // during scrolling — only the in-memory max is updated.
  //
  // Short-page guard: if the page is shorter than the viewport, the browser
  // never fires scroll events. The initial sample at load time handles this
  // by calling updateScrollDepth() once synchronously so pages that are
  // 100% visible from the start still produce a meaningful scroll value.
  // ---------------------------------------------------------------------------
  var maxScrollDepth    = 0;
  var scrollThrottleId  = null;

  function updateScrollDepth() {
    try {
      var scrolled = window.scrollY + window.innerHeight;
      var total    = document.documentElement.scrollHeight || document.body.scrollHeight;
      if (total > 0) {
        var pct = Math.round((scrolled / total) * 100);
        if (pct > maxScrollDepth) {
          maxScrollDepth = Math.min(pct, 100);
        }
      }
    } catch (_) {}
  }

  // Initial sample — handles short pages that never trigger a scroll event.
  updateScrollDepth();

  function onScrollEvent() {
    if (scrollThrottleId !== null) return;
    scrollThrottleId = setTimeout(function () {
      scrollThrottleId = null;
      updateScrollDepth();
    }, 250);
  }

  try {
    window.addEventListener("scroll", onScrollEvent, { passive: true });
  } catch (_) {
    window.addEventListener("scroll", onScrollEvent);
  }

  // ---------------------------------------------------------------------------
  // 4. Page leave — dwell_time + scroll sent ONCE on exit via sendBeacon
  //
  // onPageLeave() is the ONLY entry point for sendBeacon in this file.
  // It fires from:
  //   - visibilitychange → hidden  (tab hidden / app backgrounded)
  //   - beforeunload               (navigation away / tab close)
  //
  // Two events are sent:
  //   dwell_time  { dwell_seconds, scroll_depth }   — session attention summary
  //   scroll      { scroll_percent }                — dedicated scroll record
  //                                                   read by analytics queries
  //
  // dwellSent guards against double-fire when both browser events fire during
  // the same navigation. Reset on visibility:visible so each foreground session
  // produces its own pair of events.
  //
  // pageStartTime resets on visibility:visible so dwell reflects foreground
  // attention time rather than wall-clock time since load.
  // ---------------------------------------------------------------------------
  var pageStartTime    = Date.now();
  var dwellAccumulated = 0;
  var dwellSent        = false;

  function onPageLeave() {
    if (dwellSent) return;
    dwellSent = true;

    // Flush any pending throttle so the very last scroll position is captured.
    if (scrollThrottleId !== null) {
      clearTimeout(scrollThrottleId);
      scrollThrottleId = null;
      updateScrollDepth();
    }

    var sessionMs = Date.now() - pageStartTime;
    var totalMs   = dwellAccumulated + sessionMs;
    var dwellSecs = Math.round(totalMs / 1000);

    // a. dwell_time — session summary including scroll_depth for legacy queries
    try {
      var dwellPayload = buildPayload("dwell_time", {
        dwell_seconds: dwellSecs,
        scroll_depth:  maxScrollDepth,
      });
      sendBeaconOrFallback(JSON.stringify(dwellPayload));
    } catch (_) {}

    // b. scroll — standalone event so analytics queries that filter by
    //    event_type = 'scroll' receive a dedicated row with scroll_percent.
    try {
      var scrollPayload = buildPayload("scroll", {
        scroll_percent: maxScrollDepth,
        scroll_depth:   maxScrollDepth, // also populate the column directly
      });
      sendBeaconOrFallback(JSON.stringify(scrollPayload));
    } catch (_) {}
  }

  try {
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") {
        dwellAccumulated += Date.now() - pageStartTime;
        onPageLeave();
      } else {
        // Tab visible again — reset timer and allow next exit to send.
        pageStartTime = Date.now();
        dwellSent     = false;
      }
    });

    window.addEventListener("beforeunload", onPageLeave);
  } catch (_) {}

  } // end _wishsparkBoot

})();
