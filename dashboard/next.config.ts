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

const nextConfig: NextConfig = {
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
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
