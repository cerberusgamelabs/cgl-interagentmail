import {test} from 'node:test';
import assert from 'node:assert/strict';
import {messageBytes,retryDelay,mergeEvents,confirmedPending} from '../conversation.mjs';
test('byte limit includes JSON escapes and multibyte text',()=>{
  assert.equal(messageBytes('😀'),messageBytes('')+4);
  assert.equal(messageBytes('\n'),messageBytes('')+2);
  assert.ok(messageBytes('😀'.repeat(1000))>3000);
});
test('replayed pages replace matching events without duplication',()=>{
  const original={id:'a',created:1,state:'queued'};
  assert.deepEqual(mergeEvents([original],[{...original,state:'accepted'}]),[{...original,state:'accepted'}]);
});
test('out of order pages merge chronologically and bound memory',()=>{
  assert.deepEqual(mergeEvents([{id:'b',created:2}],[{id:'c',created:3},{id:'a',created:1}],2).map(x=>x.id),['b','c']);
});
test('only exact owner history can release an uncertain send',()=>{
  const pending={id:'request',body:'hello'};
  const row={requestId:'request',body:'hello',sender:'owner'};
  assert.equal(confirmedPending(pending,[row]),true);
  for(const patch of [{sender:'resident'},{body:'different'},{truncated:true},{requestId:'other'}])
    assert.equal(confirmedPending(pending,[{...row,...patch}]),false);
  assert.equal(confirmedPending(null,[row]),false);
});
test('foreground error backoff is bounded and resets',()=>{
  assert.deepEqual([0,1,2,3,4,100].map(retryDelay),[8000,16000,32000,60000,60000,60000]);
});
