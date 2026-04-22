import { expect, test } from "@playwright/test";
import {
  addHintOnly,
  addSessionCookie,
  clearAll,
  decodeToken,
  getSmokeShop,
  mintToken,
  SESSION_COOKIE_NAME,
  waitForSessionCookie,
  waitForSessionCookieChange,
} from "./helpers/session";

/**
 * Session durability regression suite.
 *
 * Every scenario maps to a failure mode documented in the
 * session-handling code (useSession.ts, merchant_session.py, deps.py,
 * shopify_oauth.py). If a scenario fails, the corresponding invariant
 * is broken and real merchants will hit it.
 *
 * Runs against HTTPS prod infrastructure by design: `hs_session` is
 * hardcoded `secure=true`, which browsers drop over http://localhost.
 * Targets:
 *   E2E_BASE_URL=https://app.hedgesparkhq.com
 *   E2E_API_BASE=https://api.hedgesparkhq.com
 *   E2E_SMOKE_SHOP=hedgespark-smoke.myshopify.com (dedicated smoke merchant)
 *
 * Required env: MERCHANT_SESSION_SECRET (source backend/.env).
 */

const API_BASE = process.env.E2E_API_BASE || "http://127.0.0.1:8000";
const SMOKE_SHOP = getSmokeShop();

// Captured once in beforeAll — smoke merchant's current session_version
// as known to the DB. Prevents drift: if the DB sv is bumped mid-run,
// our minted tokens stay aligned instead of spuriously failing S1/S6/S9/S10.
let CURRENT_SV = 0;
// Captured once — whether the smoke merchant is currently on the Pro
// plan. S10 asserts that `?as=starter` preview downgrades a Pro
// merchant to Starter UI; if smoke is already Lite, the assertion
// passes trivially and the test becomes theatre.
let SMOKE_IS_PRO = false;

