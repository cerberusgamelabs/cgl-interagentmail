// Portable foreground enrollment transport. No Electron/local RPC dependency.
// Credentials live in memory here; the Android layer must use protected storage.
// No automatic retries: a timeout can follow a successfully accepted operation.
import {validateOwnerProof} from './owner-proof.mjs';
import {manifest,manifestHash} from './uploads.mjs';
import {publicProfile} from './profiles.mjs';
export class MessengerTransport {
  constructor(origin,{fetchImpl=globalThis.fetch,timeoutMs=15000}={}) {
    const url=new URL(origin);
    if(url.protocol!=='https:' || url.username || url.password || url.search || url.hash || url.pathname!=='/')
      throw new Error('An HTTPS Messenger server origin is required');
    this.origin=url.origin;this.fetch=fetchImpl;this.timeoutMs=timeoutMs;this.session=null;this.generation=0;
  }
  restoreSession(value) {
    this.session=null;this.generation++;
    if(!value || typeof value.token!=='string' || !/^[A-Za-z0-9_-]{43}$/.test(value.token) ||
       !Number.isSafeInteger(value.expiresAt) || value.expiresAt<=Date.now() ||
       ![value.deviceId,value.accountId,value.homeServer].every(v=>typeof v==='string' && v.length>0 && v.length<=512))
      throw new Error('Saved login is invalid or expired; sign in again.');
    this.session={token:value.token,expiresAt:value.expiresAt,deviceId:value.deviceId,accountId:value.accountId,homeServer:value.homeServer};
  }
  async request(path,body,token) {
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),this.timeoutMs);
    try {
      const response=await this.fetch(this.origin+path,{method:'POST',redirect:'error',
        credentials:'omit',cache:'no-store',signal:controller.signal,
        headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},
        body:JSON.stringify(body)});
      if(!response.ok) {
        // Never display arbitrary server HTML, bodies, credentials or exceptions.
        const error=new Error(response.status===401?'Sign-in required':response.status===429?
          'Too many requests; try again later':response.status===503?'Messenger is unavailable':'Messenger request rejected');
        error.status=response.status;throw error;
      }
      return await response.json();
    } catch(error) {
      if(error.status)throw error;
      throw new Error('Connection interrupted; outcome is uncertain. No automatic retry was sent.');
    } finally {clearTimeout(timer);}
  }
  async login(email,password,deviceLabel) {
    // A failed new login cannot retain an earlier person's session.
    this.session=null;
    const generation=++this.generation;
    const value=await this.request('/v1/messenger/login',{email,password,device_label:deviceLabel});
    if(generation!==this.generation)throw new Error('Sign-in superseded; no session restored');
    if(typeof value.token!=='string' || !/^[A-Za-z0-9_-]{43}$/.test(value.token) ||
       !Number.isSafeInteger(value.expiresAt) || value.expiresAt<=Date.now() ||
       ![value.deviceId,value.account_id,value.home_server].every(v=>typeof v==='string' && v.length>0 && v.length<=512))
      throw new Error('Invalid sign-in response');
    this.session={token:value.token,expiresAt:value.expiresAt,deviceId:value.deviceId,
      accountId:value.account_id,homeServer:value.home_server};
    return {deviceId:value.deviceId,accountId:value.account_id,homeServer:value.home_server,expiresAt:value.expiresAt};
  }
  async friends(operation,input={}) {
    if(!['self','roster','invite','discover','request','decide','send','history','read','listen','attachment','typing'].includes(operation) || !this.session)throw Error('Friend operation unavailable');
    const session=this.session,generation=this.generation;
    const result=await this.request('/v1/messenger/friends/'+operation,input,session.token);
    if(session!==this.session || generation!==this.generation)throw Error('Account changed; response discarded');
    return result;
  }
  async accountAction(action,{email,password,token}={}) {
    if(this.session)throw new Error('Sign out before changing account access.');
    if(!['register','resend','forgot','verify','reset'].includes(action))throw new Error('Invalid account action');
    const body={};
    if(['register','resend','forgot'].includes(action)){
      if(typeof email!=='string' || email.length>254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))throw new Error('Enter a valid email.');
      body.email=email;
    }
    if(['register','reset'].includes(action)){
      if(typeof password!=='string' || password.length<12 || password.length>256)throw new Error('Use a password of 12 to 256 characters.');
      body.password=password;
    }
    if(['verify','reset'].includes(action)){
      if(typeof token!=='string' || !/^[A-Za-z0-9_-]{43}$/.test(token))throw new Error('Paste the code from your email.');
      body.token=token;
    }
    const result=await this.request('/v1/auth/'+action,body);
    const expected=action==='verify'?'verified':action==='reset'?'reset':'accepted';
    if(result?.[expected]!==true)throw new Error('Invalid account response');
    return {[expected]:true};
  }
  async requestPairing(installationId,publicKey) {
    const generation=this.generation;
    if(!this.session || this.session.expiresAt<=Date.now())throw new Error('Sign-in required');
    if(typeof installationId!=='string' || !installationId || installationId.length>512 ||
       typeof publicKey!=='string' || !publicKey || publicKey.length>256)
      throw new Error('Installation and device public key required');
    const value=await this.request('/v1/messenger/pairing/request',
      {installation_id:installationId,owner_key:publicKey},this.session.token);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    if(typeof value.challenge!=='string' || !/^[A-Za-z0-9_-]{32,128}$/.test(value.challenge) ||
       value.state!=='awaiting_local_confirmation' || !Number.isSafeInteger(value.expires_at) || value.expires_at*1000<=Date.now())
      throw new Error('Invalid pairing response');
    return {challenge:value.challenge,expiresAt:value.expires_at,state:value.state};
  }
  async pairingStatus(installationId,challenge) {
    const session=this.session,generation=this.generation;
    if(!session || session.expiresAt<=Date.now())throw new Error('Sign-in required');
    if(typeof installationId!=='string' || !installationId || installationId.length>512 ||
       typeof challenge!=='string' || !/^[A-Za-z0-9_-]{32,128}$/.test(challenge))throw new Error('Invalid pairing lookup');
    const value=await this.request('/v1/messenger/pairing/status',
      {installation_id:installationId,challenge},session.token);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    if(!['awaiting_local_confirmation','approved'].includes(value.state) ||
       !Number.isSafeInteger(value.expires_at) || value.expires_at*1000<=Date.now() ||
       (value.state==='approved' && (typeof value.grant_id!=='string' || !/^[A-Za-z0-9_-]{32,128}$/.test(value.grant_id))))
      throw new Error('Invalid pairing status');
    if(value.conversations!==undefined && (!Array.isArray(value.conversations) || value.conversations.length>100 ||
        value.conversations.some(c=>typeof c!=='string' || !c || c.length>512)))throw new Error('Invalid pairing scope');
    return {state:value.state,expiresAt:value.expires_at,
      ...(value.state==='approved'?{grantId:value.grant_id,...(value.conversations?{conversations:value.conversations,displayNames:Object.fromEntries(value.conversations.filter(c=>Object.hasOwn(value.display_names || {},c) && typeof value.display_names[c]==='string' && value.display_names[c].length<=512).map(c=>[c,value.display_names[c]]))}:{})}:{})};
  }
  async claimInvitation(installationId,challenge,publicKey) {
    const session=this.session,generation=this.generation;
    if(!session || session.expiresAt<=Date.now())throw new Error('Sign-in required');
    const result=await this.request('/v1/messenger/pairing/claim',
      {installation_id:installationId,challenge,owner_key:publicKey},session.token);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    if(result.challenge!==challenge || result.state!=='awaiting_local_confirmation' ||
       !Number.isSafeInteger(result.expires_at) || result.expires_at*1000<=Date.now())throw new Error('Invalid invitation response');
    return {installation:installationId,challenge,expiresAt:result.expires_at};
  }
  async historyPage(installationId,conversationId,grantId,sign,{after=[0,''],before=undefined,limit=30}={}) {
    const session=this.session,generation=this.generation;
    if(!session || session.expiresAt<=Date.now())throw new Error('Sign-in required');
    if(typeof sign!=='function')throw new Error('Protected device signer required');
    const raw=new TextEncoder().encode(JSON.stringify(before===undefined?{after,limit}:{before,limit}));
    const content=btoa(Array.from(raw,b=>String.fromCharCode(b)).join(''));
    const input={installation_id:installationId,conversation_id:conversationId,grant_id:grantId,content};
    const proof=await this.request('/v1/messenger/history/challenge',input,session.token);
    const intent={...input,operation:'history',home_server:session.homeServer,
      account_id:session.accountId,device_id:session.deviceId};
    const bytes=await validateOwnerProof(proof,intent);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    const signature=await sign(bytes,intent);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    const result=await this.request('/v1/messenger/history/read',{...input,nonce:proof.nonce,signature},session.token);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    if(!Array.isArray(result.events) || result.events.length>50 || !Array.isArray(result.after) ||
       result.after.length!==2 || !Number.isFinite(result.after[0]) || typeof result.after[1]!=='string' ||
       typeof result.more!=='boolean')throw new Error('Invalid history response');
    const events=result.events.map(event=>{
      if(!event || typeof event.event_id!=='string' || !Number.isFinite(event.created) ||
         !['owner','resident'].includes(event.sender) || typeof event.body!=='string' ||
         typeof event.truncated!=='boolean' || typeof event.state!=='string')throw new Error('Invalid history response');
      return {id:event.event_id,requestId:event.request_id,created:event.created,sender:event.sender,
        body:event.body,truncated:event.truncated,state:event.state,
        ...(['sent','received','no_reply','replied'].includes(event.delivery)?{delivery:event.delivery}:{}),
        documents:Array.isArray(event.documents) && event.documents.length<=4?event.documents.map(d=>{
          if(!d || typeof d.name!=='string' || d.name.length>120 || !Number.isSafeInteger(d.size) || d.size<0 || d.size>4000000)throw new Error('Invalid document metadata');
          return {name:d.name,size:d.size};
        }):[],
        imageCount:Number.isInteger(event.image_count) && event.image_count>=0 && event.image_count<=4?event.image_count:0};
    });
    return {events,after:result.after,more:result.more};
  }
  async textChunk(installationId,conversationId,grantId,query,sign) {
    const session=this.session,generation=this.generation;
    if(!session || session.expiresAt<=Date.now())throw new Error('Sign-in required');
    if(typeof sign!=='function' || typeof query?.event_id!=='string' || !query.event_id || query.event_id.length>200 ||
       !Number.isSafeInteger(query.offset) || query.offset<0 || query.offset>1000000)throw new Error('Invalid text query');
    const raw=new TextEncoder().encode(JSON.stringify({text:{event_id:query.event_id,offset:query.offset}}));
    const content=btoa(Array.from(raw,b=>String.fromCharCode(b)).join(''));
    const input={installation_id:installationId,conversation_id:conversationId,grant_id:grantId,content};
    const intent={...input,operation:'history',home_server:session.homeServer,account_id:session.accountId,device_id:session.deviceId};
    const proof=await this.request('/v1/messenger/history/challenge',input,session.token);
    const bytes=await validateOwnerProof(proof,intent);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    const signature=await sign(bytes,intent);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    const result=await this.request('/v1/messenger/history/read',{...input,nonce:proof.nonce,signature},session.token);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    return result.text;
  }
  async logout() {
    this.generation++;
    const previous=this.session;this.session=null;
    if(previous)await this.request('/v1/messenger/logout',{},previous.token);
    // If the request fails, local sign-out still happened, but server revocation
    // is NOT confirmed. Caller must display that distinction.
  }
  async syncEvents(installationId,conversationId,grantId,read,sign,push=null,listen=null,roster=null) {
    const session=this.session,generation=this.generation;
    if(!session || session.expiresAt<=Date.now())throw new Error('Sign-in required');
    if(!Array.isArray(read) || read.length>20 || read.some(id=>typeof id!=='string' || !id || id.length>200))throw new Error('Invalid read events');
    if(listen && (push || read.length || !/^[0-9a-f-]{36}$/.test(listen)))throw new Error('Invalid subscription');
    if(roster && (read.length || push || !Array.isArray(roster) || !roster.length || roster.length>64))throw Error('Invalid roster');
    const content=btoa(Array.from(new TextEncoder().encode(JSON.stringify({read,...(push?{push}:{}),...(listen?{listen}:{}),...(roster?{roster}:{})})),b=>String.fromCharCode(b)).join(''));
    if(content.length>65536)throw Error('Roster scope is too large');
    const input={installation_id:installationId,conversation_id:conversationId,grant_id:grantId,content};
    const intent={...input,operation:'events',home_server:session.homeServer,account_id:session.accountId,device_id:session.deviceId};
    const proof=await this.request('/v1/messenger/events/challenge',input,session.token);
    const bytes=await validateOwnerProof(proof,intent);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    const signature=await sign(bytes,intent);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    const result=await this.request('/v1/messenger/events/sync',{...input,nonce:proof.nonce,signature},session.token);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    if(!Number.isSafeInteger(result?.unread) || result.unread<0)throw new Error('Invalid unread state');
    if(!Array.isArray(result.subscriptions) || result.subscriptions.length>64 || result.subscriptions.some(s=>
      typeof s?.id!=='string' || !/^[0-9a-f-]{36}$/.test(s.id) || !Number.isSafeInteger(s.revision) || s.revision<0 ||
      !Number.isSafeInteger(s.unread ?? result.unread) || (s.unread ?? result.unread)<0))throw new Error('Invalid notification state');
    if(listen && (result.stream?.id!==listen || !Number.isSafeInteger(result.stream.expires_at) || result.stream.expires_at*1000<=Date.now()))throw new Error('Subscription unavailable');
    const deliveries=Array.isArray(result.deliveries) && result.deliveries.length<=100?result.deliveries.filter(d=>typeof d?.request_id==='string' && d.request_id.length<=128 && ['sent','received','no_reply','replied'].includes(d.delivery)).map(d=>({requestId:d.request_id,delivery:d.delivery})):[];
    let rosterResult;
    if(roster){
      if(!Array.isArray(result.roster) || result.roster.length!==roster.length || result.roster.some((r,i)=>r.conversation!==roster[i].conversation || typeof r.display_name!=='string' || r.display_name.length>512 || !Number.isSafeInteger(r.unread) || r.unread<0))throw Error('Invalid roster response');
      rosterResult=result.roster.map(r=>({conversation:r.conversation,displayName:r.display_name,unread:r.unread,profile:publicProfile(r.profile)}));
    }
    return {unread:result.unread,subscriptions:result.subscriptions.map(s=>({id:s.id,revision:s.revision,unread:s.unread ?? result.unread})),...(listen?{stream:result.stream}:{}),...(typeof result.display_name==='string' && result.display_name.length<=512?{displayName:result.display_name}:{}),profile:publicProfile(result.profile),deliveries,...(rosterResult?{roster:rosterResult}:{})};
  }
  async lookupSend(installationId,conversationId,grantId,requestId,body,sign,image=null,files=[]) {
    const session=this.session,generation=this.generation;
    if(!session || session.expiresAt<=Date.now())throw new Error('Sign-in required');
    if(typeof body!=='string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(requestId))throw new Error('Invalid pending send');
    const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(body));
    const body_sha256=Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');
    const content=btoa(JSON.stringify({lookup:{request_id:requestId,body_sha256,...(image?{image_sha256:image.sha256}:{}),...(files.length?{attachments_sha256:await manifestHash(files)}:{})}}));
    const input={installation_id:installationId,conversation_id:conversationId,grant_id:grantId,content};
    const intent={...input,operation:'history',home_server:session.homeServer,account_id:session.accountId,device_id:session.deviceId};
    const proof=await this.request('/v1/messenger/history/challenge',input,session.token);
    const bytes=await validateOwnerProof(proof,intent);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    const signature=await sign(bytes,intent);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    const result=await this.request('/v1/messenger/history/read',{...input,nonce:proof.nonce,signature},session.token);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    const found=result?.lookup;
    if(!found || found.request_id!==requestId || typeof found.found!=='boolean' ||
       (found.found && !['queued','submitting','accepted','held','replied','blocked','failed'].includes(found.state)))
      throw new Error('Recovery check inconclusive; original message remains held.');
    return {requestId,found:found.found,...(found.found?{state:found.state}:{})};
  }
  async sendText(installationId,conversationId,grantId,requestId,body,sign,image=null,files=[]) {
    const session=this.session,generation=this.generation;
    if(!session || session.expiresAt<=Date.now())throw new Error('Sign-in required');
    if(typeof sign!=='function' || typeof requestId!=='string' ||
       !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(requestId) ||
       typeof body!=='string' || !body.trim())throw new Error('A message and stable request ID are required');
    if(image && files.length)throw Error('Finish or remove the legacy photo before adding files');
    const raw=new TextEncoder().encode(JSON.stringify({request_id:requestId,body,...(image?{image_upload:image.upload,image_sha256:image.sha256}:{}),...(files.length?{attachments:manifest(files)}:{})}));
    if(raw.length>3000)throw new Error('Message too long for this text pilot (3 KB including envelope).');
    const content=btoa(Array.from(raw,b=>String.fromCharCode(b)).join(''));
    const input={installation_id:installationId,conversation_id:conversationId,grant_id:grantId,content};
    const proof=await this.request('/v1/messenger/send/challenge',input,session.token);
    const intent={...input,operation:'send',home_server:session.homeServer,account_id:session.accountId,device_id:session.deviceId};
    const bytes=await validateOwnerProof(proof,intent);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    const signature=await sign(bytes,intent);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    const result=await this.request('/v1/messenger/send/submit',{...input,nonce:proof.nonce,signature},session.token);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    if(result.request_id!==requestId || !['queued','submitting','accepted','held','replied','blocked','failed'].includes(result.state))
      throw new Error('Submission receipt invalid; check history before sending again.');
    return {requestId,state:result.state};
  }
  async attachment(installationId,conversationId,grantId,query,sign) {
    const session=this.session,generation=this.generation;
    if(!session || session.expiresAt<=Date.now())throw new Error('Sign-in required');
    const raw=new TextEncoder().encode(JSON.stringify(query));
    const content=btoa(Array.from(raw,b=>String.fromCharCode(b)).join(''));
    if(content.length>65536)throw new Error('Image chunk too large');
    const input={installation_id:installationId,conversation_id:conversationId,grant_id:grantId,content};
    const intent={...input,operation:'attachment',home_server:session.homeServer,account_id:session.accountId,device_id:session.deviceId};
    const proof=await this.request('/v1/messenger/attachment/challenge',input,session.token);
    const bytes=await validateOwnerProof(proof,intent);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    const signature=await sign(bytes,intent);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    const result=await this.request('/v1/messenger/attachment/transfer',{...input,nonce:proof.nonce,signature},session.token);
    if(generation!==this.generation)throw new Error('Sign-in superseded');
    return result.attachment;
  }
}
