import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomUUID} from 'node:crypto';
import {MessengerTransport} from '../transport.mjs';

test('send binds exact body and ID, projects receipt, never retries a timeout',async()=>{
  const id=randomUUID();let submits=0,signs=0;
  const t=new MessengerTransport('https://example.test',{fetchImpl:async(url,options)=>{
    const input=JSON.parse(options.body);
    if(url.endsWith('/challenge')){
      assert.deepEqual(JSON.parse(atob(input.content)),{request_id:id,body:'hello'});
      const expiry=Math.floor(Date.now()/1000)+50,nonce='n'.repeat(43);
      const p={protocol:'iam-owner-proof',version:1,nonce,grant_id:input.grant_id,
        home_server:'home',account_id:'owner',device_id:'phone',installation_id:'node',conversation_id:'pilot',
        operation:'send',content_sha256:createHash('sha256').update(Buffer.from(input.content,'base64')).digest('hex'),expires_at:expiry};
      return {ok:true,json:async()=>({nonce,payload:btoa(JSON.stringify(p)),expires_at:expiry})};
    }
    submits++;throw new Error('disconnected');
  }});
  t.session={token:'a'.repeat(43),expiresAt:Date.now()+60000,homeServer:'home',accountId:'owner',deviceId:'phone'};
  await assert.rejects(t.sendText('node','pilot','g'.repeat(43),id,'hello',async()=>{signs++;return 'signature';}),/uncertain/);
  assert.equal(signs,1);assert.equal(submits,1);
  await assert.rejects(t.sendText('node','pilot','g'.repeat(43),id,'x'.repeat(3001),async()=>''),/too long/);
  assert.equal(submits,1);
});
test('recovery requires an explicit exact-ID signed lookup result',async()=>{
  const id=randomUUID();let result={lookup:{request_id:id,found:false}},calls=0;
  const t=new MessengerTransport('https://example.test',{fetchImpl:async(url,options)=>{
    calls++;const input=JSON.parse(options.body);
    if(url.endsWith('/challenge')){
      assert.deepEqual(JSON.parse(atob(input.content)),{lookup:{request_id:id,body_sha256:createHash('sha256').update('hello').digest('hex')}});
      const expiry=Math.floor(Date.now()/1000)+50,nonce='n'.repeat(43);
      const p={protocol:'iam-owner-proof',version:1,nonce,grant_id:input.grant_id,
        home_server:'home',account_id:'owner',device_id:'phone',installation_id:'node',conversation_id:'pilot',
        operation:'history',content_sha256:createHash('sha256').update(Buffer.from(input.content,'base64')).digest('hex'),expires_at:expiry};
      return {ok:true,json:async()=>({nonce,payload:btoa(JSON.stringify(p)),expires_at:expiry})};
    }
    if(result instanceof Error)throw result;
    return {ok:true,json:async()=>result};
  }});
  t.session={token:'a'.repeat(43),expiresAt:Date.now()+60000,homeServer:'home',accountId:'owner',deviceId:'phone'};
  const lookup=()=>t.lookupSend('node','pilot','g'.repeat(43),id,'hello',async()=> 'signature');
  assert.deepEqual(await lookup(),{requestId:id,found:false});
  for(const invalid of [{},{events:[]},{lookup:{request_id:randomUUID(),found:false}},{lookup:{request_id:id,found:'false'}}]){
    result=invalid;await assert.rejects(lookup(),/inconclusive/);
  }
  result=new Error('offline');await assert.rejects(lookup(),/uncertain/);
  assert.equal(calls,12);
});
