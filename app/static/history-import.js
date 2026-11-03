/* A three-step history import: inspect, map/preview, explicitly apply.
   This file uses workspace.js helpers and the shared authenticated request().
   No file contents become HTML. The server repeats validation and permissions;
   the browser is a review aid, never the authority over stock or duplicates. */
"use strict";

let historySource = null;
let historyColumns = {};
let historyControls = [];
let historyOperation = crypto.randomUUID();

function historyInput(parent, identity, label, type, value="") {
  const text = element("label", label, parent);
  const input = element("input", undefined, parent);
  input.id = identity;
  text.htmlFor = identity;
  input.type = type;
  input.value = value;
  return input;
}

function historySelect(parent, identity, label, options, value="") {
  const text = element("label", label, parent);
  const select = element("select", undefined, parent);
  select.id = identity;
  text.htmlFor = identity;
  for (const [key, title] of options) element("option", title, select).value = key;
  select.value = value;
  return select;
}

function invalidateHistory() {
  // Changing the file/header invalidates its row IDs and all earlier choices.
  historySource = null;
  historyControls = [];
  byId("history-map-form").hidden = true;
  byId("history-apply-form").hidden = true;
}
for (const identity of ["history-file", "history-sheet", "history-header"])
  byId(identity).addEventListener("change", invalidateHistory);

byId("history-file-form").addEventListener("submit", async(event)=>{
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  invalidateHistory();
  try {
    const file = byId("history-file").files[0];
    if (!file || file.size > 2000000) throw new Error("Choose a history file under 2 MB.");
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    const source = {filename:file.name, data:btoa(binary),
      sheet:Number(byId("history-sheet").value)-1, header_row:Number(byId("history-header").value)};
    const layout = await request("/api/history-import/inspect", {
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(source)});
    historySource = source;
    historyColumns = {};
    const target = byId("history-column-mapping");
    target.replaceChildren();
    for (const [key, label] of Object.entries({name:"Product name",code:"Product code (optional)",
      date:"Date",kind:"Type: opening / receive / issue",quantity:"Quantity",notes:"Notes (optional)"})) {
      const options = [["", "Not mapped"], ...layout.headers.map((name,index)=>[String(index),`${index+1}: ${name || "Unnamed column"}`])];
      historyColumns[key] = historySelect(target, `history-column-${key}`, label, options,
        layout.mapping[key] === null ? "" : String(layout.mapping[key]));
    }
    table(["Sample source row"], layout.sample.map(row=>[row.join(" | ")]), byId("history-source-sample"));
    byId("history-map-form").hidden = false;
    status.textContent = "History columns read. Map the fields and choose the source date format.";
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
});

// Editing a mapping cannot leave an old preview available for accidental apply.
byId("history-map-form").addEventListener("change", ()=>{byId("history-apply-form").hidden=true;});
byId("history-map-form").addEventListener("submit", async(event)=>{
  event.preventDefault();
  if (!historySource) return;
  const button = event.submitter;
  button.disabled = true;
  byId("history-apply-form").hidden = true;
  try {
    const mapping = Object.fromEntries(Object.entries(historyColumns).map(([key,control])=>
      [key,control.value === "" ? null : Number(control.value)]));
    const preview = await request("/api/history-import/preview", {method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify({...historySource,mapping,
        date_format:byId("history-date-format").value})});
    historyOperation = crypto.randomUUID();
    historyControls = [];
    const target = byId("history-review-rows");
    target.replaceChildren();
    for (const source of preview.rows) {
      const group = element("fieldset", undefined, target);
      element("legend", `Row ${source.row}: ${source.name || source.code || "Unmatched product"}`, group);
      const warnings = [...source.errors];
      if (source.already_imported) warnings.push("This exact source row was previously imported. Skip it.");
      if (source.possible_duplicates.length) warnings.push("A matching dated transaction already exists.");
      if (source.repeated_in_file) warnings.push("Similar row repeated within this file.");
      if (source.matches.length !== 1) warnings.push("Choose the existing product explicitly.");
      if (warnings.length) element("p", warnings.join(" "), group);
      const safe = !warnings.length;
      const prefix = `history-row-${source.row}`;
      const action = historySelect(group,`${prefix}-action`,"Review choice",
        [["skip","Skip this row"],["import","Import this transaction"]],safe?"import":"skip");
      const product = historySelect(group,`${prefix}-product`,"Existing product",
        [["","Choose a product"],...catalog.map(row=>[row.id,`${row.name} · ${row.unit}${row.archived?" (archived)":""}`])],
        source.matches.length === 1 ? source.matches[0].id : "");
      const kind = historySelect(group,`${prefix}-kind`,"Transaction type",
        [["","Choose type"],["opening","Opening stock"],["receive","Receive"],["issue","Issue"]],source.kind);
      const day = historyInput(group,`${prefix}-date`,"Reviewed stock date","date",source.effective_date);
      const quantity = historyInput(group,`${prefix}-quantity`,"Exact quantity","text",source.quantity);
      quantity.inputMode = "decimal";
      quantity.maxLength = 40;
      const notes = historyInput(group,`${prefix}-notes`,"Notes","text",source.notes);
      notes.maxLength = 500;
      const acknowledge = historyInput(group,`${prefix}-ack`,
        "I reviewed possible duplicates; this is a separate transaction to import even if its date, type and quantity match.","checkbox");
      historyControls.push({source,action,product,kind,day,quantity,notes,acknowledge});
    }
    byId("history-review-summary").textContent = `${preview.rows.length} rows previewed. Review each selected row; stock has not changed.`;
    byId("history-apply-form").hidden = !preview.rows.length;
    status.textContent = "History preview ready. Invalid or ambiguous rows default to Skip.";
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
});

byId("history-apply-form").addEventListener("submit", async(event)=>{
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    // Keep the batch/row UUIDs on retries. A lost response must not create a
    // second set of transactions. Changed values under a used ID are rejected.
    const rows = historyControls.map(row=>row.action.value === "skip" ?
      {row:row.source.row,id:row.source.id,quantity:"",action:"skip"} : ({row:row.source.row,id:row.source.id,
      product_id:row.product.value || null,kind:row.kind.value || null,
      effective_date:row.day.value || null,quantity:row.quantity.value.trim(),notes:row.notes.value,
      action:row.action.value,acknowledge_duplicate:row.acknowledge.checked}));
    if (!rows.some(row=>row.action === "import")) throw new Error("Select at least one row to import.");
    await request("/api/history-import/apply", {method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({id:historyOperation,filename:historySource.filename.slice(0,120),rows})});
    byId("history-apply-form").hidden = true;
    await loadWorkspace();
    await loadProducts();
    status.textContent = "Selected history imported together with audit history and a verified pre-import backup.";
  } catch (error) { status.textContent = `History import: ${error.message} Review choices are kept.`; }
  finally { button.disabled = false; }
});
