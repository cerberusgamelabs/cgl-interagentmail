import {test} from 'node:test';
import assert from 'node:assert/strict';
import {MessengerTransport} from '../transport.mjs';
test('status returns only scoped grant fields and rejects malformed approval',async()=>{
  let value={state:'approved',grant_id:'g'.repeat(43),expires_at:Math.floor(Date.now()/1000)+300,private:'hidden'};
  const transport=new MessengerTransport('https://example.test',{fetchImpl:async()=>({ok:true,json:async()=>value})});
  transport.session={token:'a'.repeat(43),expiresAt:Date.now()+60000};
  assert.deepEqual(Object.keys(await transport.pairingStatus('node','b'.repeat(43))).sort(),['expiresAt','grantId','state']);
  value={...value,grant_id:[]};
  await assert.rejects(transport.pairingStatus('node','b'.repeat(43)),/Invalid pairing status/);
});
test('only HTTPS origins; no credentials, redirects or cookies',async()=>{
  for(const url of ['http://example.test','https://user:password@example.test','https://example.test/path','https://example.test/?token=x'])
    assert.throws(()=>new MessengerTransport(url));
  let call;
  const transport=new MessengerTransport('https://example.test',{fetchImpl:async(url,options)=>{
    call={url,options};return {ok:true,json:async()=>({token:'a'.repeat(43),deviceId:'phone',account_id:'owner',home_server:'home',expiresAt:Date.now()+60000})};
  }});
  const login=await transport.login('owner@example.test','password','phone');
  assert.equal(login.token,undefined);assert.equal(call.options.credentials,'omit');
  assert.equal(call.options.redirect,'error');assert.equal(call.url,'https://example.test/v1/messenger/login');
});
test('pairing projects response, cannot override identity; no retry on failure',async()=>{
  let calls=0,fail=false,last;
  const transport=new MessengerTransport('https://example.test',{fetchImpl:async(url,options)=>{
    calls++;last=JSON.parse(options.body);if(fail)throw new Error('private credential detail');
    return {ok:true,json:async()=>({challenge:'a'.repeat(43),expires_at:Math.floor(Date.now()/1000)+300,state:'awaiting_local_confirmation',secret:'hidden'})};
  }});
  transport.session={token:'b'.repeat(43),expiresAt:Date.now()+60000};
  const value=await transport.requestPairing('node','public-key');
  assert.equal(value.secret,undefined);assert.deepEqual(last,{installation_id:'node',owner_key:'public-key'});
  fail=true;await assert.rejects(transport.requestPairing('node','public-key'),/outcome is uncertain/);
  assert.equal(calls,2);
});
test('failed login and logout clear local sessions without claiming revocation',async()=>{
  const transport=new MessengerTransport('https://example.test',{fetchImpl:async()=>{throw new Error('network');}});
  transport.session={token:'a'.repeat(43)};
  await assert.rejects(transport.logout());assert.equal(transport.session,null);
  transport.session={token:'a'.repeat(43)};
  await assert.rejects(transport.login('owner@example.test','password','phone'));assert.equal(transport.session,null);
});
test('late login cannot restore session after logout',async()=>{
  let finish;
  const transport=new MessengerTransport('https://example.test',{fetchImpl:()=>new Promise(resolve=>{finish=resolve;})});
  const pending=transport.login('owner@example.test','password','phone');
  await transport.logout();
  finish({ok:true,json:async()=>({token:'a'.repeat(43),deviceId:'phone',account_id:'owner',home_server:'home',expiresAt:Date.now()+60000})});
  await assert.rejects(pending,/superseded/);assert.equal(transport.session,null);
});
