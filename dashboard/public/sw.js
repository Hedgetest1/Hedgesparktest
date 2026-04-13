/**
 * sw.js — Hedge Spark service worker (Phase Ω''').
 *
 * Minimal offline shell: caches the static landing + /app shell so the
 * PWA opens instantly even on flaky networks. Network-first for API
 * calls — we never want to serve stale revenue numbers from cache.
 */

const CACHE_NAME = "hedgespark-shell-v1";
const SHELL_PATHS = [
  "/",
  "/app",
  "/manifest.json",
  "/logo-beta-v2.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_PATHS).catch(() => {}))
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
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Never cache API or POST/non-GET — always live data.
  if (event.request.method !== "GET") return;
  if (url.pathname.startsWith("/api/") || url.hostname.includes("api.")) return;
  if (url.pathname.startsWith("/track")) return;
  if (url.pathname.startsWith("/pro/")) return;
  if (url.pathname.startsWith("/auth/")) return;

  // Network-first for HTML pages; cache shell as fallback.
  if (event.request.headers.get("accept")?.includes("text/html")) {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((c) => c.put(event.request, copy));
          return res;
        })
        .catch(() => caches.match(event.request) || caches.match("/"))
    );
    return;
  }

  // Cache-first for static assets.
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
