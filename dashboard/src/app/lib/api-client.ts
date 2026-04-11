/**
 * api-client.ts — Type-safe HedgeSpark API client.
 *
 * Built on top of `openapi-fetch` + the auto-generated `api-types.ts` that is
 * produced from the live FastAPI OpenAPI schema via `npm run api:types`.
 *
 * ════════════════════════════════════════════════════════════════════════
 * Why this exists
 * ════════════════════════════════════════════════════════════════════════
 * On 10 April 2026 we discovered 4 silent shape-drift bugs where frontend
 * components were reading field names that no longer matched the backend
 * response shape. They were invisible to normal tests because the fetches
 * still returned 200 OK, just with different keys. This module eliminates
 * two of the four bug categories at compile time:
 *
 *   ✅ URL path typos become compile-time errors
 *      (Example: `/segments` vs `/pro/segments` bug from 10/4 would fail
 *       to compile instead of silently returning 404)
 *
 *   ✅ Query parameter shape is validated at compile time
 *      (Wrong key name, wrong type, missing required params → tsc error)
 *
 *   🟡 Response body field access — PARTIAL
 *      Response bodies are typed as `unknown` today because the FastAPI
 *      endpoints don't declare `response_model=` on their route decorators.
 *      Without response_model, FastAPI can't emit the output schema into
 *      OpenAPI, so openapi-typescript has nothing to generate. Until we
 *      retrofit response models on the critical Pro endpoints, callers
 *      must cast the response to their expected shape manually.
 *
 *      → See Sprint B.1.1 in project_hardening_progress.md for the
 *        retrofit plan (highest value: /pro/cohorts/*, /pro/lift,
 *        /pro/nudges/{id}/stats, /pro/segments).
 *
 *   ❌ Renaming a backend field — NOT YET CAUGHT
 *      Same reason: the frontend type is `unknown`, so it can read any
 *      key without tsc complaining. Only the response_model retrofit
 *      unlocks this protection.
 *
 * When you change a backend endpoint:
 *   1. Restart the FastAPI backend (or wait for hot reload)
 *   2. Run `npm run api:types` from the dashboard directory
 *   3. `tsc` will scream at every URL path or query param that drifted
 *   4. Fix the call sites, commit
 *
 * ════════════════════════════════════════════════════════════════════════
 * Usage patterns
 * ════════════════════════════════════════════════════════════════════════
 *
 * GET with typed query params:
 *
 *   const { data, error, response } = await apiClient.GET("/pro/cohorts/ltv/products", {
 *     params: { query: { limit: 12 } },
 *   });
 *   // `data` is typed as unknown until the backend retrofit — cast it:
 *   const typed = data as { products: Array<{ avg_buyer_ltv: number; ... }> } | null;
 *
 * Authenticated GET (component uses `apiHeaders()`):
 *
 *   const { data } = await apiClient.GET("/pro/lift", {
 *     params: { query: { window_hours: 168 } },
 *     headers: getHeaders(apiHeaders),
 *   });
 *
 * Note about the `shop` query param:
 *   Many existing frontend fetches include `?shop=...` explicitly. The
 *   backend's `require_pro_session` dependency also reads `shop` from
 *   the session cookie, so the query param is redundant for authenticated
 *   sessions. When migrating to the typed client, you can omit `shop`
 *   and the session cookie carries it transparently. If you need to
 *   force a specific shop (e.g. admin impersonation), pass it via headers.
 *
 * Migration strategy:
 *   Migrate one endpoint at a time. Legacy `fetch()` calls continue to work
 *   alongside the typed client during the transition. No big-bang rewrite.
 *
 * Environment:
 *   The base URL is read from NEXT_PUBLIC_API_BASE_URL with a sensible fallback
 *   to the local dev backend. In production this is https://api.hedgesparkhq.com.
 */

import createClient from "openapi-fetch";
import type { paths } from "./api-types";

const API_BASE_URL =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "http://127.0.0.1:8000";

/**
 * The singleton typed API client. Share it across the app — don't re-create.
 *
 * Global headers set here apply to every request. Additional per-request
 * headers can be passed via the second arg of each method.
 */
export const apiClient = createClient<paths>({
  baseUrl: API_BASE_URL,
  credentials: "include", // session cookie flows with every request
  headers: {
    "Content-Type": "application/json",
  },
});

/**
 * Helper to apply per-call auth headers returned by `apiHeaders()` at the
 * parent component level. Use this when the component needs to inject dev
 * tokens or session-specific headers that the global client doesn't know.
 *
 *   const { data } = await apiClient.GET("/pro/lift", {
 *     params: { query: { shop, window_hours: 168 } },
 *     headers: getHeaders(apiHeaders),
 *   });
 */
export function getHeaders(
  apiHeadersFn: () => HeadersInit,
): Record<string, string> {
  const headers = apiHeadersFn();
  // Normalize HeadersInit into a plain object for openapi-fetch.
  if (headers instanceof Headers) {
    const out: Record<string, string> = {};
    headers.forEach((value, key) => { out[key] = value; });
    return out;
  }
  if (Array.isArray(headers)) {
    const out: Record<string, string> = {};
    for (const [key, value] of headers) out[key] = value;
    return out;
  }
  return headers as Record<string, string>;
}

/**
 * Re-export the paths type so components can reference specific endpoint
 * response/request shapes when they need a local type alias.
 *
 * Example:
 *   import type { paths } from "@/app/lib/api-client";
 *   type GatewayProductsResponse =
 *     paths["/pro/cohorts/ltv/products"]["get"]["responses"]["200"]["content"]["application/json"];
 */
export type { paths } from "./api-types";
