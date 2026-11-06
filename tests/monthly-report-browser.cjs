/* Optional: NODE_PATH must resolve Playwright; set INVENTORY_TEST_CHROME when
   using installed Chrome. All data is a synthetic saved report fixture. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.join(__dirname,'..');
const fixture = {layout:'monthly-stock-v1',name:'Inventory report',company_name:'Test Store',from_date:'2026-11-01',to_date:'2026-11-30',rows:[]};
for (const [category_id,category,name] of [['dry-foods','Dry Foods','BALSAMIC VINEGAR AND SALAD DRESSING'],['disposables','Disposables','Kitchen gloves'],['chemicals','Chemicals','Cleaning concentrate'],['stationery','Stationery','Pens'],['cutlery-cookeries','Cutlery & Cookeries','=SAMPLE <steel> & serving spoons']]) {
  fixture.rows.push({category_id,category,name,unit:'PCS',code:'TEST-1',custom_fields:{Packing:'1 x 5KG',Specification:'Stainless steel, long handle'},opening:'10.100',received:'2.200',stock_sheet_opening:'12.300',issued:'1.300',closing:'11.000'});
}

(async()=>{
  if(process.env.INVENTORY_REPORT_PREVIEW) fs.writeFileSync(path.join(process.env.INVENTORY_REPORT_PREVIEW,'sample-report.json'),JSON.stringify(fixture));
  const browser=await chromium.launch({headless:true,...(process.env.INVENTORY_TEST_CHROME?{executablePath:process.env.INVENTORY_TEST_CHROME}:{})});
  try {
    const source=fs.readFileSync(path.join(root,'app/static/workspace.js'),'utf8');
    const formatter=source.slice(source.indexOf('function displayQuantity'),source.indexOf('function businessToday'));
    for(const width of [320,375,412,768,1280]) {
      const page=await browser.newPage({viewport:{width,height:900}});
      await page.setContent('<style>'+fs.readFileSync(path.join(root,'app/static/styles.css'),'utf8')+'</style><main><section><h2>Excel reports</h2><div id="report-preview" class="table-scroll"></div></section></main>');
      await page.addScriptTag({content:'function element(tag,text,parent){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(parent)parent.append(node);return node;}'+formatter+fs.readFileSync(path.join(root,'app/static/report-layout.js'),'utf8')});
      await page.evaluate(report=>renderMonthlyReport(report,document.getElementById('report-preview')),fixture);
      assert.equal(await page.locator('.stock-sheet-category').count(),5);
      assert.equal(await page.locator('.stock-sheet-table thead').first().innerText().then(text=>text.includes('Received')),false);
      assert.equal(await page.locator('.stock-sheet-table tbody tr').first().locator('td.stock-sheet-stock_sheet_opening').innerText(),'12.3');
      assert.equal(await page.locator('.stock-sheet-card').first().locator('.stock-sheet-closing dd').innerText(),'11.0');
      const prefix=width<=720?'.stock-sheet-balances ':'.stock-sheet-table thead ';
      for(const [selector,color] of [['.stock-sheet-stock_sheet_opening','rgb(255, 255, 0)'],['.stock-sheet-issued','rgb(0, 176, 240)'],['.stock-sheet-closing','rgb(255, 0, 0)']]) {
        assert.equal(await page.locator(prefix+selector).first().evaluate(node=>getComputedStyle(node).backgroundColor),color);
      }
      assert.equal(await page.locator('.stock-sheet-cutlery thead .stock-sheet-code').evaluate(node=>getComputedStyle(node).backgroundColor),'rgb(255, 255, 0)');
      assert.equal(await page.locator('.stock-sheet-cutlery thead .stock-sheet-number').evaluate(node=>getComputedStyle(node).backgroundColor),'rgb(255, 255, 255)');
      assert.equal(await page.locator('#report-preview img').count(),0);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${width}px page overflow`);
      assert.equal(await page.locator('.stock-sheet-cards').first().isVisible(),width<=720);
      assert.equal(await page.locator('.stock-sheet-scroll').first().isVisible(),width>720);
      if(process.env.INVENTORY_REPORT_PREVIEW && [375,1280].includes(width)) {
        await page.screenshot({path:path.join(process.env.INVENTORY_REPORT_PREVIEW,`monthly-${width}.png`),fullPage:true});
      }
      await page.evaluate(()=>renderMonthlyReport({...JSON.parse(JSON.stringify({rows:[],from_date:'2026-11-01',to_date:'2026-11-30'}))},document.getElementById('report-preview')));
      assert.match(await page.locator('#report-preview').innerText(),/No products match/);
      await page.close();
    }
    console.log('PASS: monthly report renders all category layouts at 320, 375, 412, 768 and 1280px; numeric labels, empty results and literal text verified.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
