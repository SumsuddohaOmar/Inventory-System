/* Single-product stock UI. Decimal quantities stay strings end-to-end. The
   server, not browser arithmetic, is responsible for all stock validation.
   Each logical save keeps a UUID so a lost response can be retried safely. */
"use strict";

function stockDetails(product) {
  const panel = document.createElement("details");
  panel.className = "stock-details";
  const heading = document.createElement("summary");
  heading.textContent = `Stock details — ${product.name}`;
  const content = document.createElement("div");
  panel.append(heading, content);
  let loaded = false;

  panel.addEventListener("toggle", async () => {
    if (!panel.open || loaded) return;
    content.textContent = "Loading stock…";
    try { await renderStock(); loaded = true; }
    catch { content.textContent = "Stock could not load. Run Start Inventory.cmd and use the page it opens."; }
  });

  async function renderStock() {
    /* Build a complete view from the server snapshot. textContent ensures notes
       and product names remain plain text, never executable page markup. */
    const snapshot = await request(`/api/products/${product.id}/stock`);
    const pendingOpening=(await localAll("outbox")).find(row=>row.owner===currentUser.id&&row.product_id===product.id&&row.payload.kind==="opening");
    const hasOpening=snapshot.initialized||!!pendingOpening;
    const cachedProduct=catalog.find(value=>value.id===product.id);
    if(cachedProduct){cachedProduct.stock=snapshot;await localPut("cache",{id:`${currentUser.id}:/api/catalog`,value:catalog});}
    content.replaceChildren();
    const totals = document.createElement("div");
    totals.className = "stock-totals";
    for (const [label, field] of [["Opening", "opening"], ["Received", "received"], ["Issued", "issued"], ["Available", "balance"]]) {
      const line = document.createElement("p");
      line.textContent = `${label}: ${snapshot[field]} ${product.unit}`;
      if (field === "balance") line.className = "stock-balance";
      totals.append(line);
    }
    content.append(totals);
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = pendingOpening ? "Opening stock is pending sync. Further offline entries will wait for it; the confirmed server balance excludes pending entries."
      : snapshot.initialized ? "Opening stock is set. Record receiving/issuing below; authorized corrections preserve the original history."
      : "Set the quantity you had on your starting date. Enter 0 if starting empty. Opening stock can be saved once.";
    content.append(hint);

    const editor = document.createElement("form");
    editor.className = "stock-form";
    // Unique IDs link each visible label to its own product's control.
    function field(labelText, element, suffix) {
      const label = document.createElement("label");
      element.id = `stock-${suffix}-${product.id}`;
      label.htmlFor = element.id;
      label.textContent = labelText;
      editor.append(label, element);
      return element;
    }
    const kind = field("Entry type", document.createElement("select"), "kind");
    for (const [value, label] of hasOpening ? [["receive", "Receive stock"], ["issue", "Issue stock"]] : [["opening", "Set opening stock"]]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      kind.append(option);
    }
    const day = field("Stock date (company clock)", document.createElement("input"), "date");
    day.type = "date";
    day.required = true;
    day.max = businessToday();
    day.value = day.max;
    if (hasOpening) day.min = snapshot.initialized?snapshot.entries.find((entry) => entry.kind === "opening").effective_date:pendingOpening.payload.effective_date;
    const quantity = field(`Quantity (${product.unit}, up to ${product.precision} decimal places)`, document.createElement("input"), "quantity");
    // Text plus decimal inputmode keeps the browser from rounding a quantity.
    quantity.type = "text";
    quantity.inputMode = "decimal";
    quantity.required = true;
    quantity.maxLength = 40;
    quantity.placeholder = snapshot.initialized ? "e.g. 5" : "e.g. 100 (or 0)";
    const notes = field("Notes (optional)", document.createElement("textarea"), "notes");
    notes.maxLength = 500;
    notes.rows = 2;
    const submit = document.createElement("button");
    submit.type = "submit";
    submit.textContent = hasOpening ? "Save stock entry" : "Save opening stock";
    const checkPermission=()=>{submit.disabled=!permitted(kind.value==="issue"?"issue":"receive");};
    kind.addEventListener("change",checkPermission);checkPermission();
    const feedback = document.createElement("p");
    feedback.id = `stock-error-${product.id}`;
    feedback.className = "stock-error";
    feedback.setAttribute("role", "alert");
    quantity.setAttribute("aria-describedby", feedback.id);
    editor.append(submit, feedback);
    content.append(editor);
    let pending = null;
    editor.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (submit.disabled) return;
      const values = { kind: kind.value, effective_date: day.value, quantity: quantity.value.trim(), notes: notes.value.trim() };
      const fingerprint = JSON.stringify(values);
      // Identical retry => identical UUID. Edited values => a distinct operation.
      if (!pending || pending.fingerprint !== fingerprint) pending = { fingerprint, id: crypto.randomUUID() };
      submit.disabled = true;
      feedback.textContent = "";
      status.textContent = "Saving stock entry…";
      try {
        const result = await request(`/api/products/${product.id}/stock`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: pending.id, ...values }),
        });
        pending = null;
        status.textContent = result.queued ? `Entry kept offline for ${product.name}. It is pending sync and is not included in the server balance yet.` : `Stock entry saved for ${product.name}.`;
        // A failed refresh does not mean the committed write failed.
        editor.remove();
        try { await renderStock(); heading.focus(); }
        catch { loaded = false; status.textContent = "Entry was saved. Close and reopen stock details to refresh the balance."; }
      } catch (error) {
        feedback.textContent = error.rejected ? `Not saved: ${error.message}`
          : `Save could not be confirmed. ${error.message} Close and reopen stock details to check history before entering different data.`;
      } finally { checkPermission(); }
    });

    const historyHeading = document.createElement("h3");
    historyHeading.textContent = "Saved stock history";
    content.append(historyHeading);
    if (!snapshot.entries.length) {
      const empty = document.createElement("p");
      empty.textContent = "No stock entries yet.";
      content.append(empty);
    } else {
      const history = document.createElement("ol");
      history.className = "stock-history";
      for (const entry of snapshot.entries) {
        const row = document.createElement("li");
        const label = { opening: "Opening", receive: "Received", issue: "Issued" }[entry.kind];
        row.textContent = `${formatBusinessDate(entry.effective_date)} · ${entry.voided ? "VOIDED · " : ""}${label} ${entry.quantity} ${product.unit}${entry.notes ? ` · ${entry.notes}` : ""}`;
        if(currentUser?.permissions.includes("correct")&&!entry.voided) row.append(correctionForm(entry,renderStock));
        history.append(row);
      }
      content.append(history);
    }
  }
  // Reload on reopening so another window's saved movement is visible.
  panel.addEventListener("toggle", () => { if (!panel.open) loaded = false; });
  return panel;
}
