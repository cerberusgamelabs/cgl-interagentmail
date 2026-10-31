import {test} from 'node:test';
import assert from 'node:assert/strict';
import {MessengerTransport} from '../transport.mjs';
import {nativeFetch} from '../native-adapter.mjs';
test('all onboarding actions cross the native allowlist without credentials and keep session empty',async()=>{
  const calls=[],plugin={async request(input){calls.push(input);const action=input.path.split('/').at(-1);return {status:200,body:JSON.stringify({[action==='verify'?'verified':action==='reset'?'reset':'accepted']:true})};}};
  const transport=new MessengerTransport('https://example.test',{fetchImpl:nativeFetch(plugin,'https://example.test')});
  for(const action of ['register','resend','forgot','verify','reset']){
    await transport.accountAction(action,{email:'a@example.test',password:'long-password-test',token:'a'.repeat(43)});
    assert.equal(calls.at(-1).token,'');assert.equal(transport.session,null);
  }
  assert.deepEqual(JSON.parse(calls[2].body),{email:'a@example.test'});
  assert.deepEqual(JSON.parse(calls[3].body),{token:'a'.repeat(43)});
  await assert.rejects(transport.accountAction('admin',{}));
  await assert.rejects(transport.accountAction('register',{email:'bad',password:'short'}));
  transport.session={token:'existing'};await assert.rejects(transport.accountAction('forgot',{email:'a@example.test'}));assert.equal(calls.length,5);
});
