"use strict";

const loginForm = document.getElementById("portal-login");
const requestForm = document.getElementById("material-request-form");
const logout = document.getElementById("portal-logout");
const portalStatus = document.getElementById("portal-status");
const requestStatus = document.getElementById("request-status");
const fields = document.getElementById("request-fields");
const items = document.getElementById("request-items");
const rowTemplate = document.getElementById("request-item-template");
const addItem = document.getElementById("request-add-item");
let account;
let products = [];
let categories = [];
let pending;
let nextRowNumber = 1;

async function api(url, options) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) {
    const detail = typeof result.detail === "string" ? result.detail : "Check the required fields and try again.";
    throw Object.assign(new Error(detail), {status: response.status});
  }
  return result;
}

function addRow() {
  const row = rowTemplate.content.firstElementChild.cloneNode(true);
  const select = row.querySelector("select[name=product_id]");
  const category = row.querySelector("[data-category-filter]");
  const search = row.querySelector("input[type=search]");
  const quantity = row.querySelector("input[name=quantity]");
  const unit = row.querySelector("[data-product-unit]");
  const searchStatus = row.querySelector("[role=status]");
  const [categoryLabel, searchLabel, productLabel, quantityLabel] = row.querySelectorAll("label");
  category.id = categoryLabel.htmlFor = `request-category-${nextRowNumber}`;
  search.id = searchLabel.htmlFor = `request-search-${nextRowNumber}`;
  select.id = productLabel.htmlFor = `request-product-${nextRowNumber}`;
  quantity.id = quantityLabel.htmlFor = `request-quantity-${nextRowNumber}`;
  unit.id = `request-unit-${nextRowNumber}`;
  quantity.setAttribute("aria-describedby", unit.id);
  nextRowNumber += 1;
  category.replaceChildren(new Option("All categories", ""), ...categories.map(
    entry => new Option(entry.name, entry.id),
  ));

  function filterProducts() {
    const selected = select.value;
    const query = search.value.trim().toLowerCase();
    const matches = products.filter(product =>
      (!category.value || product.category_id === category.value) &&
      product.name.toLowerCase().includes(query),
    );
    // Searching again should never erase a product already picked for this row.
    const options = selected && !matches.some(product => product.id === selected)
      ? [products.find(product => product.id === selected), ...matches]
      : matches;
    select.replaceChildren(new Option("Select a product", ""), ...options.map(
      product => new Option(`${product.name} (${product.unit})`, product.id),
    ));
    select.value = selected;
    searchStatus.hidden = matches.length > 0;
    searchStatus.textContent = matches.length ? "" : "No products match these filters.";
  }
  search.addEventListener("input", filterProducts);
  category.addEventListener("change", filterProducts);
  select.addEventListener("change", () => {
    const product = products.find(entry => entry.id === select.value);
    unit.hidden = !product;
    unit.textContent = product ? `Unit: ${product.unit}` : "";
  });
  filterProducts();
  row.querySelector("button").addEventListener("click", () => {
    row.remove();
    if (items.childElementCount === 0) addRow();
    addItem.disabled = items.childElementCount >= 100;
  });
  items.append(row);
  addItem.disabled = items.childElementCount >= 100;
  return search;
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
  categories = catalog.categories;
  portalStatus.textContent = products.length
    ? `Signed in as ${user.username}.`
    : "The product catalog is empty. Ask the storekeeper to add items before requesting.";
  if (!products.length) return;
  items.replaceChildren();
  addRow();
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

addItem.addEventListener("click", () => addRow().focus());

requestForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (fields.disabled) return;
  if (!pending) {
    const details = Object.fromEntries(new FormData(requestForm));
    delete details.product_id;
    delete details.quantity;
    pending = {
      ...details,
      id: crypto.randomUUID(),
      items: Array.from(items.children, row => ({
        product_id: row.querySelector("select[name=product_id]").value,
        quantity: row.querySelector("input[name=quantity]").value.trim(),
      })),
    };
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
    items.replaceChildren();
    addRow();
    requestStatus.textContent = `Request submitted. Reference: ${receipt.id}`;
  } catch (error) {
    // If the response was lost, retry the same payload and ID. The database
    // can then return the original receipt without creating a second request.
    if (error.status >= 400 && error.status < 500) {
      pending = null;
      requestStatus.textContent = error.message;
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
  }
});

api("/api/session").then(session => showSession(session.user)).catch(() => {
  portalStatus.textContent = "The request portal could not connect. Reload when you are online.";
});
