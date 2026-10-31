const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../public/app.js'), 'utf8');
test('failed roster rendering retains existing DOM, then recovers and explains empty search', () => {
  const buddies = {children:['last good list'], replaceChildren(fragment){this.children=[...fragment.childNodes];}};
  const health = {textContent:''}, search = {value:''};
  const context = {roster:[{name:null},{name:'Valid'}], approvals:[], rosterHealth:health,
    $: id => id === 'search' ? search : buddies,
    document:{createDocumentFragment:()=>({childNodes:[],append(node){this.childNodes.push(node);}})}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function renderRoster()'),source.indexOf('async function refresh()')), context);
  assert.throws(()=>context.renderRoster());
  assert.deepEqual(buddies.children,['last good list']);
  context.roster=[]; context.renderRoster();
  assert.equal(health.textContent,'No registered buddies.');
  context.roster=[{name:'Valid',mailbox:'One'}]; search.value='missing'; context.renderRoster();
  assert.equal(health.textContent,'No buddies match your search.');
});
