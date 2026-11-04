/* Screens for daily work. All quantities remain strings; the server decides
   whether a change is valid. Native controls and real labels support keyboard
   entry. Dynamic account/product text is always assigned with textContent. */
"use strict";
let catalog = [];
let bulkValues = new Map();
let draftIdentity = crypto.randomUUID();
let draftRevision = 0;
let draftTimer;
let draftChain = Promise.resolve();
let companyPreferences={date_format:"YYYY-MM-DD",time_format:"24h",utc_offset_minutes:180};
const byId = (id) => document.getElementById(id);
const permitted = (permission) => currentUser?.permissions.includes(permission);

function businessToday() {
  // Cached catalog.today is a historical snapshot. Derive the current local
  // business date so an offline device still advances after midnight. The
  // server remains authoritative when the queued operation is synchronized.
  return new Date(Date.now()+companyPreferences.utc_offset_minutes*60000).toISOString().slice(0,10);
}

function element(tag, text, parent) {
  const result = document.createElement(tag);
  if(text !== undefined) result.textContent = text;
  if(parent) parent.append(result);
  return result;
}

function table(headers, rows, target) {
  target.replaceChildren();
  const result=element("table",undefined,target), head=element("thead",undefined,result), heading=element("tr",undefined,head);
  for(const title of headers) { const cell=element("th",title,heading); cell.scope="col"; }
  const body=element("tbody",undefined,result);
  for(const values of rows) {const row=element("tr",undefined,body); for(const value of values)element("td",String(value),row);}
}

async function loadWorkspace() {
  const preferences = request("/api/company").catch(()=>companyPreferences);
  byId("connection-address").textContent=location.origin;
  byId("connection-help").textContent=location.hostname==="127.0.0.1"||location.hostname==="localhost"?"This address works only on this computer. Configure local HTTPS before connecting Android; see docs/ANDROID.md.":"Use the same local Wi-Fi and install your own server's CA certificate before scanning. You can also type the address manually.";
  catalog=await request("/api/catalog");
  companyPreferences=await preferences;
  // Initialize the entire authorized history for offline use, not just products
  // whose detail panels happen to have been opened while connected.
  for(const product of catalog) await localPut("cache", {id:`${currentUser.id}:/api/products/${product.id}/stock`,value:product.stock});
  byId("dashboard-summary").textContent=`${catalog.filter(row=>!row.archived).length} active products · ${categories.length} categories · ${catalog.filter(row=>!row.stock.initialized).length} products need opening stock`;
  byId("low-stock").replaceChildren();
  for(const product of catalog.filter(row=>!row.archived && row.low_stock_minor!==null)) {
    // Decimal comparison is for alert display only; stock validation stays on
    // the server. Scale from the persisted integer threshold without rounding.
    if(Number(product.stock.balance)<=product.low_stock_minor/(10**product.precision)) element("li",`Low stock: ${product.name} — ${product.stock.balance} ${product.unit}`,byId("low-stock"));
  }
  for(const panel of document.querySelectorAll("[data-permission]")) panel.hidden=!permitted(panel.dataset.permission);
  byId("reports-section").hidden=!permitted("reports");
  byId("bulk-section").hidden=!(permitted("receive")||permitted("issue"));
  byId("product-form").closest("section").hidden=!permitted("products");
  byId("bulk-category").replaceChildren();
  for(const category of categories){const option=element("option",category.name,byId("bulk-category"));option.value=category.id;}
  byId("import-category").replaceChildren();for(const category of categories){const option=element("option",category.name,byId("import-category"));option.value=category.id;}
  byId("monthly-category").replaceChildren();for(const category of categories){const option=element("option",category.name,byId("monthly-category"));option.value=category.id;}
  const today=businessToday();
  for(const id of ["bulk-date","activity-from","activity-to","report-from","report-to"]) if(!byId(id).value)byId(id).value=today;
  byId("import-date").value=today;
  if(!byId("monthly-period").value)byId("monthly-period").value=today.slice(0,7);
  showMonth();
  const saved=(await localGet("meta",`active-draft:${currentUser.id}`))?.value;
  if(saved) await restoreLocalDraft(saved);
  drawBulkRows();
  byId("report-items").replaceChildren();
  for(const product of catalog){const label=element("label",undefined,byId("report-items"));label.className="check";const input=element("input",undefined,label);input.type="checkbox";input.value=product.id;label.append(document.createTextNode(product.name));}
  byId("manage-product").replaceChildren();
  for(const product of catalog){const option=element("option",`${product.name}${product.archived?" (archived)":""}`,byId("manage-product"));option.value=product.id;}
  showManagedProduct();
  showActivity();
  await renderQueue();
  // Populate secondary screens after the product workspace is usable.
  void Promise.allSettled([listDrafts(),permitted("products")?listCategories():Promise.resolve(),permitted("reports")?listReports():Promise.resolve(),permitted("users")?listEmployees():Promise.resolve(),
    permitted("settings")?loadSettings():Promise.resolve(),permitted("backups")?listBackups():Promise.resolve()]);
  if("serviceWorker" in navigator)navigator.serviceWorker.register("/sw.js").catch(()=>{status.textContent="Offline installation needs a trusted secure connection.";});
  if(navigator.storage?.persist)navigator.storage.persist().catch(()=>{});
  syncPending();
}

