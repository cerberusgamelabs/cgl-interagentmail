// Independent opt-in for the authenticated device's friend inbox, never owner grants.
export function friendNotifications({getTransport,getState,native,save,document,status=()=>{},uuid=()=>crypto.randomUUID(),now=()=>Date.now()}){
  let pending=false,last=0,lastSession=null,failed=false;
  async function refresh(enable=false){
    const transport=getTransport(),session=transport?.session,state=getState();
    if(lastSession!==session){lastSession=session;last=0;failed=false;status(false);}
    if(pending || document.hidden || !session || !state || !enable && (!state.friendPush || now()-last<60000))return;
    if(state.friendPush && state.friendPush.device!==session.deviceId)return;
    const valid=()=>transport===getTransport() && session===transport.session && state===getState() && !document.hidden;
    pending=true;
    try{
      const {token}=await (enable?native.enableNotifications():native.refreshNotificationToken());
      if(!valid())return;
      const previous=state.friendPush;
      // An expired server scope may have been pruned. A fresh ID avoids reusing
      // its native revision counter after same-device reauthentication.
      const scope=previous?.expiresAt>now()?{...previous,expiresAt:session.expiresAt}:
        {id:uuid(),device:session.deviceId,expiresAt:session.expiresAt};
      // Save the stable ID before network registration, including uncertain outcomes.
      if(!previous || previous.id!==scope.id || previous.expiresAt!==scope.expiresAt){
        state.friendPush=scope;
        try{await save();}catch(error){if(state.friendPush===scope){if(previous)state.friendPush=previous;else delete state.friendPush;}throw error;}
      }
      if(!valid())return;
      await native.registerNotificationScope({id:scope.id,expires:Math.floor(session.expiresAt/1000)});
      if(!valid())return;
      const result=await transport.friends('notifications',{id:scope.id,token});
      if(!valid())return;
      if(result?.id!==scope.id || !Number.isSafeInteger(result.revision) || result.revision<1 ||
        !Number.isSafeInteger(result.unread) || result.unread<0 || !Number.isSafeInteger(result.expires) ||
        result.expires>Math.floor(session.expiresAt/1000) || result.expires<=now()/1000)throw Error('Invalid notification state');
      await native.reconcileNotification({id:scope.id,revision:result.revision,unread:result.unread});
      if(valid()){failed=false;last=now();status(false);}
    }catch{if(valid()){failed=true;status(true);}if(enable)throw Error('Friend notification registration unavailable');}
    finally{pending=false;}
  }
  return {refresh,enable:()=>refresh(true),get failed(){return failed;}};
}
