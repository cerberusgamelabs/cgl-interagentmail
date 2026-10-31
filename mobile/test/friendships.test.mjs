import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createFriendships} from '../friendships.mjs';

class Element{
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.value='';this.scrollHeight=100;this.clientHeight=100;this.scrollTop=0;}
  append(...values){this.children.push(...values);}
  replaceChildren(...values){this.children=values;}
  setAttribute(){} addEventListener(){}
  querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)]);}
  showModal(){this.open=true;}close(){this.open=false;}
}
function fixture(native){
  const dialog=new Element('dialog'),state={},calls=[],document={hidden:false,createElement:tag=>new Element(tag)};
  let scope=0,failSave=false,block=false;
  const rel={id:'relation',peer:{name:'Friend'},state:'active',revision:1};
  const transport={session:{},friends:async(op,value)=>{calls.push([op,value]);if(op==='roster')return {self:{enabled:true,name:'Me'},relationships:[{...rel,state:block?'blocked':'active'}]};if(op==='history')return {events:[{id:'event',text:'<script>not HTML</script>',created:1,own:false}],more:false};if(op==='send')return {id:value.request_id,state:'sent'};throw Error('unexpected');}};
  const ui=createFriendships({dialog,document,native,getState:()=>state,getTransport:()=>transport,save:async()=>{if(failSave)throw Error('vault');},onScope:()=>scope++,uuid:()=> 'message-id'});
  const click=async label=>{const b=dialog.querySelectorAll('button').find(b=>b.textContent===label);assert.ok(b,label);await b.onclick();await new Promise(r=>setImmediate(r));};
  return {ui,dialog,state,calls,click,document,transport,get scope(){return scope;},set failSave(x){failSave=x;},set block(x){block=x;}};
}
test('friend images form a grid while document saves stay separate',async()=>{
  const f=fixture({validateImage:async()=>{},saveAttachment:async()=>{}}),original=f.transport.friends;
  f.transport.friends=async(op,value)=>op==='history'?{events:[{id:'pictures',text:'',own:false,created:1,files:[
    {kind:'image',name:'first.png'},{kind:'document',name:'notes.txt'},{kind:'image',name:'second.webp'}]}],more:false}:original(op,value);
  await f.ui.open();await f.click('Chat with Friend');
  const grids=f.dialog.querySelectorAll('div').filter(e=>e.className==='image-grid');
  assert.equal(grids.length,1);assert.equal(grids[0].dataset.count,'2');
  assert.equal(grids[0].children.length,2);
  assert.ok(f.dialog.querySelectorAll('button').some(e=>e.textContent==='Save notes.txt'));
  assert.ok(!f.calls.some(([op])=>op==='attachment'));
});
test('live friendship refresh preserves draft element and removes revoked history',async()=>{
  const f=fixture();await f.ui.open();await f.click('Chat with Friend');
  const compose=f.dialog.querySelectorAll('textarea')[0];compose.value='Unsent draft';compose.oninput();
  await f.ui.refresh();assert.equal(f.dialog.querySelectorAll('textarea')[0],compose);assert.equal(compose.value,'Unsent draft');
  assert.ok(f.dialog.querySelectorAll('p').some(p=>p.textContent.includes('<script>not HTML</script>')));
  f.block=true;await f.ui.refresh();
  assert.ok(!f.dialog.querySelectorAll('p').some(p=>p.textContent.includes('<script>')));
  assert.equal(f.state.friendDrafts.relation.text,'Unsent draft');
  await f.ui.close();assert.equal(f.scope,2);assert.equal(f.dialog.open,false);
});

test('friend attachment selection is durable and a failed vault write cannot add a file',async()=>{
  const f=fixture({pickAttachment:async()=>({kind:'document',name:'empty.txt',data:''})});
  await f.ui.open();await f.click('Chat with Friend');
  f.failSave=true;await f.click('+');
  assert.equal(f.state.friendDrafts.relation.files,undefined);
  assert.ok(!f.calls.some(([op])=>['send','attachment'].includes(op)));
  f.failSave=false;await f.click('+');
  assert.equal(f.state.friendDrafts.relation.files[0].name,'empty.txt');
  assert.equal(f.state.friendDrafts.relation.files[0].size,0);
  await f.click('Remove empty.txt');assert.equal(f.state.friendDrafts.relation.files.length,0);
});
test('failed durable pending write sends nothing; retry retains original payload',async()=>{
  const f=fixture();await f.ui.open();await f.click('Chat with Friend');
  const compose=f.dialog.querySelectorAll('textarea')[0];compose.value='Original';compose.oninput();
  f.failSave=true;await f.click('Send');assert.ok(!f.calls.some(([op])=>op==='send'));
  compose.value='Edited';compose.oninput();f.failSave=false;await f.click('Send');
  assert.deepEqual(f.calls.find(([op])=>op==='send')[1],{id:'relation',request_id:'message-id',text:'Original'});
  assert.equal(f.state.friendDrafts.relation.text,'Edited');
});

test('background does not fetch and a completed old account request cannot repopulate a cleared view',async()=>{
  const f=fixture();await f.ui.open();
  const count=f.calls.length;f.document.hidden=true;await f.ui.refresh();assert.equal(f.calls.length,count);
  f.document.hidden=false;let complete;
  f.transport.friends=()=>new Promise(resolve=>complete=resolve);
  const pending=f.ui.refresh();f.ui.clear();f.transport.session=null;
  complete({self:{enabled:true,name:'Old account'},relationships:[]});await pending;
  assert.equal(f.dialog.open,false);
  assert.ok(!f.dialog.querySelectorAll('p').some(p=>p.textContent.includes('Old account')));
});

test('friend chat uses bubbles and footer receipts with help outside the composer',async()=>{
  const f=fixture();const original=f.transport.friends;
  f.transport.friends=async(op,value)=>op==='history'?{events:[
    {id:'out',text:'Hello',created:1,own:true,delivery:'replied'},
    {id:'in',text:'<b>literal</b>',created:2,own:false}],more:false}:original(op,value);
  await f.ui.open();await f.click('Chat with Friend');
  assert.equal(f.dialog.className,'friend-chat');
  const bubbles=f.dialog.querySelectorAll('article');
  assert.equal(bubbles.length,2);assert.equal(bubbles[0].className,'owner');
  assert.equal(bubbles[1].className,'resident');
  assert.equal(bubbles[1].querySelectorAll('p')[0].textContent,'<b>literal</b>');
  const footer=bubbles[0].children.at(-1);
  assert.equal(footer.className,'message-meta');assert.equal(footer.children[0].tag,'time');
  assert.equal(footer.children[1].textContent,'\u2713');
  const help=f.dialog.querySelectorAll('section').find(e=>e.className==='friend-help');
  assert.equal(help.hidden,true);
  const compose=f.dialog.querySelectorAll('textarea')[0];compose.value='draft';compose.oninput();
  await f.click('Help');assert.equal(help.hidden,false);
  await f.click('Help');assert.equal(help.hidden,true);
  assert.equal(f.dialog.querySelectorAll('textarea')[0],compose);assert.equal(compose.value,'draft');
  assert.ok(!f.calls.some(([op])=>['send','decide','self'].includes(op)));
  await f.click('Friends');assert.equal(f.dialog.className,'');
  assert.equal(f.state.friendDrafts.relation.text,'draft');
});
