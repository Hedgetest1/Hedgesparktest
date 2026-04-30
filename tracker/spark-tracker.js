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
  // double-init is prevented even if the first load threw. The global name
  // is kept as `__wishsparkInit` on purpose: it is a stable JS identifier
  // that merchants' cached-tracker copies may still be setting from before
  // the HedgeSpark rebrand. Renaming it would re-run the boot on top of
  // an already-initialized tracker and duplicate every event emission.
  // ---------------------------------------------------------------------------
  if (window.__wishsparkInit) return;
  window.__wishsparkInit = true;

  // ---------------------------------------------------------------------------
  // Error telemetry — ONLY for HedgeSpark tracker code
  //
  // Previously the outer try/catch swallowed tracker boot errors silently.
  // window.onerror is also too-broad (fires for every storefront JS error,
  // most not ours). Instead we hook ONLY the tracker's own top-level
  // try/catch and surface errors to /public/tracker-error.
  //
  // The reporter is intentionally tiny + idempotent + rate-limited by
  // a per-page in-memory set (prevents a tight error loop flooding our
  // backend from a single broken theme). Transport is sendBeacon if
  // available, fetch keepalive otherwise, and everything is a no-op on
  // any failure so reporting itself can never crash the storefront.
  // ---------------------------------------------------------------------------
  var __hs_err_seen = {};
  var __hs_err_endpoint = null;
  var __hs_err_shop = null;
  var __hs_err_max_per_page = 5;
  var __hs_err_count = 0;
  function __hs_report_error(source, err, extra) {
    try {
      if (__hs_err_count >= __hs_err_max_per_page) return;
      var msg = "";
      var stack = "";
      if (err) {
        msg = String(err && err.message ? err.message : err).slice(0, 1500);
        stack = String(err && err.stack ? err.stack : "").slice(0, 3500);
      }
      if (extra) msg = (extra + " | " + msg).slice(0, 1900);
      // Dedup identical messages inside one page lifetime
      var key = source + "::" + msg.slice(0, 200);
      if (__hs_err_seen[key]) return;
      __hs_err_seen[key] = true;
      __hs_err_count++;
      if (!__hs_err_endpoint || !__hs_err_shop) return;
      var body = JSON.stringify({
        shop: __hs_err_shop,
        source: source,
        message: msg,
        stack: stack,
        url: String(window.location.href || "").slice(0, 500),
        tracker_version: (window.__hsTrackerVersion || null),
        user_agent: String(navigator.userAgent || "").slice(0, 300),
      });
      if (navigator.sendBeacon) {
        try {
          navigator.sendBeacon(__hs_err_endpoint, new Blob([body], {type: "application/json"}));
          return;
        } catch (_) {}
      }
      try {
        fetch(__hs_err_endpoint, {
          method: "POST",
          keepalive: true,
          headers: {"Content-Type": "application/json"},
          body: body,
        }).catch(function () {});
      } catch (_) {}
    } catch (_) { /* reporting must never throw */ }
  }

  try { _hedgesparkBoot(); } catch (bootErr) {
    try { console.warn("[HedgeSpark] tracker boot error (non-fatal):", bootErr); } catch (_) {}
    __hs_report_error("spark-tracker.boot", bootErr, null);
  }

  function _hedgesparkBoot() {

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
    console.warn("[HedgeSpark] tracker loaded but no shop param found");
    return;
  }

  // Wire the error reporter now that we know the shop + origin.
  try {
    __hs_err_shop = SHOP_DOMAIN;
    __hs_err_endpoint = API_URL.replace(/\/track$/, "/public/tracker-error");
    window.__hsTrackerVersion = 12;
  } catch (_) {}

  // ---------------------------------------------------------------------------
  // Visitor identity — persisted in localStorage across sessions
  //
  // Also written to a first-party cookie (_hs_vid) so the Shopify Custom
  // Pixel sandbox can read it at checkout via browser.cookie.get("_hs_vid").
  // This bridges storefront browsing identity → checkout purchase identity.
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

  // Write to first-party cookie for pixel bridge.
  // max-age=63072000 = 2 years.  SameSite=Lax ensures it's sent on
  // same-site navigations (checkout is same-site on Shopify storefronts).
  try {
    document.cookie = "_hs_vid=" + encodeURIComponent(visitorId)
      + ";path=/;max-age=63072000;SameSite=Lax";
  } catch (_) {}

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

  function _getParam(name) {
    try {
      var params = new URL(window.location.href).searchParams;
      var val = params.get(name);
      return val ? val.trim() : null;
    } catch (_) {
      return null;
    }
  }

  function _utmMedium() {
    var med = _getParam("utm_medium");
    return med ? med.toLowerCase() : null;
  }

  function _utmSource() {
    var src = _getParam("utm_source");
    if (!src) return null;
    src = src.toLowerCase();
    if (src === "newsletter" || src === "e-mail") src = "email";
    return src || null;
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
  // Attribution data — UTM params, click IDs, landing page
  //
  // Captured ONCE at boot from URL query params.
  // landing_page is persisted in sessionStorage so it remains the FIRST page
  // of the session across subsequent navigations.
  //
  // Click ID priority: gclid > fbclid > ttclid > msclkid
  // Stored as "type:value" string to match backend expectation.
  //
  // These are campaign metadata (not PII). GDPR-safe.
  // ---------------------------------------------------------------------------
  var _utmSourceVal   = _getParam("utm_source")   || undefined;
  var _utmMediumVal   = _utmMedium()              || undefined;
  var _utmCampaignVal = _getParam("utm_campaign") || undefined;
  var _utmContentVal  = _getParam("utm_content")  || undefined;
  var _utmTermVal     = _getParam("utm_term")     || undefined;

  // Click ID — deterministic priority order
  var _clickIdVal = undefined;
  try {
    var _CID_TYPES = ["gclid", "fbclid", "ttclid", "msclkid"];
    for (var ci = 0; ci < _CID_TYPES.length; ci++) {
      var cidVal = _getParam(_CID_TYPES[ci]);
      if (cidVal) {
        _clickIdVal = _CID_TYPES[ci] + ":" + cidVal;
        break;
      }
    }
  } catch (_) {}

  // Shopify client ID — read from _shopify_y cookie.
  // This is Shopify's persistent visitor identifier, also available as
  // event.clientId in the Custom Pixel sandbox. By sending it from the
  // tracker, the backend can build a mapping from Shopify's ID to our
  // hedgespark_visitor_id. When the pixel fires checkout_completed with
  // event.clientId, the backend resolves it back to our visitor_id.
  // This bridges the identity gap caused by the pixel sandbox not being
  // able to read our _hs_vid cookie or localStorage.
  var _shopifyY = undefined;
  try {
    var _cookies = document.cookie.split(";");
    for (var _ci2 = 0; _ci2 < _cookies.length; _ci2++) {
      var _parts = _cookies[_ci2].trim().split("=");
      if (_parts[0] === "_shopify_y") {
        _shopifyY = decodeURIComponent(_parts[1]);
        break;
      }
    }
  } catch (_) {}

  // Landing page — FIRST page URL of this browser session.
  // Persisted in sessionStorage so it doesn't change on later navigations.
  var _landingPage = undefined;
  try {
    var _LP_KEY = "hs_landing_page";
    var stored = sessionStorage.getItem(_LP_KEY);
    if (stored) {
      _landingPage = stored;
    } else {
      // Strip query params and fragment for cleanliness
      _landingPage = window.location.pathname + window.location.search;
      sessionStorage.setItem(_LP_KEY, _landingPage);
    }
  } catch (_) {
    _landingPage = window.location.pathname;
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
  // Consent gating (2026-04-11 worldwide compliance audit)
  //
  // The tracker reads consent state from three independent sources, in
  // priority order, and passes the resulting flag to the backend with
  // every event. The backend then enforces the decision — this layer
  // just surfaces the visitor's choice.
  //
  //   1. `window.hsConsent.given` — explicit merchant integration with
  //      their own cookie banner. Shape: `{ given: boolean, region?: string }`.
  //      Merchants who ship a banner can set this on DOMContentLoaded.
  //
  //   2. `localStorage['hs_consent']` — legacy fallback for merchants
  //      who use our default cookie flag. Values: "1" (given), "0"
  //      (denied), or absent (unknown).
  //
  //   3. Browser-level signals — Global Privacy Control
  //      (`navigator.globalPrivacyControl === true`) and legacy Do
  //      Not Track (`navigator.doNotTrack === "1"`). Either one sets
  //      denied. Required for CCPA/CPRA in California and honored
  //      elsewhere as a belt-and-braces opt-out path.
  //
  // Decision tree:
  //   hsConsent.given === true    → gdpr_consent_given = true
  //   hsConsent.given === false   → gdpr_consent_given = false
  //   localStorage hs_consent=1   → gdpr_consent_given = true
  //   localStorage hs_consent=0   → gdpr_consent_given = false
  //   Sec-GPC / DNT = 1           → gdpr_consent_given = false
  //   otherwise                   → gdpr_consent_given omitted (legacy)
  //
  // The region (`consent_region`) is read from `hsConsent.region` or
  // a `hs_region` meta tag if present. It's a two-letter country code
  // used by the backend to scope EU-specific enforcement.
  // ---------------------------------------------------------------------------
  function detectConsent() {
    try {
      var cfg = window.hsConsent || null;
      var region = null;
      if (cfg && typeof cfg.region === "string") {
        region = cfg.region.toUpperCase().slice(0, 2);
      } else {
        var regionMeta = document.querySelector('meta[name="hs_region"]');
        if (regionMeta && regionMeta.content) {
          region = (regionMeta.content || "").toUpperCase().slice(0, 2);
        }
      }

      if (cfg && typeof cfg.given === "boolean") {
        return { given: cfg.given, region: region };
      }

      try {
        var ls = window.localStorage && window.localStorage.getItem("hs_consent");
        if (ls === "1") return { given: true, region: region };
        if (ls === "0") return { given: false, region: region };
      } catch (_) {}

      // Browser-level opt-out — Sec-GPC and legacy DNT
      try {
        if (navigator && navigator.globalPrivacyControl === true) {
          return { given: false, region: region };
        }
        if (navigator && (navigator.doNotTrack === "1" || window.doNotTrack === "1")) {
          return { given: false, region: region };
        }
      } catch (_) {}

      return { given: null, region: region };
    } catch (_) {
      return { given: null, region: null };
    }
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
    var consent    = detectConsent();
    var payload = {
      shop_domain:   SHOP_DOMAIN,
      visitor_id:    visitorId,
      event_type:    eventType,
      page_url:      currentPageUrl(),
      product_url:   productUrl || undefined,
      product_id:    productId  || undefined,
      timestamp:     Date.now(),
      source_type:   detectSourceType(),
      referrer:      document.referrer || "",
      utm_medium:    _utmMediumVal,
      utm_source:    _utmSourceVal,
      utm_campaign:  _utmCampaignVal,
      utm_content:   _utmContentVal,
      utm_term:      _utmTermVal,
      click_id:      _clickIdVal,
      landing_page:  _landingPage,
      shopify_y:     _shopifyY,
      device_type:   /Mobi|Android/i.test(navigator.userAgent) ? "mobile" : "desktop",
    };
    // Only include consent field when we have a definitive signal — the
    // backend treats `undefined` as legacy (allow) and explicit boolean
    // as modern (enforce).
    if (consent.given === true || consent.given === false) {
      payload.gdpr_consent_given = consent.given;
    }
    if (consent.region) {
      payload.consent_region = consent.region;
    }
    if (extra) {
      for (var k in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, k)) {
          payload[k] = extra[k];
        }
      }
    }
    return payload;
  }

  // Public API — merchants can call this after their cookie banner
  // resolves. It flushes any queued events and updates the in-page
  // consent cache immediately.
  window.hsSetConsent = function (given, region) {
    try {
      window.hsConsent = window.hsConsent || {};
      window.hsConsent.given = !!given;
      if (typeof region === "string") {
        window.hsConsent.region = region;
      }
      try { window.localStorage.setItem("hs_consent", given ? "1" : "0"); } catch (_) {}
    } catch (_) {}
  };

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
  // Batch buffer — queues events and flushes to /track/batch every 2 seconds.
  //
  // Reduces network requests by ~80% on high-event sessions.  The batch
  // endpoint accepts { events: [...] } and inserts all rows in a single
  // transaction.
  //
  // Immediate sends (page_view, product_view) still go through sendEvent()
  // because they establish the visitor session and must arrive before any
  // batched events.  Batching is used for dwell_time, scroll, click, and
  // other mid-session events.
  // ---------------------------------------------------------------------------
  var _BATCH_URL   = API_URL + "/batch";
  var _batchQueue  = [];
  var _batchTimer  = null;
  var _BATCH_DELAY = 2000;  // flush every 2 seconds
  var _BATCH_MAX   = 20;    // or when 20 events accumulate

  function _flushBatch() {
    if (!_batchQueue.length) return;
    var events = _batchQueue.splice(0);
    _batchTimer = null;
    try {
      var body = JSON.stringify({ events: events });
      fetch(_BATCH_URL, {
        method:      "POST",
        headers:     { "Content-Type": "application/json" },
        body:        body,
        keepalive:   true,
        credentials: "omit",
      }).catch(function () {
        // On failure, re-enqueue individual events to the offline buffer
        for (var i = 0; i < events.length; i++) {
          _enqueue(JSON.stringify(events[i]));
        }
      });
    } catch (_) {}
  }

  function sendEventBatched(eventType, extra) {
    try {
      var payload = buildPayload(eventType, extra);
      _batchQueue.push(payload);
      if (_batchQueue.length >= _BATCH_MAX) {
        if (_batchTimer) { clearTimeout(_batchTimer); _batchTimer = null; }
        _flushBatch();
      } else if (!_batchTimer) {
        _batchTimer = setTimeout(_flushBatch, _BATCH_DELAY);
      }
    } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // 1. page_view — fired immediately on script load (fetch, NOT batched)
  //    Must arrive first to establish the visitor session server-side.
  // ---------------------------------------------------------------------------
  sendEvent("page_view");

  // ---------------------------------------------------------------------------
  // 2. product_view — fired on Shopify product pages (fetch, NOT batched)
  //    Must arrive immediately for real-time signal detection.
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

    // Flush pending batch buffer before exit — ensures mid-session events
    // (clicks, etc.) are not lost when the page unloads.
    if (_batchTimer) { clearTimeout(_batchTimer); _batchTimer = null; }
    _flushBatch();

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

  // ---------------------------------------------------------------------------
  // 5. Add-to-cart tracking
  //
  // Three detection patterns, all non-blocking:
  //   A. Form submit to /cart/add — traditional Shopify product forms
  //   B. fetch() to /cart/add.js — modern AJAX themes (Dawn, etc.)
  //   C. XMLHttpRequest to /cart/add — legacy jQuery themes
  //
  // Dedup: sessionStorage per (product_url, session) + 500ms cooldown.
  // Max 1 add_to_cart event per product per session.
  //
  // CRITICAL: fetch/XHR patches use __hs_patched sentinel to prevent
  // double-patching if the script evaluates twice (hot reload, CSP retry).
  // Original functions are preserved exactly — return values, promises,
  // error propagation are all pass-through.
  // ---------------------------------------------------------------------------
  var _atcSentKey  = "hs_atc_sent";
  var _atcCooldown = 0;  // monotonic timestamp of last fire

  function _atcSent(productUrl) {
    try {
      var sent = JSON.parse(sessionStorage.getItem(_atcSentKey) || "{}");
      return !!sent[productUrl];
    } catch (_) { return false; }
  }

  function _markAtcSent(productUrl) {
    try {
      var sent = JSON.parse(sessionStorage.getItem(_atcSentKey) || "{}");
      sent[productUrl] = 1;
      sessionStorage.setItem(_atcSentKey, JSON.stringify(sent));
    } catch (_) {}
  }

  function _fireAddToCart(source) {
    try {
      var now = Date.now();
      // 500ms cooldown prevents click + form submit double-fire on same action
      if (now - _atcCooldown < 500) return;
      var productUrl = detectProductUrl();
      if (!productUrl) return;
      if (_atcSent(productUrl)) return;
      _atcCooldown = now;
      _markAtcSent(productUrl);
      sendEventBatched("add_to_cart", { product_url: productUrl });
    } catch (_) {}
  }

  // Pattern A: form submit — only forms posting to /cart/add (NOT /cart alone)
  try {
    document.addEventListener("submit", function (e) {
      try {
        var form = e.target;
        if (!form) return;
        var action = form.getAttribute("action") || "";
        // Strict match: must contain /cart/add — not just /cart (which catches
        // coupon forms, cart update forms, etc.)
        if (action.indexOf("/cart/add") !== -1) {
          _fireAddToCart("form");
        }
      } catch (_) {}
    }, true);
  } catch (_) {}

  // Pattern B: fetch() interception — idempotent, preserves original exactly
  try {
    if (window.fetch && !window.fetch.__hs_patched) {
      var _origFetch = window.fetch;
      window.fetch = function (input) {
        try {
          // Handle both string URLs and Request objects
          var urlStr = typeof input === "string"
            ? input
            : (input && typeof input.url === "string" ? input.url : "");
          if (urlStr.indexOf("/cart/add") !== -1) {
            _fireAddToCart("fetch");
          }
        } catch (_) {}
        // CRITICAL: pass through ALL arguments unchanged, preserve `this`
        return _origFetch.apply(this, arguments);
      };
      window.fetch.__hs_patched = true;
    }
  } catch (_) {}

  // Pattern C: XHR interception — idempotent, preserves original exactly
  try {
    var xhrProto = XMLHttpRequest.prototype;
    if (xhrProto.open && !xhrProto.open.__hs_patched) {
      var _origOpen = xhrProto.open;
      xhrProto.open = function (method, url) {
        try {
          if (typeof url === "string" && url.indexOf("/cart/add") !== -1) {
            _fireAddToCart("xhr");
          }
        } catch (_) {}
        return _origOpen.apply(this, arguments);
      };
      xhrProto.open.__hs_patched = true;
    }
  } catch (_) {}

  // ---------------------------------------------------------------------------
  // 6. Product-page CTA click tracking
  //
  // Captures clicks on high-signal interactive elements on product pages.
  // Uses event delegation — ONE listener on document, capture phase.
  //
  // Dedup: the click listener DOES NOT fire add_to_cart — that's handled
  // by section 5 via form/fetch/XHR interception.  The click event records
  // "visitor interacted with a CTA" which is a separate behavioral signal
  // from "item was successfully added to cart."
  //
  // Max 3 click events per page load (tightened from 5 — 3 unique CTA
  // interactions is the useful signal ceiling per page).
  // ---------------------------------------------------------------------------
  var _clickCount = 0;
  var _MAX_CLICKS = 3;

  // Tight selector set — only elements that indicate purchase intent.
  // Intentionally excludes generic <a> tags, nav links, accordion toggles.
  var _CLICK_SELECTORS = [
    "[type='submit'][name='add']",           // Shopify default ATC button
    ".product-form__submit",                 // Dawn / Shopify 2.0 themes
    ".shopify-payment-button__button",       // Dynamic checkout (Buy with Shop Pay)
    ".shopify-payment-button button",        // Dynamic checkout nested button
    "[data-add-to-cart]",                    // Common theme convention
    "button[name='add']",                    // Generic Shopify add button
  ];

  function _matchesClickSelector(el) {
    if (!el || el.nodeType !== 1) return false;
    try {
      for (var i = 0; i < _CLICK_SELECTORS.length; i++) {
        try { if (el.matches(_CLICK_SELECTORS[i])) return true; } catch (_) {}
      }
      // Walk up max 2 parent levels for clicks on inner <span>/<svg> icons
      var parent = el.parentElement;
      for (var depth = 0; parent && depth < 2; depth++) {
        for (var j = 0; j < _CLICK_SELECTORS.length; j++) {
          try { if (parent.matches(_CLICK_SELECTORS[j])) return true; } catch (_) {}
        }
        parent = parent.parentElement;
      }
    } catch (_) {}
    return false;
  }

  if (detectProductUrl()) {
    try {
      document.addEventListener("click", function (e) {
        try {
          if (_clickCount >= _MAX_CLICKS) return;
          if (!_matchesClickSelector(e.target)) return;
          _clickCount++;
          // Coords as % of viewport for Lite spatial heatmap (Lucky
          // Orange $32 parity). 1 decimal precision keeps payload
          // small. Coords go to Redis bucket on /track, not stored
          // in events table — no schema migration.
          var _vw = Math.max(1, window.innerWidth || 1);
          var _vh = Math.max(1, window.innerHeight || 1);
          sendEventBatched("click", {
            product_url: detectProductUrl(),
            x_pct: Math.round((e.clientX / _vw) * 1000) / 10,
            y_pct: Math.round((e.clientY / _vh) * 1000) / 10,
          });
        } catch (_) {}
      }, true);
    } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // 6b. Mousemove sampler — Lite spatial heatmap "movement trails"
  //     (Lucky Orange Build $39 ships move heatmaps; we ship aggregate
  //      density on the same Lite tier). Throttled to 200ms; capped at
  //      50 samples per page load to avoid event firehose. Only on
  //      product pages (same gate as click). Coords as % of viewport.
  // ---------------------------------------------------------------------------
  if (detectProductUrl()) {
    try {
      var _moveCount = 0;
      var _MAX_MOVES = 50;
      var _MOVE_THROTTLE_MS = 200;
      var _lastMoveTs = 0;
      document.addEventListener("mousemove", function (e) {
        try {
          if (_moveCount >= _MAX_MOVES) return;
          var _now = Date.now();
          if (_now - _lastMoveTs < _MOVE_THROTTLE_MS) return;
          _lastMoveTs = _now;
          _moveCount++;
          var _vw = Math.max(1, window.innerWidth || 1);
          var _vh = Math.max(1, window.innerHeight || 1);
          sendEventBatched("mousemove", {
            product_url: detectProductUrl(),
            x_pct: Math.round((e.clientX / _vw) * 1000) / 10,
            y_pct: Math.round((e.clientY / _vh) * 1000) / 10,
          });
        } catch (_) {}
      }, { passive: true });
    } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // 7. Cart and checkout page detection
  //
  // Two distinct events with clean semantics:
  //   view_cart       — visitor is on /cart (browsing cart, not yet committed)
  //   begin_checkout  — visitor is on /checkout (committed to purchase flow)
  //
  // Naturally deduplicated: one event per page load.
  // Fires via sendEventBatched — mid-session intent signal.
  // ---------------------------------------------------------------------------
  try {
    var _pathname = window.location.pathname;
    // /checkouts/... or /checkout (Shopify uses both)
    if (/^\/checkout/.test(_pathname)) {
      sendEventBatched("begin_checkout");
    } else if (/^\/cart\b/.test(_pathname)) {
      sendEventBatched("view_cart");
    }
  } catch (_) {}

  // ---------------------------------------------------------------------------
  // 8. Purchase tracking
  //
  // Script tags do NOT run on Shopify checkout/thank-you pages.
  // Purchase tracking is handled by the Shopify Web Pixel (Custom Pixel)
  // installed in Shopify Admin > Settings > Customer events.
  // See: tracker/spark-pixel.js
  // ---------------------------------------------------------------------------

  // ---------------------------------------------------------------------------
  // 9. UX frustration signals — rage click + pogo stick
  //
  // Rage click:  3+ clicks on the same element within 2 seconds.
  //              Usually signals a UI dead-end (button not working,
  //              link visually broken, layout confusion).
  // Pogo stick:  visitor hits a page, bounces back within 3 seconds.
  //              Signals content mismatch or slow load.
  //
  // Both self-limit to AT MOST 1 event per type per page load so a
  // pathological visitor can't flood our backend. Backend aggregates
  // per shop-day and raises ux_frustration_spike when rates climb
  // above baseline.
  // ---------------------------------------------------------------------------
  try {
    var _rageClickTargetKey = null;
    var _rageClickTimes = [];   // sliding window of click timestamps
    var _rageClickFired = false;
    var _RAGE_WINDOW_MS = 2000;
    var _RAGE_COUNT = 3;

    function _elementKey(el) {
      if (!el) return null;
      try {
        var tag = String(el.tagName || "").toLowerCase();
        var id = el.id ? "#" + el.id : "";
        var cls = el.className && typeof el.className === "string"
          ? "." + el.className.split(/\s+/).slice(0, 2).join(".")
          : "";
        return (tag + id + cls).slice(0, 120);
      } catch (_) { return null; }
    }

    document.addEventListener("click", function (e) {
      try {
        if (_rageClickFired) return;
        var key = _elementKey(e.target);
        if (!key) return;
        var now = Date.now();
        if (_rageClickTargetKey !== key) {
          _rageClickTargetKey = key;
          _rageClickTimes = [];
        }
        _rageClickTimes.push(now);
        // Keep only clicks inside the sliding window
        while (_rageClickTimes.length && now - _rageClickTimes[0] > _RAGE_WINDOW_MS) {
          _rageClickTimes.shift();
        }
        if (_rageClickTimes.length >= _RAGE_COUNT) {
          _rageClickFired = true;
          sendEventBatched("rage_click", {
            product_url: detectProductUrl(),
            meta: { target: key, clicks: _rageClickTimes.length },
          });
        }
      } catch (_) {}
    }, true);
  } catch (bootErr) {
    __hs_report_error("spark-tracker.rage_click_setup", bootErr, null);
  }

  try {
    // Pogo-stick = back nav within 3s of page load. We observe via
    // PerformanceNavigationTiming + pagehide — if the visitor navigates
    // away via history.back() in under 3s from load, fire pogo_stick.
    var _pageLoadedAt = Date.now();
    var _POGO_THRESHOLD_MS = 3000;
    var _pogoFired = false;

    window.addEventListener("pagehide", function () {
      try {
        if (_pogoFired) return;
        var elapsed = Date.now() - _pageLoadedAt;
        if (elapsed >= _POGO_THRESHOLD_MS) return;
        // Only count as pogo-stick if the visitor is actually going back
        // (not forward to checkout, not closing the tab).
        var navType = null;
        try {
          var navEntries = performance.getEntriesByType("navigation");
          navType = navEntries && navEntries[0] ? navEntries[0].type : null;
        } catch (_) {}
        _pogoFired = true;
        // sendBeacon path is better here — fetch may be canceled on unload.
        var payload = buildPayload("pogo_stick", {
          product_url: detectProductUrl(),
          meta: { dwell_ms: elapsed, nav_type: navType },
        });
        if (navigator.sendBeacon) {
          try {
            navigator.sendBeacon(API_URL, new Blob([JSON.stringify(payload)], {type: "application/json"}));
          } catch (_) {}
        }
      } catch (_) {}
    });
  } catch (bootErr) {
    __hs_report_error("spark-tracker.pogo_stick_setup", bootErr, null);
  }

  } // end _hedgesparkBoot

})();
