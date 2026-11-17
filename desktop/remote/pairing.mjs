// QR/manual invitation parsing never changes the logged-in server or identity.
export function parseInvitation(code,home) {
  if(typeof code!=='string' || code.length>4096 || !/^iam-pair:[A-Za-z0-9_-]+$/.test(code))throw new Error('Invalid Desktop pairing code');
  let value;
  try{value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Uint8Array.from(atob(code.slice(9).replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0))));}
  catch{throw new Error('Invalid Desktop pairing code');}
  if(!value || Object.keys(value).sort().join(',')!=='challenge,home,installation,v' || value.v!==1 ||
     value.home!==home || typeof value.installation!=='string' || !value.installation || value.installation.length>512 ||
     typeof value.challenge!=='string' || !/^[A-Za-z0-9_-]{43}$/.test(value.challenge))throw new Error('Pairing code does not match this Messenger account server');
  return value;
}
export function enrollConversations(state,invitation,result) {
  if(!Array.isArray(result.conversations) || (!result.desktopScope && !result.conversations.length) || result.conversations.length>1024 ||
      result.conversations.some(c=>typeof c!=='string' || !c || c.length>512))throw new Error('Invalid approved conversation list');
  const buddies=(state.buddies || []).map(b=>({...b}));
  for(const conversation of new Set(result.conversations)){
    const old=buddies.find(b=>b.installation===invitation.installation && b.conversation===conversation);
    const name=Object.hasOwn(result.displayNames || {},conversation)?result.displayNames[conversation]:null;
    const grant={grantId:result.grantId,grantExpires:result.expiresAt,
      ...(typeof name==='string' && name.trim() && name.length<=512?{displayName:name}:{})};
    if(old){Object.assign(old,grant);delete old.pairing;}
    else buddies.push({installation:invitation.installation,conversation,...grant});
  }
  if(buddies.length>1024)throw new Error('Approval exceeds the 1024 saved conversation limit');
  const next={...state,buddies};delete next.accountPairing;
  if(result.desktopScope){
    next.desktopLinks=[...(state.desktopLinks || []).filter(l=>l.installation!==invitation.installation),
      {installation:invitation.installation,grantId:result.grantId,grantExpires:result.expiresAt}];
  }
  const active=buddies.find(b=>b.installation===state.installation && b.conversation===state.conversation);
  if(active){Object.assign(next,{grantId:active.grantId,grantExpires:active.grantExpires,displayName:active.displayName});if(result.conversations.includes(state.conversation) && state.installation===invitation.installation)delete next.pairing;}
  return next;
}

export function mergeDesktopRoster(state,link,roster){
  const result={conversations:roster.map(r=>r.conversation),displayNames:Object.fromEntries(roster.map(r=>[r.conversation,r.displayName])),
    grantId:link.grantId,expiresAt:link.grantExpires,desktopScope:true};
  const next=enrollConversations(state,link,result);
  // Keep drafts/history locally, but never keep access to removed registrations.
  for(const buddy of next.buddies)if(buddy.installation===link.installation && buddy.grantId===link.grantId && !result.conversations.includes(buddy.conversation)){
    delete buddy.grantId;delete buddy.grantExpires;
    if(next.installation===buddy.installation && next.conversation===buddy.conversation){delete next.grantId;delete next.grantExpires;}
  }
  if(state.accountPairing)next.accountPairing=state.accountPairing;
  return next;
}
