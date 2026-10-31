import {test} from 'node:test';
import assert from 'node:assert/strict';
import {nativeFetch,nativeSigner} from '../native-adapter.mjs';
import {MessengerTransport} from '../transport.mjs';
test('restored session is validated, projected and does not grant owner authority',()=>{
  const transport=new MessengerTransport('https://example.test');
  const session={token:'t'.repeat(43),expiresAt:Date.now()+60000,deviceId:'d',accountId:'a',homeServer:'h',admin:true};
  transport.restoreSession(session);assert.equal(transport.session.admin,undefined);
  for(const bad of [{...session,expiresAt:0},{...session,token:'bad'},{...session,deviceId:3}]){
    assert.throws(()=>transport.restoreSession(bad));assert.equal(transport.session,null);
  }
});
test('native bridge restricts endpoint and origin, preserving redirects policy',async()=>{
  let sent;const plugin={request:async(value)=>{sent=value;return {status:200,body:'{"ok":true}'};}};
  const fetch=nativeFetch(plugin,'https://example.test');
  const options={method:'POST',redirect:'error',headers:{Authorization:'Bearer '+'t'.repeat(43)},body:'{}'};
  assert.equal((await (await fetch('https://example.test/v1/messenger/logout',options)).json()).ok,true);
  assert.equal(sent.token,'t'.repeat(43));
  for(const url of ['https://other.test/v1/messenger/logout','https://example.test/admin','https://example.test/v1/messenger/logout?x=1','https://u:p@example.test/v1/messenger/logout'])
    await assert.rejects(fetch(url,options));
  await assert.rejects(fetch('https://example.test/v1/messenger/logout',{...options,redirect:'follow'}));
});
test('friend routes are explicitly allowlisted without opening arbitrary native paths',async()=>{
  const calls=[];
  const fetch=nativeFetch({request:async value=>{calls.push(value);return {status:200,body:'{}'};}},'https://example.test');
  const options={method:'POST',redirect:'error',headers:{Authorization:'Bearer '+'t'.repeat(43)},body:'{}'};
  for(const operation of ['self','roster','invite','discover','request','decide','send','history','read','listen'])
    await fetch('https://example.test/v1/messenger/friends/'+operation,options);
  assert.equal(calls.length,10);
  for(const path of ['admin','authorize','history?account=other','send/extra'])
    await assert.rejects(fetch('https://example.test/v1/messenger/friends/'+path,options));
  assert.equal(calls.length,10);
});

test('late native response is discarded after timeout/abort',async()=>{
  let complete;const control=new AbortController();
  const fetch=nativeFetch({request:()=>new Promise(resolve=>complete=resolve)},'https://example.test');
  const result=fetch('https://example.test/v1/messenger/logout',{method:'POST',redirect:'error',headers:{},body:'{}',signal:control.signal});
  control.abort();complete({status:200,body:'{}'});await assert.rejects(result,/Cancelled/);
});
test('signer carries independently checked intent to native boundary',async()=>{
  const intent={operation:'history'};let sent;
  const sign=nativeSigner({signHistory:async value=>{sent=value;return {signature:'DER'};}});
  assert.equal(await sign(new Uint8Array([1,2,3]),intent),'DER');
  assert.equal(sent.payload,'AQID');assert.equal(sent.intent,intent);
});
