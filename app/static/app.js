/* Browser controller: submit JSON to FastAPI and display the saved records.
   SQLite stays on the server. A successful fetch alone is not enough: always
   check the HTTP status, because fetch does not reject for 4xx/5xx responses. */
"use strict";

const form = document.querySelector("#product-form");
const save = document.querySelector("#save");
const refresh = document.querySelector("#refresh");
const status = document.querySelector("#status");
const products = document.querySelector("#products");
const count = document.querySelector("#count");
const categoryInput = document.querySelector("#category");
const categoryButtons = document.querySelector("#category-buttons");
let categories = [];
let selectedCategory = ""; // Empty means all categories; never stored as an ID.
let listRequest = 0; // Ignore older responses when filters are clicked quickly.

function addOptions(select, placeholder) {
  /* Both the new-product selector and existing-product selector use IDs from
     SQLite. Labels can change later without changing a product's category ID. */
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = placeholder;
  select.append(empty);
  for (const category of categories) {
    const option = document.createElement("option");
    option.value = category.id;
    option.textContent = category.name;
    select.append(option);
  }
}

function drawFilters() {
  /* Native buttons support keyboard interaction; aria-pressed announces which
     filter is active. Include Unassigned to make old products easy to find. */
  categoryButtons.replaceChildren();
  for (const category of [{ id: "", name: "All products" }, ...categories, { id: "unassigned", name: "Unassigned" }]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = category.name;
    button.setAttribute("aria-pressed", String(selectedCategory === category.id));
    button.addEventListener("click", async () => {
      selectedCategory = category.id;
      // Update state in place so keyboard focus remains on the chosen button.
      for (const sibling of categoryButtons.children) sibling.setAttribute("aria-pressed", String(sibling === button));
      if (categories.some((entry) => entry.id === category.id)) categoryInput.value = category.id;
      try { await loadProducts(); status.textContent = `Showing ${category.name.toLowerCase()}.`; }
      catch { status.textContent = "Could not load that category. Try Refresh list."; }
    });
    categoryButtons.append(button);
  }
}

function assignmentForm(product) {
  /* Each existing product can be assigned without re-creating it. Keeping its
     UUID preserves identity for future transactions. Every select has a label. */
  const editor = document.createElement("form");
  editor.className = "assignment";
  const label = document.createElement("label");
  const select = document.createElement("select");
  select.id = `category-${product.id}`;
  select.required = true;
  label.htmlFor = select.id;
  label.textContent = `Category for ${product.name}`;
  addOptions(select, "Choose a category");
  select.value = product.category_id || "";
  const button = document.createElement("button");
  button.type = "submit";
  button.className = "secondary";
  button.textContent = "Save category";
  editor.append(label, select, button);
  editor.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    try {
      await request(`/api/products/${encodeURIComponent(product.id)}/category`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category_id: select.value }),
      });
      status.textContent = `Category saved for ${product.name}.`;
      // Refresh may remove the edited row from the current category. Restore
      // focus to a stable control instead of leaving it on a detached element.
      try { await loadProducts(); await loadWorkspace(); }
      catch { status.textContent = "Category was saved, but the list could not refresh. Use Refresh list."; }
      refresh.focus();
    } catch (error) {
      status.textContent = `Category save could not be confirmed. ${error.message} Refresh the list before retrying.`;
    } finally { button.disabled = false; }
  });
  return editor;
}

async function request(url, options = {}) {
  /* Return decoded JSON or throw a readable error; callers keep form data on
     failure. Never automatically retry writes: a lost response may follow a
     successful database commit, and retrying could create a second record. */
  const response = await offlineRequest(url, options);
  if (!response.ok) {
    let body;
    try { body = await response.json(); } catch { body = {}; }
    const error = new Error(typeof body.detail === "string" ? body.detail : "Please check the entered values and try again.");
    // A validation/conflict response means the server rejected the operation;
    // a connection failure has an uncertain outcome and needs a history check.
    error.rejected = response.status >= 400 && response.status < 500;
    throw error;
  }
  return response.json();
}

