import type { BrowserContext, Page } from "@playwright/test";
import jwt from "jsonwebtoken";

export const SESSION_COOKIE_NAME = "hs_session";
export const SHOP_HINT_COOKIE_NAME = "hs_shop";

const DEFAULT_SMOKE_SHOP = "hedgespark-smoke.myshopify.com";
const DEFAULT_COOKIE_PARENT_DOMAIN = ".hedgesparkhq.com";
const DEFAULT_API_COOKIE_DOMAIN = "api.hedgesparkhq.com";

export function getSmokeShop(): string {
  return process.env.E2E_SMOKE_SHOP || DEFAULT_SMOKE_SHOP;
}

export function getCookieParentDomain(): string {
  return process.env.E2E_COOKIE_PARENT_DOMAIN || DEFAULT_COOKIE_PARENT_DOMAIN;
}

/**
 * The host that the backend scopes `hs_session` to. The backend calls
 * `response.set_cookie(... no domain attribute ...)`, which makes the
 * browser scope the cookie to the response host — api.hedgesparkhq.com
 * in production. We must inject on the SAME host; otherwise the
 * backend's fresh cookie lives on a different scope and the test
 * helper's cookie-change detection will keep seeing the stale one
 * forever.
 */
export function getApiCookieDomain(): string {
  return process.env.E2E_API_COOKIE_DOMAIN || DEFAULT_API_COOKIE_DOMAIN;
}

export function requireSecret(): string {
  const secret = process.env.MERCHANT_SESSION_SECRET || "";
  if (!secret) {
    throw new Error(
      "MERCHANT_SESSION_SECRET env var not set. Source /opt/wishspark/backend/.env before running the session-durability suite.",
    );
  }
  return secret;
}

export type MintOptions = {
  shop?: string;
  sv?: number;
  expiresInSec?: number;
  signingSecret?: string;
};

export function mintToken(opts: MintOptions = {}): string {
  const shop = opts.shop ?? getSmokeShop();
  const sv = opts.sv ?? 0;
  const expSeconds = opts.expiresInSec ?? 7 * 86_400;
  const secret = opts.signingSecret ?? requireSecret();
  const now = Math.floor(Date.now() / 1000);
  return jwt.sign(
    { shop, sv, iat: now, exp: now + expSeconds },
    secret,
    { algorithm: "HS256" },
  );
}

export function decodeToken(token: string): { shop: string; sv: number; iat: number; exp: number } {
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("malformed JWT");
  const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString());
  return {
    shop: payload.shop,
    sv: payload.sv ?? 0,
    iat: payload.iat,
    exp: payload.exp,
  };
}

type AddSessionOptions = {
  shop?: string;
  sv?: number;
  tokenOverride?: string;
  domain?: string;
};

export async function addSessionCookie(
  context: BrowserContext,
  opts: AddSessionOptions = {},
) {
  const shop = opts.shop ?? getSmokeShop();
  const token = opts.tokenOverride ?? mintToken({ shop, sv: opts.sv });
  // hs_session: scoped to the api host (what the backend actually sets).
  // hs_shop: scoped to the parent domain (cross-subdomain hint).
  await context.addCookies([
    {
      name: SESSION_COOKIE_NAME,
      value: token,
      domain: opts.domain ?? getApiCookieDomain(),
      path: "/",
      httpOnly: true,
      secure: true,
      sameSite: "None",
    },
    {
      name: SHOP_HINT_COOKIE_NAME,
      value: shop,
      domain: getCookieParentDomain(),
      path: "/",
      httpOnly: false,
      secure: true,
      sameSite: "Lax",
    },
  ]);
}

export async function addHintOnly(
  context: BrowserContext,
  opts: { shop?: string; domain?: string } = {},
) {
  const shop = opts.shop ?? getSmokeShop();
  const domain = opts.domain ?? getCookieParentDomain();
  await context.addCookies([
    {
      name: SHOP_HINT_COOKIE_NAME,
      value: shop,
      domain,
      path: "/",
      httpOnly: false,
      secure: true,
      sameSite: "Lax",
    },
  ]);
}

export async function clearAll(context: BrowserContext, page: Page) {
  await context.clearCookies();
  try {
    await page.evaluate(() => {
      try {
        window.localStorage.clear();
      } catch {
        /* localStorage can throw in some origins — swallow */
      }
    });
  } catch {
    // No loaded origin yet (e.g. about:blank) — safe to skip.
  }
}

function findSessionCookie(cookies: { name: string; domain: string; value: string }[]) {
  // Match on name only — once the backend mints a fresh cookie on the
  // api host, it replaces any prior inject at the same host/path (the
  // browser de-dupes by name+domain+path). The stale parent-domain
  // cookie can only exist if the helper was used wrongly; we still
  // pick the fresh api-host one if both exist.
  const apiDomain = getApiCookieDomain().replace(/^\./, "");
  return (
    cookies.find((c) => c.name === SESSION_COOKIE_NAME && c.domain.replace(/^\./, "") === apiDomain) ||
    cookies.find((c) => c.name === SESSION_COOKIE_NAME)
  );
}

export async function waitForSessionCookie(
  context: BrowserContext,
  timeoutMs = 15_000,
): Promise<string> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const session = findSessionCookie(await context.cookies());
    if (session?.value) return session.value;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(
    `hs_session cookie did not appear within ${timeoutMs}ms — session recovery path failed`,
  );
}

/**
 * Wait for the hs_session cookie value to differ from `previousValue`.
 * Distinguishes "cookie still present but unchanged (recovery didn't
 * fire)" from "cookie was replaced by a fresh mint (recovery worked)".
 * Required for tampered/expired/stale-sv scenarios where the invalid
 * cookie is installed by the test and only a real recovery path
 * should replace it.
 */
export async function waitForSessionCookieChange(
  context: BrowserContext,
  previousValue: string,
  timeoutMs = 20_000,
): Promise<string> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const session = findSessionCookie(await context.cookies());
    if (session?.value && session.value !== previousValue) return session.value;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(
    `hs_session cookie did not change from the injected value within ${timeoutMs}ms — recovery did not fire`,
  );
}
