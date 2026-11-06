/* Connection setup uses the signed-in browser account. Excel receives its own
   read-only password; never put it in a URL, workbook cell or localStorage. */
"use strict";
let excelProfile = null;
let excelPasswordProfile = null;
const excelMessage = message => { byId("excel-feedback").textContent = message; };
const excelRoot = identity => `${location.origin}/api/excel-feed/${identity}/odata/`;

function showExcelPassword(result) {
  byId("excel-credentials").hidden = !result.password;
  if (!result.password) return;
  excelPasswordProfile = result.id;
  byId("excel-url").value = excelRoot(result.id);
  byId("excel-password").value = result.password;
}

async function showExcelConnections() {
  const target = byId("excel-connections");
  target.replaceChildren();
  for (const profile of await request("/api/excel/connections")) {
    const row = element("li", undefined, target);
    element("p", `${profile.label} · ${profile.linked}/${profile.total} rows linked · ${profile.active ? "Enabled" : "Disabled"}`, row);
    const button = element("button", "Review product links", row);
    button.type = "button";
    button.className = "secondary";
    button.addEventListener("click", async () => {
      try { await showExcelProfile(await request(`/api/excel/connections/${profile.id}`)); }
      catch (error) { excelMessage(error.message); }
    });
  }
}

async function showExcelProfile(profile) {
  excelProfile = profile;
  if (excelPasswordProfile !== profile.id) {
    byId("excel-password").value = "";
    byId("excel-credentials").hidden = true;
  }
  byId("excel-link-editor").hidden = false;
  byId("excel-selected").textContent = `${profile.label} · ${profile.linked}/${profile.total} rows linked`;
  byId("excel-url").value = excelRoot(profile.id);
  const rows = byId("excel-workbook-row");
  rows.replaceChildren();
  // Put unresolved rows first; the server preserves existing product IDs when
  // catalog labels change, so matching is never silently redone on refresh.
  for (const item of [...profile.items].sort((a,b)=>Number(!!a.product_id)-Number(!!b.product_id))) {
    const option = element("option", `${item.product_id ? "Linked" : item.status} · ${item.name} · ${item.unit || "unit unspecified"}${item.packing ? " · "+item.packing : ""}${item.specification ? " · "+item.specification : ""}`, rows);
    option.value = item.key;
  }
  const products = byId("excel-app-product");
  products.replaceChildren();
  element("option", "Choose the matching app product", products).value="";
  for (const product of catalog) {
    const custom = JSON.parse(product.fields_json || "{}");
    const option = element("option", `${product.name} · ${product.unit}${product.code ? " · "+product.code : ""}${custom.Specification ? " · "+custom.Specification : ""}`, products);
    option.value = product.id;
  }
  const selected=()=>{
    const item=profile.items.find(item=>item.key===rows.value);
    products.value=item?.product_id || "";
  };
  rows.onchange=selected;
  selected();
  byId("excel-row-note").textContent = "Unlinked or ambiguous rows stay blank in Excel. Link them to the correct app product; no product is created or deleted here.";
}

byId("excel-setup-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  try {
    const file = byId("excel-setup-file").files[0];
    if (!file || file.size > 4*1024*1024) throw new Error("Choose the prepared connection setup JSON file (up to 4 MB).");
    const result = await request("/api/excel/connections", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(JSON.parse(await file.text()))});
    showExcelPassword(result);
    await showExcelProfile(result);
    await showExcelConnections();
    excelMessage("Connection created. Use username excel and the password shown here in Excel's Basic authentication dialog. This password is shown only now.");
  } catch (error) { excelMessage(error.message); }
  finally { button.disabled = false; }
});
byId("excel-list-refresh").addEventListener("click",()=>showExcelConnections().catch(error=>excelMessage(error.message)));
byId("excel-copy-password").addEventListener("click",async()=>{
  try { await navigator.clipboard.writeText(byId("excel-password").value); excelMessage("Excel password copied. Paste it only into Excel's connection password field."); }
  catch { excelMessage("Select and copy the Excel password from the field above."); }
});
for (const [id,action] of [["excel-rematch","match"],["excel-reset-password","password"],["excel-disable","revoke"],["excel-link-product","link"],["excel-unlink-product","unlink"]]) {
  byId(id).addEventListener("click",async()=>{
    if (!excelProfile) return;
    const button=byId(id);
    button.disabled=true;
    try {
      const values=action==="link"?{key:byId("excel-workbook-row").value,product_id:byId("excel-app-product").value}:action==="unlink"?{key:byId("excel-workbook-row").value}:{};
      const result=await request(`/api/excel/connections/${excelProfile.id}/${action}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(values)});
      if(result.password) showExcelPassword(result);
      await showExcelProfile(result);
      await showExcelConnections();
      excelMessage(action==="revoke"?"Excel refresh disabled for this connection. Saved app data is preserved.":action==="password"?"New Excel password created. The previous password no longer works. Update Excel's saved credentials.":"Product links updated. Refresh All in Excel to fetch the daily quantities.");
    } catch(error) { excelMessage(error.message); }
    finally {button.disabled=false;}
  });
}
