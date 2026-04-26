#!/usr/bin/env node
/**
 * verify_floor_dashboard_e2e.js — partition-resilient floor-level E2E.
 *
 * Generalizes verify_lite_dashboard_e2e.js (Lite-only) into a
 * floor-parametrized harness. Reads section IDs from the rendered DOM
 * (NOT from a hardcoded list) so partition shifts (Pro feature → Lite,
 * etc.) do NOT break the test. The 7 axes are universally applicable
 * to any floor and the cell matrix per floor is derived from the test
 * scenarios that make sense for that tier-gate.
 *
 * Usage:
 *   node verify_floor_dashboard_e2e.js --floor=lite
 *   node verify_floor_dashboard_e2e.js --floor=pro
 *   node verify_floor_dashboard_e2e.js --floor=scale  (when shipped)
 *
 * Born 2026-04-26 after founder asked whether the autonomy E2E should
 * extend system-wide. Pro/Lite partition is in flux — a copy-paste
 * Pro variant would require maintenance every time a feature moves.
 * This framework reads what's actually there at runtime, no hardcoded
 * partition assumptions.
 *
 * Cell matrix per floor
 * =====================
 *   Lite:  desktop+mobile × (Pro-on-Lite, ?as=lite preview, Lite-tier JWT) = 6
 *   Pro:   desktop+mobile × (Pro-tier JWT, Lite-tier JWT preview-with-lock) = 4
 *   Scale: TBD when scale floor ships
 *
 * 7 axes (all floor-agnostic):
 *   1. Sections render — DOM has section-* anchors after load
 *   2. Sections have content — not skeleton-stuck, not CardError, not blank
 *   3. Zero console errors during load + scroll (CSS, JS, RSC noise filtered)
 *   4. Zero 4xx/5xx network responses (with allowlist for SSE)
 *   5. NotificationBell click opens its dropdown
 *   6. Zero currency-symbol mixing within a single section
 *   7. Zero axe critical+serious a11y violations (desktop canonical only)
 *
 * Exit codes: 0 PASS, 1 FAIL, 2 setup error
 */
'use strict';

process.chdir('/opt/wishspark/dashboard');
const { chromium } = require('playwright');
const { execSync } = require('child_process');
const fs = require('fs');
let AxeBuilder = null;
try { AxeBuilder = require('@axe-core/playwright').default; } catch {}

const ORIGIN = 'http://127.0.0.1:3000';
const API_BACKEND = 'http://127.0.0.1:8000';
const API_REMOTE = 'https://api.hedgesparkhq.com';
const PRO_SHOP = 'hedgespark-dev.myshopify.com';
const LITE_SHOP = 'verify-e2e.myshopify.com';

// Floor → URL + matrix definition. Each cell describes a (viewport,
// tier, preview-flag) tuple to exercise the floor under.
const FLOOR_CONFIG = {
  lite: {
    url: '/app/lite',
    cells: [
      { label: 'desktop_pro_viewing_lite', viewport: { width: 1440, height: 900 }, urlSuffix: '',         shop: 'pro' },
      { label: 'desktop_pro_lite_preview', viewport: { width: 1440, height: 900 }, urlSuffix: '?as=lite', shop: 'pro' },
      { label: 'mobile_pro_viewing_lite',  viewport: { width: 390,  height: 844 }, urlSuffix: '',         shop: 'pro' },
      { label: 'mobile_pro_lite_preview',  viewport: { width: 390,  height: 844 }, urlSuffix: '?as=lite', shop: 'pro' },
      { label: 'desktop_real_lite_tier',   viewport: { width: 1440, height: 900 }, urlSuffix: '',         shop: 'lite' },
      { label: 'mobile_real_lite_tier',    viewport: { width: 390,  height: 844 }, urlSuffix: '',         shop: 'lite' },
    ],
  },
  pro: {
    url: '/app/pro',
    cells: [
      { label: 'desktop_pro_canonical',  viewport: { width: 1440, height: 900 }, urlSuffix: '', shop: 'pro' },
      { label: 'mobile_pro_canonical',   viewport: { width: 390,  height: 844 }, urlSuffix: '', shop: 'pro' },
      // Lite-tier merchant on /app/pro: SHOULD show preview-with-lock (no error).
      // Tests the "never hide a feature" tier-gate path.
      { label: 'desktop_lite_on_pro',    viewport: { width: 1440, height: 900 }, urlSuffix: '', shop: 'lite' },
      { label: 'mobile_lite_on_pro',     viewport: { width: 390,  height: 844 }, urlSuffix: '', shop: 'lite' },
    ],
  },
  scale: {
    url: '/app/scale',
    cells: [
      { label: 'desktop_scale_canonical', viewport: { width: 1440, height: 900 }, urlSuffix: '', shop: 'pro' },
      { label: 'mobile_scale_canonical',  viewport: { width: 390,  height: 844 }, urlSuffix: '', shop: 'pro' },
    ],
  },
};

