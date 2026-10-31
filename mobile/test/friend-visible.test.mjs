import {test} from 'node:test';
import assert from 'node:assert/strict';
import {friendVisibleReceipts} from '../friend-visible.mjs';

test('friend receipts require actual visibility, focus and current view; reset discards old callbacks',async()=>{
  let callback,focused=true,active=true;const sent=[],document={hidden:false,hasFocus:()=>focused};
  class Observer{constructor(cb){callback=cb;}observe(){}disconnect(){}}
  const receipts=friendVisibleReceipts({document,active:()=>active,Observer,send:async ids=>sent.push(ids)});
  const row={dataset:{friendMessage:'one'}};
  receipts.watch([row]);
  const event={target:row,isIntersecting:true,intersectionRatio:1};
  document.hidden=true;callback([event]);await new Promise(r=>setImmediate(r));assert.equal(sent.length,0);
  document.hidden=false;focused=false;callback([event]);await new Promise(r=>setImmediate(r));assert.equal(sent.length,0);
  focused=true;callback([{...event,intersectionRatio:0.2}]);await new Promise(r=>setImmediate(r));assert.equal(sent.length,0);
  callback([event]);await new Promise(r=>setImmediate(r));assert.deepEqual(sent,[['one']]);
  callback([event]);await new Promise(r=>setImmediate(r));assert.equal(sent.length,1);
  receipts.reset();callback([{...event,target:{dataset:{friendMessage:'old'}}}]);await new Promise(r=>setImmediate(r));assert.equal(sent.length,1);
  receipts.watch([row]);active=false;callback([event]);await new Promise(r=>setImmediate(r));assert.equal(sent.length,1);
});
