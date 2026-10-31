import {test} from 'node:test';
import assert from 'node:assert/strict';
import {LiveConversation} from '../live.mjs';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';

test('default browser timers retain their global receiver on Settings teardown and retry',async()=>{
  const source=(await readFile(new URL('../live.mjs',import.meta.url),'utf8')).replace('export class','class');
  const result=vm.runInNewContext(`
    let cancelled=0,scheduled=0;
    globalThis.clearTimeout=function(){if(this!==globalThis)throw new TypeError('Illegal invocation');cancelled++;};
    globalThis.setTimeout=function(){if(this!==globalThis)throw new TypeError('Illegal invocation');scheduled++;return 1;};
    ${source}
    const live=new LiveConversation({native:{closeStream:async()=>{}},subscribe:async()=>{},changed:()=>{}});
    live.stop();live.scope={key:'active'};live.retry();live.stop();
    JSON.stringify({cancelled,scheduled});
  `);
  assert.deepEqual(JSON.parse(result),{cancelled:3,scheduled:1});
});
test('subscription fences old views, deduplicates hints and reconnects without sending messages',async()=>{
  let listener,callback,resolve;const opened=[],changed=[];
  const native={addListener:async(_,fn)=>{listener=fn;},closeStream:async()=>{},openStream:async value=>opened.push(value)};
  const live=new LiveConversation({native,subscribe:async(scope,id)=>({stream:{id}}),changed:scope=>changed.push(scope.key),delay:fn=>{callback=fn;return 1;},cancel:()=>{}});
  await live.initialize();live.setScope({key:'one',origin:'https://home.test',token:'secret'});await new Promise(r=>setImmediate(r));
  const first=opened[0].id;
  listener({id:first,state:'connected'});assert.equal(live.connected,true);assert.equal(live.phase,'live');
  assert.deepEqual(changed,['one']);
  listener({id:first,state:'changed',revision:0});listener({id:first,state:'changed',revision:0});assert.deepEqual(changed,['one']);
  listener({id:first,state:'disconnected'});assert.equal(live.connected,false);callback();await new Promise(r=>setImmediate(r));
  assert.equal(opened.length,2);assert.notEqual(opened[1].id,first);
  live.setScope(null);listener({id:first,state:'changed',revision:9});assert.equal(changed.length,1);
  live.subscribe=()=>new Promise(r=>resolve=r);live.setScope({key:'two'});await new Promise(r=>setImmediate(r));const pending=live.id;live.stop();resolve({stream:{id:pending}});
  await new Promise(r=>setImmediate(r));assert.equal(opened.length,2);
});

test('native close finishes before replacement opens; stale connections never open',async()=>{
  let release;const opened=[],subscribed=[];
  const native={closeStream:()=>new Promise(r=>release=r),openStream:async value=>opened.push(value)};
  const live=new LiveConversation({native,subscribe:async(scope,id)=>{subscribed.push(scope.key);return {stream:{id}};},changed:()=>{}});
  live.setScope({key:'old'});const oldClose=release;
  live.setScope({key:'new'});const newClose=release;
  oldClose();await new Promise(r=>setImmediate(r));assert.equal(opened.length,0);
  newClose();await new Promise(r=>setImmediate(r));
  assert.deepEqual(subscribed,['new']);assert.equal(opened.length,1);
  live.stop();release();
});

test('reconnect catches up without waiting for a hint and duplicate connected events do not refetch',async()=>{
  let retry;const changed=[],opened=[];
  const live=new LiveConversation({native:{closeStream:async()=>{},openStream:async value=>opened.push(value)},
    subscribe:async(scope,id)=>({stream:{id}}),changed:scope=>changed.push(scope.key),delay:fn=>{retry=fn;return 1;},cancel:()=>{}});
  live.setScope({key:'chat'});await new Promise(r=>setImmediate(r));
  live.event({id:live.id,state:'connected'});live.event({id:live.id,state:'connected'});
  live.event({id:live.id,state:'changed',revision:0});assert.equal(changed.length,1);
  live.event({id:live.id,state:'disconnected'});assert.equal(live.phase,'retrying');
  retry();await new Promise(r=>setImmediate(r));
  live.event({id:live.id,state:'connected'});assert.equal(changed.length,2);
  live.event({id:opened[0].id,state:'changed',revision:99});assert.equal(changed.length,2);
  live.stop();live.retry();assert.equal(live.phase,'paused');
});

test('mismatched lease cannot open a native connection',async()=>{
  let opens=0;
  const live=new LiveConversation({native:{closeStream:async()=>{},openStream:async()=>opens++},
    subscribe:async()=>({stream:{id:'wrong'}}),changed:()=>{},delay:()=>1,cancel:()=>{}});
  live.setScope({key:'chat'});await new Promise(r=>setImmediate(r));
  assert.equal(opens,0);assert.equal(live.phase,'retrying');live.stop();
});
