/**
 * spark-nudge.js — HedgeSpark storefront nudge renderer (v5: holdout suppression)
 *
 * Contract
 * --------
 * Installed on Shopify product pages via a <script> tag:
 *
 *   <script async src="https://<api-host>/nudge.js?shop={{ shop.permanent_domain }}"></script>
 *
 * On load it:
 *   1. Resolves shop_domain from ?shop= on the script src URL.
 *   2. Resolves visitor_id from localStorage (hedgespark_visitor_id) — the same
 *      UUID written by spark-tracker.js.
 *   3. Detects whether the current page is a Shopify product page.
 *   4. Fetches /nudges/active?shop=&product_url=&visitor_id=
 *   5. Checks server eligibility and holdout decision:
 *        response.active === false          → no nudge configured
 *        response.eligible === false        → behavioral gate suppressed
 *        response.render_allowed === false  → holdout group suppressed (v5)
 *        all of the above pass              → render nudge
 *   6. On render: inserts an unobtrusive element near the product form.
 *   7. Sends a "shown" measurement event including the assigned copy_variant.
 *   8. On dismiss: sends a "dismissed" measurement event including copy_variant.
 *
 * v5 — Holdout suppression
 * ------------------------
 * The server assigns a deterministic fraction of eligible visitors to a holdout
 * (control) group for quasi-experimental incremental lift measurement.
 * When a visitor is in the holdout group, the server returns:
 *
 *   { active: true, eligible: true, render_allowed: false, holdout: true }
 *
 * The client MUST suppress rendering in this case and MUST NOT send any
 * measurement event (the server has already recorded the holdout_assigned event
 * server-side — no client action required).
 *
 * Backward compatibility:
 *   - render_allowed is absent on old server responses — treated as render_allowed = true
 *   - Old script checking only data.eligible will still work correctly
 *
 * A/B variant assignment (v4, unchanged)
 * ---------------------------------------
 * The server assigns one copy variant per eligible visitor deterministically:
 *     idx = hash(visitor_id + ":" + nudge_id) % n_variants
 * The same visitor always receives the same variant for a given nudge.
 * The client receives only the assigned variant — it is not aware of other variants.
 * The assigned copy_variant name (e.g., "social_proof") is included in all
 * measurement events so the server can compute per-variant stats.
 *
 * Holdout hash is computed server-side with a different namespace:
 *     holdout = hash(visitor_id + ":holdout:" + nudge_id) % 100 < holdout_pct
 * These two hashes are independent — a visitor's holdout status is unrelated
 * to which copy variant they would have seen.
 *
 * Measurement events
 * ------------------
 * "shown" event:     { event_type: "shown",     metadata: { copy_variant: "social_proof" } }
 * "dismissed" event: { event_type: "dismissed", metadata: { copy_variant: "social_proof" } }
 *
 * Holdout group: NO measurement event sent by the client.
 *   The server records "holdout_assigned" at delivery time (GET /nudges/active).
 *   Client suppression is silent — fire-and-forget, no confirmation needed.
 *
 * SessionStorage dedup (unchanged from v3):
 *   "ws_nudge_dismissed"        — nudge_id; prevents re-render after dismiss
 *   "ws_nudge_shown_{nudge_id}" — "1"; prevents duplicate "shown" events on refresh
 *
 * Design principles (unchanged)
 * ------------------------------
 *   - Separate concern from spark-tracker.js: this script ONLY renders nudges
 *     and sends nudge-specific measurement events (not behavioral events).
 *   - Zero external dependencies.  Vanilla JavaScript, ES5 compatible.
 *   - Fails silently on any error.
 *   - Idempotent: double-init guard.
 *   - All server-controlled copy is HTML-escaped before DOM insertion.
 *   - Styles scoped to [data-wishspark-nudge] attribute.
 *
 * CORS
 * ----
 * GET /nudges/active: Access-Control-Allow-Origin: * (server-set).
 * POST /nudge/event: fire-and-forget; browser never reads response; CORS irrelevant.
 * All requests use credentials: "omit".
 */