function drawBulkRows() {
  const target=byId("bulk-rows");target.replaceChildren();
  const search=byId("bulk-search").value.toLocaleLowerCase();
  for(const product of catalog.filter(row=>!row.archived && row.category_id===byId("bulk-category").value && row.name.toLocaleLowerCase().includes(search))) {
    const row=element("div",undefined,target);row.className="bulk-row";
    const label=element("label",product.name,row);label.htmlFor=`bulk-${product.id}`;
    element("small",`Available ${product.stock.balance} ${product.unit}`,label);
    const input=element("input",undefined,row);input.id=`bulk-${product.id}`;input.inputMode="decimal";input.maxLength=40;input.placeholder="Quantity";input.value=bulkValues.get(product.id)?.quantity||"";
    input.addEventListener("input",()=>{const previous=bulkValues.get(product.id)||{id:crypto.randomUUID(),notes:""};bulkValues.set(product.id,{...previous,quantity:input.value});scheduleDraft();});
    const details=element("details",undefined,row);element("summary","Item notes & details",details);
    element("p",`Unit: ${product.unit}. Precision: ${product.precision} decimal places. ${product.notes}`,details);
    const noteLabel=element("label","Item notes",details);noteLabel.htmlFor=`bulk-notes-${product.id}`;
    const notes=element("input",undefined,details);notes.id=noteLabel.htmlFor;notes.maxLength=200;notes.value=bulkValues.get(product.id)?.notes||"";
    notes.addEventListener("input",()=>{const previous=bulkValues.get(product.id)||{id:crypto.randomUUID(),quantity:""};bulkValues.set(product.id,{...previous,notes:notes.value});scheduleDraft();});
  }
}

function draftRows() {
  return [...bulkValues].filter(([,value])=>value.quantity.trim()!=="").map(([product_id,value])=>({product_id,id:value.id,kind:byId("bulk-kind").value,
    effective_date:byId("bulk-date").value,quantity:value.quantity.trim(),notes:[byId("bulk-notes").value,value.notes].filter(Boolean).join(" · ").slice(0,500)}));
}

function scheduleDraft() {clearTimeout(draftTimer);draftTimer=setTimeout(()=>saveDraft().catch(()=>{}),700);}

