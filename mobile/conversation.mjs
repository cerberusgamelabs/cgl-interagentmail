// Pure foreground state helpers: no credentials, network or implicit sends.
export function messageBytes(body){return new TextEncoder().encode(JSON.stringify({request_id:'00000000-0000-4000-8000-000000000000',body})).length;}
export function retryDelay(failures){return Math.min(60000,8000*2**Math.min(failures,3));}
export function recoveryDue(pending,now=Date.now()){
  if(!pending || pending.editing || pending.status!=='outcome uncertain')return false;
  const attempts=pending.recoveryAttempts || 0;
  if(attempts>=10 || !Number.isFinite(pending.createdAt))return false;
  const since=pending.lastRecoveryAt ?? pending.createdAt;
  return now-since>=Math.min(60000,8000*2**Math.min(attempts,3));
}
export function pendingLabel(pending){
  return pending.status==='outcome uncertain' && (pending.recoveryAttempts || 0)>=10
    ?'Delivery not confirmed. Automatic checks paused; tap Retry send'
    :pending.status;
}
export function mergeEvents(previous,incoming,limit=1000,older=false){
  const rows=new Map(previous.map(row=>[row.id,row]));
  for(const row of incoming)rows.set(row.id,row);
  const sorted=[...rows.values()].sort((a,b)=>a.created-b.created || a.id.localeCompare(b.id));
  return older?sorted.slice(0,limit):sorted.slice(-limit);
}
export function confirmedPending(pending,events){
  return !!pending && !pending.editing && !pending.image && !pending.files?.length && events.some(row=>row.sender==='owner' && row.requestId===pending.id && row.body===pending.body && !row.truncated);
}
