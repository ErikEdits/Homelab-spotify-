// Minimaler Service Worker – macht Homify als App installierbar.
// Musik und API werden bewusst nicht zwischengespeichert (Streaming vom Server).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
