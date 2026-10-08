// One Click Downloader - Service Worker
const CACHE_NAME = 'oneclick-v1';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  // Pass-through network requests
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});
