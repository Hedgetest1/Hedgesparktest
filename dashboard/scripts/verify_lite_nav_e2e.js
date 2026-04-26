#!/usr/bin/env node
/**
 * verify_lite_nav_e2e.js — real-data E2E verification for Lite-floor
 * sidebar/scroll-spy parity. Renders /app/lite as a Pro merchant
 * (hedgespark-dev) with intercepted API calls routed to localhost,
 * scrolls to every `section-lite-*` anchor, asserts the sidebar
 * highlight matches the expected NAV_ITEMS_LITE label.
 *
 * Why this exists
 * ===============
 * `audit_lite_nav_section_parity.py` (preflight) catches the SOURCE
 * mismatch class at commit time. This script catches the RUNTIME
 * class — IntersectionObserver wiring, sticky/scroll behaviors,
 * SECTION_TO_NAV resolution, React re-render timing — that source
 * grep can't see.
 *
 * Born 2026-04-26 after the founder caught the sidebar "going back
 * to LITE" while scrolling past `lite-refunds` and `lite-audience`.
 * Initial close declared "synthetic test = source parity audit"
 * which only covers the source layer. Founder pushed to 10/10:
 * "investigate why cold-state, fix the harness, prove with real
 * data". CORS Allow-Origin: * + credentials:include was the
 * blocker — fixed by sending specific origin + Allow-Credentials:
 * true and handling OPTIONS preflight.
 *
 * Requirements
 * ============
 * - Backend running at 127.0.0.1:8000 (PM2 wishspark-backend)
 * - Dashboard running at 127.0.0.1:3000 (PM2 wishspark-dashboard)
 * - Test merchant `hedgespark-dev.myshopify.com` exists with Pro plan
 * - .env file at /opt/wishspark/backend/.env (for MERCHANT_SESSION_SECRET)
 *
 * Usage
 * =====
 *   node /opt/wishspark/dashboard/scripts/verify_lite_nav_e2e.js
 *
 * Exit codes: 0 PASS, 1 nav mismatch, 2 cold-state (auth flow broken),
 *             3 setup error (token forge failed)
 */
'use strict';

process.chdir('/opt/wishspark/dashboard');
const { chromium } = require('playwright');
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const ORIGIN = 'http://127.0.0.1:3000';
const API_BACKEND = 'http://127.0.0.1:8000';
const API_REMOTE = 'https://api.hedgesparkhq.com';
const TEST_SHOP = 'hedgespark-dev.myshopify.com';

// ── (1) Forge a session token via the backend's create_session_token ──
function forgeSessionToken() {
  const py = '/opt/wishspark/backend/venv/bin/python';
  const cmd = `set -a && source /opt/wishspark/backend/.env && set +a && ${py} -c "
import sys
sys.path.insert(0, '/opt/wishspark/backend')
from app.core.merchant_session import create_session_token
print(create_session_token('${TEST_SHOP}'))
"`;
  try {
    return execSync(cmd, { shell: '/bin/bash', encoding: 'utf8' }).trim();
  } catch (e) {
    console.error('Failed to forge session token:', e.message);
    process.exit(3);
  }
}

// ── (2) Lite-floor section → NAV_ITEMS_LITE label expected ──
const EXPECTED = {
  'section-lite-rars':        'Revenue at risk',
  'section-lite-today':       'Today',
  'section-lite-last7':       'Last 7 days',
  'section-lite-peers':       'You vs peers',
  'section-lite-pnl':         'Profit',
  'section-lite-attribution': 'Attribution',
  'section-lite-retention':   'Retention',
  'section-lite-refunds':     'Refunds',
  'section-lite-audience':    'Audience',
  'section-lite-signals':     'Signals',
};

