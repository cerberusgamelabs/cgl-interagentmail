import {test} from 'node:test';
import assert from 'node:assert/strict';
import {activeFriendBuddies} from '../friend-buddies.mjs';

test('accepted friends from both request directions become rows without owner grants',()=>{
  const relationships=[true,false].map((incoming,i)=>({id:String(i),incoming,state:'active',peer:{name:'Iris'},unread:2,grantId:'must-not-copy'}));
  assert.deepEqual(activeFriendBuddies({self:{enabled:true},relationships}),[
    {id:'0',name:'Iris',unread:2},{id:'1',name:'Iris',unread:2}]);
});
test('pending, revoked, blocked and disabled identities are not buddy entries',()=>{
  const relationships=['pending','revoked','blocked','active'].map(state=>({id:state,state,blocked:true,peer:{name:'Friend'}}));
  assert.deepEqual(activeFriendBuddies({self:{enabled:true},relationships}),[]);
  assert.deepEqual(activeFriendBuddies({self:{enabled:false},relationships:[{id:'x',state:'active',peer:{name:'Friend'}}]}),[]);
});
test('roster validation, deduplication and unread bounds',()=>{
  assert.throws(()=>activeFriendBuddies({relationships:{}}));
  assert.throws(()=>activeFriendBuddies({self:{enabled:true},relationships:[{id:'x',state:'active'}]}));
  const r={id:'x',state:'active',peer:{name:'<b>literal</b>'},unread:5000};
  assert.deepEqual(activeFriendBuddies({self:{enabled:true},relationships:[r,r]}),[{id:'x',name:'<b>literal</b>',unread:2000}]);
});
