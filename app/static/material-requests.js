"use strict";

const loginForm = document.getElementById("portal-login");
const requestForm = document.getElementById("material-request-form");
const logout = document.getElementById("portal-logout");
const portalStatus = document.getElementById("portal-status");
const requestStatus = document.getElementById("request-status");
const fields = document.getElementById("request-fields");
const items = document.getElementById("request-items");
const rowTemplate = document.getElementById("request-item-template");
const category = document.getElementById("request-category");
const search = document.getElementById("request-search");
const noProducts = document.getElementById("request-no-products");
const review = document.getElementById("request-review");
const reviewCount = document.getElementById("request-item-count");
const reviewItems = document.getElementById("request-selected-items");
const itemsError = document.getElementById("request-items-error");
const historyPanel = document.getElementById("request-history");
const historyRefresh = document.getElementById("request-history-refresh");
const historyStatus = document.getElementById("request-history-status");
const historyResults = document.getElementById("request-history-results");
const historyOlder = document.getElementById("request-history-older");
const templatesPanel = document.getElementById("request-templates");
const templatesList = document.getElementById("request-template-list");
const templateName = document.getElementById("request-template-name");
const templateStatus = document.getElementById("request-template-status");
const duplicateConfirmation = document.getElementById("request-duplicate-confirmation");
const allowDuplicate = document.getElementById("request-allow-duplicate");
const editStatus = document.getElementById("request-edit-status");
const notification = document.getElementById("request-notification");
const quantities = new Map();
const itemRemarks = new Map();
let historyCursor;
let savedTemplates = [];
let quantityError;
let account;
let products = [];
let pending;
let replaces;
let syncingRequest = false;

const pendingKey = id => `hub16-request:${account.id}:${id}`;

async function api(url, options) {
  const response = await inventoryFetch(url, options);
  const result = await response.json();
  if (!response.ok) {
    const detail = result.detail;
    const message = typeof detail === "string" ? detail : detail?.message || "Check the required fields and try again.";
    throw Object.assign(new Error(message), {status: response.status, productId: detail?.product_id, code: detail?.code});
  }
  return result;
}

function renderReview() {
  const selected = products.filter(product => quantities.has(product.id));
  review.hidden = !selected.length;
  reviewCount.textContent = `Review request items (${selected.length})`;
  reviewItems.replaceChildren(...selected.map(product => {
    const entry = document.createElement("li");
    entry.textContent = `${product.name}: ${quantities.get(product.id)} ${product.unit}`;
    return entry;
  }));
}

function renderProducts() {
  const query = search.value.trim().toLowerCase();
  const matches = products.filter(product =>
    (!category.value || product.category_id === category.value) &&
    product.name.toLowerCase().includes(query),
  );
  // The list is only a view. A single draft keyed by product stays intact
  // when the category or search changes and contains each product only once.
  items.replaceChildren(...matches.map(product => {
    const row = rowTemplate.content.firstElementChild.cloneNode(true);
    const label = row.querySelector("label");
    const quantity = row.querySelector("input");
    const remark = row.querySelector(".request-item-remark");
    const remarkPanel = remark.closest("details");
    remark.id = `request-remark-${product.id}`;
    remarkPanel.querySelector("label").htmlFor = remark.id;
    remark.value = itemRemarks.get(product.id) ?? "";
    remarkPanel.open = Boolean(remark.value);
    remark.addEventListener("input", () => itemRemarks.set(product.id, remark.value));
    const feedback = row.querySelector(".request-row-error");
    quantity.id = label.htmlFor = `request-quantity-${product.id}`;
    feedback.id = `${quantity.id}-error`;
    if (quantityError?.productId === product.id) {
      feedback.textContent = quantityError.message;
      feedback.hidden = false;
      quantity.setAttribute("aria-invalid", "true");
      quantity.setAttribute("aria-describedby", feedback.id);
    }
    label.querySelector("strong").textContent = product.name;
    label.querySelector("small").textContent = product.unit;
    quantity.value = quantities.get(product.id) ?? "";
    quantity.addEventListener("input", () => {
      const value = quantity.value.trim();
      if (value) quantities.set(product.id, value);
      else quantities.delete(product.id);
      itemsError.hidden = true;
      if (quantityError?.productId === product.id) {
        quantityError = undefined;
        feedback.hidden = true;
        quantity.removeAttribute("aria-invalid");
        quantity.removeAttribute("aria-describedby");
      }
      renderReview();
    });
    return row;
  }));
  noProducts.hidden = matches.length > 0;
}