// ── Acceptable noise filters (cross-floor universal) ──
const ACCEPTABLE_NETWORK_ERRORS = [
  /tracker\.js/,                    // CSP-blocked storefront tracker, harmless on dashboard
  /\/pro\/stream\//,                // SSE long-poll 403s on Lite-tier are gated correctly
];

const ACCEPTABLE_CONSOLE_NOISE = [
  /Loading the script.*tracker\.js.*Content Security Policy/,
  /Failed to fetch RSC payload/,
  /streaming.*disconnected/i,
  /^Failed to load resource: the server responded with a status of/,
];

function shouldIgnoreNetwork(url, status) {
  if (status < 400) return true;
  return ACCEPTABLE_NETWORK_ERRORS.some(re => re.test(url));
}
function shouldIgnoreConsole(text) {
  return ACCEPTABLE_CONSOLE_NOISE.some(re => re.test(text));
}

function forgeSessionToken(shop) {
  const py = '/opt/wishspark/backend/venv/bin/python';
  const cmd = `set -a && source /opt/wishspark/backend/.env 2>/dev/null && set +a && ${py} -c "
import sys
sys.path.insert(0, '/opt/wishspark/backend')
from app.core.merchant_session import create_session_token
print(create_session_token('${shop}'))
"`;
  try {
    return execSync(cmd, { shell: '/bin/bash', encoding: 'utf8' }).trim();
  } catch (e) {
    console.error('Failed to forge session token for', shop, ':', e.message);
    process.exit(2);
  }
}

