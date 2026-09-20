// Saves the app shell so the page opens without internet. feed.json is network-first, with the last copy as fallback.
const SHELL = "thw-public-shell-v1";
const FEED = "thw-public-feed-v1";
const FILES = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => ![SHELL, FEED].includes(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;   // never touch other sites
  if (url.pathname.endsWith("/feed.json")) {
    e.respondWith(
      fetch(e.request)
        .then((r) => { if (r.ok) { const copy = r.clone(); caches.open(FEED).then((c) => c.put("feed", copy)); } return r; })
        .catch(() => caches.open(FEED).then((c) => c.match("feed")).then((r) => r || new Response("{}", { status: 503 })))
    );
    return;
  }
  e.respondWith(
    fetch(e.request)
      .then((r) => { if (r.ok) { const copy = r.clone(); caches.open(SHELL).then((c) => c.put(e.request, copy)); } return r; })
      .catch(() => caches.match(e.request).then((r) => r || caches.match("./")))
  );
});
