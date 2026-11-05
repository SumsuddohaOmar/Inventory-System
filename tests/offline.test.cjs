/* Run with: node --test tests/offline.test.cjs
   This small browser substitute exercises the real offline request function.
   It does not claim to test Android storage or a real service worker. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function browser(fetch, store, timeoutSignal = AbortSignal) {
  const status = {textContent:''};
  const context = vm.createContext({fetch, Response, URL, AbortSignal: timeoutSignal, AbortController, setTimeout, clearTimeout,
    location:{origin:'http://localhost:8000'},
    window:{addEventListener(){}}, setInterval(){},
    document:{querySelector(selector){return selector.startsWith('meta[') ? {content:'disabled'} : status;}}, mockStore:store});
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

test('a browser without AbortSignal.timeout can still send authenticated API requests', async () => {
  const app = browser(async (_url, options) => {
    assert.ok(options.signal instanceof AbortSignal);
    return new Response('{}');
  }, async () => undefined, {});
  assert.equal((await app.request('/api/company')).status, 200);
});

// Separate maps represent independent browser/device storage. Reusing a map
// represents reopening a tab in the SAME profile; the production code runs in
// a fresh JS context each time. Server behavior is tested separately in Python.
function device(fetch, stores = new Map(), cloud = true) {
  function node() {
    return {children:[],textContent:'',listeners:{},append(...items){this.children.push(...items);},
      replaceChildren(){this.children=[];},addEventListener(name,fn){this.listeners[name]=fn;}};
  }
  const queue=node(), status=node(), events={};
  const context=vm.createContext({fetch,Response,URL,AbortSignal,AbortController,setTimeout,clearTimeout,
    crypto:require('node:crypto').webcrypto,navigator:{onLine:true},location:{origin:'https://inventory.example'},
    window:{addEventListener(name,fn){events[name]=fn;}},setInterval(){},
    document:{createElement:node,querySelector(selector){
      if(selector.startsWith('meta['))return {content:cloud?'enabled':'disabled'};
      return selector==='#sync-queue'?queue:status;
    }},mockStore:async(name,_mode,action)=>{
      if(!stores.has(name))stores.set(name,new Map());
      const table=stores.get(name);
      return structuredClone(action({get:key=>table.get(key),getAll:()=>[...table.values()],
        put:value=>{table.set(value.id,structuredClone(value));return value.id;},delete:key=>table.delete(key)}));
    }});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static/offline.js'),'utf8'),context);
  vm.runInContext('currentUser={id:"employee-1",permissions:["read","receive","issue","correct"]};localStore=mockStore;',context);
  return {stores,queue,status,events,run:code=>vm.runInContext(code,context),
    rows:()=>[...(stores.get('outbox')||new Map()).values()],
    async save(id='one',quantity='2'){
      context.payload=JSON.stringify({id,kind:'receive',quantity,effective_date:'2026-11-01',notes:''});
      return vm.runInContext('offlineRequest("/api/products/rice/stock",{method:"POST",headers:{"Content-Type":"application/json"},body:payload})',context);
    }};
}
const offline=async()=>{throw Error('connection lost');};
const session=()=>new Response(JSON.stringify({user:{id:'employee-1'}}));

test('two devices retain independent queues for the same user; reload preserves each UUID',async()=>{
  const first=device(offline), second=device(offline);
  assert.equal((await first.save('first')).status,202);
  await second.save('second');
  assert.deepEqual(first.rows().map(row=>row.id),['first']);
  assert.deepEqual(second.rows().map(row=>row.id),['second']);
  const reopened=device(offline,first.stores);
  await reopened.save('first');
  assert.deepEqual(reopened.rows().map(row=>row.id),['first']);
  assert.equal(reopened.rows()[0].depends_on.length,0);
  await reopened.run('renderQueue()');
  let warned=false;reopened.events.beforeunload({preventDefault(){warned=true;}});
  assert.equal(warned,true);
});

test('lost response and uncertain server error retain the durable retry operation',async()=>{
  let app;
  app=device(async()=>{assert.equal(app.rows()[0].id,'lost');throw Error('response lost after commit');});
  await app.save('lost');
  let sent;
  const retry=device(async(url,options)=>{
    if(url==='/api/session')return session();
    sent=JSON.parse(options.body).operations;
    assert.equal(options.headers['X-Inventory-Owner'],'employee-1');
    return new Response(JSON.stringify([{id:'lost',status:'saved'}]));
  },app.stores);
  await retry.run('syncPending()');
  assert.equal(sent[0].id,'lost');assert.equal(retry.rows().length,0);
  const failing=device(async()=>new Response('Unavailable',{status:503}));
  assert.equal((await failing.save('uncertain')).status,202);
  assert.equal(failing.rows().length,1);
});

test('a definite online stock rejection keeps no phantom queued save',async()=>{
  const app=device(async()=>new Response('Not enough stock',{status:409}));
  assert.equal((await app.save()).status,409);
  assert.equal(app.rows().length,0);
});

test('sync does not submit another account queue or a queue from another origin',async()=>{
  const app=device(offline);await app.save('mine');
  const row=app.rows()[0];app.stores.get('outbox').set('foreign',{...row,id:'foreign',origin:'https://other.example'});
  let posts=0;
  const other=device(async(url)=>{if(url!='/api/session')posts++;return new Response(JSON.stringify({user:{id:'employee-2'}}));},app.stores);
  await other.run('syncPending()');assert.equal(posts,0);assert.equal(other.rows().length,2);
  await other.run('currentUser={id:"employee-2",permissions:["read"]};renderQueue()');
  assert.equal(other.queue.children.length,0);
});

test('held entries stay held, dependent entries wait, and unrelated products continue',async()=>{
  const queued=device(offline);await queued.save('conflict');await queued.save('dependent');
  queued.stores.get('outbox').set('independent',{...queued.rows()[0],id:'independent',product_id:'soap',payload:{...queued.rows()[0].payload,id:'independent'}});
  assert.deepEqual(queued.rows()[1].depends_on,['conflict']);
  let batches=[];
  const app=device(async(url,options)=>{
    if(url==='/api/session')return session();
    if(url==='/api/sync/status')return new Response('[]');
    const operations=JSON.parse(options.body).operations;batches.push(operations);
    return new Response(JSON.stringify(operations.map(row=>({id:row.id,status:row.id==='independent'?'saved':row.id==='conflict'?'held':'waiting',reason:'Review stock'}))));
  },queued.stores);
  await app.run('syncPending()');await app.run('syncPending()');
  assert.deepEqual(batches[1].map(row=>row.id),['dependent']);
  assert.equal(app.rows()[0].state,'held');
  assert.ok(!app.rows().some(row=>row.id==='independent'));
  // A retry of the same held entry must not bypass explicit review.
  assert.equal((await app.save('conflict')).status,202);
  assert.equal(app.rows()[0].state,'held');
});

test('storage failure prevents an untracked cloud write and leaves typed input with caller',async()=>{
  let sent=false;const app=device(async()=>{sent=true;return new Response('{}');});
  await app.run('const previousStore=localStore;localStore=async(name,mode,action)=>{if(mode==="readwrite")throw Error("quota");return previousStore(name,mode,action);};');
  await assert.rejects(()=>app.save(),/Nothing was sent: browser storage is unavailable/);
  assert.equal(sent,false);
});

test('browser cleanup failure never turns confirmed cloud success into an unconfirmed save',async()=>{
  const app=device(async()=>new Response('{"saved":true}'));
  await app.run('const originalStore=localStore;localStore=async(name,mode,action)=>originalStore(name,mode,table=>action({...table,delete(){throw Error("browser cleanup failed");}}));');
  assert.equal((await app.save('confirmed')).status,200);
  assert.equal(app.rows()[0].id,'confirmed');
  assert.match(app.status.textContent,/prevents duplicates/);
});

test('authorized local review preserves original conflict and rewires dependent entries',async()=>{
  const queued=device(offline);await queued.save('conflict');await queued.save('dependent');
  queued.stores.get('outbox').get('conflict').state='held';
  const app=device(offline,queued.stores);await app.run('renderQueue()');
  const button=app.queue.children[0].children.find(child=>child.listeners.click);
  await button.listeners.click();
  assert.ok(app.stores.get('conflicts').has('conflict'));
  const replacement=app.rows().find(row=>row.resolves_id==='conflict');
  assert.ok(replacement);assert.notEqual(replacement.id,'conflict');
  assert.deepEqual(app.rows().find(row=>row.id==='dependent').depends_on,[replacement.id]);
});

test('local-server mode retains legacy pending data without sending it to SQLite',async()=>{
  const queued=device(offline);await queued.save('legacy');
  delete queued.stores.get('outbox').get('legacy').origin;
  let calls=0;const app=device(async()=>{calls++;throw Error('offline');},queued.stores,false);
  await app.run('syncPending()');await app.run('renderQueue()');
  assert.equal(calls,0);assert.equal(app.rows().length,1);
  assert.match(app.status.textContent,/Cloud sync unavailable/);
});