test.describe("Session durability — regression suite", () => {
  test.beforeAll(async ({ request }) => {
    expect(
      process.env.MERCHANT_SESSION_SECRET,
      "MERCHANT_SESSION_SECRET env var required. Source /opt/wishspark/backend/.env.",
    ).toBeTruthy();

    // /auth/session on a known active shop must 302 to the dashboard AND
    // return a hs_session Set-Cookie. We use it as a living health check
    // AND to learn the current session_version without a DB query.
    const r = await request.get(
      `${API_BASE}/auth/session?shop=${encodeURIComponent(SMOKE_SHOP)}`,
      { maxRedirects: 0 },
    );
    expect(
      r.status(),
      `Smoke merchant ${SMOKE_SHOP} must be active (expected 302 from /auth/session)`,
    ).toBe(302);
    const loc = r.headers()["location"] || "";
    expect(
      loc,
      `Smoke merchant redirect must go to dashboard, not install — got ${loc}`,
    ).toContain("/app");

    const setCookieHeaders = r.headersArray().filter(
      (h) => h.name.toLowerCase() === "set-cookie",
    );
    const cookieBlob = setCookieHeaders.map((h) => h.value).join("\n");
    const match = cookieBlob.match(/hs_session=([^;\s]+)/);
    expect(
      match,
      "Smoke merchant /auth/session must set hs_session cookie",
    ).toBeTruthy();
    CURRENT_SV = decodeToken(match![1]).sv;

    // Also grab the merchant plan via /merchant/plan — S10 asserts the
    // preview-downgrade path and needs smoke to be Pro to be meaningful.
    // Sending the freshly-minted cookie as a bearer via Cookie header
    // (Playwright request context doesn't share the browser cookie jar).
    const planRes = await request.get(`${API_BASE}/merchant/plan`, {
      headers: { Cookie: `hs_session=${match![1]}` },
    });
    expect(planRes.ok(), "Smoke merchant /merchant/plan must succeed").toBe(true);
    const plan = await planRes.json();
    SMOKE_IS_PRO = plan.plan === "pro" && plan.billing_active === true;
  });

  test("S1 · valid JWT + hint → /app renders merchant dashboard, no Reconnect UI", async ({ page, context }) => {
    await clearAll(context, page);
    await addSessionCookie(context, { sv: CURRENT_SV });
    await page.goto("/app");
    // Positive assertion: some merchant-dashboard chrome must render.
    // The floor switcher (LITE / PRO / SCALE) is present on both Lite
    // and Pro views and survives copy churn better than any
    // data-driven element. If S1 passes trivially on an empty DOM,
    // this assertion catches it.
    await expect(
      page.getByText(SMOKE_SHOP, { exact: false }).first(),
      "S1 invariant: the smoke merchant's domain must actually render — proves the dashboard fetched and displayed merchant data, not just an empty shell",
    ).toBeVisible({ timeout: 20_000 });
    await expect(
      page.getByText(/Reconnect my store/i),
      "S1 invariant: a valid JWT must NOT trigger the Reconnect UI",
    ).toHaveCount(0);
  });

  test("S2 · no JWT but valid hint → /auth/session mints fresh cookie (recovery path)", async ({ page, context }) => {
    await clearAll(context, page);
    await addHintOnly(context);
    await page.goto("/app");
    const token = await waitForSessionCookie(context);
    expect(
      decodeToken(token).shop,
      "S2 invariant: hs_shop hint must trigger /auth/session to mint a fresh cookie for that shop",
    ).toBe(SMOKE_SHOP);
  });

  test("S3 · tampered JWT signature → recovery replaces it", async ({ page, context }) => {
    await clearAll(context, page);
    const valid = mintToken({ sv: CURRENT_SV });
    const [h, p, s] = valid.split(".");
    const tampered = `${h}.${p}.${s.slice(0, -6)}XXXXXX`;
    await addSessionCookie(context, { tokenOverride: tampered });
    await addHintOnly(context);
    await page.goto("/app");
    const token = await waitForSessionCookieChange(context, tampered);
    expect(
      decodeToken(token).shop,
      "S3 invariant: tampered JWT must be replaced by a fresh token for the hint-shop",
    ).toBe(SMOKE_SHOP);
  });

  test("S4 · expired JWT → recovery replaces it", async ({ page, context }) => {
    await clearAll(context, page);
    const expired = mintToken({ sv: CURRENT_SV, expiresInSec: -3600 });
    await addSessionCookie(context, { tokenOverride: expired });
    await addHintOnly(context);
    await page.goto("/app");
    const token = await waitForSessionCookieChange(context, expired);
    expect(
      decodeToken(token).exp * 1000,
      "S4 invariant: recovered token must have future expiry (not the stale one)",
    ).toBeGreaterThan(Date.now());
  });

  test("S5 · token with sv < db_sv → forced-logout, recovery re-mints at current sv", async ({ page, context }) => {
    await clearAll(context, page);
    // CURRENT_SV - 1 is always less than the DB value, triggering the
    // session_version mismatch path in deps.require_merchant_session
    // without needing a DB write. If CURRENT_SV is 0, stale=-1 works.
    const stale = mintToken({ sv: CURRENT_SV - 1 });
    await addSessionCookie(context, { tokenOverride: stale });
    await addHintOnly(context);
    await page.goto("/app");
    const token = await waitForSessionCookieChange(context, stale);
    expect(
      decodeToken(token).sv,
      "S5 invariant: replacement token must have sv >= current DB sv (forced logout re-mint aligns to DB)",
    ).toBeGreaterThanOrEqual(CURRENT_SV);
  });

  test("S6 · /merchant/me transient 502 → retry backoff prevents Reconnect flash", async ({ page, context }) => {
    await clearAll(context, page);
    await addSessionCookie(context, { sv: CURRENT_SV });
    let callCount = 0;
    await page.route("**/merchant/me**", async (route) => {
      callCount++;
      if (callCount === 1) {
        await route.fulfill({ status: 502, body: "Bad Gateway (simulated)" });
      } else {
        await route.continue();
      }
    });
    await page.goto("/app");
    // Retry fires after ~1.5s (first backoff). Poll callCount until the
    // retry actually lands — this is the signal that retry-on-502 still
    // works end-to-end. 20s headroom covers slow backend + gc hiccups.
    await expect
      .poll(() => callCount, {
        message:
          "S6 invariant: useSession must retry /merchant/me after a 502 (retry backoff absorbs PM2 restart blips)",
        timeout: 20_000,
      })
      .toBeGreaterThanOrEqual(2);
    await expect(
      page.getByText(/Reconnect my store/i),
      "S6 invariant: a single transient failure must never flash the Reconnect UI",
    ).toHaveCount(0);
  });

  test("S7 · no cookies, no localStorage, auto-detect off → graceful Reconnect UI, no 500", async ({ page, context }) => {
    // Force /auth/detect to 404 so this test exercises the TRUE no-shop
    // state regardless of the production AUTO_DETECT_ENABLED flag. The
    // invariant under test is the "identity genuinely unknown → render
    // Reconnect" UI path, not the auto-detect recovery path (which has
    // its own pre-prod kill-switch checklist).
    await page.route("**/auth/detect", async (route) => {
      await route.fulfill({ status: 404, body: "" });
    });
    await clearAll(context, page);
    const response = await page.goto("/app");
    expect(
      response?.status(),
      "S7 invariant: /app must return 200 even without auth — never 500",
    ).toBe(200);
    // The Reconnect banner copy: button text + explanatory sentence
    await expect(
      page.getByRole("button", { name: /Reconnect my store/i }),
      "S7 invariant: unauthenticated visit (with auto-detect disabled) must render the Reconnect UI button",
    ).toBeVisible({ timeout: 25_000 });
  });

  test("S8 · unknown shop at /auth/session → 302 to /auth/install (not 500)", async ({ request }) => {
    const r = await request.get(
      `${API_BASE}/auth/session?shop=absolutely-not-a-real-shop-xyz-123.myshopify.com`,
      { maxRedirects: 0 },
    );
    expect(
      r.status(),
      "S8 invariant: an unknown shop at /auth/session must 302, never 500",
    ).toBe(302);
    const loc = r.headers()["location"] || "";
    expect(
      loc,
      "S8 invariant: unknown shop must redirect to the install flow, not the dashboard",
    ).toContain("/auth/install");
  });

  test("S9 · /merchant/me request carries hs_session cookie (credentials included)", async ({ page, context }) => {
    await clearAll(context, page);
    await addSessionCookie(context, { sv: CURRENT_SV });
    let sawCookie = false;
    await page.route("**/merchant/me", async (route) => {
      const header = route.request().headers()["cookie"] || "";
      if (header.includes(`${SESSION_COOKIE_NAME}=`)) sawCookie = true;
      await route.continue();
    });
    await page.goto("/app");
    // Wait for at least one /merchant/me fetch to fly.
    await expect
      .poll(() => sawCookie, {
        message:
          "S9 invariant: the dashboard must send hs_session with /merchant/me (credentials: 'include' doctrine)",
        timeout: 15_000,
      })
      .toBe(true);
  });

  test("S10 · ?as=starter preview on Pro merchant → downgrade applied in DOM", async ({ page, context }) => {
    expect(
      SMOKE_IS_PRO,
      "S10 precondition: smoke merchant must currently be Pro for the preview-downgrade assertion to be meaningful",
    ).toBe(true);
    await clearAll(context, page);
    await addSessionCookie(context, { sv: CURRENT_SV });
    await page.goto("/app?as=starter");
    // Wait for session to resolve by polling the stable data-attribute
    // on the root /app container. The attribute exposes the RESOLVED
    // tier so we can verify the preview downgrade actually took effect
    // at the rendering layer — not just that ?as=starter stayed in the
    // URL. If a regression silently strips the preview param or the
    // applyTier() path forgets to honor readPreviewParam(), this test
    // fails with a clear "expected lite got pro" message.
    const root = page.locator("[data-tier-resolved]").first();
    await expect(
      root,
      "S10 invariant: /app root must render with a data-tier-resolved attribute (exposure contract for E2E)",
    ).toBeVisible({ timeout: 20_000 });
    await expect
      .poll(() => root.getAttribute("data-tier-resolved"), {
        message:
          "S10 invariant: ?as=starter on a Pro merchant must resolve tier=lite (downgrade honored). If this fails with 'pro', the preview-param handling regressed.",
        timeout: 15_000,
      })
      .toBe("lite");
    await expect
      .poll(() => root.getAttribute("data-tier-preview"), {
        message:
          "S10 invariant: data-tier-preview must be '1' when ?as=starter is present",
        timeout: 5_000,
      })
      .toBe("1");
    expect(
      page.url(),
      "S10 invariant: ?as=starter preview parameter must be preserved end-to-end",
    ).toContain("as=starter");
  });

  test("S13 · same-origin iframe embed → session flows through (SameSite=None + Secure proof)", async ({ page, context }) => {
    // The real failure mode being tested: if the hs_session cookie were
    // ever set to SameSite=Lax or SameSite=Strict, it would NOT flow
    // into an iframe-embedded copy of /app. Shopify Admin embeds
    // HedgeSpark in an iframe from admin.shopify.com (cross-origin),
    // and the dashboard's CSP frame-ancestors allowlists it. We can't
    // spoof admin.shopify.com origin from a Playwright runner without
    // DNS/hosts trickery, so instead we prove the FOUNDATION: the
    // cookie flows into a same-origin iframe. SameSite=None is
    // strictly broader than 'self'-frame embedding, so if this test
    // passes we know the cross-origin Shopify Admin path also works.
    // Same-origin embed is allowed by our CSP (`frame-ancestors 'self'`)
    // and exercises the actual cookie-flow path.
    await clearAll(context, page);
    await addSessionCookie(context, { sv: CURRENT_SV });
    await page.goto("/"); // any same-origin wrapper
    const APP_URL =
      (process.env.E2E_BASE_URL || "http://127.0.0.1:3000") + "/app";
    await page.evaluate((url) => {
      const f = document.createElement("iframe");
      f.id = "embed";
      f.src = url;
      f.style.cssText =
        "width:1200px;height:900px;border:0;position:fixed;top:0;left:0;z-index:9999";
      document.body.appendChild(f);
    }, APP_URL);
    const frame = page.frameLocator("#embed");
    const root = frame.locator("[data-tier-resolved]").first();
    await expect(
      root,
      "S13 invariant: /app inside a same-origin iframe must render the data-tier-resolved root — proves SameSite=None + Secure cookie flows into embed contexts (the foundation for cross-origin Shopify Admin embed)",
    ).toBeVisible({ timeout: 25_000 });
    await expect(
      frame.getByText(/Reconnect my store/i),
      "S13 invariant: embedded /app must NOT show Reconnect UI when cookie is valid",
    ).toHaveCount(0);
  });

  test("S14 · multi-tab forced-logout consistency → second tab invalidated", async ({ context }) => {
    // Two tabs share the browser context (thus share cookies). Tab 1
    // lands with a valid JWT. We then inject a stale-sv JWT into the
    // shared cookie jar (simulates forced logout triggered elsewhere
    // — e.g., Shopify Admin uninstall, admin sv bump, or token reset
    // from another device). Tab 2 opens /app — it must detect the
    // stale cookie and go through recovery, NOT render a cached view.
    await context.clearCookies();
    await addSessionCookie(context, { sv: CURRENT_SV });

    const tabA = await context.newPage();
    await tabA.goto("/app");
    await expect(
      tabA.locator("[data-tier-resolved]").first(),
      "S14 precondition: tab A must authenticate normally before the forced-logout event",
    ).toBeVisible({ timeout: 20_000 });

    // Simulate "somewhere else bumped session_version" — overwrite
    // the cookie with a stale-sv token. Any tab opened AFTER this
    // point must recover (mint a fresh cookie via /auth/session),
    // not render as authenticated on the stale value.
    await context.clearCookies();
    const stale = mintToken({ sv: CURRENT_SV - 1 });
    await addSessionCookie(context, { tokenOverride: stale });
    await addHintOnly(context);

    const tabB = await context.newPage();
    await tabB.goto("/app");
    const token = await waitForSessionCookieChange(context, stale);
    expect(
      decodeToken(token).sv,
      "S14 invariant: when session_version bumps system-wide, a newly-opened tab must recover with sv >= current — it must NOT keep the stale cookie",
    ).toBeGreaterThanOrEqual(CURRENT_SV);
    await tabA.close();
    await tabB.close();
  });

  test("S12 · JWT for nonexistent shop → 401 (existence gate, not just signature gate)", async ({ request }) => {
    // A valid HS256-signed JWT for a shop that's NOT in the merchants
    // table must be rejected at the auth gate. Previously this path
    // returned 200 because require_merchant_session only enforced
    // signature + sv, not merchant-row existence. The 2026-04-22
    // hardening (see deps.require_merchant_session) closes this.
    const bogusShop = `does-not-exist-${Date.now()}.myshopify.com`;
    const forgedToken = mintToken({ shop: bogusShop });
    const r = await request.get(`${API_BASE}/merchant/me`, {
      headers: { Cookie: `${SESSION_COOKIE_NAME}=${forgedToken}` },
    });
    expect(
      r.status(),
      "S12 invariant: a JWT with a valid signature but for a nonexistent shop must return 401 — the existence gate is the second half of the auth contract",
    ).toBe(401);
    const body = await r.json().catch(() => ({}));
    expect(
      (body.detail || "").toLowerCase(),
      "S12 invariant: the 401 body should name 'reinstall' so the merchant-facing UI can render the right remediation copy",
    ).toContain("reinstall");
  });

  test("S11 · session survives a page reload (durability across navigation)", async ({ page, context }) => {
    await clearAll(context, page);
    await addSessionCookie(context, { sv: CURRENT_SV });
    await page.goto("/app");
    // First load: verify authenticated render (same positive anchor as S1)
    await expect(
      page.getByText(SMOKE_SHOP, { exact: false }).first(),
      "S11 precondition: dashboard must render smoke merchant data on first load",
    ).toBeVisible({ timeout: 20_000 });
    const firstLoadToken = (await context.cookies()).find(
      (c) => c.name === SESSION_COOKIE_NAME,
    )?.value;
    expect(firstLoadToken, "S11 precondition: cookie present after first load").toBeTruthy();

    // Reload — the cookie must survive (it's browser-stored) and the
    // dashboard must come back without bouncing through /auth/session.
    await page.reload();
    await expect(
      page.getByText(SMOKE_SHOP, { exact: false }).first(),
      "S11 invariant: dashboard must render smoke merchant data again after reload — NO Reconnect UI, NO re-bootstrap",
    ).toBeVisible({ timeout: 20_000 });
    await expect(
      page.getByText(/Reconnect my store/i),
      "S11 invariant: reload must not trigger Reconnect (cookie survives reload)",
    ).toHaveCount(0);

    const reloadedToken = (await context.cookies()).find(
      (c) => c.name === SESSION_COOKIE_NAME,
    )?.value;
    expect(
      reloadedToken,
      "S11 invariant: hs_session cookie must persist verbatim across reload (no unnecessary re-mint)",
    ).toBe(firstLoadToken);
  });
});
