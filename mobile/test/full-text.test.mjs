import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {downloadText} from '../full-text.mjs';
import {MessengerTransport} from '../transport.mjs';
function response(text){const raw=Buffer.from(text);return query=>({event_id:query.event_id,offset:query.offset,total:raw.length,
  sha256:createHash('sha256').update(raw).digest('hex'),data:raw.subarray(query.offset,query.offset+65536).toString('base64')});}
test('complete message spans chunks without breaking UTF-8 and preserves literal markup',async()=>{
  const value='<script>not executable</script>😀'.repeat(6000),offsets=[],part=response(value);
  assert.equal(await downloadText('r:reply',async query=>{offsets.push(query.offset);return part(query);}),value);
  assert.ok(offsets.length>1);assert.equal(offsets[1],65536);
  assert.equal(await downloadText('empty:notice',response('')),'');
});
test('wrong event, offset, size, encoding, hash and incomplete responses fail closed',async()=>{
  for(const change of [{event_id:'private:reply'},{offset:1},{total:1000001},{total:-1},{data:'!'},{data:''},{sha256:'0'.repeat(64)}])
    await assert.rejects(downloadText('r:reply',query=>({...response('hello')(query),...change})));
  const bytes=Buffer.from([255]);
  await assert.rejects(downloadText('r:reply',query=>({...response('x')(query),data:bytes.toString('base64'),sha256:createHash('sha256').update(bytes).digest('hex')})));
});
test('change or revocation between chunks returns no partial message and never retries',async()=>{
  const part=response('a'.repeat(70000));let calls=0;
  await assert.rejects(downloadText('r:reply',query=>{calls++;return {...part(query),...(calls>1?{sha256:'0'.repeat(64)}:{})};}));
  assert.equal(calls,2);calls=0;
  await assert.rejects(downloadText('r:reply',query=>{if(++calls>1)throw Error('revoked');return part(query);}));
  assert.equal(calls,2);
});

test('text transport signs exact event and offset; late logout cannot return content',async()=>{
  const calls=[];let signs=0,logoutDuringRead=false;
  const session={token:'a'.repeat(43),expiresAt:Date.now()+60000,homeServer:'home',accountId:'owner',deviceId:'phone'};
  const transport=new MessengerTransport('https://example.test',{fetchImpl:async(url,options)=>{
    const input=JSON.parse(options.body);calls.push({url,input});
    if(url.endsWith('/challenge')){
      const payload={protocol:'iam-owner-proof',version:1,nonce:'n'.repeat(43),grant_id:input.grant_id,
        installation_id:input.installation_id,conversation_id:input.conversation_id,operation:'history',
        home_server:'home',account_id:'owner',device_id:'phone',expires_at:Math.floor(Date.now()/1000)+50,
        content_sha256:createHash('sha256').update(Buffer.from(input.content,'base64')).digest('hex')};
      return {ok:true,json:async()=>({nonce:payload.nonce,expires_at:payload.expires_at,payload:Buffer.from(JSON.stringify(payload)).toString('base64')})};
    }
    if(logoutDuringRead){transport.session=null;transport.generation++;}
    return {ok:true,json:async()=>({text:response('hello')({event_id:'r:reply',offset:0})})};
  }});
  transport.session=session;
  const sign=async(_,intent)=>{signs++;assert.deepEqual(JSON.parse(atob(intent.content)),{text:{event_id:'r:reply',offset:0}});return 'AAAA';};
  const transfer=query=>transport.textChunk('node','pilot','g'.repeat(43),query,sign);
  assert.equal(await downloadText('r:reply',transfer),'hello');assert.equal(signs,1);assert.equal(calls.length,2);
  logoutDuringRead=true;await assert.rejects(transfer({event_id:'r:reply',offset:0}),/superseded/);
});
