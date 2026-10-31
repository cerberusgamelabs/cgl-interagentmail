// Only locally enrolled conversations, stored inside the existing encrypted vault.
// A cached grant is not proof of current access: every request still checks it.
const fields=['installation','conversation','displayName','pairing','grantId','grantExpires','draft','draftImage','draftFiles','pending','lastSend','push'];
export function buddyName(value){return typeof value?.displayName==='string' && value.displayName.trim() && value.displayName.length<=512?value.displayName:value?.conversation || '';}
export function updateBuddyName(state,buddy,name){
  if(typeof name!=='string' || !name.trim() || name.length>512)return false;
  const saved=(state.buddies || []).find(b=>buddyKey(b)===buddyKey(buddy));
  if(!saved || saved.grantId!==buddy.grantId)return false;
  const changed=saved.displayName!==name;
  saved.displayName=name;
  if(buddyKey(state)===buddyKey(saved))state.displayName=name;
  return changed;
}
export function buddyKey(value){return value?.installation && value?.conversation?JSON.stringify([value.installation,value.conversation]):null;}
export function buddySnapshot(value){return Object.fromEntries(fields.filter(k=>value[k]!==undefined).map(k=>[k,value[k]]));}
export function rememberBuddy(list,value){
  const id=buddyKey(value);if(!id)return list;
  if(list.length>=64 && !list.some(item=>buddyKey(item)===id))throw new Error('This pilot supports up to 64 saved conversations.');
  const next=list.filter(item=>buddyKey(item)!==id);next.push(buddySnapshot(value));return next;
}
export function selectBuddy(state,buddy){
  const next={...state};for(const name of fields)delete next[name];
  return {...next,...buddySnapshot(buddy)};
}

// Restore local continuity only after authenticating the same account on the
// same server/key. A new login device does not inherit old device-bound grants.
export function restoreAfterLogin(saved,origin,session,fingerprint){
  if(!saved || saved.origin!==origin || saved.keyFingerprint!==fingerprint ||
     !session?.accountId || !session?.homeServer ||
     saved.session?.accountId!==session.accountId || saved.session?.homeServer!==session.homeServer)return {origin};
  const next=structuredClone(saved);
  delete next.session;
  if(saved.session.deviceId!==session.deviceId){
    for(const value of [next,...(next.buddies || [])]){
      for(const field of ['grantId','grantExpires','pairing','push'])delete value[field];
    }
    delete next.accountPairing;
  }
  return next;
}
