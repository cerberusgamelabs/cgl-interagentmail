import {test} from 'node:test';
import assert from 'node:assert/strict';
import {buddyKey,buddyName,updateBuddyName,rememberBuddy,selectBuddy,restoreAfterLogin} from '../buddies.mjs';

test('same account recovery keeps contacts and drafts but never reuses another device grant',()=>{
  const session={accountId:'owner',homeServer:'relay',deviceId:'old'};
  const buddy={installation:'node',conversation:'friend',grantId:'grant',grantExpires:123,pairing:{},push:{},draft:'keep',pending:{id:'original'}};
  const saved={...buddy,origin:'https://relay.test',keyFingerprint:'key',session,buddies:[buddy],accountPairing:{}};
  const restored=restoreAfterLogin(saved,saved.origin,{...session,deviceId:'new'},'key');
  for(const value of [restored,...restored.buddies]){
    assert.equal(value.draft,'keep');assert.equal(value.pending.id,'original');
    for(const field of ['grantId','grantExpires','pairing','push'])assert.equal(value[field],undefined);
  }
  assert.equal(restored.accountPairing,undefined);assert.equal(restored.session,undefined);
  assert.equal(saved.grantId,'grant');assert.equal(saved.session.deviceId,'old');
  assert.equal(restoreAfterLogin(saved,saved.origin,session,'key').grantId,'grant');
  for(const [origin,next,key] of [[saved.origin,{...session,accountId:'other'},'key'],
    ['https://other.test',session,'key'],[saved.origin,{...session,homeServer:'other'},'key'],[saved.origin,session,'new-key']])
    assert.deepEqual(restoreAfterLogin(saved,origin,next,key),{origin});
});

test('chosen names update existing buddies without changing routes, grants or drafts',()=>{
  const buddy={installation:'node',conversation:'project',grantId:'grant',draft:'keep',pending:{id:'original'}};
  const state={...buddy,buddies:[{...buddy},{installation:'other',conversation:'project',displayName:'Other'}]};
  assert.equal(updateBuddyName(state,buddy,'Rowan'),true);
  assert.equal(buddyName(state),'Rowan');assert.equal(state.conversation,'project');
  assert.equal(state.buddies[0].pending.id,'original');assert.equal(state.buddies[0].draft,'keep');
  assert.equal(state.buddies[1].displayName,'Other');
  assert.equal(updateBuddyName(state,{...buddy,grantId:'expired'},'Wrong'),false);
  assert.equal(updateBuddyName(state,buddy,'x'.repeat(513)),false);
  assert.equal(buddyName(selectBuddy(state,{installation:'node',conversation:'unnamed'})),'unnamed');
  assert.equal(rememberBuddy([],state)[0].displayName,'Rowan');
});
test('identity includes installation and mailbox; snapshots contain no session credentials',()=>{
  const first={installation:'one',conversation:'same',grantId:'a',session:{token:'secret'}};
  const second={installation:'two',conversation:'same',grantId:'b'};
  assert.notEqual(buddyKey(first),buddyKey(second));
  const list=rememberBuddy(rememberBuddy([],first),second);
  assert.equal(list.length,2);assert.equal(list[0].session,undefined);
  assert.equal(rememberBuddy(list,{...first,draft:'new'}).length,2);
});
test('selecting a conversation replaces rather than merges permissions and pending send',()=>{
  const next=selectBuddy({origin:'https://example.test',grantId:'a',pending:{id:'old'},draft:'old',pairing:{}},{installation:'two',conversation:'new'});
  assert.equal(next.origin,'https://example.test');assert.equal(next.grantId,undefined);assert.equal(next.pending,undefined);assert.equal(next.draft,undefined);assert.equal(next.pairing,undefined);
});
test('bounded roster rejects additions but permits updates at capacity',()=>{
  const list=Array.from({length:64},(_,i)=>({installation:'node',conversation:String(i)}));
  assert.throws(()=>rememberBuddy(list,{installation:'node',conversation:'extra'}),/64/);
  assert.equal(rememberBuddy(list,{installation:'node',conversation:'0',draft:'new'}).length,64);
});
