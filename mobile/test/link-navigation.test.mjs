import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../app.mjs',import.meta.url),'utf8');
function harness({failSave=false,screen='home'}={}){
  const elements={connectionSettings:{},addConversation:{},messages:{scrollTop:15},status:{textContent:'Ready'}};
  const state={installation:'desktop',conversation:'Rowan',grantId:'existing',draft:'unsent',pending:{id:'original'},buddies:[{conversation:'Rowan'}]};
  const calls=[];
  const context=vm.createContext({$:id=>elements[id],state,screen,queuedLive:true,queuedOlder:{},draftTimer:1,timer:2,
    live:{stop:()=>calls.push('stop')},clearTimeout:()=>{},save:async()=>{calls.push('save');if(failSave)throw Error('save failed');},
    buddyKey:()=> 'buddy',views:new Map(),events:[],cursor:[0,''],following:true,catchingUp:false,
    historyLoaded:true,olderCursor:null,hasOlder:false,readTrackerKey:'old',
    closeImage:()=>calls.push('closeImage'),closeText:()=>calls.push('closeText'),message:()=>{},
    run:async work=>work()});
  const leave=source.match(/async function leaveConversation\(next\)\{[\s\S]*?\n\}/)?.[0];
  assert.ok(leave,'real navigation function exists');
  vm.runInContext(leave,context);
  const binding=source.split('\n').find(line=>line.startsWith("$('connectionSettings').onclick="));
  vm.runInContext(binding,context);
  return {elements,state,calls,context};
}
test('Link your desktop shares the Settings route and preserves active conversation state',async()=>{
  const h=harness(),before=structuredClone(h.state);
  assert.equal(h.elements.addConversation.onclick,h.elements.connectionSettings.onclick);
  await h.elements.addConversation.onclick();
  assert.equal(h.context.screen,'setup');assert.deepEqual(h.state,before);
  assert.equal(h.calls.filter(x=>x==='save').length,1);
  assert.ok(!source.includes('switchBuddy({})'));
});
test('link navigation fails closed on save failure without discarding draft or grant',async()=>{
  const h=harness({failSave:true}),before=structuredClone(h.state);
  await assert.rejects(h.elements.addConversation.onclick(),/save failed/);
  assert.equal(h.context.screen,'home');assert.deepEqual(h.state,before);
  assert.ok(!h.calls.includes('closeImage'));
});
test('Settings route retains chat view for return navigation',async()=>{
  const h=harness({screen:'chat'});
  await h.elements.connectionSettings.onclick();
  assert.equal(h.context.views.get('buddy').scroll,15);
  assert.equal(h.context.screen,'setup');assert.equal(h.state.pending.id,'original');
  assert.ok(h.calls.includes('closeText'));
});
