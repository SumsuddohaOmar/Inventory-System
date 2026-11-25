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
const quantities = new Map();
let quantityError;
let account;
let products = [];
let pending;

async function api(url, options) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) {
    const detail = result.detail;
    const message = typeof detail === "string" ? detail : detail?.message || "Check the required fields and try again.";
    throw Object.assign(new Error(message), {status: response.status, productId: detail?.product_id});
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
async function showSession(user) {
  account = user;
  loginForm.hidden = Boolean(user);
  logout.hidden = !user;
  requestForm.hidden = true;
  if (!user) {
    portalStatus.textContent = "";
    return;
  }
  if (!["mrf_catalog", "mrf_submit"].every(permission => user.permissions.includes(permission))) {
    portalStatus.textContent = "This account cannot submit material requests. Sign out to use a request account.";
    return;
  }
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
  if (fields.disabled) return;
  if (!pending) {
    if (!quantities.size || quantities.size > 100) {
      requestStatus.textContent = "";
      itemsError.textContent = "Enter quantities for 1 to 100 items in one request.";
      itemsError.hidden = false;
      itemsError.focus();
      return;
    }
    pending = {
      ...Object.fromEntries(new FormData(requestForm)),
      id: crypto.randomUUID(),
      items: Array.from(quantities, ([product_id, quantity]) => ({product_id, quantity})),
    };
  }
  itemsError.hidden = true;
  if (quantityError) {
    quantityError = undefined;
    renderProducts();
  }
  fields.disabled = true;
  logout.disabled = true;
  requestStatus.textContent = "Submitting request…";
  try {
    const receipt = await api("/api/mrf/requests", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Inventory-Owner": account.id},
      body: JSON.stringify(pending),
    });
    pending = null;
    requestForm.reset();
    quantities.clear();
    review.open = false;
    renderProducts();
    renderReview();
    requestStatus.textContent = `Request submitted. Reference: ${receipt.id}`;
  } catch (error) {
    // If the response was lost, retry the same payload and ID. The database
    // can then return the original receipt without creating a second request.
    if (error.status >= 400 && error.status < 500) {
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
      }
    } else {
      requestStatus.textContent = "Submission could not be confirmed. Select Submit again to retry the same request.";
    }
  } finally {
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
});

api("/api/session").then(session => showSession(session.user)).catch(() => {
  portalStatus.textContent = "The request portal could not connect. Reload when you are online.";
});
