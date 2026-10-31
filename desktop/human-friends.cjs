// Window-scoped human authentication. Tokens never leave the main process or persist.
function createHumanFriends({fetchImpl=fetch}={}) {
  let session=null,generation=0;
  const allowed=new Set(['roster','self','invite','discover','request','decide']);
  async function post(origin,path,value,token){
    const url=new URL(origin);
    if(url.protocol!=='https:' || url.username || url.password || url.pathname!=='/' || url.search || url.hash)throw Error('Invalid Messenger origin');
    const response=await fetchImpl(url.origin+path,{method:'POST',redirect:'error',signal:AbortSignal.timeout(15000),
      headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},body:JSON.stringify(value)});
    if(!response.ok)throw Error(response.status===401?'Sign-in required':'Messenger request rejected; refresh before retrying');
    const raw=await response.text();if(raw.length>150000)throw Error('Response too large');return JSON.parse(raw);
  }
  async function logout(){const old=session;session=null;generation++;if(old)await post(old.origin,'/v1/messenger/logout',{},old.token);return {signedIn:false};}
  return {
    async call(config,operation,value={}){
      if(operation==='status')return {signedIn:Boolean(session),account:session?.account,origin:config.origin};
      if(operation==='logout')return logout();
      if(operation==='login'){
        if(session)await logout();const g=++generation;
        if(typeof value.email!=='string' || value.email.length>320 || typeof value.password!=='string' || value.password.length>4096)throw Error('Email and password required');
        const result=await post(config.origin,'/v1/messenger/login',{email:value.email,password:value.password,device_label:'IAM Desktop friends'});
        if(g!==generation){if(result.token)await post(config.origin,'/v1/messenger/logout',{},result.token);throw Error('Window closed or sign-in superseded');}
        if(typeof result.token!=='string' || !/^[A-Za-z0-9_-]{43}$/.test(result.token) || !Number.isSafeInteger(result.expiresAt) || result.expiresAt<=Date.now() || typeof result.account_id!=='string')throw Error('Invalid sign-in response');
        session={origin:config.origin,account:result.account_id,token:result.token,expires:result.expiresAt};
        return {signedIn:true,account:session.account,origin:session.origin};
      }
      if(!allowed.has(operation))throw Error('Unsupported human friend operation');
      const old=session,g=generation;
      if(!old || old.origin!==config.origin || old.expires<=Date.now())throw Error('Sign-in required');
      const result=await post(old.origin,'/v1/messenger/friends/'+operation,value,old.token);
      if(g!==generation || old!==session)throw Error('Account changed; response discarded');return result;
    },
    close(){return logout().catch(()=>{});}
  };
}
module.exports={createHumanFriends};
