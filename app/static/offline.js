/* Account-partitioned IndexedDB snapshots and a durable stock outbox.
   The service worker caches only the shell. API data is never put in a shared
   HTTP cache. Pending operations survive reloads and are not deleted on errors.
   Browser storage can still be cleared by Android/user actions: it is not a
   guaranteed backup. Each saved stock operation carries an idempotency UUID. */
"use strict";
let currentUser = null;
let syncing = false;
let pendingCount = 0;

// IndexedDB belongs to this browser profile AND website origin. It is never
// uploaded as a queue or copied to another device. Only individual stock
// operations are sent to this website's cloud API. Missing metadata fails closed.
function cloudSyncEnabled() {
  return document.querySelector('meta[name="inventory-cloud-sync"]')?.content === "enabled";
}
const accountRows = (rows, owner) => rows.filter(row => row.owner === owner &&
  (!row.origin || row.origin === location.origin)); // Keep old queues readable.

// Older Safari versions have secure random bytes but may lack randomUUID.
function inventoryUUID() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
}

async function inventoryFetch(url, options = {}) {
  if (options.signal) return fetch(url, options);
  if (typeof AbortSignal.timeout === "function") {
    return fetch(url, {...options, signal: AbortSignal.timeout(12000)});
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12000);
  try {
    return await fetch(url, {...options, signal: controller.signal});
  } finally {
    clearTimeout(timer);
  }
}

async function localDB() {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("local-inventory-v1", 1);
    open.onupgradeneeded = () => {
      for (const name of ["cache", "outbox", "meta", "conflicts"]) open.result.createObjectStore(name, {keyPath:"id"});
    };
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
}

async function localStore(name, mode, action) {
  const db = await localDB();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(name, mode);
    const operation = action(transaction.objectStore(name));
    let result;
    operation.onsuccess = () => { result = operation.result; };
    transaction.oncomplete = () => { db.close(); resolve(result); };
    transaction.onerror = transaction.onabort = () => { db.close(); reject(transaction.error); };
  });
}
const localGet = (store, id) => localStore(store, "readonly", (table) => table.get(id));
const localPut = (store, value) => localStore(store, "readwrite", (table) => table.put(value));
const localAll = (store) => localStore(store, "readonly", (table) => table.getAll());
const localDelete = (store, id) => localStore(store, "readwrite", (table) => table.delete(id));

