// Multi-floor scroll-spy regression probe.
//
// Born 2026-04-30 after founder caught a sidebar highlight bug on
// /app/pro where 5+ sections showed activeNav="null" while scrolling
// (the IntersectionObserver-based scroll-spy missed Pro-distinct
// anchors that had no NAV_ITEMS_PRO entries). This smoke test
// scrolls every section on Lite + Pro top-to-bottom and asserts
// every probe lands on a non-null nav slot. Runtime: ~30s for both
// floors. Pre-merchant cost: 0 (dev/CI only). Post-merchant cost:
// 0 (no production calls).
//
// Usage:
//   1. Backend + dashboard running (pm2 status).
//   2. HS_SESSION env var = a Pro-tier session token. Forge with:
//      python -c "from app.core.merchant_session import \
//        create_session_token; \
//        print(create_session_token('hedgespark-dev.myshopify.com'))"
//   3. node dashboard/scripts/scrollspy_smoke.js
//
// Exits 0 = all nav slots lit; 1 = at least one null; 2 = env
// missing (treated as skip in CI).
const { chromium } = require('playwright');

const ROUTES = [
  { path: '/app/lite', label: 'Lite' },
  { path: '/app/pro',  label: 'Pro' },
];

async function probe(page, route) {
  await page.goto(`http://127.0.0.1:3000${route.path}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(5000);

  const inventory = await page.evaluate(() => {
    const sections = Array.from(document.querySelectorAll("main [id^='section-']")).map(s => ({
      id: s.id,
      top: s.offsetTop,
    })).sort((a, b) => a.top - b.top);
    const navs = Array.from(document.querySelectorAll('aside nav a, aside nav button')).map(b => ({
      label: (b.textContent || '').trim().slice(0, 40),
      kind: b.tagName,
      href: b.getAttribute('href') || null,
    }));
    const main = document.querySelector('main');
    return { sections, navs, mainScrollHeight: main?.scrollHeight, mainClientHeight: main?.clientHeight };
  });

  const results = [];
  for (const sec of inventory.sections) {
    await page.evaluate((y) => {
      document.querySelector('main').scrollTo({ top: y, behavior: 'instant' });
    }, sec.top - 50);
    await page.waitForTimeout(700);
    const active = await page.evaluate(() => {
      const a = document.querySelector('aside nav a.bg-\\[\\#d4893a\\]\\/15, aside nav button.bg-\\[\\#d4893a\\]\\/15');
      return a ? (a.textContent || '').trim().slice(0, 40) : null;
    });
    results.push({ section: sec.id, top: sec.top, activeNav: active });
  }
  return { route, inventory, results };
}

(async () => {
  const sessionToken = process.env.HS_SESSION;
  if (!sessionToken) { console.error('HS_SESSION required'); process.exit(2); }
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addCookies([{
    name: 'hs_session', value: sessionToken,
    domain: '127.0.0.1', path: '/', httpOnly: true, secure: false, sameSite: 'Lax',
  }]);
  await ctx.route('**/*', async (route) => {
    const url = route.request().url();
    if (url.startsWith('https://api.hedgesparkhq.com/')) {
      const newUrl = url.replace('https://api.hedgesparkhq.com', 'http://127.0.0.1:8000');
      try {
        const resp = await ctx.request.fetch(newUrl, {
          method: route.request().method(),
          headers: { ...route.request().headers(), cookie: `hs_session=${sessionToken}` },
          data: route.request().postData(),
        });
        await route.fulfill({
          status: resp.status(),
          headers: { ...resp.headers(), 'access-control-allow-origin': 'http://127.0.0.1:3000', 'access-control-allow-credentials': 'true' },
          body: await resp.body(),
        });
      } catch { route.abort(); }
    } else { route.continue(); }
  });
  const page = await ctx.newPage();
  page.on('pageerror', e => console.error('PAGE ERR:', e.message));

  let totalSections = 0, totalNullActive = 0;
  for (const route of ROUTES) {
    const { inventory, results } = await probe(page, route);
    console.log(`\n========== ${route.label} (${route.path}) ==========`);
    console.log(`Sections: ${inventory.sections.length} · Nav slots: ${inventory.navs.length} · Scroll height: ${inventory.mainScrollHeight}px`);
    for (const r of results) {
      const flag = r.activeNav === null || r.activeNav === 'null' ? '  ❌ NULL' : '  ✓';
      console.log(`${flag} ${r.section.padEnd(36)} y=${String(r.top).padStart(6)} → activeNav=${JSON.stringify(r.activeNav)}`);
      totalSections++;
      if (r.activeNav === null || r.activeNav === 'null') totalNullActive++;
    }
  }
  console.log(`\n===== SUMMARY =====`);
  console.log(`${totalSections} section(s) probed across ${ROUTES.length} floor(s)`);
  console.log(`${totalNullActive} null/empty active highlights`);
  await browser.close();
  process.exit(totalNullActive === 0 ? 0 : 1);
})();
