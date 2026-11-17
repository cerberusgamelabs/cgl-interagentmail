// Lives only inside the account's encrypted state. No plaintext web storage.
const LIMIT=4*1024*1024;
export function cachedAvatar(state,id,grant,sha256){
  const entry=state?.avatarCache?.find(e=>e.id===id && e.grant===grant && e.sha256===sha256);
  return entry && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(entry.uri) && entry.uri.length<=1400000 ? entry : null;
}
export function rememberAvatar(state,id,grant,image){
  const entry={id,grant,...image};
  if(!cachedAvatar({avatarCache:[entry]},id,grant,image.sha256))return;
  const entries=[entry,...(state.avatarCache || []).filter(e=>e.id!==id)];
  let bytes=0;state.avatarCache=entries.slice(0,48).filter(e=>{bytes+=e.uri.length;return bytes<=LIMIT;});
}
