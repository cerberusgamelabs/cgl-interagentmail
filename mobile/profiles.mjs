import {downloadImage} from './images.mjs';
export function publicProfile(value){
  const result={};
  if(typeof value?.status==='string' && value.status.length<=180)result.status=value.status;
  if(['','available','busy','away','do_not_disturb'].includes(value?.presence))result.presence=value.presence;
  const a=value?.avatar;
  const runtime=value?.runtime;
  if(runtime && ['available','busy','away','approval needed','not attached','unavailable'].includes(runtime.availability) && typeof runtime.stale==='boolean'){
    const context={};
    if(runtime.context?.state==='compacting')context.state='compacting';
    else if(Number.isInteger(runtime.context?.remainingPercent) && runtime.context.remainingPercent>=0 && runtime.context.remainingPercent<=100){context.remainingPercent=runtime.context.remainingPercent;context.stale=runtime.context.stale===true;}
    result.runtime={availability:runtime.availability,stale:runtime.stale,context};
  }
  if(a && ['image/png','image/jpeg','image/webp'].includes(a.mime) && /^[0-9a-f]{64}$/.test(a.sha256) && Number.isSafeInteger(a.total) && a.total>0 && a.total<=1000000)result.avatar={mime:a.mime,sha256:a.sha256,total:a.total};
  return result;
}
export async function downloadAvatar(meta,transfer){
  return downloadImage('avatar',0,async({offset})=>{
    const value=await transfer({action:'read_avatar',sha256:meta.sha256,offset});
    if(value.sha256!==meta.sha256 || value.total!==meta.total || value.mime!==meta.mime)throw Error('Avatar changed');
    return {...value,event_id:'avatar',index:0};
  });
}