async function offlineRequest(url, options={}) {
  /* Only stock creates are queued automatically. Corrections require explicit
     online revision review; never silently turn every failed write into sync. */
  const cacheable = !options.method && currentUser && (url==="/openapi.json" || /^\/api\/(products|categories|catalog|company)/.test(url));
  const key = currentUser ? `${currentUser.id}:${url}` : "";
  const owner = currentUser?.id;
  if (owner) {
    options = {...options, headers:{...options.headers, "X-Inventory-Owner":owner}};
  }
  const stockCreate = options.method === "POST" && /^\/api\/products\/([^/]+)\/stock$/.exec(url);
  let queued;
  if (stockCreate && owner && cloudSyncEnabled()) {
    // Persist BEFORE sending: closing a tab or losing the response must not
    // lose the retry UUID. Never overwrite a held row with an automatic retry.
    const payload = JSON.parse(options.body);
    queued = await localGet("outbox", payload.id);
    const existing = !!queued;
    if (!queued) {
      const rows = accountRows(await localAll("outbox"), owner);
      queued = {id:payload.id, owner, origin:location.origin, product_id:stockCreate[1], payload,
        depends_on:rows.filter(row=>row.product_id===stockCreate[1]).map(row=>row.id), state:"pending", created:Date.now()};
      try { await localPut("outbox", queued); }
      catch {
        const error = new Error("Nothing was sent: browser storage is unavailable. Keep your typed entry and free storage before retrying.");
        error.rejected = true;
        throw error;
      }
    }
    if (queued.owner !== owner || (queued.origin && queued.origin !== location.origin) || JSON.stringify(queued.payload) !== JSON.stringify(payload)) {
      throw new Error("This save ID belongs to a different pending entry. Review pending entries first.");
    }
    await renderQueue();
    options = {...options, headers:{...options.headers, "X-Inventory-Owner":owner}};
    // Dependencies and held conflicts must use the server review/sync path.
    if (existing || queued.state !== "pending" || queued.depends_on.length) {
      return new Response(JSON.stringify({queued:true}), {status:202});
    }
  }
  try {
    const response = await inventoryFetch(url, options);
    if (queued) {
      if (response.ok || (response.status >= 400 && response.status < 500)) {
        // A definite rejection leaves the original form/input intact. An
        // uncertain server failure stays queued under the SAME retry identity.
        try {
          await localDelete("outbox", queued.id);
          await renderQueue();
        } catch {
          // A browser cleanup failure does not undo a confirmed cloud result.
          // If the row remains, its UUID makes the later retry harmless.
          document.querySelector("#sync-status").textContent = "Cloud replied, but browser queue cleanup failed. Keep browser data and retry sync; the same save ID prevents duplicates.";
        }
      } else {
        return new Response(JSON.stringify({queued:true}), {status:202});
      }
    }
    if (response.ok && cacheable) {
      // A full browser disk must not turn a confirmed server save/read into a
      // network failure. Warn that this device cannot promise offline reads.
      try { await localPut("cache", {id:key, value:await response.clone().json()}); }
      catch { document.querySelector("#sync-status").textContent = "Browser storage unavailable. Online data works; offline copies could not be saved."; }
    }
    return response;
  } catch (error) {
    if (cacheable) {
      const saved = await localGet("cache", key);
      if (saved) return new Response(JSON.stringify(saved.value), {headers:{"Content-Type":"application/json"}});
      // Category buttons may be used for the first time after disconnecting.
      // Derive these lists from the complete account-specific catalog instead
      // of requiring every possible filter URL to have been visited online.
      if (url === "/api/products" || url.startsWith("/api/products?")) {
        const snapshot = await localGet("cache", `${owner}:/api/catalog`);
        if (snapshot) {
          const category = new URL(url, location.origin).searchParams.get("category_id");
          const products = snapshot.value.filter(product => !product.archived &&
            (!category || (category === "unassigned" ? !product.category_id : product.category_id === category)));
          return new Response(JSON.stringify(products), {headers:{"Content-Type":"application/json"}});
        }
      }
    }
    if (queued) {
      await renderQueue();
      return new Response(JSON.stringify({queued:true}), {status:202,headers:{"Content-Type":"application/json"}});
    }
    throw error;
  }
}

async function renderQueue() {
  const target = document.querySelector("#sync-queue");
  if (!target || !currentUser) return;
  const owner = currentUser.id;
  const rows = accountRows(await localAll("outbox"), owner).sort((a,b)=>a.created-b.created);
  if (currentUser?.id !== owner) return;
  pendingCount = rows.length;
  document.querySelector("#sync-status").textContent = `${navigator.onLine ? "Connection available" : "Offline"} · ${rows.length} entries pending on this browser${cloudSyncEnabled() ? "" : " · Cloud sync unavailable here"}`;
  target.replaceChildren();
  for (const row of rows) {
    const item = document.createElement("li");
    const text = document.createElement("p");
    const productName = typeof catalog !== "undefined" ? catalog.find(product=>product.id===row.product_id)?.name : null;
    text.textContent = `${productName || row.product_id} · ${row.payload.effective_date} · ${row.payload.kind} ${row.payload.quantity} · ${row.state}${row.reason ? `: ${row.reason}`:""}`;
    item.append(text);
    if (row.state === "held" && row.reviewable !== false && currentUser.permissions.includes("correct")) {
      const label = document.createElement("label");
      const amount = document.createElement("input");
      amount.id = `resolve-${row.id}`;
      amount.value = row.payload.quantity;
      label.htmlFor = amount.id;
      label.textContent = "Reviewed quantity";
      const retry = document.createElement("button");
      retry.textContent = "Keep original & retry reviewed entry";
      retry.addEventListener("click", async()=>{
        if (currentUser?.id !== owner || !cloudSyncEnabled()) return;
        retry.disabled = true;
        try {
        // Preserve the rejected version before replacing the active queued row.
        await localPut("conflicts", {...row, reviewed_at:Date.now()});
        const identity = inventoryUUID();
        const next = {...row,id:identity,payload:{...row.payload,id:identity,quantity:amount.value},state:"pending",reason:"",resolves_id:row.id};
        await localPut("outbox", next);
        for (const dependent of accountRows(await localAll("outbox"), owner).filter(value=>value.depends_on.includes(row.id))) {
          await localPut("outbox", {...dependent,state:"pending",depends_on:dependent.depends_on.map(value=>value===row.id?identity:value)});
        }
        await localDelete("outbox", row.id);
        await syncPending();
        } finally { retry.disabled = false; }
      });
      item.append(label,amount,retry);
    }
    target.append(item);
  }
}

