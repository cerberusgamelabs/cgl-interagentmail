import {test} from 'node:test';
import assert from 'node:assert/strict';
import {MessengerTransport} from '../transport.mjs';
function transport(value,type='application/json') {
  return new MessengerTransport('https://example.test',{fetchImpl:async()=>({ok:false,status:503,
    headers:{get:()=>type},json:async()=>value})});
}
test('new public error codes display server-authored messages without client mapping',async()=>{
  await assert.rejects(transport({public_error:{version:1,code:'new_server_condition',message:'Please wait for the desktop.'}}).request('/test',{}),
    e=>e.message==='Please wait for the desktop.' && e.code==='new_server_condition' && e.status===503);
});
test('raw errors, invalid envelopes and HTML use offline-safe local fallback',async()=>{
  for(const value of [{error:'private exception'},{public_error:{version:2,code:'bad',message:'Unsupported'}},
    {public_error:{version:1,code:'bad',message:'<script>bad</script>'}},
    {public_error:{version:1,code:'bad',message:'x'.repeat(301)}}])
    await assert.rejects(transport(value).request('/test',{}),/Messenger is unavailable/);
  await assert.rejects(transport({public_error:{version:1,code:'bad',message:'wrong type'}},'text/html').request('/test',{}),/Messenger is unavailable/);
});
