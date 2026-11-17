// Main-process-only owner client. Reuses the reviewed phone wire protocol.
// Neither keys nor bearer credentials are returned to renderer windows.
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {pathToFileURL}=require('node:url');
const remoteId=(node,conversation)=>'remote:'+Buffer.from(JSON.stringify([node,conversation])).toString('base64url');
class RemoteDesktop {
  constructor({safeStorage,file,config,transportFactory,now=()=>Date.now()}) {
    Object.assign(this,{safeStorage,file,config,transportFactory,now});
    this.state={links:[],pending:[]};this.client=null;this.scope=null;
    this.buddies=[];this.histories=new Map();this.refreshing=null;this.checked=0;this.error=null;
  }
  secure() {
    if(!this.safeStorage.isEncryptionAvailable() || this.safeStorage.getSelectedStorageBackend?.()==='basic_text')
      throw Error('Protected operating-system credential storage is required.');
  }
  save() {
    this.secure();fs.mkdirSync(path.dirname(this.file),{recursive:true});
    const encrypted=this.safeStorage.encryptString(JSON.stringify(this.state));
    const temporary=this.file+'.tmp';fs.writeFileSync(temporary,encrypted,{mode:0o600});fs.renameSync(temporary,this.file);
  }
  async prepare() {
    const config=await this.config();
    if(!config.enabled)throw Error('Cross-PC linking is not configured on this computer.');
    if(this.preparing)await this.preparing;
    const scope=JSON.stringify([config.origin,config.owner,config.node,config.epoch,config.peers]);
    if(this.scope===scope && this.client)return config;
    this.client=null;this.buddies=[];this.histories.clear();this.scope=scope;
    this.state={links:[],pending:[]};this.secure();
    if(fs.existsSync(this.file)) {
      const saved=JSON.parse(this.safeStorage.decryptString(fs.readFileSync(this.file)));
      if(saved.scope===scope)this.state=saved;
    }
    this.state.scope=scope;
    const factory=this.transportFactory || (async origin=>{
      const {MessengerTransport}=await import(pathToFileURL(path.join(__dirname,'remote','transport.mjs')).href);
      return new MessengerTransport(origin);
    });
    this.preparing=factory(config.origin);
    try {this.client=await this.preparing;}finally{this.preparing=null;}
    if(this.state.session && this.state.session.expiresAt>this.now())this.client.restoreSession(this.state.session);
    return config;
  }
  async login(email,password) {
    const config=await this.prepare();
    // Account is pinned by local enrollment; failed login cannot reuse a bearer.
    this.state.session=null;this.save();
    const session=await this.client.login(email,password,'IAM Desktop computer');
    if(![session.accountId,'user:'+session.accountId].includes(config.owner)) {
      await this.client.logout().catch(()=>{});throw Error('Sign in with the account that owns both computers.');
    }
    if(!this.state.privateKey) {
      const {privateKey,publicKey}=crypto.generateKeyPairSync('ec',{namedCurve:'prime256v1'});
      this.state.privateKey=privateKey.export({type:'pkcs8',format:'pem'});
      this.state.publicKey=publicKey.export({type:'spki',format:'der'}).toString('base64');
    }
    this.state.session=this.client.session;this.save();return this.status();
  }
  async status() {
    const config=await this.prepare();
    return {signedIn:Boolean(this.client.session && this.client.session.expiresAt>this.now()),
      peers:config.peers,links:(this.state.links || []).map(l=>({node:l.node,approved:Boolean(l.grantId),expires:l.expiresAt})),
      queued:this.state.pending?.length || 0,error:this.error};
  }
  async pair(node) {
    const config=await this.prepare();
    if(!config.peers.includes(node))throw Error('Computer is not an allowed peer.');
    const result=await this.client.requestPairing(node,this.state.publicKey);
    this.state.links=this.state.links.filter(l=>l.node!==node);
    this.state.links.push({node,challenge:result.challenge});this.save();
    return {state:result.state,note:'Approve this device in Messenger on the other computer, then check approval here.'};
  }
  async check(node) {
    await this.prepare();const link=this.state.links.find(l=>l.node===node);
    if(!link)throw Error('Request pairing first.');
    const result=await this.client.pairingStatus(node,link.challenge);
    if(result.state==='approved') {
      if(!result.desktopScope)throw Error('Approve the desktop roster scope on the other computer.');
      Object.assign(link,{grantId:result.grantId,expiresAt:result.expiresAt});this.save();this.checked=0;
    }
    return {state:result.state};
  }
  signer(link,conversation) {
    return async (bytes,intent)=>{
      const config=await this.prepare(),session=this.client.session;
      if(!session || !config.peers.includes(link.node) || !this.state.links.some(l=>l.node===link.node && l.grantId===link.grantId) ||
          intent.installation_id!==link.node || intent.conversation_id!==conversation || intent.grant_id!==link.grantId ||
          intent.account_id!==session.accountId || intent.device_id!==session.deviceId || intent.home_server!==session.homeServer ||
          !['history','events','send'].includes(intent.operation))throw Error('Owner proof scope changed.');
      return crypto.sign('sha256',Buffer.from(bytes),this.state.privateKey).toString('base64');
    };
  }
  find(mailbox) {
    const buddy=this.buddies.find(b=>b.mailbox===mailbox);
    if(!buddy)throw Error('Remote conversation is unavailable or not approved.');
    const link=this.state.links.find(l=>l.node===buddy.node && l.grantId===buddy.grantId);
    if(!link || link.expiresAt*1000<=this.now())throw Error('Remote conversation approval expired.');
    return {buddy,link};
  }
  refresh() {
    if(this.refreshing || this.now()-this.checked<5000)return;
    this.checked=this.now();
    this.refreshing=this.update().catch(()=>{this.error='Remote computer unavailable or approval expired; queued messages are retained.';
      for(const b of this.buddies)b.availability='remote unavailable';}).finally(()=>{this.refreshing=null;});
  }
  async update() {
    await this.prepare();if(!this.client.session)return;
    const session=this.client.session,scope=this.scope;
    const buddies=[];
    for(const link of this.state.links.filter(l=>l.grantId && l.expiresAt*1000>this.now())) {
      const result=await this.client.syncEvents(link.node,'@desktop',link.grantId,[],this.signer(link,'@desktop'));
      if(this.client.session!==session || this.scope!==scope)throw Error('Account changed; remote roster discarded.');
      for(const item of result.desktopRoster || [])buddies.push({mailbox:remoteId(link.node,item.conversation),
        name:item.displayName+' · '+link.node,node:link.node,conversation:item.conversation,grantId:link.grantId,
        remote:true,sessionConnected:true,responding:false,availability:'remote',context:null,unread:0,profile:{}});
    }
    this.buddies=buddies;this.error=null;
    await this.flush();
  }
  async send(mailbox,args) {
    await this.prepare();const {buddy,link}=this.find(mailbox);
    if(args.images?.length || args.files?.length)throw Error('Cross-PC desktop attachments are not enabled.');
    if(typeof args.body!=='string' || !args.body.trim() || Buffer.byteLength(JSON.stringify({request_id:args.request_id,body:args.body}))>3000 ||
        !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(args.request_id))throw Error('Remote text must fit the 3 KB signed-message limit.');
    const old=this.state.pending.find(p=>p.id===args.request_id);
    if(old && (old.mailbox!==mailbox || old.body!==args.body))throw Error('Request identity conflict.');
    if(!old) {
      if(this.state.pending.length>=100)throw Error('Remote draft queue is full.');
      this.state.pending.push({id:args.request_id,mailbox,node:buddy.node,grantId:link.grantId,body:args.body,created:this.now()/1000,expires:this.now()+86400000,state:'queued'});
      this.save();
    }
    this.checked=0;this.refresh();return {request_id:args.request_id,state:'queued'};
  }
  async flush() {
    for(const pending of this.state.pending) {
      if(['submitted','expired','blocked'].includes(pending.state))continue;
      if(pending.expires<=this.now()){pending.state='expired';this.save();continue;}
      const {buddy,link}=this.find(pending.mailbox), sign=this.signer(link,buddy.conversation);
      if(pending.grantId!==link.grantId){pending.state='blocked';this.save();continue;}
      try {
        // Look up an uncertain attempt before any resend. Stable UUID prevents duplicates.
        if(pending.state==='uncertain') {
          const found=await this.client.lookupSend(link.node,buddy.conversation,link.grantId,pending.id,pending.body,sign);
          if(found.found){pending.state='submitted';this.save();continue;}
        }
        pending.state='uncertain';this.save();
        await this.client.sendText(link.node,buddy.conversation,link.grantId,pending.id,pending.body,sign);
        pending.state='submitted';this.save();
      }catch {this.error='A remote send is awaiting verification. Its request ID is retained; no blind retry was sent.';break;}
    }
  }
  async history(mailbox) {
    await this.prepare();const {buddy,link}=this.find(mailbox);
    const session=this.client.session,scope=this.scope;
    const cached=this.histories.get(mailbox) || {after:[0,''],events:[],checked:0};
    if(this.now()-cached.checked>5000) {
      try {
        const page=await this.client.historyPage(link.node,buddy.conversation,link.grantId,this.signer(link,buddy.conversation),{after:cached.after,limit:50});
        if(this.client.session!==session || this.scope!==scope)throw Error('Account changed; remote history discarded.');
        const events=new Map(cached.events.map(e=>[e.id,e]));for(const e of page.events)events.set(e.id,e);
        cached.events=[...events.values()].slice(-400);cached.after=page.after;cached.checked=page.more?0:this.now();this.histories.set(mailbox,cached);
      }catch {this.error='Remote history is currently unavailable; showing cached messages.';}
    }
    const rows=new Map();
    for(const event of cached.events) {
      const id=event.requestId || event.id,row=rows.get(id) || {id,created:event.created,body:null,reply:null,images:[],files:[],reply_images:[],reply_files:[]};
      if(event.sender==='owner')row.body=event.body;else {row.reply=event.body;row.replied_at=event.created;}
      row.state=event.state;rows.set(id,row);
    }
    for(const pending of this.state.pending.filter(p=>p.mailbox===mailbox))if(!rows.has(pending.id))rows.set(pending.id,
      {id:pending.id,created:pending.created,body:pending.body,reply:null,state:pending.state,images:[],files:[],reply_images:[],reply_files:[]});
    const received=new Set(cached.events.map(e=>e.requestId));
    const retained=this.state.pending.filter(p=>!(p.mailbox===mailbox && p.state==='submitted' && received.has(p.id)));
    if(retained.length!==this.state.pending.length){this.state.pending=retained;this.save();}
    return [...rows.values()].sort((a,b)=>a.created-b.created);
  }
  async markRead(mailbox,ids) {
    await this.prepare();const {buddy,link}=this.find(mailbox);
    await this.client.syncEvents(link.node,buddy.conversation,link.grantId,ids,this.signer(link,buddy.conversation));return {marked:true};
  }
  async logout() {
    await this.prepare();const previous=this.client;
    this.state={scope:this.scope,links:[],pending:[]};this.buddies=[];this.histories.clear();this.save();
    await previous.logout();return {signedOut:true};
  }
}
module.exports={RemoteDesktop,remoteId};