async function syncPending() {
  if (syncing || !currentUser || !cloudSyncEnabled()) return;
  syncing = true;
  const owner = currentUser.id;
  let notice = "";
  try {
    // Cookies are shared between tabs. Verify the live account, then bind every
    // write to it on the SERVER as well, closing the sign-in-change race.
    const session = await inventoryFetch("/api/session");
    if (!session.ok || (await session.json()).user?.id !== owner || currentUser?.id !== owner) {
      notice = "Sign back in to the original account to sync. All pending entries are kept.";
      return;
    }
    const headers = {"Content-Type":"application/json", "X-Inventory-Owner":owner};
    const held=accountRows(await localAll("outbox"), owner).filter(row=>row.state==="held");
    if(held.length){const checked=await inventoryFetch("/api/sync/status",{method:"POST",headers,body:JSON.stringify({ids:held.slice(0,200).map(row=>row.id)})});if(checked.ok)for(const row of await checked.json())if(row.resolved)await localDelete("outbox",row.id);}
    const rows = accountRows(await localAll("outbox"), owner).filter(row=>["pending","waiting"].includes(row.state)).sort((a,b)=>a.created-b.created);
    if (!rows.length) return;
    if (currentUser?.id !== owner) return;
    const response = await inventoryFetch("/api/sync", {method:"POST",headers,
      body:JSON.stringify({operations:rows.slice(0,200).map(row=>({...row.payload,product_id:row.product_id,depends_on:row.depends_on,resolves_id:row.resolves_id}))})});
    if (!response.ok) { notice="Sync needs sign-in or permission review. All pending entries are kept."; return; }
    const sent = rows.slice(0,200);
    for (const result of await response.json()) {
      if (!sent.some(row=>row.id===result.id)) continue;
      if (result.status === "saved" || result.status === "resolved") await localDelete("outbox", result.id);
      else {
        const row = rows.find(value=>value.id===result.id);
        if (row) await localPut("outbox", {...row,state:result.status==="waiting"?"waiting":"held",reason:result.reason,reviewable:result.reviewable});
      }
    }
  } catch { notice="Cloud could not be reached. All pending entries are kept on this browser."; }
  finally {
    syncing = false;
    try { await renderQueue(); }
    catch { notice="Browser storage is unavailable. Do not clear browser data; retry after freeing storage."; }
    if (notice && currentUser?.id === owner) document.querySelector("#sync-status").textContent += ` · ${notice}`;
  }
}

window.addEventListener("online", ()=>syncPending());
window.addEventListener("beforeunload", (event)=>{
  // A visible pending count is persistent; this extra warning is best-effort.
  if (pendingCount) { event.preventDefault(); event.returnValue=""; }
});
setInterval(()=>{ if(currentUser) syncPending(); },15000);