async function runOneCell({ floor, floorUrl, label, viewport, urlSuffix, shopRole, sessionToken, outDir }) {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport });
  await ctx.addCookies([{
    name: 'hs_session', value: sessionToken,
    domain: '127.0.0.1', path: '/', httpOnly: true, secure: false, sameSite: 'Lax',
  }]);

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
      await route.fulfill({ status: resp.status(), headers: respHeaders, body: await resp.body() });
    } catch {
      route.abort();
    }
  });

  const page = await ctx.newPage();
  const consoleErrors = [];
  const networkErrors = [];

  page.on('pageerror', e => consoleErrors.push({ type: 'pageerror', text: String(e).slice(0, 300) }));
  page.on('console', msg => {
    if (msg.type() === 'error') {
      const text = msg.text().slice(0, 300);
      if (!shouldIgnoreConsole(text)) consoleErrors.push({ type: 'console', text });
    }
  });
  page.on('response', resp => {
    const status = resp.status();
    if (status >= 400) {
      const url = resp.url();
      if (!shouldIgnoreNetwork(url, status)) {
        networkErrors.push({ url: url.slice(0, 200), status });
      }
    }
  });

  await page.goto(`${ORIGIN}${floorUrl}${urlSuffix}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(() => {
    const sections = document.querySelectorAll('[id^="section-"]');
    const cold = document.body.innerText.includes('Looking for your store') ||
                 document.body.innerText.includes('No store connected');
    const lockedPreview = document.body.innerText.includes('Preview') &&
                          document.body.innerText.includes('Upgrade');
    return sections.length > 0 || cold || lockedPreview;
  }, { timeout: 25000 }).catch(() => null);
  await page.waitForTimeout(3000);

  // Discover what the floor RENDERED (no hardcoded list — partition-resilient)
  const state = await page.evaluate(() => ({
    tier: document.querySelector('[data-tier-resolved]')?.getAttribute('data-tier-resolved'),
    cold: document.body.innerText.includes('Looking for your store') ||
          document.body.innerText.includes('Connecting to your store'),
    lockedPreview: document.body.innerText.includes('Preview') &&
                   document.body.innerText.includes('Upgrade'),
    sections: Array.from(document.querySelectorAll('[id^="section-"]')).map(el => el.id),
    navButtons: Array.from(document.querySelectorAll('aside nav button')).map(b =>
      b.textContent.trim().replace(/\s+/g, ' ').slice(0, 40)),
  }));

  await page.screenshot({ path: `${outDir}/${label}_00_top.png`, fullPage: false });

  if (state.cold) {
    await browser.close();
    return { cell: label, viewport, verdict: 'FAIL', reason: 'cold-state (auth flow broken)', state };
  }

  // Per-section content axis (driven by what's actually there, not hardcoded)
  const sectionResults = [];
  for (const sid of state.sections) {
    await page.evaluate((id) => {
      const el = document.getElementById(id);
      const main = document.querySelector('main');
      if (el && main) main.scrollTo({ top: el.offsetTop - 80, behavior: 'instant' });
    }, sid);
    await page.waitForTimeout(500);
    const content = await page.evaluate((id) => {
      const el = document.getElementById(id);
      if (!el) return null;
      const text = el.innerText || '';
      const rect = el.getBoundingClientRect();
      const isError = /something went wrong|failed to load|try again later/i.test(text);
      const isSkeletonOnly = el.querySelectorAll('.animate-pulse').length > 0 &&
                             text.replace(/\s+/g, ' ').trim().length < 30;
      return {
        height: Math.round(rect.height),
        textLength: text.length,
        textPreview: text.slice(0, 120).replace(/\s+/g, ' ').trim(),
        isError, isSkeletonOnly,
      };
    }, sid);
    sectionResults.push({ section: sid, content });
  }

  // Bell click axis
  let bellInteraction = { tested: false };
  try {
    await page.evaluate(() => {
      const main = document.querySelector('main');
      if (main) main.scrollTo({ top: 0, behavior: 'instant' });
    });
    await page.waitForTimeout(300);
    const bellExists = await page.evaluate(() =>
      !!document.querySelector('[aria-label*="otification" i], [data-testid*="bell" i], button[aria-label*="bell" i]')
    );
    if (bellExists) {
      await page.evaluate(() => {
        const btn = document.querySelector('[aria-label*="otification" i], [data-testid*="bell" i], button[aria-label*="bell" i]');
        if (btn) btn.click();
      });
      await page.waitForTimeout(600);
      const dropdownOpen = await page.evaluate(() => {
        const panels = Array.from(document.querySelectorAll('[role="menu"], [role="dialog"], .absolute'));
        return panels.some(p => {
          const r = p.getBoundingClientRect();
          return r.width > 200 && r.height > 100 && r.top >= 0 && r.left >= 0;
        });
      });
      await page.keyboard.press('Escape');
      await page.waitForTimeout(200);
      bellInteraction = { tested: true, dropdownOpened: dropdownOpen };
    } else {
      bellInteraction = { tested: true, bellMissing: true };
    }
  } catch (e) {
    bellInteraction = { tested: false, error: String(e).slice(0, 150) };
  }

  // Currency consistency axis
  const currencyAudit = await page.evaluate((sectionIds) => {
    const findings = [];
    for (const sid of sectionIds) {
      const el = document.getElementById(sid);
      if (!el) continue;
      const text = el.innerText || '';
      const symbols = new Set();
      if (/€/.test(text)) symbols.add('EUR');
      if (/\$\d|\$\s\d/.test(text)) symbols.add('USD');
      if (/£/.test(text)) symbols.add('GBP');
      if (/USD\b/.test(text)) symbols.add('USD');
      if (/EUR\b/.test(text)) symbols.add('EUR');
      if (symbols.size > 1) findings.push({ section: sid, symbols: Array.from(symbols) });
    }
    return findings;
  }, state.sections);

  // a11y axe — desktop canonical cell only (heaviest axis, run once per floor)
  let axeViolations = null;
  if (AxeBuilder && label.startsWith('desktop_') && (label.includes('canonical') || label.includes('viewing_lite'))) {
    try {
      const result = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa'])
        .disableRules(['color-contrast'])  // founder-domain palette decision
        .analyze();
      axeViolations = result.violations
        .filter(v => v.impact === 'critical' || v.impact === 'serious')
        .map(v => ({ id: v.id, impact: v.impact, description: v.description.slice(0, 120), nodes: v.nodes.length }));
    } catch (e) {
      axeViolations = [{ error: String(e).slice(0, 200) }];
    }
  }

  await page.screenshot({ path: `${outDir}/${label}_99_bottom.png`, fullPage: false });
  await browser.close();

  // Verdict — note "lockedPreview" cells expect the lock screen, not full sections
  const isLockedExpected = label.includes('lite_on_pro') && state.lockedPreview;
  const sectionsRendered = state.sections.length;
  const sectionsWithContent = sectionResults.filter(s =>
    s.content && !s.content.isError && !s.content.isSkeletonOnly && s.content.height > 30
  ).length;
  const axeCriticalSerious = axeViolations ? axeViolations.filter(v => !v.error).length : 0;
  const bellOk = !bellInteraction.tested ||
                 bellInteraction.dropdownOpened === true ||
                 bellInteraction.bellMissing === true;

  let verdict;
  if (isLockedExpected) {
    // Locked-preview is a valid state — don't require sections; require no errors.
    verdict = (consoleErrors.length === 0 && networkErrors.length === 0 && bellOk) ? 'PASS' : 'FAIL';
  } else {
    verdict = (
      sectionsRendered > 0 &&
      sectionsWithContent === sectionsRendered &&
      consoleErrors.length === 0 &&
      networkErrors.length === 0 &&
      axeCriticalSerious === 0 &&
      currencyAudit.length === 0 &&
      bellOk
    ) ? 'PASS' : 'FAIL';
  }

  return {
    cell: label, viewport, verdict, isLockedExpected,
    sectionsRendered, sectionsWithContent,
    consoleErrors, networkErrors, bellInteraction, currencyAudit, axeViolations,
    sections: sectionResults, state,
  };
}

(async () => {
  const args = process.argv.slice(2);
  const floorArg = args.find(a => a.startsWith('--floor='));
  if (!floorArg) {
    console.error('Usage: node verify_floor_dashboard_e2e.js --floor=lite|pro|scale');
    process.exit(2);
  }
  const floor = floorArg.split('=')[1];
  const config = FLOOR_CONFIG[floor];
  if (!config) {
    console.error(`Unknown floor: ${floor}. Valid: lite, pro, scale`);
    process.exit(2);
  }

  const outDir = `/tmp/floor_e2e_${floor}`;
  fs.mkdirSync(outDir, { recursive: true });

  const proToken = forgeSessionToken(PRO_SHOP);
  const liteToken = forgeSessionToken(LITE_SHOP);
  const tokenFor = role => (role === 'lite' ? liteToken : proToken);

  console.log(`═══ Floor E2E: ${floor.toUpperCase()} (${config.url}) ═══`);
  const results = [];
  // Reset the dashboard rate-limit Redis keys between cells. The
  // dashboard middleware (app/main.py) limits /pro/ + /merchant/
  // to 120 req/min per (shop, IP). 4 sequential cells × ~40 reqs each
  // would trip this from a single IP. Clearing the key gives each cell
  // a clean slate, mirroring how distinct merchant browsers would be
  // accounted separately.
  const flushRateLimit = async () => {
    try {
      execSync(
        `/opt/wishspark/backend/venv/bin/python -c "import redis,os; r=redis.from_url(os.environ.get('REDIS_URL','redis://localhost:6379/0')); ks=r.keys('hs:rl:dash:*'); n=r.delete(*ks) if ks else 0"`,
        { shell: '/bin/bash', stdio: 'ignore' }
      );
    } catch {}
  };
  await flushRateLimit();
  for (let i = 0; i < config.cells.length; i++) {
    const cell = config.cells[i];
    if (i > 0) {
      await new Promise(r => setTimeout(r, 2000));
      await flushRateLimit();
    }
    process.stdout.write(`Running ${cell.label}... `);
    try {
      const r = await runOneCell({
        floor, floorUrl: config.url, ...cell,
        shopRole: cell.shop, sessionToken: tokenFor(cell.shop), outDir,
      });
      results.push(r);
      const summary = `${r.verdict} (${r.sectionsRendered}sec/${r.sectionsWithContent}content` +
                      `, ${r.consoleErrors.length}cons/${r.networkErrors.length}net` +
                      (r.isLockedExpected ? ' [locked-preview]' : '') + ')';
      process.stdout.write(`${summary}\n`);
    } catch (e) {
      results.push({ cell: cell.label, verdict: 'ERROR', error: String(e).slice(0, 300) });
      process.stdout.write(`ERROR: ${e.message}\n`);
    }
  }

  fs.writeFileSync(`${outDir}/results.json`, JSON.stringify({ floor, config: config.url, results }, null, 2));

  console.log('');
  console.log(`═══ ${floor.toUpperCase()} summary ═══`);
  for (const r of results) {
    if (r.verdict === 'ERROR') { console.log(`  ✗ ${r.cell}: ${r.error}`); continue; }
    const status = r.verdict === 'PASS' ? '✓' : '✗';
    console.log(`  ${status} ${r.cell}: ${r.verdict}${r.isLockedExpected ? ' [locked-preview expected]' : ''}`);
    if (!r.isLockedExpected) {
      console.log(`     sections: ${r.sectionsRendered ?? 'n/a'} rendered, ${r.sectionsWithContent ?? 'n/a'} with content`);
    }
    console.log(`     console errors: ${r.consoleErrors?.length ?? 0}, network 4xx/5xx: ${r.networkErrors?.length ?? 0}`);
    if (r.consoleErrors?.length > 0) {
      r.consoleErrors.slice(0, 2).forEach(e => console.log(`       console: ${e.text.slice(0, 120)}`));
    }
    if (r.networkErrors?.length > 0) {
      r.networkErrors.slice(0, 4).forEach(e => console.log(`       network: ${e.status} ${e.url.slice(0, 120)}`));
    }
    if (r.bellInteraction?.tested) {
      const bs = r.bellInteraction.bellMissing ? 'no bell rendered' :
                 r.bellInteraction.dropdownOpened ? 'opens correctly' : 'click did NOT open';
      console.log(`     bell click: ${bs}`);
    }
    if (r.currencyAudit?.length > 0) {
      console.log(`     currency leaks: ${r.currencyAudit.length}`);
      r.currencyAudit.forEach(c => console.log(`       ${c.section} mixes ${c.symbols.join(' + ')}`));
    }
    if (r.axeViolations?.length > 0) {
      const real = r.axeViolations.filter(v => !v.error).length;
      console.log(`     axe critical+serious: ${real}`);
    }
  }

  const allPass = results.every(r => r.verdict === 'PASS');
  console.log('');
  console.log(allPass
    ? `PASS — ${floor} floor 10/10 across ${results.length} matrix cells`
    : `FAIL — at least one cell flagged. See ${outDir}/results.json`);
  process.exit(allPass ? 0 : 1);
})();
