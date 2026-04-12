"""
consent_banner.py — Serve a drop-in consent banner script for merchants.

GET /consent-banner.js  → serves a self-contained JS snippet that merchants
can include via a <script> tag in their Shopify theme. The snippet renders
a minimal, accessible cookie consent banner and wires it to
window.hsSetConsent() (provided by spark-tracker.js).

The banner stores the visitor's choice in localStorage so it only shows
once. It respects GPC/DNT — if the browser signals opt-out, the banner
doesn't appear and consent is denied automatically.

This is NOT a full CMP — it's a lightweight default for merchants who don't
have their own consent management platform. Merchants with OneTrust,
Cookiebot, etc. should call window.hsSetConsent() from their CMP instead.
"""
from __future__ import annotations

from fastapi import APIRouter
from starlette.responses import Response

router = APIRouter(tags=["consent"])

_CONSENT_BANNER_JS = r"""
(function() {
  'use strict';

  // Already decided — don't show again
  var stored = localStorage.getItem('hs_consent');
  if (stored === '1' || stored === '0') {
    if (typeof window.hsSetConsent === 'function') {
      window.hsSetConsent(stored === '1', detectRegion());
    }
    return;
  }

  // Browser-level opt-out — respect silently, no banner needed
  if (navigator.globalPrivacyControl === true ||
      navigator.doNotTrack === '1' ||
      window.doNotTrack === '1') {
    localStorage.setItem('hs_consent', '0');
    if (typeof window.hsSetConsent === 'function') {
      window.hsSetConsent(false, detectRegion());
    }
    return;
  }

  function detectRegion() {
    try {
      var tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
      if (tz.indexOf('Europe') === 0) return 'EU';
      if (tz.indexOf('America/Los_Angeles') === 0 ||
          tz.indexOf('America/San_Francisco') === 0) return 'US-CA';
      if (tz.indexOf('America') === 0) return 'US';
      if (tz.indexOf('Asia/Sao_Paulo') === 0 ||
          tz.indexOf('America/Sao_Paulo') === 0) return 'BR';
    } catch(e) {}
    return 'unknown';
  }

  function onChoice(given) {
    localStorage.setItem('hs_consent', given ? '1' : '0');
    if (typeof window.hsSetConsent === 'function') {
      window.hsSetConsent(given, detectRegion());
    }
    var el = document.getElementById('hs-consent-banner');
    if (el) el.remove();
  }

  // Inject banner after DOM ready
  function render() {
    var banner = document.createElement('div');
    banner.id = 'hs-consent-banner';
    banner.setAttribute('role', 'dialog');
    banner.setAttribute('aria-label', 'Cookie consent');
    banner.innerHTML = [
      '<div style="position:fixed;bottom:0;left:0;right:0;z-index:999999;',
      'background:rgba(8,8,17,0.95);border-top:1px solid rgba(255,255,255,0.08);',
      'padding:16px 24px;display:flex;align-items:center;justify-content:space-between;',
      'flex-wrap:wrap;gap:12px;font-family:-apple-system,BlinkMacSystemFont,sans-serif;',
      'font-size:13px;color:#c8c8d0;backdrop-filter:blur(12px)">',
      '<p style="margin:0;flex:1;min-width:200px;line-height:1.5">',
      'This store uses analytics cookies to improve your shopping experience. ',
      '<a href="/privacy-policy" target="_blank" style="color:#a78bfa;text-decoration:underline">Privacy Policy</a>',
      '</p>',
      '<div style="display:flex;gap:8px;flex-shrink:0">',
      '<button id="hs-consent-deny" style="padding:8px 18px;border-radius:8px;',
      'border:1px solid rgba(255,255,255,0.12);background:transparent;color:#94a3b8;',
      'cursor:pointer;font-size:13px;font-weight:500">Decline</button>',
      '<button id="hs-consent-accept" style="padding:8px 18px;border-radius:8px;',
      'border:none;background:#e8a04e;color:#080811;cursor:pointer;font-size:13px;',
      'font-weight:600">Accept</button>',
      '</div></div>'
    ].join('');
    document.body.appendChild(banner);

    document.getElementById('hs-consent-accept').addEventListener('click', function() {
      onChoice(true);
    });
    document.getElementById('hs-consent-deny').addEventListener('click', function() {
      onChoice(false);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', render);
  } else {
    render();
  }
})();
""".strip()


@router.get("/consent-banner.js")
def consent_banner_script():
    """Serve the consent banner script.

    Merchants add this to their Shopify theme:
        <script src="https://api.hedgesparkhq.com/consent-banner.js" defer></script>

    The script must load AFTER spark-tracker.js so that window.hsSetConsent
    is available. Using `defer` ensures correct ordering.
    """
    return Response(
        content=_CONSENT_BANNER_JS,
        media_type="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "public, max-age=86400, stale-while-revalidate=3600",
            "Access-Control-Allow-Origin": "*",
        },
    )