async function saveDraft() {
  /* Serialize autosaves so response order cannot overwrite a newer revision.
     Always save locally first, even if Windows cannot currently be reached. */
  draftChain=draftChain.catch(()=>{}).then(async()=>{
    const identity=draftIdentity;
    const value={identity,revision:draftRevision,values:[...bulkValues],category:byId("bulk-category").value,kind:byId("bulk-kind").value,date:byId("bulk-date").value,notes:byId("bulk-notes").value};
    await localPut("meta",{id:`draft:${currentUser.id}:${identity}`,value});
    await localPut("meta",{id:`active-draft:${currentUser.id}`,value:identity});
    const entries=draftRows();
    if(!entries.length){byId("draft-status").textContent="Empty draft kept locally.";return;}
    try {
      const result=await request(`/api/drafts/${identity}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({revision:draftRevision,entries})});
      if(draftIdentity===identity)draftRevision=result.revision;
      value.revision=result.revision;
      await localPut("meta",{id:`draft:${currentUser.id}:${identity}`,value});
      byId("draft-status").textContent="Draft saved locally and on Windows. Stock has not changed.";
    }catch(error){
      // A response can be lost AFTER the server saved a draft. Reconcile an
      // identical server payload rather than trapping it at revision zero.
      if(error.rejected)try{const versions=await request("/api/drafts");const version=versions.find(row=>row.id===identity&&JSON.stringify(JSON.parse(row.payload_json))===JSON.stringify(entries));if(version){draftRevision=version.revision;value.revision=version.revision;await localPut("meta",{id:`draft:${currentUser.id}:${identity}`,value});return;}}catch{}
      byId("draft-status").textContent=error.rejected?`Draft kept locally. ${error.message}`:"Draft kept locally; Windows is unreachable.";throw error;}
  });
  return draftChain;
}

async function restoreLocalDraft(identity) {
  const saved=(await localGet("meta",`draft:${currentUser.id}:${identity}`))?.value;
  if(!saved)return;
  if (!["receive", "issue"].includes(saved.kind)) {
    byId("draft-status").textContent = "This opening-stock draft is preserved. Use Products → Stock details to enter opening balances.";
    return;
  }
  draftIdentity=saved.identity;draftRevision=saved.revision;bulkValues=new Map(saved.values);
  byId("bulk-category").value=saved.category;byId("bulk-kind").value=saved.kind;byId("bulk-date").value=saved.date;byId("bulk-notes").value=saved.notes;
}

async function listDrafts() {
  const target=byId("draft-list");target.replaceChildren();
  const local=(await localAll("meta")).filter(row=>row.id.startsWith(`draft:${currentUser.id}:`));
  for(const saved of local){const item=element("li",undefined,target),button=element("button",`Open ${saved.value.kind} draft · ${saved.value.date}`,item);button.type="button";button.className="secondary";
    button.addEventListener("click",async()=>{await restoreLocalDraft(saved.value.identity);drawBulkRows();byId("bulk-section").scrollIntoView();});}
  try{for(const saved of await request("/api/drafts"))if(!local.some(value=>value.value.identity===saved.id)){
    const entries=JSON.parse(saved.payload_json);if(!entries.length)continue;
    const item=element("li",undefined,target),button=element("button",`Open Windows-saved draft · ${entries[0].effective_date}`,item);button.type="button";button.className="secondary";
    button.addEventListener("click",async()=>{
      if(new Set(entries.map(entry=>`${entry.kind}:${entry.effective_date}`)).size>1){status.textContent="This draft contains different dates/types. It has been preserved unchanged.";return;}
      if (entries[0].kind === "opening") { byId("draft-status").textContent = "This opening-stock draft is preserved. Use Products → Stock details."; return; }
      const first=entries[0],product=catalog.find(value=>value.id===first.product_id);
      const value={identity:saved.id,revision:saved.revision,values:entries.map(entry=>[entry.product_id,{id:entry.id,quantity:entry.quantity,notes:entry.notes}]),category:product?.category_id||categories[0]?.id,kind:first.kind,date:first.effective_date,notes:""};
      await localPut("meta",{id:`draft:${currentUser.id}:${saved.id}`,value});await localPut("meta",{id:`active-draft:${currentUser.id}`,value:saved.id});await restoreLocalDraft(saved.id);drawBulkRows();byId("bulk-section").scrollIntoView();
    });
  }}catch{}
}

async function queueRows(rows) {
  for(const row of rows){const payload={...row};delete payload.product_id;const existing=(await localAll("outbox")).filter(value=>value.owner===currentUser.id&&value.product_id===row.product_id&&value.id!==row.id);
    await localPut("outbox",{id:row.id,owner:currentUser.id,product_id:row.product_id,payload,depends_on:existing.map(value=>value.id),state:"pending",created:Date.now()});}
  await renderQueue();
}

byId("bulk-form").addEventListener("submit",async(event)=>{
  event.preventDefault();clearTimeout(draftTimer);
  const rows=draftRows();if(!rows.length){status.textContent="Enter at least one quantity.";return;}
  const button=event.submitter;button.disabled=true;
  try{
    await saveDraft();
    await request(`/api/drafts/${draftIdentity}/finalize`,{method:"POST"});
    status.textContent="Bulk entry finalized. All products saved together.";
  }catch(error){
    if(error.rejected){byId("draft-status").textContent=`Not finalized: ${error.message} Your draft is kept.`;return;}
    const today=businessToday();
    if(rows.some(row=>row.effective_date>today)){status.textContent="Future-dated entries remain drafts. Review and finalize them on or after their date.";return;}
    await queueRows(rows);status.textContent="Bulk rows kept offline for sync. Pending entries are not included in confirmed balances.";
  }finally{button.disabled=false;}
  await localDelete("meta",`draft:${currentUser.id}:${draftIdentity}`);await localDelete("meta",`active-draft:${currentUser.id}`);
  draftIdentity=crypto.randomUUID();draftRevision=0;bulkValues.clear();drawBulkRows();
  await loadWorkspace();await loadProducts();
});
byId("new-draft").addEventListener("click",async()=>{try{await saveDraft();}catch{}draftIdentity=crypto.randomUUID();draftRevision=0;bulkValues.clear();byId("bulk-notes").value="";await localPut("meta",{id:`active-draft:${currentUser.id}`,value:draftIdentity});drawBulkRows();await listDrafts();});
for(const id of ["bulk-category","bulk-search"])byId(id).addEventListener("input",drawBulkRows);
for(const id of ["bulk-kind","bulk-date","bulk-notes"])byId(id).addEventListener("input",scheduleDraft);

function correctionForm(entry,reload) {
  const details=document.createElement("details");element("summary","Correct this entry",details);
  const form=element("form",undefined,details);
  const fields={};
  for(const [key,label,type] of [["quantity","Correct quantity","text"],["effective_date","Correct date","date"],["notes","Correction notes","text"]]){
    const text=element("label",label,form),input=element("input",undefined,form);input.id=`correct-${entry.id}-${key}`;text.htmlFor=input.id;input.type=type;input.value=entry[key];input.required=key!=="notes";fields[key]=input;
  }
  const label=element("label",undefined,form);label.className="check";const voided=element("input",undefined,label);voided.type="checkbox";voided.disabled=entry.kind==="opening";label.append(document.createTextNode("Void this receipt/issue (keep history)"));
  const button=element("button","Save correction",form);
  const operation=crypto.randomUUID();
  form.addEventListener("submit",async(event)=>{event.preventDefault();button.disabled=true;try{
    await request(`/api/entries/${entry.id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:operation,expected_revision:entry.revision,
      quantity:fields.quantity.value,effective_date:fields.effective_date.value,notes:fields.notes.value,voided:voided.checked})});
    status.textContent="Correction saved with before/after audit history.";await reload();
  }catch(error){status.textContent=error.message;}finally{button.disabled=false;}});
  return details;
}

