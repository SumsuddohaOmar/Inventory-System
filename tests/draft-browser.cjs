/* Exercise actual bulk submit/autosave code in a phone-sized Chromium page.
   Synthetic server and browser-storage fixtures never touch live inventory. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const source=fs.readFileSync(path.join(__dirname,'../app/static/workspace.js'),'utf8');
const draftCode=source.slice(source.indexOf('function draftRows()'),source.indexOf('function correctionForm('));
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.INVENTORY_TEST_CHROME?{executablePath:process.env.INVENTORY_TEST_CHROME}:{})});
  try{
    for(const scenario of ['lost-response-edited','lost-response-same','legacy-stale','other-device','other-tab','slow-autosave']){
      const page=await browser.newPage({viewport:{width:375,height:812},isMobile:true,hasTouch:true});
      await page.setContent('<form id="bulk-form"><select id="bulk-category"><option value="dry-foods">Dry Foods</option></select><select id="bulk-kind"><option value="receive">Receive</option><option value="issue">Issue</option></select><input id="bulk-date" value="2026-11-01"><input id="bulk-notes" value=""><input id="quantity" value="2"><div id="bulk-rows"></div><p id="draft-status"></p><button type="submit">Finalize bulk entry</button><button id="new-draft" type="button">Start another draft</button></form><input id="bulk-search"><ul id="draft-list"></ul><section id="bulk-section"></section>');
      await page.addScriptTag({content:`
        const scenario=${JSON.stringify(scenario)};
        let bulkValues=new Map([['product',{id:'entry',quantity:'2',notes:''}]]);
        let draftIdentity='draft',draftRevision=0,draftTimer,draftChain=Promise.resolve();const draftAttempts=new Map();
        let currentUser={id:'phone-user'},catalog=[],categories=[];
        const inventoryUUID=()=>crypto.randomUUID();
        const byId=id=>document.getElementById(id);
        const status=byId('draft-status');
        const store=new Map();
        const copy=x=>JSON.parse(JSON.stringify(x));
        async function localGet(table,id){return store.get(id)&&copy(store.get(id));}
        async function localPut(table,row){store.set(row.id,copy(row));}
        async function localDelete(table,id){store.delete(id);}
        async function localAll(){return [];}
        async function renderQueue(){} async function loadWorkspace(){} async function loadProducts(){}
        function drawBulkRows(){} function businessToday(){return '2026-11-07';}
        function element(tag,text,parent){const node=document.createElement(tag);node.textContent=text||'';parent?.append(node);return node;}
        let cloud=null, lost=true, gate, finalizeCalls=0, putCalls=0, posted=null;
        const reordered=rows=>rows.map(row=>Object.fromEntries(Object.entries(row).reverse()));
        async function request(url,options={}){
          if(url.endsWith('/finalize')){finalizeCalls++;posted=copy(cloud.entries);return {};}
          if(options.method==='PUT'){
            if(options.headers['X-Inventory-Owner']!=='phone-user')throw new Error('Missing original draft owner');
            putCalls++;
            const body=JSON.parse(options.body);
            if(cloud&&body.revision!==cloud.revision){const error=new Error('Draft changed elsewhere. Reload it before editing.');error.rejected=true;throw error;}
            cloud={revision:(cloud?.revision||0)+1,entries:reordered(body.entries)};
            if(putCalls===1&&scenario==='slow-autosave')await new Promise(resolve=>gate=resolve);
            if(lost&&scenario.startsWith('lost-response')){lost=false;throw new Error('Connection interrupted after save');}
            return {revision:cloud.revision};
          }
          if(url==='/api/drafts')return cloud?[{id:'draft',revision:cloud.revision,payload_json:JSON.stringify(cloud.entries)}]:[];
          throw new Error('Unexpected request '+url);
        }
      `+draftCode});
      if(scenario==='slow-autosave'){
        await page.evaluate(()=>{window.inFlight=saveDraft();});
        await page.waitForFunction(()=>!!gate);
        await page.locator('button[type=submit]').click();
        await page.evaluate(()=>gate());
      }else{
        await page.evaluate(async()=>{try{await saveDraft();}catch{}});
        if(scenario==='legacy-stale')await page.evaluate(()=>{const saved=store.get('draft:phone-user:draft');saved.value.revision=0;delete saved.value.pendingEntries;delete saved.value.previousAttempt;});
        // Emulate a phone restoring its durable draft after a reload, with
        // a stale revision left behind by the lost response.
        await page.evaluate(async()=>{draftRevision=0;await restoreLocalDraft('draft');});
        if(scenario!=='lost-response-same')await page.evaluate(()=>{bulkValues.get('product').quantity='3';byId('quantity').value='3';});
        if(['other-device','other-tab'].includes(scenario))await page.evaluate(()=>{
          cloud.entries[0].quantity='9';cloud.revision++;
          if(scenario==='other-tab'){
            const saved=store.get('draft:phone-user:draft');
            saved.value.values[0][1].quantity='9';saved.value.pendingEntries=copy(cloud.entries);saved.value.revision=cloud.revision;
          }
        });
        await page.locator('button[type=submit]').click();
      }
      await page.waitForFunction(()=>!document.querySelector('button[type=submit]').disabled);
      const result=await page.evaluate(()=>({finalizeCalls,posted,cloud,quantity:byId('quantity').value,message:byId('draft-status').textContent,local:[...store.values()]}));
      if(['other-device','other-tab'].includes(scenario)){
        assert.equal(result.finalizeCalls,0);
        assert.equal(result.cloud.entries[0].quantity,'9');
        assert.equal(result.quantity,'3');
        assert.match(result.message,/Not finalized/);
        assert.ok(result.local.some(row=>row.value?.values?.[0]?.[1]?.quantity==='3'));
      }else{
        assert.equal(result.finalizeCalls,1,scenario);
        assert.equal(result.posted[0].quantity,['lost-response-edited','legacy-stale'].includes(scenario)?'3':'2',scenario);
      }
      await page.close();
    }
    console.log('PASS: mobile bulk finalization recovers lost/reordered saves and restored stale revisions; waits for slow autosave; genuine cross-device conflicts preserve both versions without finalizing.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
