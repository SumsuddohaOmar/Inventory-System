/* Full UI feedback placement and mobile hierarchy regression.
   Uses only draft-http-server.py's disposable local inventory. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.INVENTORY_TEST_CHROME?{executablePath:process.env.INVENTORY_TEST_CHROME}:{})});
  try{
    for(const width of [320,375,412,1280]){
      const context=await browser.newContext({viewport:{width,height:850},isMobile:width<720,hasTouch:width<720});
      const page=await context.newPage(),errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      await page.goto('http://127.0.0.1:8129');
      await page.locator('#username').fill('draft-test');
      await page.locator('#password').fill('local test passphrase');
      await page.locator('#auth-submit').click();
      await page.locator('#main').waitFor({state:'visible'});
      await page.locator('a[href="#entry"]').click();
      await page.locator('.bulk-product-name').first().waitFor();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${width}px overflow`);
      const typography=await page.locator('.bulk-row').first().evaluate(row=>({
        name:parseFloat(getComputedStyle(row.querySelector('.bulk-product-name')).fontSize),
        metadata:parseFloat(getComputedStyle(row.querySelector('small')).fontSize),
        input:parseFloat(getComputedStyle(row.querySelector('input')).fontSize),
        inputHeight:row.querySelector('input').getBoundingClientRect().height,
        nameWidth:row.querySelector('label').getBoundingClientRect().width,
        inputWidth:row.querySelector('input').getBoundingClientRect().width
      }));
      if(width<720){
        assert.ok(typography.name>typography.metadata);
        assert.equal(typography.input,16);
        assert.ok(typography.inputHeight>=44&&typography.nameWidth>typography.inputWidth);
      }
      for(const row of await page.locator('.bulk-product-name').all()){
        assert.ok(await row.evaluate(node=>node.scrollWidth<=node.clientWidth+1),'Product name clipped');
      }
      await page.locator('#bulk-form button[type=submit]').click();
      assert.match(await page.locator('#draft-status').innerText(),/Enter at least one quantity/);
      assert.equal(await page.locator('#status').innerText(),'');
      assert.ok(await page.locator('#draft-status').evaluate(node=>node.closest('form').id==='bulk-form'));
      await page.locator('#bulk-date').fill('2026-11-02');
      await page.locator('#bulk-rows input[inputmode=decimal]').first().fill('1');
      await page.locator('#bulk-form button[type=submit]').click();
      await page.waitForFunction(()=>!document.querySelector('#bulk-form button[type=submit]').disabled);
      assert.match(await page.locator('#draft-status').innerText(),/Bulk entry finalized/);
      assert.doesNotMatch(await page.locator('#draft-status').innerText(),/Stock has not changed/);
      if(process.env.INVENTORY_UI_PREVIEW&&width===375){
        fs.mkdirSync(process.env.INVENTORY_UI_PREVIEW,{recursive:true});
        await page.locator('.bulk-row').first().scrollIntoViewIfNeeded();
        await page.screenshot({path:path.join(process.env.INVENTORY_UI_PREVIEW,'mobile-entry.png'),fullPage:false});
        await page.locator('#bulk-form button[type=submit]').scrollIntoViewIfNeeded();
        await page.screenshot({path:path.join(process.env.INVENTORY_UI_PREVIEW,'mobile-confirmation.png'),fullPage:false});
      }
      // Settings contains many independent forms. Their feedback must stay
      // beside the action, never at the page end or in another feature.
      await page.locator('a[href="#settings"]').click();
      await page.locator('summary').filter({hasText:/^Optional app PIN lock$/}).click();
      await page.locator('#new-pin').fill('abc');
      await page.locator('#pin-save').click();
      assert.match(await page.locator('#pin-save + .action-feedback').innerText(),/Use a PIN/);
      await page.locator('summary').filter({hasText:/^Company settings & month closing$/}).click();
      await page.locator('#company-name').fill('Feedback test store');
      await page.locator('#settings-form button').click();
      await page.waitForFunction(()=>document.querySelector('#settings-form .action-feedback')?.textContent.includes('Company settings saved'));
      await page.locator('summary').filter({hasText:/^Manage product details & alerts$/}).click();
      await page.locator('#manage-notes').fill('Disposable UI test note');
      await page.locator('#manage-form button').filter({hasText:'Save product details'}).click();
      await page.waitForFunction(()=>document.querySelector('#manage-form .action-feedback')?.textContent.includes('Product details saved'));
      await page.locator('summary').filter({hasText:/^Backups$/}).click();
      await page.locator('#backup-create').click();
      await page.waitForFunction(()=>document.querySelector('#backup-create + .action-feedback')?.textContent.includes('Unavailable'));
      await page.locator('summary').filter({hasText:/^Audit history$/}).click();
      await page.locator('#audit-refresh').click();
      await page.waitForFunction(()=>document.getElementById('audit-status').textContent.includes('audit events loaded'));
      await page.locator('a[href="#history"]').click();
      await page.locator('#activity-refresh').click();
      await page.waitForFunction(()=>document.querySelector('#activity-refresh + .action-feedback')?.textContent.includes('Activity refreshed'));
      await page.locator('#monthly-refresh').click();
      await page.waitForFunction(()=>document.querySelector('#monthly-refresh + .action-feedback')?.textContent.includes('Monthly inventory refreshed'));
      await page.locator('a[href="#products"]').click();
      const stockPanel=page.locator('.stock-details').first();
      await stockPanel.locator('summary').first().click();
      const stockForm=stockPanel.locator('.stock-form');
      await stockForm.locator('select').selectOption('issue');
      await stockForm.locator('input[type=date]').fill('2026-11-02');
      await stockForm.locator('input[inputmode=decimal]').fill('99999');
      await stockForm.locator('button[type=submit]').click();
      await page.waitForFunction(()=>document.querySelector('.stock-details .stock-error')?.textContent.includes('Not saved'));
      assert.equal(await stockForm.locator('input[inputmode=decimal]').inputValue(),'99999');
      await stockForm.locator('input[inputmode=decimal]').fill('1');
      await stockForm.locator('button[type=submit]').click();
      await page.waitForFunction(()=>document.querySelector('.stock-details > .action-feedback')?.textContent.includes('Stock entry saved'));
      await page.locator('#name').fill('UI sample '+width);
      await page.locator('#unit').fill('PCS');
      await page.locator('#category').selectOption('dry-foods');
      await page.locator('#save').click();
      await page.waitForFunction(()=>document.querySelector('#product-form .action-feedback')?.textContent.includes('saved.'));
      assert.equal(await page.locator('#status').innerText(),'');
      // A delayed result must not appear as a notification over another menu.
      await page.locator('a[href="#reports"]').click();
      let releaseReport;
      await page.route('**/api/reports',async route=>{
        if(route.request().method()==='POST')await new Promise(resolve=>releaseReport=resolve);
        await route.continue();
      });
      await page.locator('#report-from').fill('2026-11-01');
      await page.locator('#report-to').fill('2026-11-30');
      await page.locator('#report-form button').click();
      await page.locator('a[href="#settings"]').click();
      await page.locator('#pin-save').click();
      assert.ok(releaseReport);
      releaseReport();
      await page.waitForFunction(()=>document.querySelector('#report-form .action-feedback')?.textContent.includes('Report preview saved'));
      assert.match(await page.locator('#pin-save + .action-feedback').innerText(),/Use a PIN/);
      assert.ok(!(await page.locator('.feedback-notice:visible').allTextContents()).some(text=>text.includes('Report preview saved')),'Old request notification leaked into Settings');
      if(width===375){
        await page.evaluate(async()=>{await navigator.serviceWorker.ready;});
        await page.reload();
        await page.waitForFunction(()=>!!navigator.serviceWorker.controller);
        await page.locator('#main').waitFor({state:'visible'});
        await context.setOffline(true);
        await page.reload();
        await page.locator('#main').waitFor({state:'visible'});
        await page.locator('a[href="#entry"]').click();
        await page.locator('.bulk-product-name').first().waitFor();
        await page.locator('#bulk-form button[type=submit]').click();
        assert.match(await page.locator('#draft-status').innerText(),/Enter at least one quantity/);
        await context.setOffline(false);
      }
      assert.deepEqual(errors,[]);
      console.log(`PASS: ${width}px inline success/error feedback across Entry, Products, History, Reports and Settings; mobile hierarchy and delayed response isolation.`);
      await context.close();
    }
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
