const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createUpdateFeed}=require('../update-feed.cjs');
test('all windows share one inflight fetch and one unchanged broadcast',async()=>{
 let calls=0,resolve;const received=[];
 const feed=createUpdateFeed(()=>{calls++;return new Promise(r=>resolve=r);},v=>received.push(v));
 const requests=Array.from({length:9},()=>feed.snapshot());
 resolve({buddies:[]});await Promise.all(requests);
 assert.equal(calls,1);assert.equal(received.length,1);
 await feed.snapshot();assert.equal(calls,1);
 const pending=feed.refresh();resolve({buddies:[]});await pending;
 assert.equal(received.length,1);
});
test('changes for unopened buddies broadcast; failures retry without marking read',async()=>{
 let fail=false,revision=1;const received=[];
 const feed=createUpdateFeed(async()=>{if(fail)throw Error('offline');return {buddies:[{mailbox:'unopened',unread:revision,historyRevision:revision}]};},v=>received.push(v));
 await feed.refresh();fail=true;await assert.rejects(feed.refresh());
 fail=false;revision=2;await feed.refresh();assert.equal(received.length,2);
 assert.equal(received[1].buddies[0].unread,2);
 feed.stop();revision=3;await feed.refresh();assert.equal(received.length,2);
});