function copyRequestItems(itemsToCopy) {
  const available = new Set(products.map(product => product.id));
  let skipped = 0;
  for (const item of itemsToCopy) {
    if (!available.has(item.product_id)) skipped += 1;
    else if (!quantities.has(item.product_id)) quantities.set(item.product_id, item.quantity);
  }
  renderProducts();
  renderReview();
  return skipped ? `Available items added. ${skipped} unavailable items were skipped.`
    : "Items added to the draft. Existing quantities were kept.";
}

function requestProgress(record) {
  const pendingItems = record.items.some(item => Number(item.pending) > 0);
  if (record.cancelled_at) return "Cancelled";
  if (record.pending_action) return record.pending_action === "close" ? "Pending closed" : "Pending transferred";
  if (!record.approved_at) return "Awaiting approval";
  if (!record.issues.length) return record.prepared_at ? "Prepared" : "Approved";
  return pendingItems ? "Partially issued" : "Issued";
}

async function checkRequestNotifications() {
  if (!account || historyPanel.hidden || !navigator.onLine || document.hidden) return;
  const key = `hub16-progress:${account.id}`;
  try {
    const page = await api("/api/mrf/my-requests", {headers: {"X-Inventory-Owner": account.id}});
    const before = (await localGet("meta", key))?.value;
    const progress = Object.fromEntries(page.requests.map(record => [
      record.id, `${requestProgress(record)}:${record.issues.length}:${record.issues.filter(issue => issue.received).length}`,
    ]));
    if (before) {
      const changes = Object.entries(progress).filter(([id, state]) => id in before && before[id] !== state).length;
      if (changes) {
        notification.textContent = changes === 1 ? "A material request has an update. Open Request history to review it."
          : `${changes} material requests have updates. Open Request history to review them.`;
        notification.hidden = false;
      }
    }
    await localPut("meta", {id: key, value: progress});
  } catch {
    // Notifications resume on the next online check.
  }
}

