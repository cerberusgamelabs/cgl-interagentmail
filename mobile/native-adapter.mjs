const paths=new Set(['/v1/auth/register','/v1/auth/resend','/v1/auth/forgot','/v1/auth/verify','/v1/auth/reset',
  '/v1/messenger/login','/v1/messenger/logout','/v1/messenger/pairing/request',
  '/v1/messenger/friends/self','/v1/messenger/friends/roster','/v1/messenger/friends/invite',
  '/v1/messenger/friends/discover','/v1/messenger/friends/request','/v1/messenger/friends/decide',
  '/v1/messenger/friends/send','/v1/messenger/friends/history','/v1/messenger/friends/read',
  '/v1/messenger/friends/listen','/v1/messenger/friends/attachment','/v1/messenger/friends/typing',
  '/v1/messenger/events/challenge','/v1/messenger/events/sync',
  '/v1/messenger/pairing/status','/v1/messenger/pairing/claim','/v1/messenger/history/challenge','/v1/messenger/history/read',
  '/v1/messenger/send/challenge','/v1/messenger/send/submit',
  '/v1/messenger/attachment/challenge','/v1/messenger/attachment/transfer']);
export function nativeFetch(plugin,origin) {
  const home=new URL(origin).origin;
  return async(url,options)=>{
    const target=new URL(url);
    if(target.origin!==home || !paths.has(target.pathname) || target.search || target.hash ||
      target.username || target.password || options.method!=='POST' || options.redirect!=='error')throw new Error('Request not allowed');
    if(options.signal?.aborted)throw new Error('Cancelled');
    const token=options.headers.Authorization?.replace(/^Bearer /,'') || '';
    const result=await plugin.request({origin:home,path:target.pathname,body:options.body,token});
    if(options.signal?.aborted)throw new Error('Cancelled');
    return {ok:result.status>=200 && result.status<300,status:result.status,json:async()=>JSON.parse(result.body)};
  };
}
export function nativeSigner(plugin) {
  return async(bytes,intent)=>{
    const payload=btoa(Array.from(bytes,b=>String.fromCharCode(b)).join(''));
    return (await (intent.operation==='events'?plugin.signEvents({payload,intent}):intent.operation==='attachment'?plugin.signAttachment({payload,intent}):intent.operation==='send'?plugin.signSend({payload,intent}):plugin.signHistory({payload,intent}))).signature;
  };
}
