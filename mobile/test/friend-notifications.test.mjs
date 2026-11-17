import {test} from 'node:test';
import assert from 'node:assert/strict';
import {friendNotifications} from '../friend-notifications.mjs';
function fixture(){
  let time=100000,fail=false;const calls=[],warnings=[],state={},document={hidden:false};
  const transport={session:{deviceId:'d',expiresAt:1000000},friends:async(op,input)=>{calls.push([op,input]);if(fail)throw Error('secret');return {id:input.id,revision:1,unread:0,expires:1000};}};
  const native={enableNotifications:async()=>({token:'token'}),refreshNotificationToken:async()=>({token:'token'}),registerNotificationScope:async s=>calls.push(['scope',s]),reconcileNotification:async s=>calls.push(['reconcile',s])};
  const controller=friendNotifications({getTransport:()=>transport,getState:()=>state,document,native,save:async()=>calls.push(['save']),status:v=>warnings.push(v),uuid:()=> '00000000-0000-4000-8000-000000000001',now:()=>time});
  return {controller,state,transport,native,document,calls,warnings,set fail(v){fail=v;},set time(v){time=v;}};
}
test('friend notifications require explicit opt-in, save stable scope and cap expiry',async()=>{
  const f=fixture();await f.controller.refresh();assert.equal(f.calls.length,0);
  await f.controller.enable();assert.equal(f.state.friendPush.device,'d');assert.equal(f.calls[0][0],'save');
  assert.equal(f.calls.find(c=>c[0]==='scope')[1].expires,1000);
  assert.deepEqual(f.calls.filter(c=>c[0]==='notifications')[0][1],{id:f.state.friendPush.id,token:'token'});
  const count=f.calls.length;await f.controller.refresh();assert.equal(f.calls.length,count);
  f.time=161000;await f.controller.refresh();assert.ok(f.calls.length>count);
});
test('friend renewal respects background/device/session changes and preserves warning until recovery',async()=>{
  const f=fixture();f.fail=true;await assert.rejects(f.controller.enable());assert.equal(f.controller.failed,true);
  const id=f.state.friendPush.id;f.document.hidden=true;const count=f.calls.length;await f.controller.refresh();assert.equal(f.calls.length,count);
  f.document.hidden=false;f.fail=false;await f.controller.refresh();assert.equal(f.controller.failed,false);assert.equal(f.state.friendPush.id,id);
  f.time=222000;f.native.refreshNotificationToken=async()=>{f.transport.session={deviceId:'other',expiresAt:1000000};return {token:'token'};};
  const prior=f.calls.length;await f.controller.refresh();assert.equal(f.calls.length,prior);
  await f.controller.refresh();assert.equal(f.calls.length,prior);
});
test('expired scope gets a fresh revision namespace and failed vault write cannot register',async()=>{
  const f=fixture();f.state.friendPush={id:'old',device:'d',expiresAt:1};
  await f.controller.refresh();assert.notEqual(f.state.friendPush.id,'old');
  const calls=[],state={},transport={session:{deviceId:'d',expiresAt:1000000},friends:async()=>calls.push('network')};
  const controller=friendNotifications({getTransport:()=>transport,getState:()=>state,document:{hidden:false},now:()=>100000,
    native:{enableNotifications:async()=>({token:'token'})},uuid:()=> 'new',save:async()=>{throw Error('disk');}});
  await assert.rejects(controller.enable());assert.equal(state.friendPush,undefined);assert.deepEqual(calls,[]);
});
