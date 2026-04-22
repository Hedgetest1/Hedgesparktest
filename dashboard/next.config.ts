import type { NextConfig } from "next";

/**
 * Security headers applied to every route served by the Next.js
 * dashboard (2026-04-11 worldwide-compliance audit).
 *
 * The dashboard is embedded inside the Shopify admin iframe AND served
 * standalone at app.hedgesparkhq.com. Both contexts need slightly
 * different framing rules:
 *
 *   - frame-ancestors allows Shopify admin + merchant myshopify.com
 *     origins (both forms of the embedded app delivery).
 *   - form-action allows the backend API so OAuth form POSTs still
 *     work for install flows.
 *   - CSP is NOT strict-dynamic yet because Next.js 15 still needs
 *     inline scripts for hydration; we enumerate the required origins
 *     instead.
 *
 * HSTS, COOP, CORP, Permissions-Policy, X-Content-Type-Options,
 * Referrer-Policy are always-on and match the backend middleware
 * (app/main.py security_headers_middleware).
 */

const BACKEND = "https://api.hedgesparkhq.com";
const SHOPIFY_ADMIN = "https://admin.shopify.com";
const SHOPIFY_STOREFRONTS = "https://*.myshopify.com";

const csp = [
  "default-src 'self'",
  `connect-src 'self' ${BACKEND}`,
  // Next.js hydration needs inline scripts. We whitelist 'unsafe-inline'
  // here and rely on the backend-side strict CSP + security_preflight_guard
  // to prevent injection of untrusted content.
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: https:",
  "font-src 'self' data:",
  `frame-ancestors 'self' ${SHOPIFY_ADMIN} ${SHOPIFY_STOREFRONTS}`,
  "form-action 'self' " + BACKEND,
  "base-uri 'self'",
  "object-src 'none'",
  "frame-src 'self' " + SHOPIFY_ADMIN,
  "upgrade-insecure-requests",
].join("; ");

const securityHeaders = [
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value:
      "camera=(), microphone=(), geolocation=(), interest-cohort=(), " +
      "browsing-topics=(), payment=(), usb=(), midi=()",
  },
  { key: "Content-Security-Policy", value: csp },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin-allow-popups" },
  { key: "Cross-Origin-Resource-Policy", value: "same-site" },
  // X-Frame-Options intentionally omitted — superseded by CSP
  // frame-ancestors, which correctly allows Shopify admin embedding.
];

// Email clients (Gmail, Outlook, Apple Mail) proxy external images through
// their own CDN (e.g. googleusercontent.com). The global `Cross-Origin-
// Resource-Policy: same-site` header blocks those proxies — images render
// as a broken-placeholder square on mobile Gmail in particular (desktop
// sometimes recovers via fallback fetch after minutes).
//
// For static image assets the Spectre/CORB concern CORP protects against
// does not apply (PNG/JPG/SVG/WEBP/GIF are inert: the browser will never
// mis-parse them as a script). Override CORP to `cross-origin` for image
// extensions so email logos render reliably on every client.
//
// Born 2026-04-22 after empirical observation on Gmail mobile: logo-beta-v2.png
// rendered as "?" in a box on phones, desktop occasionally.
const imageAssetHeaders = securityHeaders.map((h) =>
  h.key === "Cross-Origin-Resource-Policy"
    ? { key: "Cross-Origin-Resource-Policy", value: "cross-origin" }
    : h,
);

// Static image assets used by email templates. When an email client's
// image proxy (Gmail's googleusercontent.com, Apple Mail's CDN, etc.)
// fetches these cross-origin, the global `same-site` CORP would block
// them. Hardcoded list so the override is surgical — every addition is
// reviewed rather than wildcard-opened.
const EMAIL_ASSET_PATHS = [
  "/logo-beta-v2.png",
  "/hedgespark-logo.png",
  "/hedgespark.png",
  "/logo-hedgespark.png",
  "/logo.png",
];

const nextConfig: NextConfig = {
  async headers() {
    return [
      // Order matters, and Next.js applies LATER matching rules on top of
      // earlier ones: catch-all first, then specific image-path overrides
      // so the email-asset CORP override (cross-origin) wins over the
      // default (same-site) from the catch-all.
      {
        source: "/:path*",
        headers: securityHeaders,
      },
      ...EMAIL_ASSET_PATHS.map((p) => ({
        source: p,
        headers: imageAssetHeaders,
      })),
    ];
  },
  // Tier-named floor URLs (founder directive 2026-04-20).
  // Canonical paths: /app/lite, /app/pro, /app/scale.
  // Legacy paths redirected 308 (permanent) to preserve bookmarks.
  // `/app` itself redirects to /app/lite so the URL bar always shows
  // the tier the merchant is looking at.
  async redirects() {
    return [
      { source: "/app", destination: "/app/lite", permanent: true },
      { source: "/app/intelligence", destination: "/app/pro", permanent: true },
      { source: "/app/operations", destination: "/app/scale", permanent: true },
    ];
  },
};

export default nextConfig;