(async () => {
  const sessionToken = forgeSessionToken();
  const OUT = '/tmp/lite_nav_e2e';
  fs.mkdirSync(OUT, { recursive: true });

  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addCookies([{
    name: 'hs_session', value: sessionToken,
    domain: '127.0.0.1', path: '/', httpOnly: true, secure: false, sameSite: 'Lax',
  }]);

  // ── (3) Route api.hedgesparkhq.com → 127.0.0.1:8000 with proper CORS ──
  await ctx.route('**/*', async (route, request) => {
    const url = request.url();
    if (!url.startsWith(API_REMOTE)) return route.continue();

    if (request.method() === 'OPTIONS') {
      return route.fulfill({
        status: 204,
        headers: {
          'access-control-allow-origin': ORIGIN,
          'access-control-allow-credentials': 'true',
          'access-control-allow-methods': 'GET, POST, PUT, DELETE, PATCH, OPTIONS',
          'access-control-allow-headers': 'content-type, authorization, x-request-id, x-api-key',
          'access-control-max-age': '600',
        },
        body: '',
      });
    }
    const newUrl = url.replace(API_REMOTE, API_BACKEND);
    try {
      const resp = await ctx.request.fetch(newUrl, {
        method: request.method(),
        headers: { ...request.headers(), cookie: `hs_session=${sessionToken}`, origin: ORIGIN },
        data: request.postData(),
      });
      const respHeaders = { ...resp.headers() };
      respHeaders['access-control-allow-origin'] = ORIGIN;
      respHeaders['access-control-allow-credentials'] = 'true';
      await route.fulfill({
        status: resp.status(),
        headers: respHeaders,
        body: await resp.body(),
      });
    } catch {
      route.abort();
    }
  });

  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/app/lite`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(() => {
    const sections = document.querySelectorAll('[id^="section-lite-"]');
    const cold = document.body.innerText.includes('Looking for your store') ||
                 document.body.innerText.includes('No store connected');
    return sections.length > 0 || cold;
  }, { timeout: 25000 }).catch(() => null);
  await page.waitForTimeout(2500);

  const state = await page.evaluate(() => ({
    tier: document.querySelector('[data-tier-resolved]')?.getAttribute('data-tier-resolved'),
    cold: document.body.innerText.includes('Looking for your store') ||
          document.body.innerText.includes('Connecting to your store'),
    sections: Array.from(document.querySelectorAll('[id^="section-lite-"]')).map(el => el.id),
  }));

  if (state.cold || state.sections.length === 0) {
    console.error('FAIL: cold-state. tier=' + state.tier + ', sections=' + state.sections.length);
    await page.screenshot({ path: `${OUT}/cold_state.png`, fullPage: false });
    await browser.close();
    process.exit(2);
  }

  // ── (4) Scroll to each section, check sidebar nav active state ──
  const expectedSections = Object.keys(EXPECTED);
  const results = [];
  for (const sid of expectedSections) {
    const exists = await page.evaluate((id) => !!document.getElementById(id), sid);
    if (!exists) { results.push({ section: sid, exists: false, activeNav: null }); continue; }
    await page.evaluate((id) => {
      const el = document.getElementById(id);
      const main = document.querySelector('main');
      if (el && main) main.scrollTo({ top: el.offsetTop - 100, behavior: 'instant' });
    }, sid);
    await page.waitForTimeout(900);
    const activeNav = await page.evaluate(() => {
      const buttons = Array.from(document.querySelectorAll('aside nav button'));
      const active = buttons.find(b =>
        b.className.includes('bg-[#d4893a]/15') ||
        (b.className.includes('text-[#e8a04e]') && b.className.includes('shadow-'))
      );
      return active ? { label: active.textContent.trim().replace(/\s+/g, ' ').slice(0, 40) } : null;
    });
    await page.screenshot({ path: `${OUT}/${sid}.png`, fullPage: false });
    results.push({ section: sid, exists: true, activeNav });
  }

  fs.writeFileSync(`${OUT}/results.json`, JSON.stringify({ state, results }, null, 2));
  await browser.close();

  // ── (5) Assert ──
  const failures = results.filter(r => {
    if (!r.exists) return true;
    if (!r.activeNav) return true;
    return !r.activeNav.label.includes(EXPECTED[r.section]);
  });

  console.log(`Lite nav E2E: ${results.length - failures.length}/${results.length} sections OK`);
  for (const r of results) {
    const status = r.exists && r.activeNav && r.activeNav.label.includes(EXPECTED[r.section]) ? '✓' : '✗';
    console.log(`  ${status} ${r.section} → ${r.activeNav?.label || 'NONE'} (expected ${EXPECTED[r.section]})`);
  }

  if (failures.length === 0) {
    console.log('PASS — every Lite section produces the correct sidebar highlight.');
    console.log(`Evidence: ${OUT}/`);
    process.exit(0);
  }
  console.error(`FAIL — ${failures.length} sections produced wrong/missing highlight. Evidence: ${OUT}/results.json`);
  process.exit(1);
})();
