// Focused browser-script regressions without a browser or third-party packages.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('echo/dashboard/static/app.js', 'utf8');
function harness() {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {innerHTML:'',textContent:'',focus(){},classList:{remove(){}}});
    return nodes.get(id);
  };
  let requests=0;
  const context=vm.createContext({
    document:{querySelector:node,addEventListener(){}},
    location:{protocol:'file:'},history:{replaceState(){}},
    fetch:()=>{requests++;throw new Error('Unexpected request from source file');},
    setTimeout,clearTimeout,URLSearchParams,
  });
  vm.runInContext(source,context);
  return {context,node,requests:()=>requests};
}
test('opening source file shows a live-app link without calling the API',()=>{
  const h=harness();
  assert.match(h.node('#main').innerHTML,/http:\/\/127\.0\.0\.1:8877\/app\//);
  assert.match(h.node('#main').innerHTML,/Echo Dashboard.command/);
  assert.equal(h.node('#agent-status').textContent,'Local file');
  assert.equal(h.requests(),0);
});
test('locking clears cached account data and navigation cannot render it again',()=>{
  const h=harness();
  vm.runInContext("state={connections:[{label:'Private account'}]};locked('Signed out');navigate('activity')",h.context);
  assert.equal(vm.runInContext('state',h.context),null);
  assert.match(h.node('#main').innerHTML,/Signed out/);
  assert.doesNotMatch(h.node('#main').innerHTML,/Private account/);
});
const accounts = `state={providers:[{id:'gmail',name:'Gmail',description:'Read email',auth:'google',category:'Everyday'}],google_configured:true,connections:[
  {id:'home',provider:'gmail',label:'Home',status:'connected',enabled:1,tool_count:2},
  {id:'school',provider:'gmail',label:'School',status:'connected',enabled:1,tool_count:2},
  {id:'custom-school',provider:'custom',label:'School Canvas',status:'error',enabled:0,tool_count:0}
]};`;
test('Your connections lists individual accounts and search finds their labels',()=>{
  const h=harness();
  vm.runInContext(accounts+"tab='connected';renderProviders()",h.context);
  assert.match(h.node('#providers').innerHTML,/data-account="home"/);
  assert.match(h.node('#providers').innerHTML,/data-account="school"/);
  assert.match(h.node('#providers').innerHTML,/School Canvas/);
  vm.runInContext("query='school';renderProviders()",h.context);
  assert.doesNotMatch(h.node('#providers').innerHTML,/data-account="home"/);
  assert.match(h.node('#providers').innerHTML,/data-account="school"/);
  vm.runInContext("tab='all';renderProviders()",h.context);
  assert.match(h.node('#providers').innerHTML,/2 accounts/);
  assert.match(h.node('#providers').innerHTML,/Home, School/);
});
test('account details expose separate controls, inline rename, and another-account setup',()=>{
  const h=harness();
  vm.runInContext(accounts+"selected='gmail';selectedAccount='school';renderDetail()",h.context);
  const html=h.node('#detail').innerHTML;
  assert.match(html,/selected-account" id="account-school"/);
  for(const id of ['home','school']) {
    assert.match(html,new RegExp(`data-connection="${id}" data-action="rename"`));
    assert.match(html,new RegExp(`class="rename-form" data-account="${id}" hidden`));
  }
  assert.match(html,/>Add another account</);
  assert.match(html,/e.g. School or Home/);
  vm.runInContext("state.connections[1].label='<script>bad</script>';renderDetail()",h.context);
  assert.doesNotMatch(h.node('#detail').innerHTML,/<script>/);
  assert.match(h.node('#detail').innerHTML,/&lt;script&gt;/);
});
