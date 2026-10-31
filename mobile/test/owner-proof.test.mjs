import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {validateOwnerProof} from '../owner-proof.mjs';
import {MessengerTransport} from '../transport.mjs';
const now=Date.now();
const content=btoa('{"after":[0,""],"limit":30}');
const intent={grant_id:'g'.repeat(43),home_server:'home',account_id:'owner',device_id:'phone',
  installation_id:'node',conversation_id:'pilot',operation:'history',content};
function proof(changes={}) {
  const value={protocol:'iam-owner-proof',version:1,nonce:'n'.repeat(43),...intent,
    content_sha256:createHash('sha256').update(Buffer.from(content,'base64')).digest('hex'),
    expires_at:Math.floor(now/1000)+50,...changes};delete value.content;
  return {nonce:'n'.repeat(43),expires_at:value.expires_at,payload:Buffer.from(JSON.stringify(value)).toString('base64')};
}
test('proof validation binds every intent field, expiry and exact content',async()=>{
  assert.ok((await validateOwnerProof(proof(),intent,{now})).length>0);
  for(const key of ['grant_id','home_server','account_id','device_id','installation_id','conversation_id','operation'])
    await assert.rejects(validateOwnerProof(proof({[key]:'other'}),intent,{now}));
  for(const change of [{content_sha256:'0'.repeat(64)},{nonce:'wrong'},{expires_at:0},{expires_at:Math.floor(now/1000)+100},
    {extra:'unreviewed'},{protocol:'other'}])await assert.rejects(validateOwnerProof(proof(change),intent,{now}));
});
test('altered relay proof never reaches signer or history read',async()=>{
  let calls=0,signs=0;
  const transport=new MessengerTransport('https://example.test',{fetchImpl:async()=>{
    calls++;return {ok:true,json:async()=>proof({conversation_id:'other'})};
  }});
  transport.session={token:'a'.repeat(43),expiresAt:now+60000,homeServer:'home',accountId:'owner',deviceId:'phone'};
  await assert.rejects(transport.historyPage('node','pilot',intent.grant_id,async()=>{signs++;}));
  assert.equal(calls,1);assert.equal(signs,0);
});