async function loadTemplates() {
  savedTemplates = await api("/api/mrf/my-templates", {headers: {"X-Inventory-Owner": account.id}});
  templatesList.replaceChildren(new Option("Select a saved list", ""), ...savedTemplates.map(
    template => new Option(template.name, template.name),
  ));
}
templatesPanel.addEventListener("toggle", async () => {
  if (!templatesPanel.open) return;
  try {
    await loadTemplates();
  } catch (error) {
    templateStatus.textContent = error.message;
  }
});
document.getElementById("request-template-add").addEventListener("click", () => {
  const template = savedTemplates.find(entry => entry.name === templatesList.value);
  if (template) templateStatus.textContent = copyRequestItems(template.items);
});
document.getElementById("request-template-save").addEventListener("click", async event => {
  const button = event.currentTarget;
  const name = templateName.value.trim();
  if (!name || !quantities.size) {
    templateStatus.textContent = "Enter a list name and at least one quantity.";
    return;
  }
  button.disabled = true;
  try {
    await api("/api/mrf/my-templates", {
      method: "PUT", headers: {"Content-Type": "application/json", "X-Inventory-Owner": account.id},
      body: JSON.stringify({name, items: Array.from(quantities, ([product_id, quantity]) => ({product_id, quantity}))}),
    });
    await loadTemplates();
    templatesList.value = name;
    templateStatus.textContent = "Item list saved.";
  } catch (error) {
    templateStatus.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

async function loadRequestHistory(before = null) {
  if (historyRefresh.disabled) return;
  historyRefresh.disabled = historyOlder.disabled = true;
  historyStatus.textContent = "Loading request history…";
  try {
    const query = before ? `?before=${encodeURIComponent(before)}` : "";
    const page = await api(`/api/mrf/my-requests${query}`, {
      headers: {"X-Inventory-Owner": account.id},
    });
    if (!before) historyResults.replaceChildren();
    for (const record of page.requests) {
      const entry = document.createElement("li");
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      const receivedCount = record.issues.filter(issue => issue.received).length;
      const status = requestProgress(record);
      summary.textContent = `${record.work_date} · ${record.shift} · ${status}`;
      details.append(summary);
      const info = document.createElement("p");
      info.textContent = `Reference: ${record.id} · ${receivedCount} of ${record.issues.length} issues received`;
      details.append(info);
      const lines = document.createElement("ul");
      for (const item of record.items) {
        const line = document.createElement("li");
        const remaining = record.pending_action ? "Unissued" : "Pending";
        line.textContent = `${item.product} · Requested ${item.quantity} ${item.unit} · Issued ${item.issued} · ${remaining} ${item.pending}`;
        lines.append(line);
      }
      details.append(lines);
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "secondary";
      copy.textContent = "Add items to new request";
      copy.addEventListener("click", () => {
        if (pending) {
          historyStatus.textContent = "Finish retrying the current request before copying items.";
          return;
        }
        historyStatus.textContent = copyRequestItems(record.items);
      });
      details.append(copy);
      if (!record.approved_at && !record.cancelled_at) {
        const edit = document.createElement("button");
        edit.type = "button";
        edit.className = "secondary";
        edit.textContent = "Edit request";
        edit.addEventListener("click", () => {
          if (pending || quantities.size) {
            historyStatus.textContent = "Finish the current draft before editing another request.";
            return;
          }
          replaces = record.id;
          for (const name of ["requester_name", "employee_id", "requester_position", "shift", "work_date", "notes", "priority", "required_date"]) {
            requestForm.elements.namedItem(name).value = record[name] ?? "";
          }
          itemRemarks.clear();
          for (const item of record.items) if (item.remarks) itemRemarks.set(item.product_id, item.remarks);
          historyStatus.textContent = copyRequestItems(record.items);
          editStatus.hidden = false;
          requestForm.elements.namedItem("requester_name").focus();
        });
        details.append(edit);
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "secondary";
        cancel.textContent = "Cancel request";
        cancel.addEventListener("click", async () => {
          if (cancel.textContent === "Cancel request") {
            cancel.textContent = "Confirm cancellation";
            return;
          }
          cancel.disabled = true;
          try {
            await api(`/api/mrf/my-requests/${record.id}/cancel`, {
              method: "POST", headers: {"X-Inventory-Owner": account.id},
            });
            await loadRequestHistory();
          } catch (error) {
            historyStatus.textContent = error.message;
            cancel.disabled = false;
          }
        });
        details.append(cancel);
      }
      entry.append(details);
      historyResults.append(entry);
    }
    historyCursor = page.next;
    historyOlder.hidden = !historyCursor;
    historyStatus.textContent = historyResults.childElementCount ? "" : "No requests have been submitted from this account.";
  } catch (error) {
    historyStatus.textContent = error.message;
  } finally {
    historyRefresh.disabled = historyOlder.disabled = false;
  }
}

historyPanel.addEventListener("toggle", () => {
  if (historyPanel.open) {
    notification.hidden = true;
    void loadRequestHistory();
  }
});
historyRefresh.addEventListener("click", () => loadRequestHistory());
historyOlder.addEventListener("click", () => loadRequestHistory(historyCursor));

async function showSession(user) {
  account = user;
  loginForm.hidden = Boolean(user);
  logout.hidden = !user;
  requestForm.hidden = true;
  historyPanel.hidden = true;
  if (!user) {
    portalStatus.textContent = "";
    return;
  }
  if (!["mrf_catalog", "mrf_submit"].every(permission => user.permissions.includes(permission))) {
    portalStatus.textContent = "This account cannot submit material requests. Sign out to use a request account.";
    return;
  }
  historyPanel.hidden = false;
  void checkRequestNotifications();
  const catalog = await api("/api/mrf/catalog", {
    headers: {"X-Inventory-Owner": user.id},
  });
  products = catalog.products;
  category.replaceChildren(new Option("All categories", ""), ...catalog.categories.map(
    entry => new Option(entry.name, entry.id),
  ));
  portalStatus.textContent = products.length
    ? `Signed in as ${user.username}.`
    : "The product catalog is empty. Ask the storekeeper to add items before requesting.";
  if (!products.length) return;
  renderProducts();
  requestForm.hidden = false;
  await restorePendingRequest();
}

async function restorePendingRequest() {
  const prefix = `hub16-request:${account.id}:`;
  const saved = (await localAll("meta").catch(() => [])).find(row => row.id.startsWith(prefix));
  if (!saved) return;
  pending = saved.value;
  replaces = pending.replaces;
  editStatus.hidden = !replaces;
  duplicateConfirmation.hidden = !pending.allow_duplicate;
  allowDuplicate.checked = Boolean(pending.allow_duplicate);
  for (const name of ["requester_name", "employee_id", "requester_position", "shift", "work_date", "notes", "priority", "required_date"]) {
    requestForm.elements.namedItem(name).value = pending[name] ?? (name === "priority" ? "normal" : "");
  }
  quantities.clear();
  itemRemarks.clear();
  for (const item of pending.items) {
    quantities.set(item.product_id, item.quantity);
    if (item.remarks) itemRemarks.set(item.product_id, item.remarks);
  }
  renderProducts();
  renderReview();
  requestStatus.textContent = "A request is saved on this device. Select Submit request to retry.";
  if (navigator.onLine) void sendRequest();
}

loginForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = loginForm.querySelector("button");
  button.disabled = true;
  try {
    const {username, password, remember} = loginForm.elements;
    const user = await api("/api/session", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({username: username.value, password: password.value, remember: remember.checked}),
    });
    loginForm.reset();
    await showSession(user);
  } catch (error) {
    portalStatus.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

logout.addEventListener("click", async () => {
  try {
    await api("/api/logout", {method: "POST"});
    location.reload();
  } catch (error) {
    portalStatus.textContent = error.message;
  }
});

category.addEventListener("change", () => {
  search.value = "";
  renderProducts();
});
search.addEventListener("input", renderProducts);

requestForm.addEventListener("submit", async event => {
  event.preventDefault();
  await sendRequest();
});

window.addEventListener("online", () => {
  if (pending) void sendRequest();
  void checkRequestNotifications();
});
setInterval(checkRequestNotifications, 45000);

async function sendRequest() {
  if (syncingRequest) return;
  if (!pending) {
    if (!quantities.size || quantities.size > 100) {
      requestStatus.textContent = "";
      itemsError.textContent = "Enter quantities for 1 to 100 items in one request.";
      itemsError.hidden = false;
      itemsError.focus();
      return;
    }
    const details = Object.fromEntries(new FormData(requestForm));
    if (!details.required_date) delete details.required_date;
    pending = {
      ...details,
      ...(replaces ? {replaces} : {}),
      id: inventoryUUID(),
      allow_duplicate: allowDuplicate.checked,
      items: Array.from(quantities, ([product_id, quantity]) => ({
        product_id, quantity, remarks: itemRemarks.get(product_id) ?? "",
      })),
    };
  }
  syncingRequest = true;
  itemsError.hidden = true;
  if (quantityError) {
    quantityError = undefined;
    renderProducts();
  }
  fields.disabled = true;
  logout.disabled = true;
  requestStatus.textContent = "Submitting request…";
  try {
    // Keep the operation ID and payload before sending. If the response is
    // lost, reconnect or reload can replay the exact same submission.
    const stored = await localPut("meta", {id: pendingKey(pending.id), value: pending}).then(() => true, () => false);
    if (!navigator.onLine) {
      requestStatus.textContent = stored
        ? "Saved on this device. The request will retry when you reconnect."
        : "Offline storage is unavailable. Keep this page open and reconnect to retry.";
      return;
    }
    const receipt = await api("/api/mrf/requests", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Inventory-Owner": account.id},
      body: JSON.stringify(pending),
    });
    await localDelete("meta", pendingKey(pending.id)).catch(() => {});
    pending = null;
    replaces = undefined;
    editStatus.hidden = true;
    requestForm.reset();
    duplicateConfirmation.hidden = true;
    quantities.clear();
    itemRemarks.clear();
    review.open = false;
    renderProducts();
    renderReview();
    requestStatus.textContent = `Request submitted. Reference: ${receipt.id}`;
    if (historyPanel.open) void loadRequestHistory();
  } catch (error) {
    // If the response was lost, retry the same payload and ID. The database
    // can then return the original receipt without creating a second request.
    if (error.status === 401 || error.status === 403 ||
        (error.status === 409 && error.message.includes("signed-in account"))) {
      requestStatus.textContent = "Sign in again with this account to retry the saved request.";
    } else if (error.status >= 400 && error.status < 500) {
      await localDelete("meta", pendingKey(pending.id)).catch(() => {});
      pending = null;
      const product = products.find(entry => entry.id === error.productId);
      if (product) {
        // Reveal the row even when the user submitted from another category.
        quantityError = {productId: product.id, message: error.message};
        category.value = product.category_id || "";
        if (category.selectedIndex < 0) category.value = "";
        search.value = "";
        renderProducts();
        requestStatus.textContent = "";
      } else {
        requestStatus.textContent = error.message;
        if (error.code === "duplicate_items") {
          duplicateConfirmation.hidden = false;
          allowDuplicate.checked = false;
        }
      }
    } else {
      requestStatus.textContent = "Submission could not be confirmed. Select Submit again to retry the same request.";
    }
  } finally {
    syncingRequest = false;
    fields.disabled = false;
    logout.disabled = false;
    // Keep the visible draft in sync with the pending payload during a retry.
    for (const control of fields.querySelectorAll("input, select, textarea, button[type=button]")) {
      control.disabled = Boolean(pending);
    }
    if (quantityError) {
      const quantity = document.getElementById(`request-quantity-${quantityError.productId}`);
      quantity?.scrollIntoView({block: "center"});
      quantity?.focus({preventScroll: true});
    }
  }
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
api("/api/session").then(session => showSession(session.user)).catch(() => {
  portalStatus.textContent = "Connect to sign in. Requests saved on this device can retry after you sign in.";
});
