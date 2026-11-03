/* Run with: node --test tests/offline.test.cjs
   This small browser substitute exercises the real offline request function.
   It does not claim to test Android storage or a real service worker. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function browser(fetch, store) {
  const status = {textContent:''};
  const context = vm.createContext({fetch, Response, URL, AbortSignal,
    location:{origin:'http://localhost:8000'},
    window:{addEventListener(){}}, setInterval(){},
    document:{querySelector(){return status;}}, mockStore:store});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static/offline.js'),'utf8'), context);
  vm.runInContext('currentUser={id:"employee-1",permissions:["read"]}; localStore=mockStore;', context);
  return {request:(url, options) => {
    context.testUrl=url;context.testOptions=options;
    return vm.runInContext('offlineRequest(testUrl,testOptions)', context);
  },status};
}

test('a full browser disk does not discard a successful server response', async()=>{
  const app=browser(async()=>new Response('[{"name":"Rice"}]'),async()=>{throw Error('quota');});
  const response=await app.request('/api/products');
  assert.equal(response.status,200);
  assert.equal((await response.json())[0].name,'Rice');
  assert.match(app.status.textContent,/storage unavailable/);
});

test('an unvisited offline category is derived from this account catalog', async()=>{
  const products=[{id:'a',category_id:'dry-foods',archived:0},{id:'b',category_id:'chemicals',archived:0},{id:'c',category_id:'dry-foods',archived:1}];
  const seen=[];
  const app=browser(async()=>{throw Error('offline');},async(name,mode,action)=>action({get(key){
    seen.push(key);return key==='employee-1:/api/catalog'?{value:products}:undefined;
  }}));
  assert.deepEqual(await (await app.request('/api/products?category_id=dry-foods')).json(),[products[0]]);
  assert.ok(seen.every(key=>key.startsWith('employee-1:')));
});

test('a rejected online write is never silently put in the offline outbox', async()=>{
  let writes=0;
  const app=browser(async()=>new Response('Forbidden',{status:403}),async()=>{writes++;});
  const response=await app.request('/api/products/example/stock',{method:'POST',body:'{}'});
  assert.equal(response.status,403);
  assert.equal(writes,0);
});
