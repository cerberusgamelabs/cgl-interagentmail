// One foreground conversation subscription, with bounded reconnect and stale-view fencing.
export class LiveConversation {
  constructor({native,subscribe,changed,status=()=>{},delay=(...args)=>globalThis.setTimeout(...args),cancel=(...args)=>globalThis.clearTimeout(...args)}) {
    Object.assign(this,{native,subscribe,changed,status,delay,cancel});this.generation=0;this.scope=null;this.connected=false;this.attempt=0;this.phase='paused';
  }
  async initialize(){this.listener=await this.native.addListener('messengerStream',event=>this.event(event));}
  setScope(scope){
    if(scope?.key===this.scope?.key)return;
    this.stop();this.scope=scope;this.attempt=0;if(scope)this.connect();
  }
  stop(){this.generation++;this.cancel(this.timer);this.scope=null;this.id=null;this.connected=false;this.phase='paused';this.closing=Promise.resolve(this.native.closeStream()).catch(()=>{});}
  async connect(){
    const scope=this.scope,generation=++this.generation;if(!scope)return;
    this.id=crypto.randomUUID();this.revision=-1;
    this.connected=false;this.phase='connecting';this.status();
    try{
      // Do not let a delayed native teardown close the replacement stream.
      await this.closing;
      if(generation!==this.generation)return;
      const result=await this.subscribe(scope,this.id);
      if(generation!==this.generation)return;
      if(result?.stream?.id!==this.id)throw new Error('Stream identity mismatch');
      await this.native.openStream({origin:scope.origin,token:scope.token,id:result.stream.id,...(scope.friends?{kind:'friends'}:{})});
      if(generation!==this.generation)return;
    }catch{if(generation===this.generation)this.retry();}
  }
  retry(){if(!this.scope)return;this.connected=false;this.phase='retrying';this.status();this.cancel(this.timer);const generation=this.generation;
    this.timer=this.delay(()=>{if(generation===this.generation)this.connect();},Math.min(60000,2000*2**Math.min(this.attempt++,5)));}
  event(event){
    if(!this.scope || event.id!==this.id)return;
    if(event.state==='connected'){
      if(this.connected)return;
      this.connected=true;this.phase='live';this.attempt=0;this.cancel(this.timer);
      // A reconnect must fetch authoritative state even if the first hint was
      // missed. Revision zero is the relay's redundant initial catch-up hint.
      this.revision=Math.max(this.revision,0);this.status();this.changed(this.scope);
    }
    else if(event.state==='disconnected')this.retry();
    else if(event.state==='changed' && Number.isSafeInteger(event.revision) && event.revision>this.revision){this.revision=event.revision;this.changed(this.scope);}
  }
}
