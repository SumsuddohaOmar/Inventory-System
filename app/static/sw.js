/* Cache only our application shell. Never cache authenticated API responses,
   account pages or user data here; offline.js partitions those in IndexedDB. */
const SHELL="inventory-shell-0.4.2-product-delete-feedback";
const FILES=["/","/static/styles.css","/static/app.js","/static/stock.js","/static/auth.js","/static/offline.js","/static/workspace.js","/static/history-import.js","/static/manifest.webmanifest","/static/icon.svg"];
self.addEventListener("install",event=>event.waitUntil(caches.open(SHELL).then(cache=>cache.addAll(FILES))));
// Do not force activation while unsaved/offline work may be using an old shell.
self.addEventListener("activate",event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith("inventory-shell-")&&key!==SHELL).map(key=>caches.delete(key))))));
self.addEventListener("fetch",event=>{
  const url=new URL(event.request.url);
  if(event.request.method!=="GET"||url.origin!==location.origin||url.pathname.startsWith("/api/")) return;
  if(!FILES.includes(url.pathname))return;
  event.respondWith(fetch(event.request).catch(()=>caches.match(event.request)));
});
