import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseInvitation,enrollConversations} from '../pairing.mjs';
const invitation={v:1,home:'home',installation:'desktop',challenge:'a'.repeat(43)};
const encode=value=>'iam-pair:'+Buffer.from(JSON.stringify(value)).toString('base64url');

test('QR chosen names are metadata, including duplicate labels and unknown fields',()=>{
  const next=enrollConversations({buddies:[]},invitation,{conversations:['one','two'],grantId:'g',expiresAt:99,
    displayNames:{one:'Sophie',two:'Sophie',outside:'Not enrolled'}});
  assert.deepEqual(next.buddies.map(b=>[b.conversation,b.displayName]),[['one','Sophie'],['two','Sophie']]);
});
test('QR cannot change home or inject fields/URL navigation',()=>{
  assert.deepEqual(parseInvitation(encode(invitation),'home'),invitation);
  for(const code of ['https://evil.test',encode({...invitation,home:'other'}),encode({...invitation,admin:true})])assert.throws(()=>parseInvitation(code,'home'));
});
test('account enrollment merges only approved roster preserving pending sends/drafts',()=>{
  const state={installation:'desktop',conversation:'one',draft:'typing',buddies:[{installation:'desktop',conversation:'one',pending:{id:'original'},draft:'saved'}],accountPairing:invitation};
  const next=enrollConversations(state,invitation,{conversations:['one','two'],grantId:'new',expiresAt:99});
  assert.equal(next.buddies.length,2);assert.equal(next.buddies[0].pending.id,'original');
  assert.equal(next.buddies[0].draft,'saved');assert.equal(next.draft,'typing');assert.equal(next.grantId,'new');
  assert.equal(next.accountPairing,undefined);assert.equal(state.buddies[0].grantId,undefined);
  assert.throws(()=>enrollConversations(state,invitation,{conversations:[]}));
});
