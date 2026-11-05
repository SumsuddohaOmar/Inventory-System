/* Optional real IndexedDB regression check:
   node tests/offline-browser.cjs
   Requires Playwright plus Chromium (or set INVENTORY_TEST_CHROME to Chrome).
   Requests are intercepted: no real server, accounts or inventory are touched. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const script = fs.readFileSync(path.join(__dirname,'../app/static/offline.js'),'utf8');
const html = `<meta name="inventory-cloud-sync" content="enabled"><p id="sync-status"></p><ul id="sync-queue"></ul><script src="/offline.js"></script>`;

(async()=>{
  const browser = await chromium.launch({headless:true,
    ...(process.env.INVENTORY_TEST_CHROME ? {executablePath:process.env.INVENTORY_TEST_CHROME} : {})});
  try {
    const devices=[];
    for(let i=0;i<2;i++) {
      const context=await browser.newContext();
      const state={online:false,posted:[]};
      await context.route('https://inventory.test/**',async route=>{
        const url=new URL(route.request().url());
        if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
        if(url.pathname==='/offline.js')return route.fulfill({contentType:'text/javascript',body:script});
        if(!state.online)return route.abort('failed');
        if(url.pathname==='/api/session')return route.fulfill({json:{user:{id:'same-employee'}}});
        const operations=route.request().postDataJSON().operations;
        state.posted.push(...operations);
        return route.fulfill({json:operations.map(row=>({id:row.id,status:'saved'}))});
      });
      const page=await context.newPage();
      await page.goto('https://inventory.test/');
      await page.evaluate(()=>{currentUser={id:'same-employee',permissions:['read','receive']};});
      const identity=await page.evaluate(async()=>{
        const id=inventoryUUID();
        const response=await offlineRequest('/api/products/rice/stock',{method:'POST',body:JSON.stringify({id,kind:'receive',quantity:'2',effective_date:'2026-11-01',notes:''})});
        if(response.status!==202)throw Error('Offline entry was not retained');
        return id;
      });
      devices.push({context,page,state,identity});
    }
    for(const device of devices) {
      // Reload proves real IndexedDB persistence, rather than JS-memory state.
      await device.page.reload();
      await device.page.evaluate(()=>{currentUser={id:'same-employee',permissions:['read','receive']};});
      assert.deepEqual(await device.page.evaluate(async()=>(await localAll('outbox')).map(row=>row.id)),[device.identity]);
    }
    // Two tabs in one device share its queue; another device does not.
    const tab=await devices[0].context.newPage();await tab.goto('https://inventory.test/');
    assert.deepEqual(await tab.evaluate(async()=>(await localAll('outbox')).map(row=>row.id)),[devices[0].identity]);
    devices[0].state.online=true;
    await devices[0].page.evaluate(()=>syncPending());
    assert.equal(await tab.evaluate(async()=>(await localAll('outbox')).length),0);
    assert.deepEqual(await devices[1].page.evaluate(async()=>(await localAll('outbox')).map(row=>row.id)),[devices[1].identity]);
    assert.deepEqual(devices[0].state.posted.map(row=>row.id),[devices[0].identity]);
    console.log('PASS: real IndexedDB survives reload; tabs share one queue; devices stay independent; syncing one leaves the other untouched.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
