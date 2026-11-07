/* Run draft-http-server.py first. Real app, API, IndexedDB and mobile input;
   all inventory belongs to a disposable local test server, never Render. */
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.INVENTORY_TEST_CHROME?{executablePath:process.env.INVENTORY_TEST_CHROME}:{})});
  try{
    for(const mode of ['desktop','phone','phone-stale']){
      const mobile=mode!=='desktop';
      const context=await browser.newContext({viewport:{width:mobile?375:1280,height:900},isMobile:mobile,hasTouch:mobile});
      const page=await context.newPage();
      const requests=[],errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      page.on('response',async response=>{try{if(response.url().includes('/api/drafts'))requests.push({url:response.url(),status:response.status(),body:await response.text()});}catch{/* A context can close after the tested save finishes. */}});
      await page.goto('http://127.0.0.1:8129');
      await page.locator('#username').fill('draft-test');
      await page.locator('#password').fill('local test passphrase');
      await page.locator('#auth-submit').click();
      await page.locator('#main').waitFor({state:'visible'});
      await page.locator('a[href="#entry"]').click();
      await page.locator('#bulk-rows input[inputmode="decimal"]').first().waitFor();
      if(mode==='phone-stale'){
        await page.locator('#bulk-date').fill('2026-11-02');
        await page.locator('#bulk-rows input[inputmode="decimal"]').first().fill('2');
        await page.waitForFunction(()=>document.getElementById('draft-status').textContent.includes('Draft saved locally and on the server'));
        const old=await page.evaluate(()=>({identity:draftIdentity,revision:draftRevision}));
        // Simulate an old phone draft after a server-side product removal or
        // another editor changed its contents. Its cloud version stays intact.
        const changed=await page.evaluate(async old=>{
          const response=await fetch(`/api/drafts/${old.identity}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:old.revision,entries:[]})});
          return response.status;
        },old);
        assert.equal(changed,200);
        await page.locator('#bulk-rows input[inputmode="decimal"]').first().fill('3');
        await page.locator('#bulk-form button[type="submit"]').click();
        await page.waitForFunction(()=>document.getElementById('draft-status').textContent.includes('Not finalized'));
        await page.route('**/api/drafts/**',async route=>{
          if(route.request().method()==='PUT'&&route.request().url().includes(old.identity))await new Promise(resolve=>setTimeout(resolve,750));
          await route.continue();
        });
        await page.locator('#new-draft').click();
        // A fast phone user begins typing as soon as the form is enabled.
        await page.locator('#bulk-rows input[inputmode="decimal"]').first().fill('4');
        const postedId=await page.evaluate(()=>[...bulkValues.values()][0].id);
        await page.locator('#bulk-form button[type="submit"]').click();
        await page.waitForFunction(()=>!document.querySelector('#bulk-form button[type=submit]').disabled);
        const message=await page.locator('#status').innerText();
        assert.match(message,/Bulk entry finalized/,JSON.stringify({mode,message,draft:await page.locator('#draft-status').innerText(),requests,errors}));
        const saved=await page.evaluate(async({oldId,postedId})=>{
          const local=await localGet('meta',`draft:${currentUser.id}:${oldId}`);
          const drafts=await(await fetch('/api/drafts')).json();
          const stock=await(await fetch(`/api/products/${catalog[0].id}/stock`)).json();
          return {localQuantity:local.value.values[0][1].quantity,old:drafts.find(row=>row.id===oldId),posted:stock.entries.filter(row=>row.id===postedId)};
        },{oldId:old.identity,postedId});
        assert.equal(saved.localQuantity,'3');
        assert.equal(saved.old.finalized,0);
        assert.equal(saved.old.payload_json,'[]');
        assert.equal(saved.posted.length,1);
        assert.equal(saved.posted[0].quantity,'4.0');
        console.log('PASS: conflicting phone draft preserved; starting a fresh draft on a slow connection finalizes only the new quantity.');
        await context.close();
        continue;
      }
      for(const [kind,quantity]of [['receive','2'],['issue','1']]){
        await page.locator('#bulk-kind').selectOption(kind);
        await page.locator('#bulk-date').fill('2026-11-02');
        await page.locator('#bulk-rows input[inputmode="decimal"]').first().fill(quantity);
        await page.waitForFunction(()=>document.getElementById('draft-status').textContent.includes('Draft saved locally and on the server'));
        await page.locator('#bulk-form button[type="submit"]').click();
        await page.waitForFunction(()=>document.getElementById('status').textContent.includes('Bulk entry finalized')||document.getElementById('draft-status').textContent.includes('Not finalized'));
        const message=await page.locator('#status').innerText();
        assert.match(message,/Bulk entry finalized/,JSON.stringify({mode,kind,message,draft:await page.locator('#draft-status').innerText(),requests,errors}));
        // An explicit new draft must have a fresh identity and no inherited
        // revision, even while a debounce timer or slow save is still present.
        await page.locator('#new-draft').click();
      }
      assert.deepEqual(errors,[]);
      console.log('PASS: full '+mode+' app finalized receiving and issuing through real HTTP and IndexedDB.');
      await context.close();
    }
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
