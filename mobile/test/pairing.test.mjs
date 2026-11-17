import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseInvitation,enrollConversations,mergeDesktopRoster} from '../pairing.mjs';
const invitation={v:1,home:'home',installation:'desktop',challenge:'a'.repeat(43)};
const encode=value=>'iam-pair:'+Buffer.from(JSON.stringify(value)).toString('base64url');

test('desktop discovery adds future agents and removes access without erasing drafts',()=>{
  const link={installation:'desktop',grantId:'wide',grantExpires:999};
  const state={buddies:[{installation:'desktop',conversation:'old',grantId:'wide',draft:'keep',pending:{id:'saved'}}]};
  const next=mergeDesktopRoster(state,link,[{conversation:'future',displayName:'New agent'}]);
  assert.equal(next.buddies[0].grantId,undefined);assert.equal(next.buddies[0].draft,'keep');
  assert.equal(next.buddies[0].pending.id,'saved');assert.equal(state.buddies[0].grantId,'wide');
  assert.equal(next.buddies[1].grantId,'wide');assert.equal(next.buddies[1].displayName,'New agent');
  assert.deepEqual(next.desktopLinks,[link]);
  assert.deepEqual(enrollConversations({},invitation,{conversations:[],grantId:'g',expiresAt:999,desktopScope:true}).buddies,[]);
  assert.equal(enrollConversations({},invitation,{conversations:['one'],grantId:'g',expiresAt:999}).desktopLinks,undefined);
});

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
