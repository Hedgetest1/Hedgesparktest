/**
 * sw.js — Hedge Spark service worker.
 *
 * Mission: make the dashboard open instantly on flaky networks by
 * caching the offline shell (landing + /app HTML + manifest + logo),
 * WITHOUT ever serving stale JavaScript or CSS chunks after a deploy.
 *
 * Why this file was rewritten 2026-04-20 (b5b0526 follow-up):
 *   The prior version used cache-first for every GET that wasn't an
 *   API/track/pro/auth path. That swallowed `_next/static/*` chunks
 *   into the same cache bucket. After a deploy, the dashboard shipped
 *   NEW chunk hashes but the browser kept serving the OLD JS bundle
 *   forever because cache-first returns the cached copy before ever
 *   checking the network. This is why the previous session-persistence
 *   fix (retry logic + /auth/session redirect change) did not reach
 *   the founder's browser — the service worker was silently serving
 *   the pre-fix dashboard JS.
 *
 * New policy (strictly narrower):
 *   - Only shell PATHS are intercepted: "/", "/app", "/manifest.json",
 *     "/logo-beta-v2.png". Everything else passes through to the
 *     network, honoring Next.js's immutable-hash HTTP cache headers
 *     for _next/static/* (Cache-Control: immutable, max-age=31536000).
 *   - Shell is network-first with cache fallback — merchants on flaky
 *     networks see the last-good HTML if the network is truly down.
 *     Fresh HTML always wins when the network is up, so a new deploy
 *     is picked up on the next load.
 *   - CACHE_NAME is bumped to force activation + eviction of the
 *     v1 cache that held the stale JS chunks. Bump again on any
 *     future shell-behavior change.
 */

const CACHE_NAME = "hedgespark-shell-v2";
const SHELL_PATHS = new Set([
  "/",
  "/app",
  "/manifest.json",
  "/logo-beta-v2.png",
]);

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      // Add shell entries one-by-one so a single failure doesn't block
      // the install. Silent catch is deliberate — offline cache is a
      // convenience, not a correctness guarantee.
      Promise.all(
        Array.from(SHELL_PATHS).map((p) =>
          cache.add(p).catch(() => {})
        )
      )
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k.startsWith("hedgespark-shell-") && k !== CACHE_NAME)
          .map((k) => caches.delete(k))
      )
    )
  );
  // Take over all open tabs immediately so the new policy applies
  // without a refresh. Paired with skipWaiting above.
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;

  const url = new URL(event.request.url);

  // Only intercept requests for the exact shell paths on the same
  // origin as the service worker. Everything else — _next/static JS
  // and CSS chunks, images, API calls, third-party — passes through
  // to the network and uses browser + HTTP cache as normal.
  if (url.origin !== self.location.origin) return;
  if (!SHELL_PATHS.has(url.pathname)) return;

  // Network-first for shell entries. On failure, fall back to the
  // cached copy so the PWA still opens. Cache is refreshed on every
  // successful fetch so the next offline load has fresh-ish HTML.
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE_NAME).then((c) => c.put(event.request, copy).catch(() => {}));
        return res;
      })
      .catch(() =>
        caches.match(event.request).then((cached) => cached || caches.match("/"))
      )
  );
});
