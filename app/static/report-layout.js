/* Monthly stock-sheet presentation. All figures come from the saved server
   report, so preview and download refer to the same historical snapshot.
   "Open stock" includes this month's receipts, as requested for this sheet.
   It does not change a product's original opening-stock ledger entry. */
"use strict";

function stockSheetColumns(categoryId) {
  const columns = [["number", "S.no"]];
  if (categoryId === "cutlery-cookeries") columns.push(["code", "Item code"]);
  columns.push(["name", "Item name"]);
  if (categoryId === "dry-foods") columns.push(["packing", "Packing"]);
  if (categoryId === "cutlery-cookeries") columns.push(["specification", "Specification"]);
  columns.push(["unit", "Unit"], ["stock_sheet_opening", "Open stock"], ["issued", "Consumed"], ["closing", "Available stock"]);
  return columns;
}

function renderMonthlyReport(report, target) {
  target.replaceChildren();
  target.classList.add("stock-sheet-preview");
  const banner = element("div", undefined, target);
  banner.className = "stock-sheet-banner";
  element("h3", "MONTHLY STOCK REPORT", banner);
  element("p", `${report.company_name || "Local Inventory"} STOCK SHEET`, banner);
  if (report.name && report.name !== "Inventory report") element("p", report.name, banner);
  // Parse the date explicitly in UTC: Safari and other browser time zones
  // must show the month chosen in the report, not a neighboring local day.
  const start = new Date(report.from_date + "T12:00:00Z");
  const month = new Intl.DateTimeFormat("en", {month:"long", year:"numeric", timeZone:"UTC"}).format(start);
  element("p", month, banner);
  const dates = element("p", undefined, banner);
  dates.className = "stock-sheet-dates";
  element("span", report.from_date, dates);
  dates.append(document.createTextNode(" to "));
  element("span", report.to_date, dates);
  element("p", "Open stock includes the previous closing stock and all receiving for this month. Available stock = Open stock − Consumed. Only synchronized, saved stock is included.", target).className = "hint";
  if (!report.rows.length) {
    element("p", "No products match this report. Include zero-stock products or change the product selection to see more items.", target);
    return;
  }
  const groups = new Map();
  for (const row of report.rows) {
    const key = row.category_id || row.category;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  const quantityKeys = new Set(["stock_sheet_opening", "issued", "closing"]);
  for (const rows of groups.values()) {
    const category = element("section", undefined, target);
    category.className = "stock-sheet-category";
    element("h3", rows[0].category, category);
    const columns = stockSheetColumns(rows[0].category_id);
    const scroller = element("div", undefined, category);
    scroller.className = "stock-sheet-scroll";
    scroller.tabIndex = 0;
    scroller.setAttribute("role", "region");
    scroller.setAttribute("aria-label", `${rows[0].category} monthly stock sheet. Scroll for all columns.`);
    const grid = element("table", undefined, scroller);
    grid.className = "stock-sheet-table";
    if (rows[0].category_id === "cutlery-cookeries") grid.classList.add("stock-sheet-cutlery");
    element("caption", `${rows[0].category} · ${month}`, grid);
    const heading = element("tr", undefined, element("thead", undefined, grid));
    for (const [key, label] of columns) {
      const cell = element("th", label, heading);
      cell.scope = "col";
      cell.className = "stock-sheet-" + key;
    }
    const body = element("tbody", undefined, grid);
    const cards = element("div", undefined, category);
    cards.className = "stock-sheet-cards";
    rows.forEach((original, index) => {
      const custom = label => Object.entries(original.custom_fields || {}).find(([key]) => key.trim().toLowerCase() === label)?.[1] || "";
      const row = {...original, number:index+1, packing:custom("packing"), specification:custom("specification")};
      const tr = element("tr", undefined, body);
      for (const [key] of columns) {
        const value = quantityKeys.has(key) ? displayQuantity(row[key]) : (row[key] || "—");
        const cell = element(key === "name" ? "th" : "td", value, tr);
        if (key === "name") cell.scope = "row";
        cell.className = "stock-sheet-" + key;
      }
      // Phones show the complete name plus three labeled balances. This keeps
      // the sheet readable without pinning columns over each other on a phone.
      const card = element("article", undefined, cards);
      card.className = "stock-sheet-card";
      element("h4", `${row.number}. ${row.name}`, card);
      const metadata = element("dl", undefined, card);
      for (const [key,label] of columns.filter(([key]) => !quantityKeys.has(key) && !["name","number"].includes(key))) {
        if (!row[key]) continue;
        element("dt", label, metadata);
        element("dd", row[key], metadata);
      }
      const balances = element("dl", undefined, card);
      balances.className = "stock-sheet-balances";
      for (const [key,label] of columns.filter(([key]) => quantityKeys.has(key))) {
        const balance = element("div", undefined, balances);
        balance.className = "stock-sheet-" + key;
        element("dt", label, balance);
        element("dd", displayQuantity(row[key]), balance);
      }
    });
  }
}
