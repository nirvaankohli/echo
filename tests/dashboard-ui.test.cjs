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
