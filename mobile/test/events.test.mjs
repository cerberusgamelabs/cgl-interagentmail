import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {MessengerTransport} from '../transport.mjs';
test('read acknowledgment signs exact IDs with events authority and projects counts',async()=>{
  const calls=[];let signed;
  const transport=new MessengerTransport('https://example.test',{fetchImpl:async(url,options)=>{
    const body=JSON.parse(options.body);calls.push({url,body});
    if(url.endsWith('/challenge')){
      const proof={protocol:'iam-owner-proof',version:1,nonce:'n'.repeat(43),grant_id:body.grant_id,
        home_server:'home',account_id:'owner',device_id:'phone',installation_id:'node',conversation_id:'buddy',operation:'events',
        content_sha256:createHash('sha256').update(Buffer.from(body.content,'base64')).digest('hex'),expires_at:Math.floor(Date.now()/1000)+50};
      return {ok:true,json:async()=>({nonce:proof.nonce,payload:Buffer.from(JSON.stringify(proof)).toString('base64'),expires_at:proof.expires_at})};
    }
    return {ok:true,json:async()=>({unread:2,subscriptions:[{id:'00000000-0000-4000-8000-000000000001',revision:3,unread:0}]})};
  }});
  transport.session={token:'t'.repeat(43),expiresAt:Date.now()+60000,homeServer:'home',accountId:'owner',deviceId:'phone'};
  const result=await transport.syncEvents('node','buddy','g'.repeat(43),['one:reply'],async(bytes,intent)=>{signed=intent;return 'AAAA';});
  assert.equal(result.unread,2);assert.equal(signed.operation,'events');
  assert.equal(result.subscriptions[0].unread,0);
  assert.deepEqual(JSON.parse(Buffer.from(signed.content,'base64').toString()),{read:['one:reply']});
  assert.equal(calls.length,2);assert.ok(calls[1].url.endsWith('/events/sync'));
  await assert.rejects(transport.syncEvents('node','buddy','grant',Array(21).fill('x'),()=>{}));
  assert.equal(calls.length,2);
});
