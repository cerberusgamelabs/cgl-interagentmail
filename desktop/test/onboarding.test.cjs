const test=require('node:test'), assert=require('node:assert/strict');
const fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
const main=fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8');
const ui=fs.readFileSync(path.join(__dirname,'../public/app.js'),'utf8');

test('first-session creation is mailbox-bound, native-confirmed, and not a raw renderer RPC',()=>{
  const block=main.slice(main.indexOf("if (action === 'ensure-session'"),main.indexOf("if (action === 'viewer-document'"));
  assert.match(block,/sender\.searchParams\.get\('mailbox'\) !== args\.mailbox/);
  assert.match(block,/defaultId: 0, cancelId: 0/);
  assert.match(block,/answer\.response !== 1/);
  const allowlist=main.match(/if \(!\[([^\n]+)\]\.includes\(action\)\) throw new Error\('Unsupported operation'\)/)[1];
  assert.doesNotMatch(allowlist,/first-session-confirm|first-session-review/);
});

test('cancelling first-session confirmation preserves draft and does not send',async()=>{
  const elements={compose:{querySelector:()=>button},body:{value:'Hello'}};
  const button={disabled:false}, attachButton={disabled:false}, calls=[];
  const context={mailbox:'New',sending:false,loadingImages:false,stagedImages:[],lastAttempt:null,
    crypto:{randomUUID:()=> 'request'},attachButton,showAttachments:()=>{},refresh:async()=>{},
    $:id=>elements[id], call:async(action)=>{calls.push(action);return {state:'cancelled'};}};
  vm.createContext(context);
  vm.runInContext(ui.slice(ui.indexOf("$('compose').onsubmit ="),ui.indexOf("if (mailbox !== 'buddies') {",ui.indexOf("$('compose').onsubmit ="))),context);
  await elements.compose.onsubmit({preventDefault(){}});
  assert.deepEqual(calls,['ensure-session']);
  assert.equal(elements.body.value,'Hello');
  assert.equal(button.disabled,false);
  assert.equal(attachButton.disabled,false);
});