function showActivity() {
  const from=byId("activity-from").value,to=byId("activity-to").value,search=byId("activity-search").value.toLocaleLowerCase();
  const rows=[];
  for(const product of catalog.filter(value=>value.name.toLocaleLowerCase().includes(search)))for(const entry of product.stock.entries){
    if(entry.voided||entry.effective_date<from||entry.effective_date>to)continue;
    rows.push([entry.effective_date,product.name,entry.kind,entry.quantity,product.unit,entry.notes]);
  }
  rows.sort((a,b)=>a[0].localeCompare(b[0])||a[1].localeCompare(b[1]));
  if(byId("activity-view").value==="daily"){
    // BigInt scaled arithmetic keeps daily display totals exact, too.
    const grouped=new Map();
    for(const row of rows){const key=JSON.stringify(row.slice(0,3));const parts=row[3].split(".");const digits=parts[1]?.length||0;const minor=BigInt(parts.join(""));const current=grouped.get(key)||{row:[...row],minor:0n,digits};current.minor+=minor;grouped.set(key,current);}
    const totals=[...grouped.values()].map(value=>{const raw=value.minor.toString().padStart(value.digits+1,"0");value.row[3]=value.digits?`${raw.slice(0,-value.digits)}.${raw.slice(-value.digits)}`:raw;value.row[5]="Daily total; correct individual transactions in Stock details.";return value.row;});
    table(["Date","Product","Type","Quantity","Unit","Notes"],totals,byId("activity-results"));
  }else table(["Date","Product","Type","Quantity","Unit","Notes"],rows,byId("activity-results"));
}
byId("activity-refresh").addEventListener("click",async()=>{catalog=await request("/api/catalog");showActivity();});

byId("report-form").addEventListener("submit",async(event)=>{event.preventDefault();try{
  const report=await request("/api/reports",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({start:byId("report-from").value,end:byId("report-to").value,kind:byId("report-kind").value,name:byId("report-name").value,exclude_zero:byId("report-zero").checked,
    product_ids:[...byId("report-items").querySelectorAll("input:checked")].map(input=>input.value)})});
  table(["Product","Category","Unit","Opening","Received","Issued","Available"],report.rows.map(row=>[row.name,row.category,row.unit,row.opening,row.received,row.issued,row.closing]),byId("report-preview"));
  await listReports();status.textContent="Report preview saved as a version. Download it below.";
}catch(error){status.textContent=error.message;}});
async function listReports(){const templates=await request("/api/templates");byId("template-list").replaceChildren();for(const template of templates)element("li",template.name,byId("template-list"));byId("report-history").replaceChildren();for(const report of await request("/api/reports")){const row=element("li",undefined,byId("report-history"));element("span",`${report.name} · ${report.from_date} to ${report.to_date}${report.needs_review?" · Review after corrections":""} `,row);const link=element("a","Download built-in Excel",row);link.href=`/api/reports/${report.id}/excel`;for(const template of templates){element("span"," · ",row);const alternate=element("a",`Use ${template.name}`,row);alternate.href=`/api/reports/${report.id}/excel?template_id=${template.id}`;}}}

