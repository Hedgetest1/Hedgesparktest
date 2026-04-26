const { chromium } = require('playwright');

(async () => {
  const sessionToken = process.env.HS_SESSION;
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
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
          headers: { ...resp.headers(), 'access-control-allow-origin': '*' },
          body: await resp.body(),
        });
      } catch { route.abort(); }
    } else { route.continue(); }
  });
  const page = await ctx.newPage();
  await page.goto('http://127.0.0.1:3000/app/lite', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(5000);

  // STRUCTURAL TEST — force tall content into main, measure if body scrolls
  const result = await page.evaluate(() => {
    const main = document.querySelector('main');
    if (!main) return { err: 'no main' };

    // Inject 5000px tall div into main
    const tall = document.createElement('div');
    tall.style.height = '5000px';
    tall.style.background = 'red';
    tall.id = 'test-tall-injection';
    main.appendChild(tall);

    // Force layout
    main.offsetHeight;
    document.body.offsetHeight;

    const beforeScroll = {
      mainScrollHeight: main.scrollHeight,
      mainClientHeight: main.clientHeight,
      mainCanScroll: main.scrollHeight > main.clientHeight + 1,
      bodyScrollHeight: document.body.scrollHeight,
      bodyClientHeight: document.body.clientHeight,
      bodyCanScroll: document.body.scrollHeight > document.body.clientHeight + 1,
      htmlScrollHeight: document.documentElement.scrollHeight,
      htmlClientHeight: document.documentElement.clientHeight,
      htmlCanScroll: document.documentElement.scrollHeight > document.documentElement.clientHeight + 1,
    };

    // Try to scroll main + body
    main.scrollTo(0, 999999);
    window.scrollTo(0, 999999);
    document.documentElement.scrollTop = 999999;

    const afterScroll = {
      mainScrolledTo: main.scrollTop,
      mainAtBottom: main.scrollTop + main.clientHeight >= main.scrollHeight - 1,
      bodyScrollTop: document.body.scrollTop,
      htmlScrollTop: document.documentElement.scrollTop,
      windowScrollY: window.scrollY,
    };

    // Cleanup
    tall.remove();

    return { beforeScroll, afterScroll };
  });

  console.log(JSON.stringify(result, null, 2));
  await browser.close();
})();
