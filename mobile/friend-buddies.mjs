// Friend rows never enter owner pairing state or acquire owner grants.
export function activeFriendBuddies(roster){
  if(!roster || !Array.isArray(roster.relationships) || roster.relationships.length>200)throw Error('Invalid friend roster');
  if(!roster.self?.enabled)return [];
  const rows=new Map();
  for(const r of roster.relationships){
    if(r.state!=='active' || r.blocked)continue;
    if(typeof r.id!=='string' || !r.id || typeof r.peer?.name!=='string' || !r.peer.name)throw Error('Invalid friend');
    rows.set(r.id,{id:r.id,name:r.peer.name,unread:Number.isSafeInteger(r.unread)&&r.unread>0?Math.min(2000,r.unread):0});
  }
  return [...rows.values()];
}