let customFieldControls=[];
function addCustomField(name="",value=""){const row=element("div",undefined,byId("custom-fields")),identity=crypto.randomUUID(),label=element("label","Custom field name",row),nameInput=element("input",undefined,row);nameInput.id=`field-name-${identity}`;label.htmlFor=nameInput.id;nameInput.maxLength=60;nameInput.value=name;const valueLabel=element("label","Custom field value",row),valueInput=element("input",undefined,row);valueInput.id=`field-value-${identity}`;valueLabel.htmlFor=valueInput.id;valueInput.maxLength=1000;valueInput.value=value;customFieldControls.push([nameInput,valueInput]);}
function managedFields(){return Object.fromEntries([["Packing",byId("manage-packing").value],["Specification",byId("manage-specification").value],...customFieldControls.filter(([name])=>name.value.trim()).map(([name,value])=>[name.value.trim(),value.value])]);}
byId("custom-field-add").addEventListener("click",()=>addCustomField());
function showManagedProduct(){const product=catalog.find(row=>row.id===byId("manage-product").value);if(!product)return;byId("manage-name").value=product.name;byId("manage-code").value=product.code;byId("manage-notes").value=product.notes;byId("manage-order").value=product.sort_order;byId("manage-archived").checked=!!product.archived;byId("manage-low").value=product.low_stock_minor===null?"":(product.low_stock_minor/(10**product.precision)).toFixed(product.precision);const fields=JSON.parse(product.fields_json||"{}");byId("manage-packing").value=fields.Packing||"";byId("manage-specification").value=fields.Specification||"";byId("custom-fields").replaceChildren();customFieldControls=[];for(const [key,value]of Object.entries(fields))if(!["Packing","Specification"].includes(key))addCustomField(key,value);}
byId("manage-product").addEventListener("change",showManagedProduct);
byId("manage-form").addEventListener("submit",async(event)=>{event.preventDefault();try{await request(`/api/catalog/${byId("manage-product").value}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:byId("manage-name").value,code:byId("manage-code").value,notes:byId("manage-notes").value,sort_order:Number(byId("manage-order").value),archived:byId("manage-archived").checked,low_stock:byId("manage-low").value,custom_fields:managedFields()})});await loadWorkspace();await loadProducts();status.textContent="Product details saved; history retained.";}catch(error){status.textContent=error.message;}});
byId("safe-delete-product").addEventListener("click",async()=>{try{await request(`/api/catalog/${byId("manage-product").value}`,{method:"DELETE"});await loadWorkspace();await loadProducts();status.textContent="Unused product deleted; the action remains in audit history.";}catch(error){status.textContent=error.message;}});

const permissionNames={read:"View inventory",products:"Manage products",receive:"Receive & opening stock",issue:"Issue stock",correct:"Correct & void entries",reports:"Reports & Excel",audit:"Read audit history",backups:"Create backups",settings:"Company settings & month closing",users:"Manage accounts"};
for(const [permission,name]of Object.entries(permissionNames)){const label=element("label",undefined,byId("employee-permissions"));label.className="check";const input=element("input",undefined,label);input.type="checkbox";input.value=permission;input.checked=permission==="read";label.append(document.createTextNode(name));}
byId("employee-form").addEventListener("submit",async(event)=>{event.preventDefault();try{await request("/api/users",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:byId("employee-name").value,password:byId("employee-password").value,permissions:[...byId("employee-permissions").querySelectorAll("input:checked")].map(input=>input.value)})});byId("employee-password").value="";await listEmployees();status.textContent="Employee created.";}catch(error){status.textContent=error.message;}});
async function listEmployees(){byId("employee-list").replaceChildren();for(const user of await request("/api/users")){const row=element("li",undefined,byId("employee-list"));element("strong",`${user.username}${user.admin?" · Administrator":""}`,row);const form=element("form",undefined,row),checks=[];
  for(const [permission,name]of Object.entries(permissionNames)){const label=element("label",undefined,form);label.className="check";const input=element("input",undefined,label);input.type="checkbox";input.value=permission;input.checked=user.permissions.includes(permission);input.disabled=user.admin;checks.push(input);label.append(document.createTextNode(name));}
  const label=element("label",undefined,form);label.className="check";const active=element("input",undefined,label);active.type="checkbox";active.checked=user.active;label.append(document.createTextNode("Account active"));element("button","Save permissions",form);
  form.addEventListener("submit",async(event)=>{event.preventDefault();try{await request(`/api/users/${user.id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({permissions:checks.filter(input=>input.checked).map(input=>input.value),active:active.checked})});await listEmployees();status.textContent="Permissions updated. Server checks apply immediately.";}catch(error){status.textContent=error.message;}});
}}
byId("audit-refresh").addEventListener("click",async()=>{try{byId("audit-list").replaceChildren();for(const row of await request("/api/audit")){const item=element("li",`${row.created_at} · ${row.action} · actor ${row.actor}`,byId("audit-list"));const detail=element("details",undefined,item);element("summary","Before & after",detail);element("pre",`${row.before_json||"—"}\n→\n${row.after_json||"—"}`,detail);}}catch(error){status.textContent=error.message;}});
async function listBackups(){byId("backup-list").replaceChildren();for(const row of await request("/api/backups"))element("li",`${row.name} · ${row.size} bytes`,byId("backup-list"));}
byId("backup-create").addEventListener("click",async()=>{try{await request("/api/backups",{method:"POST"});await listBackups();status.textContent="Verified backup created.";}catch(error){status.textContent=error.message;}});
async function loadSettings(){const settings=await request("/api/settings");byId("company-name").value=settings.company_name||"";byId("company-offset").value=settings.utc_offset_minutes??180;byId("company-date-format").value=settings.date_format||"YYYY-MM-DD";byId("company-time-format").value=settings.time_format||"24h";byId("backup-locations").value=(settings.backup_locations||[]).join("\n");byId("backup-status").textContent=settings.backup_status||"Automatic backup check pending.";byId("closed-months").replaceChildren();for(const month of await request("/api/months"))element("li",`${month.month} · Closed`,byId("closed-months"));}
byId("settings-form").addEventListener("submit",async(event)=>{event.preventDefault();try{await request("/api/settings",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({company_name:byId("company-name").value,utc_offset_minutes:Number(byId("company-offset").value),date_format:byId("company-date-format").value,time_format:byId("company-time-format").value,backup_locations:byId("backup-locations").value.split("\n").map(value=>value.trim()).filter(Boolean)})});await loadWorkspace();status.textContent="Company settings saved.";}catch(error){status.textContent=error.message;}});
function formatBusinessDate(value){const [year,month,day]=value.split("-");return companyPreferences.date_format==="DD/MM/YYYY"?`${day}/${month}/${year}`:companyPreferences.date_format==="MM/DD/YYYY"?`${month}/${day}/${year}`:value;}
for(const [id,closed]of [["close-month",true],["open-month",false]])byId(id).addEventListener("click",async()=>{try{await request(`/api/months/${byId("closing-month").value}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({closed})});await loadSettings();status.textContent=closed?"Month closed.":"Month reopened.";}catch(error){status.textContent=error.message;}});
byId("sync-now").addEventListener("click",async()=>{await syncPending();await loadWorkspace();await loadProducts();});
byId("server-holds-refresh").addEventListener("click",async()=>{try{byId("server-holds").replaceChildren();for(const held of await request("/api/sync/holds")){
  const original=JSON.parse(held.payload_json),item=element("li",undefined,byId("server-holds"));element("p",`${original.kind} ${original.quantity} · ${original.effective_date} · ${held.reason}`,item);
  const form=element("form",undefined,item),fields={};for(const [key,label,type]of [["quantity","Reviewed quantity","text"],["effective_date","Reviewed date","date"],["notes","Review notes","text"]]){const text=element("label",label,form),input=element("input",undefined,form);input.id=`held-${held.id}-${key}`;text.htmlFor=input.id;input.type=type;input.value=original[key]||"";input.required=key!=="notes";fields[key]=input;}
  const button=element("button","Apply reviewed replacement",form),identity=crypto.randomUUID();form.addEventListener("submit",async(event)=>{event.preventDefault();button.disabled=true;try{
    const results=await request("/api/sync",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({operations:[{id:identity,product_id:original.product_id,kind:original.kind,quantity:fields.quantity.value,effective_date:fields.effective_date.value,notes:fields.notes.value,resolves_id:held.id,depends_on:original.depends_on||[]}]})});
    status.textContent=results[0].status==="saved"?"Held entry resolved and recorded in audit history.":results[0].reason;await loadWorkspace();
  }catch(error){status.textContent=error.message;}finally{button.disabled=false;}});
}}catch(error){status.textContent=error.message;}});

function minorValue(value){return BigInt(value.replace(".",""));}
function minorText(value,precision){const negative=value<0n;const raw=(negative?-value:value).toString().padStart(precision+1,"0");return `${negative?"-":""}${precision?`${raw.slice(0,-precision)}.${raw.slice(-precision)}`:raw}`;}
function showMonth(){
  const period=byId("monthly-period").value;if(!period)return;
  const [year,month]=period.split("-").map(Number),days=new Date(year,month,0).getDate();
  const host=byId("monthly-grid");host.replaceChildren();const grid=element("table",undefined,host),thead=element("thead",undefined,grid),head=element("tr",undefined,thead);
  for(const label of ["Product","Unit","Opening allocation",...Array.from({length:days},(_,index)=>[`${index+1} R`,`${index+1} I`]).flat(),"Available at month end"])element("th",label,head).scope="col";
  const body=element("tbody",undefined,grid);
  for(const product of catalog.filter(row=>row.category_id===byId("monthly-category").value)){
    const entries=product.stock.entries.filter(entry=>!entry.voided),first=`${period}-01`,last=`${period}-${String(days).padStart(2,"0")}`;
    const opening=entries.filter(entry=>entry.effective_date<first||(entry.kind==="opening"&&entry.effective_date<=last)).reduce((sum,entry)=>sum+(entry.kind==="issue"?-minorValue(entry.quantity):minorValue(entry.quantity)),0n);
    const closing=entries.filter(entry=>entry.effective_date<=last).reduce((sum,entry)=>sum+(entry.kind==="issue"?-minorValue(entry.quantity):minorValue(entry.quantity)),0n);
    const row=element("tr",undefined,body);element("th",product.name,row).scope="row";element("td",product.unit,row);element("td",minorText(opening,product.precision),row);
    for(let day=1;day<=days;day++)for(const kind of ["receive","issue"]){const date=`${period}-${String(day).padStart(2,"0")}`,matching=entries.filter(entry=>entry.kind===kind&&entry.effective_date===date),total=matching.reduce((sum,entry)=>sum+minorValue(entry.quantity),0n);
      const cell=element("td",undefined,row),button=element("button",minorText(total,product.precision),cell);button.className="cell-button";button.type="button";button.setAttribute("aria-label",`${product.name}, ${date}, ${kind}, ${minorText(total,product.precision)} ${product.unit}`);
      button.addEventListener("click",()=>editDailyCell(product,date,kind,matching,total));}
    element("td",minorText(closing,product.precision),row);
  }
}
function editDailyCell(product,date,kind,entries,total){const target=byId("monthly-editor");target.replaceChildren();element("h3",`${product.name} · ${date} · ${kind}`,target);
  element("p","Choose individual corrections below, or change the daily total. Decreases reduce the newest entries first and preserve all before/after history.",target);
  for(const entry of entries)if(permitted("correct"))target.append(correctionForm(entry,async()=>{catalog=await request("/api/catalog");showMonth();target.replaceChildren();}));
  if(permitted("correct")){const form=element("form",undefined,target),label=element("label","New daily total",form),input=element("input",undefined,form);input.id="daily-new-total";label.htmlFor=input.id;input.value=minorText(total,product.precision);input.inputMode="decimal";input.required=true;const button=element("button","Apply total with recorded corrections",form),identity=crypto.randomUUID();
    form.addEventListener("submit",async(event)=>{event.preventDefault();button.disabled=true;try{await request(`/api/products/${product.id}/daily-total`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:identity,kind,effective_date:date,quantity:input.value,expected_entries:Object.fromEntries(entries.map(entry=>[entry.id,entry.revision]))})});catalog=await request("/api/catalog");showMonth();target.replaceChildren();status.textContent="Daily total updated with preserved audit history.";}catch(error){status.textContent=error.message;}finally{button.disabled=false;}});
  }else element("p","Your account can view this cell. Correction permission is required to change it.",target);
  target.scrollIntoView();
}
byId("monthly-refresh").addEventListener("click",async()=>{catalog=await request("/api/catalog");showMonth();});

byId("category-create-form").addEventListener("submit",async(event)=>{event.preventDefault();try{await request("/api/category-management",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:byId("new-category-name").value,position:categories.length})});await initialize();await loadWorkspace();status.textContent="Category added.";}catch(error){status.textContent=error.message;}});
async function listCategories(){byId("category-management").replaceChildren();for(const category of await request("/api/category-management")){const row=element("li",undefined,byId("category-management")),form=element("form",undefined,row),label=element("label","Category name",form),name=element("input",undefined,form);name.id=`category-name-${category.id}`;label.htmlFor=name.id;name.value=category.name;name.required=true;name.maxLength=120;
  const orderLabel=element("label","Display order",form),order=element("input",undefined,form);order.id=`category-order-${category.id}`;orderLabel.htmlFor=order.id;order.type="number";order.min="0";order.value=category.position;
  const archiveLabel=element("label",undefined,form);archiveLabel.className="check";const archived=element("input",undefined,archiveLabel);archived.type="checkbox";archived.checked=!!category.archived;archiveLabel.append(document.createTextNode("Archived (preserve history)"));element("button","Save category",form);
  form.addEventListener("submit",async(event)=>{event.preventDefault();try{await request(`/api/category-management/${encodeURIComponent(category.id)}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:name.value,position:Number(order.value),archived:archived.checked})});await initialize();await loadWorkspace();status.textContent="Category saved; stock history retained.";}catch(error){status.textContent=error.message;}});
}}

