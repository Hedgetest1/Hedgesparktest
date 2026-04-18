/**
 * spark-crosssell.js — Storefront holdout enforcement for execution opportunities.
 *
 * Lightweight script that gates on-site cross-sell / upsell rendering
 * based on the visitor's holdout assignment.
 *
 * Flow:
 *   1. Read visitor_id from localStorage (same identity as spark-tracker.js)
 *   2. Call GET /products/executions/eligibility?shop=X&visitor_id=Y
 *   3. Cache response in sessionStorage (5 min TTL)
 *   4. Expose window.__hsCrossSell.canRender(productUrl) for widget gating
 *
 * Fail-safe: if endpoint fails or is unreachable, render is ALLOWED.
 * Rationale: false suppression (hiding a working widget) is worse than
 * occasional holdout leakage (which is detected and degrades confidence).
 *
 * Usage in theme / widget code:
 *   if (window.__hsCrossSell && !window.__hsCrossSell.canRender("/products/canvas-wallet")) {
 *     return; // holdout — suppress silently
 *   }
 *   // render cross-sell widget normally
 *
 * Deployment: added to storefront via Shopify ScriptTag or theme snippet,
 * after spark-tracker.js (requires visitor_id to exist).
 */
(function () {
  "use strict";

  if (window.__hsCrossSell) return; // prevent double-init

  // Minimal error reporter — posts to /public/tracker-error. Never throws.
  function _hsReportErr(source, err) {
    try {
      if (!err || !window.__wsXsShop || !window.__wsXsEndpoint) return;
      var body = JSON.stringify({
        shop: window.__wsXsShop,
        source: source,
        message: String((err && err.message) || err).slice(0, 1500),
        stack: String((err && err.stack) || "").slice(0, 3500),
        url: String(window.location && window.location.href || "").slice(0, 500),
        tracker_version: 13,
        user_agent: String(navigator.userAgent || "").slice(0, 300),
      });
      if (navigator.sendBeacon) {
        try { navigator.sendBeacon(window.__wsXsEndpoint, new Blob([body], {type:"application/json"})); return; } catch(_){}
      }
      fetch(window.__wsXsEndpoint, {method:"POST", keepalive:true, headers:{"Content-Type":"application/json"}, body:body}).catch(function(){});
    } catch (_) {}
  }

  var CACHE_KEY = "hs_crosssell_eligibility";
  var CACHE_TTL_MS = 300000; // 5 minutes

  // ---------------------------------------------------------------------------
  // Resolve shop_domain and visitor_id (same sources as spark-tracker.js)
  // ---------------------------------------------------------------------------

  var SHOP_DOMAIN = "";
  var API_URL = "";

  try {
    var scripts = document.querySelectorAll("script[src]");
    for (var i = 0; i < scripts.length; i++) {
      if (scripts[i].src && scripts[i].src.indexOf("spark-crosssell") !== -1) {
        var srcUrl = new URL(scripts[i].src);
        SHOP_DOMAIN = srcUrl.searchParams.get("shop") || "";
        API_URL = srcUrl.origin;
        break;
      }
    }
  } catch (_) {}

  if (!SHOP_DOMAIN) {
    try {
      SHOP_DOMAIN = new URL(window.location.href).searchParams.get("shop") || "";
    } catch (_) {}
  }

  var visitorId = "";
  try {
    visitorId = localStorage.getItem("hedgespark_visitor_id") || "";
  } catch (_) {}

  // Wire the error reporter now that we know shop + API origin.
  try {
    if (SHOP_DOMAIN) {
      window.__wsXsShop = SHOP_DOMAIN;
      window.__wsXsEndpoint = (API_URL || window.location.origin) + "/public/tracker-error";
    }
  } catch (_) {}

  // ---------------------------------------------------------------------------
  // Eligibility cache (sessionStorage, 5 min TTL)
  // ---------------------------------------------------------------------------

  // Map of product_url → { render_allowed: bool, execution_id: string }
  var eligibilityMap = {};
  var loaded = false;

  function loadCache() {
    try {
      var raw = sessionStorage.getItem(CACHE_KEY);
      if (!raw) return false;
      var cached = JSON.parse(raw);
      if (!cached || !cached.ts || Date.now() - cached.ts > CACHE_TTL_MS) {
        sessionStorage.removeItem(CACHE_KEY);
        return false;
      }
      eligibilityMap = cached.map || {};
      loaded = true;
      return true;
    } catch (_) {
      return false;
    }
  }

  function saveCache() {
    try {
      sessionStorage.setItem(CACHE_KEY, JSON.stringify({
        ts: Date.now(),
        map: eligibilityMap,
      }));
    } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // Fetch eligibility from backend
  // ---------------------------------------------------------------------------

  function fetchEligibility() {
    if (!SHOP_DOMAIN || !visitorId || !API_URL) {
      loaded = true; // no identity = allow all (fail-safe)
      return;
    }

    var url = API_URL + "/products/executions/eligibility"
      + "?shop=" + encodeURIComponent(SHOP_DOMAIN)
      + "&visitor_id=" + encodeURIComponent(visitorId);

    try {
      fetch(url, {
        method: "GET",
        credentials: "omit",
        headers: { "Accept": "application/json" },
      })
        .then(function (res) {
          if (!res.ok) throw new Error("eligibility " + res.status);
          return res.json();
        })
        .then(function (data) {
          if (data && Array.isArray(data.executions)) {
            for (var i = 0; i < data.executions.length; i++) {
              var ex = data.executions[i];
              if (ex.product_b) {
                eligibilityMap[ex.product_b.toLowerCase()] = {
                  render_allowed: !!ex.render_allowed,
                  execution_id: ex.execution_id || "",
                  group_type: ex.group_type || "exposed",
                };
              }
            }
          }
          loaded = true;
          saveCache();
        })
        .catch(function () {
          // Fail-safe: allow rendering on network error
          loaded = true;
        });
    } catch (_) {
      loaded = true;
    }
  }

  // ---------------------------------------------------------------------------
  // Public API: window.__hsCrossSell.canRender(productUrl)
  // ---------------------------------------------------------------------------

  window.__hsCrossSell = {
    /**
     * Check if a cross-sell widget for productUrl should be rendered.
     *
     * Returns true (render) or false (holdout — suppress).
     *
     * If eligibility hasn't loaded yet, returns true (fail-safe: render allowed).
     * If the product_url has no execution, returns true (no holdout applies).
     *
     * @param {string} productUrl — canonical product URL (e.g. "/products/canvas-wallet")
     * @returns {boolean}
     */
    canRender: function (productUrl) {
      if (!loaded) return true; // fail-safe: not yet loaded → allow
      if (!productUrl) return true;

      var key = productUrl.toLowerCase().replace(/\/$/, "");
      var entry = eligibilityMap[key];
      if (!entry) return true; // no execution for this product → allow

      return entry.render_allowed;
    },

    /** Check if eligibility data has loaded. */
    isReady: function () {
      return loaded;
    },

    /** Force re-fetch (e.g. after SPA navigation). */
    refresh: function () {
      loaded = false;
      eligibilityMap = {};
      fetchEligibility();
    },
  };

  // ---------------------------------------------------------------------------
  // Boot: load cache or fetch
  // ---------------------------------------------------------------------------

  try {
    if (!loadCache()) {
      fetchEligibility();
    }
  } catch (err) {
    _hsReportErr("spark-crosssell.boot", err);
  }
})();