(function () {
  "use strict";

  // Safety guard — prevent double-init
  if (window.__wishsparkNudgeInit) return;
  window.__wishsparkNudgeInit = true;

  // ---------------------------------------------------------------------------
  // Configuration — resolved from script src ?shop= param
  // ---------------------------------------------------------------------------
  var API_HOST    = "";
  var SHOP_DOMAIN = "";

  try {
    var scriptEl = document.currentScript;
    if (!scriptEl) {
      var scripts = document.querySelectorAll("script[src]");
      for (var i = 0; i < scripts.length; i++) {
        if (scripts[i].src && scripts[i].src.indexOf("nudge.js") !== -1) {
          scriptEl = scripts[i];
          break;
        }
      }
    }
    if (scriptEl && scriptEl.src) {
      var srcUrl  = new URL(scriptEl.src);
      SHOP_DOMAIN = srcUrl.searchParams.get("shop") || "";
      API_HOST    = srcUrl.origin;
    }
  } catch (_) {}

  // Fallback: page URL ?shop= (dev / testing)
  if (!SHOP_DOMAIN) {
    try {
      SHOP_DOMAIN = new URL(window.location.href).searchParams.get("shop") || "";
    } catch (_) {}
  }

  if (!SHOP_DOMAIN || !API_HOST) {
    return;
  }

  // ---------------------------------------------------------------------------
  // Product page detection
  // ---------------------------------------------------------------------------
  function detectProductUrl() {
    var m = window.location.pathname.match(/\/products\/([^/?#]+)/);
    return m ? "/products/" + m[1] : null;
  }

  var productUrl = detectProductUrl();
  if (!productUrl) {
    return;
  }

  // ---------------------------------------------------------------------------
  // Visitor identity
  // ---------------------------------------------------------------------------
  var visitorId = null;
  try {
    visitorId = localStorage.getItem("hedgespark_visitor_id") || null;
  } catch (_) {}

  // ---------------------------------------------------------------------------
  // Dismissal state
  // ---------------------------------------------------------------------------
  function getDismissedNudgeId() {
    try { return sessionStorage.getItem("ws_nudge_dismissed") || null; } catch (_) { return null; }
  }

  function recordDismissal(nudgeId) {
    try { sessionStorage.setItem("ws_nudge_dismissed", String(nudgeId)); } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // Shown-event dedup (per tab session per nudge_id)
  // ---------------------------------------------------------------------------
  function hasTrackedShown(nudgeId) {
    try { return sessionStorage.getItem("ws_nudge_shown_" + nudgeId) === "1"; } catch (_) { return false; }
  }

  function markShownTracked(nudgeId) {
    try { sessionStorage.setItem("ws_nudge_shown_" + nudgeId, "1"); } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // HTML escaping
  // ---------------------------------------------------------------------------
  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g,  "&amp;")
      .replace(/</g,  "&lt;")
      .replace(/>/g,  "&gt;")
      .replace(/"/g,  "&quot;")
      .replace(/'/g,  "&#39;");
  }

  // ---------------------------------------------------------------------------
  // Measurement event transport
  //
  // sendNudgeEvent(eventType, nudgeId, useSendBeacon, copyVariant)
  //
  // copyVariant — the assigned variant name returned by the server
  //   ("high_interest" | "social_proof" | "").
  //   Included in event_meta so the backend can attribute each event to
  //   its variant for per-variant stats and winner selection.
  //
  // "shown"     → fetch with keepalive (immediate)
  // "dismissed" → sendBeacon (reliable on close) → fetch fallback
  //
  // Holdout visitors: no event sent — server records holdout_assigned directly.
  //
  // Dedup: "shown" events are suppressed if sessionStorage key already set.
  // Dismissed events are naturally deduplicated (one dismiss per session).
  // ---------------------------------------------------------------------------
  function sendNudgeEvent(eventType, nudgeId, useSendBeacon, copyVariant) {
    // Dedup for "shown" — one per tab session per nudge_id
    if (eventType === "shown") {
      if (hasTrackedShown(nudgeId)) return;
      markShownTracked(nudgeId); // mark before send (fire-and-forget)
    }

    var payload = JSON.stringify({
      shop:        SHOP_DOMAIN,
      nudge_id:    nudgeId,
      visitor_id:  visitorId,
      product_url: productUrl,
      event_type:  eventType,
      // Include assigned copy_variant in metadata for per-variant measurement.
      // copyVariant may be "" for legacy/product-level nudges — that is fine;
      // the server stores whatever is sent and filters on IS NOT NULL for variant stats.
      metadata:    { copy_variant: copyVariant || null },
    });

    var url = API_HOST + "/nudge/event";

    if (useSendBeacon && navigator.sendBeacon) {
      try {
        var sent = navigator.sendBeacon(
          url,
          new Blob([payload], { type: "application/json" })
        );
        if (!sent) { _fetchNudgeEvent(url, payload); }
      } catch (_) {
        _fetchNudgeEvent(url, payload);
      }
    } else {
      _fetchNudgeEvent(url, payload);
    }
  }

  function _fetchNudgeEvent(url, body) {
    try {
      fetch(url, {
        method:      "POST",
        headers:     { "Content-Type": "application/json" },
        body:        body,
        keepalive:   true,
        credentials: "omit",
      }).catch(function () {});
    } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // Styles
  // ---------------------------------------------------------------------------
  function injectStyles() {
    if (document.getElementById("ws-nudge-styles")) return;
    var style = document.createElement("style");
    style.id = "ws-nudge-styles";
    style.textContent = [
      "[data-wishspark-nudge]{",
      "  display:flex;align-items:flex-start;gap:10px;",
      "  padding:12px 14px;margin:14px 0;",
      "  background:#f8f8f8;border:1px solid #e4e4e4;border-radius:6px;",
      "  font-family:inherit;font-size:13px;line-height:1.4;",
      "  color:#333;box-sizing:border-box;position:relative;",
      "}",
      "[data-wishspark-nudge] .ws-nudge__icon{",
      "  flex-shrink:0;width:18px;height:18px;margin-top:1px;",
      "  fill:#e06000;",
      "}",
      "[data-wishspark-nudge] .ws-nudge__body{ flex:1;min-width:0; }",
      "[data-wishspark-nudge] .ws-nudge__badge{",
      "  display:inline-block;padding:1px 7px;margin-bottom:4px;",
      "  background:#e06000;color:#fff;border-radius:3px;",
      "  font-size:11px;font-weight:600;letter-spacing:0.4px;text-transform:uppercase;",
      "}",
      "[data-wishspark-nudge] .ws-nudge__headline{",
      "  margin:0 0 2px;font-weight:600;font-size:13px;color:#222;",
      "}",
      "[data-wishspark-nudge] .ws-nudge__subtext{ margin:0;font-size:12px;color:#666; }",
      "[data-wishspark-nudge] .ws-nudge__scarcity{",
      "  display:inline-block;margin-top:5px;font-size:11px;font-weight:600;color:#c84b00;",
      "}",
      "[data-wishspark-nudge] .ws-nudge__close{",
      "  position:absolute;top:8px;right:10px;",
      "  background:none;border:none;cursor:pointer;",
      "  font-size:16px;line-height:1;color:#999;padding:0;",
      "}",
      "[data-wishspark-nudge] .ws-nudge__close:hover{ color:#555; }",
    ].join("\n");
    (document.head || document.documentElement).appendChild(style);
  }

  // ---------------------------------------------------------------------------
  // Nudge element builder
  //
  // copyVariant is threaded through here so the dismiss handler can include
  // the assigned variant in the "dismissed" measurement event.
  // ---------------------------------------------------------------------------
  function buildNudgeElement(nudgeId, copyConfig, copyVariant) {
    var headline     = copyConfig.headline      || "High interest right now";
    var subtext      = copyConfig.subtext       || "";
    var badge        = copyConfig.badge         || "";
    var scarcityText = copyConfig.scarcity_text || "";

    var iconSvg = [
      '<svg class="ws-nudge__icon" viewBox="0 0 24 24"',
      ' xmlns="http://www.w3.org/2000/svg" aria-hidden="true">',
      '<path d="M13.5 0.67s.74 2.65.74 4.8c0 2.06-1.35 3.73-3.41 3.73',
      '-2.07 0-3.63-1.67-3.63-3.73l.03-.36C5.21 7.51 4 10.62 4 14',
      'c0 4.42 3.58 8 8 8s8-3.58 8-8C20 8.61 17.41 3.8 13.5.67z"/>',
      "</svg>",
    ].join("");

    var el = document.createElement("div");
    el.setAttribute("data-wishspark-nudge", "true");
    el.setAttribute("data-nudge-id",      String(nudgeId));
    el.setAttribute("data-copy-variant",  String(copyVariant || ""));

    var inner = iconSvg;
    inner += '<div class="ws-nudge__body">';
    if (badge) {
      inner += '<span class="ws-nudge__badge">' + escapeHtml(badge) + "</span>";
    }
    inner += '<p class="ws-nudge__headline">' + escapeHtml(headline) + "</p>";
    if (subtext) {
      inner += '<p class="ws-nudge__subtext">' + escapeHtml(subtext) + "</p>";
    }
    inner += "</div>";
    inner += '<button class="ws-nudge__close" aria-label="Dismiss">&times;</button>';
    inner += '<a class="ws-nudge__powered" href="https://hedgesparkhq.com?ref=nudge"'
           + ' target="_blank" rel="noopener" style="display:block;text-align:right;'
           + 'font-size:9px;color:rgba(255,255,255,0.3);text-decoration:none;'
           + 'margin-top:4px;font-family:sans-serif;">Powered by Hedge Spark</a>';
    el.innerHTML = inner;

    // Dismiss handler — passes copyVariant to measurement event
    var closeBtn = el.querySelector(".ws-nudge__close");
    if (closeBtn) {
      closeBtn.addEventListener("click", function () {
        recordDismissal(nudgeId);
        try { el.parentNode && el.parentNode.removeChild(el); } catch (_) {}
        // Use sendBeacon for reliability (user may navigate away after dismiss)
        sendNudgeEvent("dismissed", nudgeId, true, copyVariant);
      });
    }

    return el;
  }

  // ---------------------------------------------------------------------------
  // DOM insertion
  // ---------------------------------------------------------------------------
  function insertNudge(el) {
    var anchors = [
      document.querySelector("form[action*='/cart/add']"),
      document.querySelector(".product-form"),
      document.querySelector("#product-form"),
      document.querySelector(".product__title"),
      document.querySelector(".product-single__title"),
      document.querySelector("h1"),
    ];
    for (var i = 0; i < anchors.length; i++) {
      var anchor = anchors[i];
      if (anchor && anchor.parentNode) {
        anchor.parentNode.insertBefore(el, anchor.nextSibling);
        return;
      }
    }
    if (document.body && document.body.firstChild) {
      document.body.insertBefore(el, document.body.firstChild);
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  //
  // copyVariant is a first-class parameter — it flows from the server
  // response through renderNudge → buildNudgeElement → dismiss handler,
  // and is included in both "shown" and "dismissed" measurement events.
  // ---------------------------------------------------------------------------
  function renderNudge(nudgeId, copyConfig, copyVariant) {
    // Dismissal check
    var dismissed = getDismissedNudgeId();
    if (dismissed && dismissed === String(nudgeId)) {
      return;
    }
    // Idempotency
    if (document.querySelector("[data-wishspark-nudge]")) {
      return;
    }

    injectStyles();
    var el = buildNudgeElement(nudgeId, copyConfig, copyVariant);
    insertNudge(el);

    // Send "shown" measurement event with assigned variant.
    // SessionStorage dedup prevents duplicate sends on page refresh.
    sendNudgeEvent("shown", nudgeId, false, copyVariant);
  }

  // ---------------------------------------------------------------------------
  // API fetch, holdout check, and conditional render
  //
  // v5 eligibility decision tree:
  //
  //   data.active === false          → no nudge, do nothing
  //   data.eligible === false        → behavioral gate suppressed, do nothing
  //   data.render_allowed === false  → holdout suppressed, do nothing (v5 addition)
  //                                    (server already recorded holdout_assigned event)
  //   all pass                       → render nudge
  //
  // Backward compatibility:
  //   - render_allowed absent → treated as render_allowed = true (old server compat)
  //   - eligible absent       → treated as eligible = true (pre-gating compat)
  //
  // data.copy_variant — the server-assigned variant name.
  //   Threaded into renderNudge() and from there into all measurement events.
  //   This is the v4 addition: copy_variant was previously ignored here.
  //
  // data.ab_experiment — present when nudge has copy_variants.
  //   Not used by the client (it just renders whatever copy_config it got),
  //   but useful for debugging.
  //
  // data.holdout — present and true when visitor is in holdout group.
  //   Not used by the client beyond suppression; included for debugging only.
  // ---------------------------------------------------------------------------
  function fetchAndRender() {
    var params = (
      "shop="         + encodeURIComponent(SHOP_DOMAIN) +
      "&product_url=" + encodeURIComponent(productUrl)
    );
    if (visitorId) {
      params += "&visitor_id=" + encodeURIComponent(visitorId);
    }

    var url = API_HOST + "/nudges/active?" + params;

    try {
      fetch(url, {
        method:      "GET",
        credentials: "omit",
        headers:     { "Accept": "application/json" },
      })
        .then(function (res) {
          if (!res.ok) return null;
          return res.json();
        })
        .then(function (data) {
          if (!data || !data.active) {
            // No active nudge for this product.
            return;
          }

          // Behavioral eligibility gate — undefined on old server responses = eligible
          var eligible = (data.eligible === undefined) ? true : data.eligible;
          if (!eligible) {
            // Visitor did not pass behavioral quality threshold.
            return;
          }

          // Holdout suppression (v5) — render_allowed absent on old server = allowed
          // When render_allowed is explicitly false, this visitor is in the control
          // group.  The server has already recorded a holdout_assigned measurement
          // event.  The client must not render and must not send any event.
          var renderAllowed = (data.render_allowed === undefined) ? true : data.render_allowed;
          if (!renderAllowed) {
            // Visitor is in holdout group — silently suppress.
            // No client-side event needed: server recorded holdout_assigned.
            return;
          }

          // Pass the server-assigned copy_variant through to rendering and measurement.
          renderNudge(
            data.nudge_id,
            data.copy_config   || {},
            data.copy_variant  || ""
          );
        })
        .catch(function () {});
    } catch (_) {}
  }

  // ---------------------------------------------------------------------------
  // Entry point
  // ---------------------------------------------------------------------------
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fetchAndRender);
  } else {
    fetchAndRender();
  }

})();