let importRows=[];let importChoices=[];let importOperation=crypto.randomUUID();
byId("import-preview-form").addEventListener("submit",async(event)=>{event.preventDefault();
  const feedback = byId("import-feedback"), button = event.submitter;
  button.disabled = true;
  byId("import-apply-form").hidden = true;
  feedback.textContent = "Reading file…";
  try{const file=byId("import-file").files[0];if(!file || !/\.(xlsx|csv)$/i.test(file.name)) throw new Error("Choose an .xlsx workbook or UTF-8 .csv file. Save older .xls files as .xlsx first.");
  if(file.size>2000000)throw new Error("Use a file under 2 MB.");
  const bytes=new Uint8Array(await file.arrayBuffer());let binary="";for(const byte of bytes)binary+=String.fromCharCode(byte);
  const preview=await request("/api/import/preview",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({filename:file.name,data:btoa(binary),sheet:Number(byId("import-sheet").value)-1,header_row:Number(byId("import-header").value)})});
  importRows=preview.rows;importChoices=[];importOperation=crypto.randomUUID();byId("import-preview").replaceChildren();
  for(const [index,row]of importRows.entries()){const box=element("div",undefined,byId("import-preview"));element("strong",`Row ${row.row}: ${row.name}`,box);
    const label=element("label","Review choice",box),select=element("select",undefined,box);select.id=`import-choice-${index}`;label.htmlFor=select.id;
    for(const [value,text]of [["skip","Skip this row"],["create","Create a new product"],...row.matches.map(match=>[match.id,`Link existing ${match.name} (${match.match})`])]){const option=element("option",text,select);option.value=value;}
    select.value=row.matches.length?"skip":"create";
    const unitLabel=element("label","Unit for new product",box),unit=element("input",undefined,box);unit.id=`import-unit-${index}`;unitLabel.htmlFor=unit.id;unit.value=row.unit;unit.maxLength=30;
    element("p",`Opening: ${row.opening||"not imported"}; Code: ${row.code||"none"}`,box);importChoices.push({select,unit});
  }byId("import-apply-form").hidden=false;feedback.textContent=`Preview ready: ${importRows.length} rows. No inventory changed.`;
}catch(error){feedback.textContent=error.message;}finally{button.disabled=false;}});
byId("import-apply-form").addEventListener("submit",async(event)=>{event.preventDefault();try{const rows=importRows.map((row,index)=>{const choice=importChoices[index];return {...row,unit:choice.unit.value,action:["create","skip"].includes(choice.select.value)?choice.select.value:"link",target_id:["create","skip"].includes(choice.select.value)?null:choice.select.value};});
  await request("/api/import/apply",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:importOperation,category_id:byId("import-category").value,date:byId("import-date").value,rows})});
  byId("import-apply-form").hidden=true;await loadWorkspace();await loadProducts();byId("import-feedback").textContent="Reviewed import applied with a pre-import backup.";
}catch(error){byId("import-feedback").textContent=error.message;}});

