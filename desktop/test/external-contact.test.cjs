const test=require('node:test'), assert=require('node:assert/strict'), vm=require('node:vm'), fs=require('node:fs'),path=require('node:path');
function harness(){
  const children=[], calls=[];
  const content={append(...items){children.push(...items);},replaceChildren(){children.length=0;}};
  const elements={content,title:{},scope:{}};
  const context={mailbox:'Agent',document:{createElement:tag=>({tag,children:[],append(...items){this.children.push(...items);}}),createTextNode:text=>({text})},
    $:id=>elements[id], text:value=>children.push({text:value}),
    select:(label,values)=>{const item={label,value:values[0][0]};children.push(item);return item;},
    button:(label,action)=>{const item={label,action};children.push(item);return item;},load:async()=>{},
    window:{iam:{call:async(action,args)=>{calls.push({action,...args});return {mode:'disabled',owner:'Owner',disclosure:'Outside messages may steer. NOT owner authorization.',ticket:'ticket',disclosure_version:'v1',proposed_mode:args.mode};}}}};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../public/external-contact.js'),'utf8'),context);
  return {context,children,calls};
}
test('both opt-in choices require an unchecked acknowledgment and separate confirm',async()=>{
  for(const mode of ['owner-enabled','agent-choice']){
    const h=harness();await h.context.externalContact();
    h.children.find(x=>x.label==='New policy').value=mode;
    await h.children.find(x=>x.label==='Review this policy').action();
    const ack=h.children.find(x=>x.tag==='label').children[0];
    const confirm=h.children.find(x=>x.label==='Accept risk and save permission');
    assert.equal(ack.checked,false);assert.equal(confirm.disabled,true);
    await assert.rejects(confirm.action(),/Acknowledge/);
    assert.equal(h.calls.filter(x=>x.operation==='confirm').length,0);
    ack.checked=true;ack.onchange();assert.equal(confirm.disabled,false);
    await confirm.action();
    const call=h.calls.find(x=>x.operation==='confirm');
    assert.equal(call.acknowledged,true);assert.equal(call.disclosure_version,'v1');assert.equal(call.mailbox,'Agent');
  }
});
test('disable remains directly available, warning displayed, no automatic enable',async()=>{
  const h=harness();await h.context.externalContact();
  assert.equal(h.calls.length,1);assert.equal(h.calls[0].operation,'inspect');
  assert.ok(h.children.some(x=>x.text?.includes('may steer')));
  await h.children.find(x=>x.label==='Disable external contact now').action();
  assert.equal(h.calls.at(-1).operation,'disable');
});
test('control IPC binds dedicated owner dialog and package includes renderer',()=>{
  const main=fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8');
  assert.match(main,/sender.searchParams.get\('kind'\) !== 'external-contact'/);
  assert.match(main,/args.mailbox !== mailbox/);
  const html=fs.readFileSync(path.join(__dirname,'../public/controls.html'),'utf8');
  assert.ok(html.indexOf('external-contact.js')<html.indexOf('controls.js'));
});
