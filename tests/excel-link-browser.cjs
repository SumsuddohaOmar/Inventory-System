/* Browser-only connection setup checks. All requests use synthetic fixtures;
   no live app, credentials or workbook contents are transmitted. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const root=path.join(__dirname,'..');
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.INVENTORY_TEST_CHROME?{executablePath:process.env.INVENTORY_TEST_CHROME}:{})});
  try {
    const index=fs.readFileSync(path.join(root,'app/static/index.html'),'utf8');
    const start=index.lastIndexOf('<details',index.indexOf('id="excel-setup-form"'));
    const section=index.slice(start,index.indexOf('</details>',start)+10).replace('<details ','<details open ');
    for(const width of [320,375,1280]) {
      const page=await browser.newPage({viewport:{width,height:900}});
      await page.setContent('<meta name="viewport" content="width=device-width, initial-scale=1"><style>'+fs.readFileSync(path.join(root,'app/static/styles.css'),'utf8')+'</style><main><section>'+section+'</section></main>');
      await page.addScriptTag({content:`
        const byId=id=>document.getElementById(id);
        function element(tag,text,parent){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(parent)parent.append(node);return node;}
        const catalog=[{id:'rice',name:'Rice',unit:'KG',fields_json:'{}'},{id:'oil',name:'Oil',unit:'BTL',fields_json:'{}'}];
        let profile={id:'fixture',label:'Synthetic workbook',linked:1,total:2,active:true,items:[{key:'dry-foods:7',name:'Rice',unit:'KG',product_id:'rice',status:'Linked'},{key:'dry-foods:8',name:'Oil',unit:'BTL',product_id:null,status:'Not linked'}]};
        async function request(url,options={}){
          if(options.method==='POST') {
            if(url.endsWith('/link')){const values=JSON.parse(options.body);profile.items.find(i=>i.key===values.key).product_id=values.product_id;profile.linked=2;}
            if(url.endsWith('/unlink')){const values=JSON.parse(options.body);profile.items.find(i=>i.key===values.key).product_id=null;profile.linked=1;}
            if(url.endsWith('/revoke'))profile.active=false;
            return {...profile,...(url.endsWith('/password')||url==='/api/excel/connections'?{password:'fixture-only-password'}:{})};
          }
          return url==='/api/excel/connections'?[profile]:profile;
        }
      `+fs.readFileSync(path.join(root,'app/static/excel-link.js'),'utf8')});
      await page.locator('#excel-setup-file').setInputFiles({name:'setup.json',mimeType:'application/json',buffer:Buffer.from('{}')});
      await page.locator('#excel-setup-form button').click();
      await page.waitForFunction(()=>document.getElementById('excel-feedback').textContent.includes('Connection created'));
      assert.equal(await page.locator('#excel-password').inputValue(),'fixture-only-password');
      assert.equal(await page.locator('#excel-password').getAttribute('type'),'password');
      assert.equal(await page.locator('#excel-workbook-row').inputValue(),'dry-foods:8');
      await page.locator('#excel-app-product').selectOption('oil');
      await page.locator('#excel-link-product').click();
      await page.waitForFunction(()=>document.getElementById('excel-selected').textContent.includes('2/2'));
      await page.locator('#excel-workbook-row').selectOption('dry-foods:7');
      await page.locator('#excel-unlink-product').click();
      await page.waitForFunction(()=>document.getElementById('excel-selected').textContent.includes('1/2'));
      await page.locator('#excel-disable').click();
      await page.waitForFunction(()=>document.getElementById('excel-feedback').textContent.includes('disabled'));
      assert.match(await page.locator('#excel-connections').innerText(),/Disabled/);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${width}px overflow`);
      await page.evaluate(()=>showExcelProfile({id:'another-profile',label:'Another workbook',linked:0,total:0,active:true,items:[]}));
      assert.equal(await page.locator('#excel-credentials').isVisible(),false);
      assert.equal(await page.locator('#excel-password').inputValue(),'');
      await page.close();
    }
    console.log('PASS: Excel setup, product review, disable, scoped password display and mobile layout at 320, 375 and 1280px.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