let templateData="";let templateControls=[];
byId("template-inspect-form").addEventListener("submit",async(event)=>{event.preventDefault();try{const file=byId("template-file").files[0];if(file.size>2000000)throw new Error("Use a template under 2 MB.");const bytes=new Uint8Array(await file.arrayBuffer());let binary="";for(const byte of bytes)binary+=String.fromCharCode(byte);templateData=btoa(binary);
  const layout=await request("/api/templates/inspect",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({data:templateData})});
  table(["Row","First worksheet sample"],layout.sample.map((row,index)=>[index+1,row.join(" | ")]),byId("template-sample"));
  byId("template-name").value=file.name;byId("template-mapping").replaceChildren();templateControls=[];
  for(const [index,category]of categories.entries()){
    const suggested=layout.suggested_mapping?.find(row=>row.category_id===category.id);
    const group=element("fieldset",undefined,byId("template-mapping"));element("legend",category.name,group);const fields={};
    function input(key,label,value="",type="text"){const text=element("label",label,group),control=element("input",undefined,group);control.id=`template-${index}-${key}`;text.htmlFor=control.id;control.type=type;control.value=value;fields[key]=control;return control;}
    const sheetLabel=element("label","Worksheet (blank means this category is not mapped)",group),sheet=element("select",undefined,group);sheet.id=`template-${index}-sheet`;sheetLabel.htmlFor=sheet.id;element("option","Not mapped",sheet).value="";for(const name of layout.sheets)element("option",name,sheet).value=name;sheet.value=layout.sheets[index]||"";fields.sheet=sheet;
    input("start_row","First product row",suggested?.start_row||"","number");input("end_row","Last product row to fill/clear",suggested?.end_row||"","number");
    const cutlery=category.id==="cutlery-cookeries",food=category.id==="dry-foods";
    for(const [key,label,value]of [["name","Item name column",cutlery?"C":"B"],["unit","Unit column (blank if absent)",cutlery?"":"C"],["code","Item code column (optional)",cutlery?"B":""],["field:Packing","Packing column (optional)",food?"D":""],["field:Specification","Specification column (optional)",cutlery?"D":""],["opening","Opening column",food||cutlery?"E":"D"],["received","Received column (optional)",""],["issued","Consumed / Issued column",food||cutlery?"F":"E"],["closing","Available column",food||cutlery?"G":"F"]])input(key,label,value);
    const modeLabel=element("label","Meaning of the company's Opening figure",group),mode=element("select",undefined,group);mode.id=`template-${index}-opening-mode`;modeLabel.htmlFor=mode.id;for(const [value,label]of [["","Choose after reviewing company format"],["period_start","Stock at period start (map Received separately if receipts exist)"],["with_receipts","Stock available before consumption: opening plus receipts"]])element("option",label,mode).value=value;fields.opening_mode=mode;
    input("serial","Serial-number column (optional)","A");input("title_cell","Report title cell (optional)","");input("period_cell","Reporting-period cell (optional)","");input("issue_period_cell","Issue-period heading cell (optional)","");
    if(suggested){sheet.value=suggested.sheet;mode.value=suggested.opening_mode;for(const [key,value]of Object.entries(suggested.columns))if(fields[key])fields[key].value=value;for(const key of ["title_cell","period_cell","issue_period_cell"])fields[key].value=suggested[key]||"";}
    templateControls.push({category,fields});
  }byId("template-save-form").hidden=false;status.textContent="Template read locally. Review worksheet ranges and opening-stock meaning before saving.";
}catch(error){status.textContent=error.message;}});
byId("template-save-form").addEventListener("submit",async(event)=>{event.preventDefault();try{const mapping=templateControls.filter(row=>row.fields.sheet.value).map(({category,fields})=>({category_id:category.id,sheet:fields.sheet.value,start_row:Number(fields.start_row.value),end_row:Number(fields.end_row.value),opening_mode:fields.opening_mode.value,period_style:"company",title_cell:fields.title_cell.value.trim().toUpperCase(),period_cell:fields.period_cell.value.trim().toUpperCase(),issue_period_cell:fields.issue_period_cell.value.trim().toUpperCase(),columns:Object.fromEntries(Object.entries(fields).filter(([key,value])=>!["sheet","start_row","end_row","opening_mode","title_cell","period_cell","issue_period_cell"].includes(key)&&value.value.trim()).map(([key,value])=>[key,value.value.trim().toUpperCase()]))}));
  await request("/api/templates",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:byId("template-name").value,data:templateData,mapping})});await listReports();status.textContent="Mapped company template saved. Existing report versions can now use it.";
}catch(error){status.textContent=error.message;}});

// Screen changes hide panels without rebuilding their forms or losing draft input.
function showInventoryScreen() {
  const links = [...document.querySelectorAll(".screen-nav a")];
  const selected = links.find(link => link.hash === location.hash) || links[0];
  for (const link of links) {
    if (link === selected) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  for (const panel of document.querySelectorAll("[data-screen]"))
    panel.hidden = "#" + panel.dataset.screen !== selected.hash;
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", showInventoryScreen);
showInventoryScreen();
