/* SaM's Radio service worker: offline app shell + on-demand playlist caching. */
"use strict";

const SHELL_CACHE = "sams-shell-v1";
const PLAYLIST_CACHE = "sams-playlist-v1";
const SHELL_ASSETS = [
  "/", "/index.html", "/styles.css", "/app.js",
  "/manifest.json", "/icon-192.png", "/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((c) => c.addAll(SHELL_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL_CACHE && k !== PLAYLIST_CACHE)
                      .map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Build a 206 Partial Content response by slicing a cached full (200) response.
async function rangeFromCache(cached, rangeHeader) {
  const buf = await cached.arrayBuffer();
  const total = buf.byteLength;
  const m = /bytes=(\d*)-(\d*)/.exec(rangeHeader || "");
  let start = m && m[1] ? parseInt(m[1], 10) : 0;
  let end = m && m[2] ? parseInt(m[2], 10) : total - 1;
  if (isNaN(start)) start = 0;
  if (isNaN(end) || end >= total) end = total - 1;
  const slice = buf.slice(start, end + 1);
  const headers = new Headers();
  headers.set("Content-Type", cached.headers.get("Content-Type") || "audio/mpeg");
  headers.set("Content-Range", `bytes ${start}-${end}/${total}`);
  headers.set("Content-Length", String(slice.byteLength));
  headers.set("Accept-Ranges", "bytes");
  return new Response(slice, { status: 206, statusText: "Partial Content", headers });
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  // Media/cover: network first, fall back to the playlist cache when offline.
  if (url.pathname.startsWith("/api/stream/") || url.pathname.startsWith("/api/cover/")) {
    event.respondWith((async () => {
      try {
        return await fetch(req);
      } catch (err) {
        const cache = await caches.open(PLAYLIST_CACHE);
        const key = url.pathname;  // cached without query/Range
        const cached = await cache.match(key);
        if (!cached) throw err;
        const range = req.headers.get("range");
        if (range && url.pathname.startsWith("/api/stream/")) {
          return rangeFromCache(cached.clone(), range);
        }
        return cached;
      }
    })());
    return;
  }

  // Other API calls (moods, radio, health, scores): network only.
  if (url.pathname.startsWith("/api/")) return;

  // App shell: cache-first so the UI loads with no network.
  event.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(SHELL_CACHE).then((c) => c.put(req, copy)).catch(() => {});
      return res;
    }).catch(() => caches.match("/index.html")))
  );
});

// Messages from the page: cache or clear the current playlist.
self.addEventListener("message", (event) => {
  const data = event.data || {};
  const client = event.source;
  const reply = (msg) => client && client.postMessage(msg);

  if (data.type === "CACHE_TRACKS") {
    const paths = data.paths || [];  // list of /api/stream/.. and /api/cover/.. paths
    event.waitUntil((async () => {
      const cache = await caches.open(PLAYLIST_CACHE);
      let done = 0;
      for (const path of paths) {
        try {
          const res = await fetch(path, { cache: "no-store" });  // full 200, no Range
          if (res.ok) await cache.put(path, res.clone());
        } catch (e) { /* skip failures */ }
        done++;
        reply({ type: "CACHE_PROGRESS", done, total: paths.length });
      }
      reply({ type: "CACHE_DONE", total: paths.length });
    })());
  } else if (data.type === "CLEAR_PLAYLIST") {
    event.waitUntil(caches.delete(PLAYLIST_CACHE).then(() => reply({ type: "CACHE_CLEARED" })));
  }
});
