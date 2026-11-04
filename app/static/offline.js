/* Account-partitioned IndexedDB snapshots and a durable stock outbox.
   The service worker caches only the shell. API data is never put in a shared
   HTTP cache. Pending operations survive reloads and are not deleted on errors.
   Browser storage can still be cleared by Android/user actions: it is not a
   guaranteed backup. Each saved stock operation carries an idempotency UUID. */
"use strict";
let currentUser = null;
let syncing = false;

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
  try {
    const response = await inventoryFetch(url, options);
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
        const snapshot = await localGet("cache", `${currentUser.id}:/api/catalog`);
        if (snapshot) {
          const category = new URL(url, location.origin).searchParams.get("category_id");
          const products = snapshot.value.filter(product => !product.archived &&
            (!category || (category === "unassigned" ? !product.category_id : product.category_id === category)));
          return new Response(JSON.stringify(products), {headers:{"Content-Type":"application/json"}});
        }
      }
    }
    const match = url.match(/^\/api\/products\/([^/]+)\/stock$/);
    if (options.method === "POST" && match && currentUser) {
      const payload = JSON.parse(options.body);
      const rows = (await localAll("outbox")).filter(row => row.owner === currentUser.id && row.product_id === match[1]);
      await localPut("outbox", {id:payload.id, owner:currentUser.id, product_id:match[1], payload,
        depends_on:rows.filter(row=>row.id!==payload.id).map(row=>row.id), state:"pending", created:Date.now()});
      await renderQueue();
      return new Response(JSON.stringify({queued:true}), {status:202,headers:{"Content-Type":"application/json"}});
    }
    throw error;
  }
}

async function renderQueue() {
  const target = document.querySelector("#sync-queue");
  if (!target || !currentUser) return;
  const rows = (await localAll("outbox")).filter(row=>row.owner===currentUser.id).sort((a,b)=>a.created-b.created);
  document.querySelector("#sync-status").textContent = `${navigator.onLine ? "Connection available" : "Offline"} · ${rows.length} entries pending`;
  target.replaceChildren();
  for (const row of rows) {
    const item = document.createElement("li");
    const text = document.createElement("p");
    text.textContent = `${row.payload.effective_date} · ${row.payload.kind} ${row.payload.quantity} · ${row.state}${row.reason ? `: ${row.reason}`:""}`;
    item.append(text);
    if (row.state === "held" && currentUser.permissions.includes("correct")) {
      const label = document.createElement("label");
      const amount = document.createElement("input");
      amount.id = `resolve-${row.id}`;
      amount.value = row.payload.quantity;
      label.htmlFor = amount.id;
      label.textContent = "Reviewed quantity";
      const retry = document.createElement("button");
      retry.textContent = "Keep original & retry reviewed entry";
      retry.addEventListener("click", async()=>{
        // Preserve the rejected version before replacing the active queued row.
        await localPut("conflicts", {...row, reviewed_at:Date.now()});
        const identity = inventoryUUID();
        const next = {...row,id:identity,payload:{...row.payload,id:identity,quantity:amount.value},state:"pending",reason:"",resolves_id:row.id};
        await localPut("outbox", next);
        for (const dependent of rows.filter(value=>value.depends_on.includes(row.id))) {
          await localPut("outbox", {...dependent,state:"pending",depends_on:dependent.depends_on.map(value=>value===row.id?identity:value)});
        }
        await localDelete("outbox", row.id);
        await syncPending();
      });
      item.append(label,amount,retry);
    }
    target.append(item);
  }
}

async function syncPending() {
  if (syncing || !currentUser) return;
  syncing = true;
  try {
    const held=(await localAll("outbox")).filter(row=>row.owner===currentUser.id&&row.state==="held");
    if(held.length){const checked=await fetch("/api/sync/status",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ids:held.slice(0,200).map(row=>row.id)})});if(checked.ok)for(const row of await checked.json())if(row.resolved)await localDelete("outbox",row.id);}
    const rows = (await localAll("outbox")).filter(row=>row.owner===currentUser.id&&["pending","waiting"].includes(row.state)).sort((a,b)=>a.created-b.created);
    if (!rows.length) return;
    const response = await fetch("/api/sync", {method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({operations:rows.slice(0,200).map(row=>({...row.payload,product_id:row.product_id,depends_on:row.depends_on,resolves_id:row.resolves_id}))})});
    if (!response.ok) { document.querySelector("#sync-status").textContent="Sync needs sign-in or permission review. All pending entries are kept."; return; }
    for (const result of await response.json()) {
      if (result.status === "saved" || result.status === "resolved") await localDelete("outbox", result.id);
      else {
        const row = rows.find(value=>value.id===result.id);
        if (row) await localPut("outbox", {...row,state:result.status==="waiting"?"waiting":"held",reason:result.reason});
      }
    }
  } catch { /* Reachability, not navigator.onLine, determines actual sync success. */ }
  finally { syncing = false; await renderQueue(); }
}

window.addEventListener("online", ()=>syncPending());
window.addEventListener("beforeunload", (event)=>{
  // A visible pending count is persistent; this extra warning is best-effort.
  if (document.querySelector("#sync-queue")?.children.length) { event.preventDefault(); event.returnValue=""; }
});
setInterval(()=>{ if(currentUser) syncPending(); },15000);