async function loadProducts() {
  /* Replace the list using DOM nodes and textContent. User-supplied product
     names are text, never HTML; this prevents stored script injection. */
  const requestNumber = ++listRequest;
  const query = selectedCategory ? `?category_id=${encodeURIComponent(selectedCategory)}` : "";
  const records = await request(`/api/products${query}`);
  if (requestNumber !== listRequest) return;
  products.replaceChildren();
  for (const product of records) {
    const item = document.createElement("li");
    const name = document.createElement("strong");
    const unit = document.createElement("span");
    const category = document.createElement("span");
    name.textContent = product.name;
    unit.textContent = `Unit: ${product.unit}`;
    category.textContent = `Category: ${categories.find((entry) => entry.id === product.category_id)?.name || "Unassigned"}`;
    item.append(name, unit, category, stockDetails(product), assignmentForm(product));
    products.append(item);
  }
  count.textContent = records.length ? `${records.length} product${records.length === 1 ? "" : "s"} shown.` : "No products in this view yet. Choose another category or add a product.";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault(); // Keep this page open rather than navigating on submit.
  if (save.disabled) return; // Ignore a second submission while one is in flight.
  const name = form.elements.name.value.trim();
  const unit = form.elements.unit.value.trim();
  const category_id = categoryInput.value;
  const precision = Number(form.elements.precision.value);
  if (!category_id) { status.textContent = "Choose a category for this product."; categoryInput.focus(); return; }
  if (!name || !unit) {
    status.textContent = "Enter a product name and unit; spaces alone are not enough.";
    (!name ? form.elements.name : form.elements.unit).focus();
    return;
  }
  save.disabled = true;
  status.textContent = "Saving product…";
  try {
    await request("/api/products", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, unit, category_id, precision }),
    });
    // Reset only after confirmed persistence. A refresh failure must not imply
    // that the save failed: users might otherwise accidentally save it twice.
    form.reset();
    categoryInput.value = category_id; // Convenient for several products in one category.
    // Reveal the saved record even if the user was browsing another category.
    selectedCategory = category_id;
    drawFilters();
    status.textContent = `${name} saved to SQLite.`;
    try { await loadProducts(); await loadWorkspace(); }
    catch { status.textContent = `${name} was saved, but the list could not refresh. Use Refresh list.`; }
    form.elements.name.focus();
  } catch (error) {
    status.textContent = `Save could not be confirmed. ${error.message} If the connection was interrupted, refresh the list before trying again.`;
  } finally {
    save.disabled = false; // Also restore the button after errors.
  }
});

refresh.addEventListener("click", async () => {
  refresh.disabled = true;
  try { await initialize(); await loadWorkspace(); status.textContent = "Product list refreshed."; }
  catch { status.textContent = "Could not load products. Check that the local server is running."; }
  finally { refresh.disabled = false; }
});

async function initialize() {
  /* An older running server has no categories endpoint. Keep Save disabled and
     explain the restart instead of allowing partially upgraded form submissions. */
  save.disabled = true;
  categoryInput.disabled = true;
  const metadata = await request("/openapi.json");
  if (!metadata.paths?.["/api/products/{product_id}/stock"]) {
    throw new Error("This running server needs the stock update.");
  }
  categories = await request("/api/categories");
  const previous = categoryInput.value;
  categoryInput.replaceChildren();
  addOptions(categoryInput, "Choose a category");
  categoryInput.value = previous;
  drawFilters();
  await loadProducts();
  categoryInput.disabled = false;
  save.disabled = false;
}

sessionGate().then(async(ready)=>{ if(ready) { await initialize(); await loadWorkspace(); } }).catch(() => {
  count.textContent = "Products could not be loaded.";
  status.textContent = "Please run Start Inventory.cmd to load the latest update and use the page it opens. Your saved products are kept.";
});
