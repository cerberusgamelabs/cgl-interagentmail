import {registerPlugin,Capacitor} from './capacitor.mjs';
import {MessengerTransport} from './transport.mjs';
import {nativeFetch,nativeSigner} from './native-adapter.mjs';
import {messageBytes,retryDelay,mergeEvents,confirmedPending,recoveryDue,pendingLabel} from './conversation.mjs';
import {buddyKey,buddyName,updateBuddyName,rememberBuddy,selectBuddy,restoreAfterLogin} from './buddies.mjs';
import {uploadImage,downloadImage} from './images.mjs';
import {imagePreviews} from './image-grid.mjs';
const previews=imagePreviews({document});
import {fitDraft} from './composer.mjs';
import {parseInvitation,enrollConversations} from './pairing.mjs';
import {LiveConversation} from './live.mjs';
import {downloadText} from './full-text.mjs';
import {downloadDocument} from './documents.mjs';
import {prepareAttachment,addAttachment,uploadAttachments,manifest} from './uploads.mjs';
import {packState,unpackState} from './protected-state.mjs';
import {publicProfile,downloadAvatar} from './profiles.mjs';
import {installEdgeBack} from './edge-back.mjs';
import {createFriendships} from './friendships.mjs';
const native=registerPlugin('OwnerDevice');
const $=id=>document.getElementById(id);
let transport=null,state=null,key=null,busy=false,cursor=[0,''];
let expiredState=null;
let events=[],timer=null,failures=0,saveQueue=Promise.resolve(),draftTimer=null,following=true,catchingUp=false;
let sendStage='',readingHistory=false;
let historyLoaded=false,olderCursor=null,hasOlder=false;
let screen='home';
let queuedOlder=null,loadingOlder=false;
let live=null,queuedLive=false;
const profiles=new Map(),avatars=new Map();
let avatarBusy=false;
let friendUI=null;
$('openFriends').onclick=()=>run(async()=>{
  await leaveConversation('home');
  friendUI ||= createFriendships({dialog:$('friendships'),getTransport:()=>transport,getState:()=>state,save,document,onScope:updateLive,native});
  await friendUI.open();
});
function drawProfile(){
  const id=buddyKey(state),profile=profiles.get(id);
  $('chatAvatar').textContent=buddyName(state).slice(0,1) || '?';
  const image=avatars.get(id);
  $('chatPortrait').hidden=!image || image.sha256!==profile?.avatar?.sha256;
  $('chatPortrait').src=$('chatPortrait').hidden?'':image.uri;
  $('chatAvatar').hidden=!$('chatPortrait').hidden;
  const runtime=profile?.runtime,context=runtime?.context;
  $('chatPresence').textContent=runtime && !runtime.stale?runtime.availability:profile?.presence?`Chosen: ${profile.presence.replaceAll('_',' ')}`:'Presence unavailable';
  $('chatStatus').textContent=profile?.status || '';
  $('chatContext').textContent=context?.state==='compacting' && !runtime.stale?'Compacting…':Number.isInteger(context?.remainingPercent)?`Context ~${context.remainingPercent}% left${runtime.stale || context.stale?' (last known)':''}`:'Context unavailable';
}
async function refreshAvatar(buddy){
  const id=buddyKey(buddy),meta=profiles.get(id)?.avatar;
  if(avatarBusy || !meta || avatars.get(id)?.sha256===meta.sha256 || document.hidden || !['chat','home'].includes(screen) || screen==='chat' && buddyKey(state)!==id)return;
  const current=transport,view=screen;
  const check=()=>{if(current!==transport || !current?.session || screen!==view || document.hidden || !state.buddies.some(b=>buddyKey(b)===id && b.grantId===buddy.grantId))throw Error('Profile view changed');};
  avatarBusy=true;
  try{const image=await downloadAvatar(meta,async query=>{
      await new Promise(resolve=>setTimeout(resolve,650));check();
      const value=await current.attachment(buddy.installation,buddy.conversation,buddy.grantId,query,async(bytes,intent)=>{check();return nativeSigner(native)(bytes,intent);});check();return value;
    });
    if(!document.hidden && screen===view && current===transport && profiles.get(id)?.avatar?.sha256===meta.sha256){
      avatars.set(id,{sha256:meta.sha256,uri:`data:${image.mime};base64,${image.data}`});drawProfile();drawBuddies();
    }
  }catch{/* No broken photo or stale profile is published; retry on later sync. */}
  finally{avatarBusy=false;}
}
function drawLiveStatus(){
  const label=$('liveStatus');label.hidden=true;
  label.textContent=live?.connected?'Live updates connected':
    live?.phase==='connecting'?'Connecting live updates; periodic refresh available':
    live?.phase==='retrying'?'Live updates reconnecting; using periodic refresh':
    document.hidden?'Live updates paused in background':'Using periodic refresh';
  const notice=$('connectionNotice');
  if(!failures){notice.hidden=screen!=='chat' || live?.phase!=='retrying';notice.textContent='Connection interrupted. Reconnecting; your draft is kept.';}
}
function updateLive(){
  if(!live){drawLiveStatus();return;}
  const session=transport?.session;
  if($('friendships').open && session && !document.hidden){
    live.setScope({key:JSON.stringify(['friends',session.deviceId]),origin:transport.origin,token:session.token,transport,friends:true});return;
  }
  const groups=rosterGroups();
  if(screen==='home' && !groups.length && session && !document.hidden){
    live.setScope({key:JSON.stringify(['friends',session.deviceId]),origin:transport.origin,token:session.token,transport,friends:true});return;
  }
  if(screen==='home' && groups.length && session && !document.hidden){
    live.setScope({key:JSON.stringify(['roster',session.deviceId,groups]),origin:transport.origin,token:session.token,transport,groups});drawLiveStatus();return;
  }
  live.setScope(screen==='chat' && state?.grantId && session && !document.hidden?{
    key:JSON.stringify([session.deviceId,state.installation,state.conversation,state.grantId]),
    origin:transport.origin,token:session.token,transport,installation:state.installation,conversation:state.conversation,grantId:state.grantId}:null);
  drawLiveStatus();
}
function rosterGroups(){
  const groups=new Map();
  for(const buddy of state?.buddies || [])if(buddy.grantId && buddy.grantExpires*1000>Date.now()){
    if(!groups.has(buddy.installation))groups.set(buddy.installation,[]);
    groups.get(buddy.installation).push({conversation:buddy.conversation,grant:buddy.grantId});
  }
  return [...groups].map(([installation,roster])=>({installation,roster}));
}
async function refreshRoster(groups=rosterGroups(),listen=null,current=transport){
  let stream=null,renamed=false;
  for(const group of groups){
    if(document.hidden || screen!=='home' || current!==transport || !current?.session)return;
    const first=group.roster[0];
    const result=await current.syncEvents(group.installation,first.conversation,first.grant,[],nativeSigner(native),null,listen,group.roster);
    if(document.hidden || screen!=='home' || current!==transport || !current.session)return;
    for(const item of result.roster || []){
      const scope=group.roster.find(b=>b.conversation===item.conversation),buddy={installation:group.installation,conversation:item.conversation,grantId:scope.grant};
      const saved=state.buddies.find(b=>buddyKey(b)===buddyKey(buddy) && b.grantId===scope.grant);if(!saved)continue;
      profiles.set(buddyKey(buddy),item.profile);unreadCounts.set(buddyKey(buddy),item.unread);
      renamed=updateBuddyName(state,buddy,item.displayName) || renamed;
    }
    if(result.stream)stream={id:result.stream.id,expires_at:Math.min(stream?.expires_at || Infinity,result.stream.expires_at)};
    drawBuddies();
  }
  if(renamed)await save();
  setTimeout(async()=>{for(const buddy of state?.buddies || []){if(screen!=='home' || document.hidden || current!==transport)break;await refreshAvatar(buddy);}},0);
  return {stream};
}
async function refreshFriendSummary(){
  const current=transport,session=current?.session;if(!session || document.hidden || screen!=='home' || $('friendships').open)return;
  try{const roster=await current.friends('roster');
    if(current!==transport || session!==current.session || document.hidden || screen!=='home' || $('friendships').open)return;
    if(!Array.isArray(roster.relationships) || roster.relationships.length>200)return;
    const requests=roster.relationships.filter(r=>r.state==='pending' && r.incoming).length;
    const unread=roster.relationships.reduce((n,r)=>n+(Number.isSafeInteger(r.unread)&&r.unread>0?Math.min(2000,r.unread):0),0);
    $('openFriends').textContent='Friends and invitations'+(requests||unread?` (${requests} requests, ${unread} unread)`:'');
  }catch{/* Default-off or unavailable friendship service does not break owner chat. */}
}
async function refreshLive(){queuedLive=false;if(screen==='home'){await refreshRoster();await refreshFriendSummary();return;}await loadHistory();await syncUnread({...state});}
let openedImage=null;
function closeImage(){openedImage=null;if($('imageViewer').open)$('imageViewer').close();$('viewImage').src='';}
$('closeImage').onclick=closeImage;
$('imageViewer').addEventListener('close',()=>{openedImage=null;$('viewImage').src='';});
$('saveImage').onclick=()=>run(async()=>{
  if(!openedImage)throw new Error('Open the image again before saving');
  const result=await native.saveAttachment({name:'Image.'+({ 'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[openedImage.mime]),data:openedImage.data});
  message(result.cancelled?'Save cancelled.':'Image saved.');
},{preserveComposer:true});
function closeText(){if($('textViewer').open)$('textViewer').close();$('fullText').textContent='';}
$('closeText').onclick=closeText;
$('textViewer').addEventListener('close',()=>{$('fullText').textContent='';});
async function openFullText(item){
  const selected=buddyKey(state),grant=state.grantId,currentTransport=transport;
  const ensureView=()=>{if(document.hidden || screen!=='chat' || transport!==currentTransport ||
    buddyKey(state)!==selected || state.grantId!==grant)throw new Error('Message view changed; open the message again');};
  message('Loading full message securely…');
  const text=await downloadText(item.id,query=>{
    ensureView();
    return currentTransport.textChunk(state.installation,state.conversation,grant,query,async(bytes,intent)=>{
      ensureView();return nativeSigner(native)(bytes,intent);
    });
  });
  ensureView();$('fullText').textContent=text;$('textViewer').showModal();message('Full message opened.');
}
function transferImage(query){return transport.attachment(state.installation,state.conversation,state.grantId,query,nativeSigner(native));}
function scopedAttachment(){
  const selected=buddyKey(state),grant=state.grantId,current=transport,installation=state.installation,conversation=state.conversation;
  const check=()=>{if(document.hidden || screen!=='chat' || transport!==current || buddyKey(state)!==selected || state.grantId!==grant)throw new Error('Conversation changed; open the attachment again');};
  return async query=>{check();const value=await current.attachment(installation,conversation,grant,query,async(bytes,intent)=>{check();return nativeSigner(native)(bytes,intent);});check();return value;};
}
$('pickImage').onclick=()=>run(async()=>{
  if(state.draftFiles?.length)throw Error('Remove the original files before selecting a resized photo');
  const photo=await native.pickPhoto();if(photo.cancelled)return;
  const old=state.draftImage;
  state.draftImage={...photo,upload:crypto.randomUUID()};
  try{await save();}catch(error){state.draftImage=old;throw error;}
  message('Photo selected. A resized JPEG copy without original metadata will be shared only when you press Send.');
});
$('removeImage').onclick=()=>run(async()=>{const old=state.draftImage;delete state.draftImage;try{await save();}catch(error){state.draftImage=old;throw error;}});
$('pickFile').onclick=()=>run(async()=>{
  if(state.draftImage)throw Error('Remove the legacy photo before adding original files');
  const selected=await native.pickAttachment();if(selected.cancelled)return;
  const file=await prepareAttachment(selected),old=state.draftFiles;
  state.draftFiles=addAttachment(old || [],file);
  try{await save();}catch(error){state.draftFiles=old;throw error;}
  message('Original file selected. Its metadata will be shared when you press Send.');
});
async function uploadPending(pending){
  if(pending.image)await uploadImage(pending.image,transferImage);
  if(pending.files?.length)await uploadAttachments(pending.files,scopedAttachment(),(file,bytes)=>{
    sendStage=`uploading ${file.name}: ${Math.round(bytes/Math.max(1,file.size)*100)}%`;
    $('pending').textContent=sendStage;
  });
}
const views=new Map();
const unreadCounts=new Map();
let readTracker=null,readTrackerKey='',unreadCursor=0,unreadBusy=false,pushToken=null;
async function syncUnread(buddy,ids=[]) {
  const currentTransport=transport;
  const signer=async(bytes,intent)=>{
    if(ids.length && (screen!=='chat' || document.hidden || $('textViewer').open || $('chatHelp').open || !document.hasFocus() || buddyKey(state)!==buddyKey(buddy)))throw new Error('Read view no longer active');
    return nativeSigner(native)(bytes,intent);
  };
  const push=buddy.push && pushToken?{id:buddy.push.id,token:pushToken}:null;
  if(push)await native.registerNotificationScope({id:push.id,expires:Math.min(buddy.grantExpires,Math.floor(currentTransport.session.expiresAt/1000))});
  const result=await currentTransport.syncEvents(buddy.installation,buddy.conversation,buddy.grantId,ids,signer,push);
  if(transport!==currentTransport || !transport.session)return false;
  profiles.set(buddyKey(buddy),publicProfile(result.profile));
  if(screen==='chat' && buddyKey(state)===buddyKey(buddy)){
    const deliveries=new Map((result.deliveries || []).map(d=>[d.requestId,d.delivery]));
    events=events.map(item=>item.sender==='owner' && deliveries.has(item.requestId)?{...item,delivery:deliveries.get(item.requestId)}:item);drawHistory();
  }
  if(screen==='chat'){drawProfile();await refreshAvatar(buddy);}
  unreadCounts.set(buddyKey(buddy),result.unread);
  if(updateBuddyName(state,buddy,result.displayName)){
    await save();
    if(screen==='chat')$('pageTitle').textContent=buddyName(state);
  }
  for(const scope of result.subscriptions || [])await native.reconcileNotification({id:scope.id,revision:scope.revision,unread:scope.unread});
  drawBuddies();return true;
}
$('enableNotifications').onclick=()=>run(async()=>{
  if(!state?.grantId)throw new Error('Pair this conversation first.');
  const {token}=await native.enableNotifications();
  pushToken=token;
  state.push={id:state.push?.id || crypto.randomUUID(),token};await save();
  await native.registerNotificationScope({id:state.push.id,expires:Math.min(state.grantExpires,Math.floor(transport.session.expiresAt/1000))});
  await transport.syncEvents(state.installation,state.conversation,state.grantId,[],nativeSigner(native),state.push);
  message('Notification registration accepted. Alerts are generic; desktop reads clear them after synchronization.');
});
async function checkVisibleReads() {
  if(screen!=='chat' || !state?.grantId || document.hidden || busy || $('textViewer').open || $('chatHelp').open)return;
  const id=buddyKey(state)+'|'+state.grantId;
  if(id!==readTrackerKey){
    readTrackerKey=id;const buddy={...state};
    readTracker=globalThis.IAMReadVisible.tracker($('messages'),document,ids=>syncUnread(buddy,ids));
  }
  await readTracker();
}
async function checkUnreadCounts() {
  if(document.hidden || unreadBusy || busy || !transport?.session)return;
  if(screen==='home'){
    if(live?.connected)return;
    unreadBusy=true;try{await refreshRoster();}catch{/* Keep cached labels; subscription retries independently. */}finally{unreadBusy=false;}return;
  }
  const buddies=(state?.buddies || []).filter(b=>b.grantId && b.grantExpires*1000>Date.now());
  if(!buddies.length)return;
  unreadBusy=true;
  const buddy=buddies[unreadCursor++ % buddies.length];
  try{
    if(buddy.push){try{pushToken=(await native.refreshNotificationToken()).token;}catch{pushToken=null;}}
    await syncUnread(buddy);
  }catch{unreadCounts.delete(buddyKey(buddy));drawBuddies();}
  finally{unreadBusy=false;}
}
function drawBuddies(){
  $('buddies').replaceChildren();
  const buddies=state?.buddies || [],filter=$('buddySearch').value.trim().toLocaleLowerCase();
  let shown=0;
  for(const buddy of buddies){
    if(filter && !`${buddyName(buddy)} ${buddy.conversation} ${buddy.installation}`.toLocaleLowerCase().includes(filter))continue;
    shown++;
    const button=document.createElement('button');
    const expired=buddy.grantExpires && buddy.grantExpires*1000<=Date.now();
    button.className='buddy';
    const picture=avatars.get(buddyKey(buddy)),profile=profiles.get(buddyKey(buddy));
    const avatar=document.createElement(picture && picture.sha256===profile?.avatar?.sha256?'img':'span');avatar.className='buddy-avatar';
    if(picture && picture.sha256===profile?.avatar?.sha256){avatar.src=picture.uri;avatar.alt='';}
    else avatar.textContent=Array.from(buddyName(buddy) || '?')[0].toLocaleUpperCase();
    const info=document.createElement('span');info.className='buddy-info';
    const name=document.createElement('strong');name.textContent=buddyName(buddy);
    const detail=document.createElement('small');detail.textContent=`${buddy.installation} · ${expired?'Pairing expired':buddy.grantId?'Paired':'Pairing pending'}${buddy.pending?' · Pending send':''}`;
    info.append(name,detail);button.append(avatar,info);
    const count=unreadCounts.get(buddyKey(buddy));
    const badge=document.createElement('span');badge.className='buddy-unread';
    badge.textContent=count===undefined?'Unread not checked':count>0?`${count} unread`:'';
    button.append(badge);
    button.disabled=busy;
    button.onclick=()=>run(()=>switchBuddy(buddy));$('buddies').append(button);
  }
  $('buddySummary').textContent=`${buddies.length} saved conversation${buddies.length===1?'':'s'}`;
  $('emptyBuddies').hidden=shown>0;
  $('emptyBuddies').textContent=buddies.length?'No matching saved conversations.':'No linked conversations yet. Link your desktop to get started.';
  $('activeConversation').textContent=state?.conversation?`Conversation: ${buddyName(state)}`:'Choose or pair a conversation';
}
async function switchBuddy(buddy){
  queuedLive=false;live?.stop();
  queuedOlder=null;
  clearTimeout(draftTimer);
  await save(); // Do not leave a chat if its pending send/draft cannot be persisted.
  const previous=state,id=buddyKey(state);
  if(id && screen==='chat')views.set(id,{events,cursor,following,catchingUp,historyLoaded,olderCursor,hasOlder,scroll:$('messages').scrollTop,status:$('status').textContent});
  state=selectBuddy(state,buddy);
  try{await save();}catch(error){state=previous;throw error;}
  screen=state.grantId?'chat':'setup';readTrackerKey='';
  closeImage();
  const view=views.get(buddyKey(state));
  events=view?.events || [];cursor=view?.cursor || [0,''];following=view?.following ?? true;catchingUp=view?.catchingUp || false;
  historyLoaded=view?.historyLoaded || false;olderCursor=view?.olderCursor || null;hasOlder=view?.hasOlder || false;
  failures=0;sendStage='';$('draft').value=state.pending?.editing?(state.draft || ''):(state.pending?.body || state.draft || '');
  $('installation').value=state.installation || '';$('conversation').value=state.conversation || '';
  $('settings').open=false;
  $('buddyPanel').open=false;
  render();drawHistory();if(view)$('messages').scrollTop=view.scroll;
  if(state.grantId)await loadOpenedChat();
  else message('Enter the next conversation and request its own Desktop approval.');
}
async function loadOpenedChat(){
  // Navigation has persisted its draft and selected the correct scoped view.
  // Keep typing available during the initial read just as during refreshes.
  readingHistory=true;render();
  message(historyLoaded?'Checking for new messages...':'Loading conversation...');
  await loadHistory();
}
$('buddySearch').addEventListener('input',drawBuddies);
async function leaveConversation(next){
  queuedLive=false;live?.stop();
  queuedOlder=null;
  clearTimeout(draftTimer);
  await save();
  if(screen==='chat' && buddyKey(state))views.set(buddyKey(state),{events,cursor,following,catchingUp,historyLoaded,olderCursor,hasOlder,scroll:$('messages').scrollTop,status:$('status').textContent});
  closeImage();closeText();screen=next;readTrackerKey='';clearTimeout(timer);
  message(next==='home'?'Choose a conversation. Unread counts update without marking messages read.':'Connection settings. No conversation is marked read here.');
}
$('backHome').onclick=()=>run(()=>leaveConversation('home'));
$('openHelp').onclick=$('pendingNotice').onclick=()=>{$('chatHelp').showModal();};
$('closeHelp').onclick=()=>$('chatHelp').close();
$('helpHome').onclick=()=>run(async()=>{await leaveConversation('home');$('chatHelp').close();});
// Linking is navigation, not selection of an empty/legacy conversation.
// Keep the active grant, pending send and draft intact, just like Settings.
$('connectionSettings').onclick=$('addConversation').onclick=()=>run(()=>leaveConversation('setup'));
installEdgeBack(document,{enabled:()=>screen==='chat' && !busy && !$('chatHelp').open && !$('textViewer').open && !$('imageViewer').open,
  back:()=>run(()=>leaveConversation('home')),width:()=>window.innerWidth});
window.addEventListener('iamBack',()=>{
  if($('friendships').open){friendUI?.close();return;}
  if($('chatHelp').open){$('chatHelp').close();return;}
  if($('textViewer').open){closeText();return;}
  if($('imageViewer').open){closeImage();return;}
  if(screen!=='home')run(()=>leaveConversation('home'));
});
$('renewPairing').onclick=()=>run(async()=>{
  if(!state?.conversation || !confirm('Request fresh Desktop approval for this conversation? Existing messages and pending-send IDs will be kept.'))return;
  const pairing=await transport.requestPairing(state.installation,key.publicKey);
  state.pairing=pairing;delete state.grantId;delete state.grantExpires;await save();
  $('settings').open=true;message('Compare the new request and fingerprint on Desktop. Pending text is retained until this conversation is approved again.');
});
function schedule(){updateLive();clearTimeout(timer);if(screen==='chat' && state?.grantId && !document.hidden && (!live?.connected || catchingUp || state.pending || failures))timer=setTimeout(()=>run(loadHistory),catchingUp && !failures?4000:retryDelay(failures));}
function message(value,error=false){$('status').textContent=value;$('helpStatus').textContent=value;
  if(error && screen==='chat'){$('connectionNotice').textContent=value;$('connectionNotice').hidden=false;}
}
function render(){
  const chat=screen==='chat' && !!state?.grantId;
  document.body.classList.toggle('chat-ready',chat);
  drawBuddies();
  const signed=!!transport?.session;
  native.setConversationBack?.({enabled:signed && screen!=='home'})?.catch(()=>{});
  $('pageTitle').textContent=signed?(chat?buddyName(state):screen==='setup'?'Connection settings':'IAM Messenger'):'IAM Messenger';
  $('profileHeader').hidden=!chat;$('profileLines').hidden=!chat;$('chatContext').hidden=!chat;drawProfile();
  $('openHelp').hidden=!chat;
  $('backHome').hidden=!signed || screen!=='setup';$('backHome').disabled=busy;
  $('connectionSettings').hidden=!signed || screen!=='home';$('connectionSettings').disabled=busy;
  $('latest').hidden=!chat;
  $('messages').hidden=!chat;
  $('buddyPanel').hidden=!signed || screen!=='home';
  $('openFriends').hidden=!signed || screen!=='home';
  if(!signed)$('openFriends').textContent='Friends and invitations';
  $('settings').hidden=!signed || screen!=='setup';
  $('activeConversation').hidden=true;
  if(screen==='setup')$('settings').open=true;
  $('login').hidden=signed;$('paired').hidden=!signed;
  $('identity').textContent=signed?`Account: ${transport.session.accountId} · Device: ${transport.session.deviceId}`:'';
  $('fingerprint').textContent=key?.fingerprint || '';
  $('protection').textContent=key?.hardwareBacked?'Device reports hardware-backed key storage.':'Android Keystore; hardware backing not confirmed on this device.';
  $('challenge').textContent=state?.pairing?.challenge || '';
  $('accountPairingStatus').textContent=state?.accountPairing?`Awaiting approval for ${state.accountPairing.installation}. Invitation expires ${new Date(state.accountPairing.expiresAt*1000).toLocaleTimeString()}.`:'';
  $('installation').disabled=!!state?.pairing;$('conversation').disabled=!!state?.pairing;
  $('check').disabled=!state?.pairing || busy;$('history').disabled=!state?.grantId || busy;
  $('retryDevice').hidden=!!key;
  $('composer').hidden=!chat;
  fitDraft($('draft'));
  $('renewPairing').disabled=busy || !state?.conversation;
  $('send').textContent=state?.pending?(state.pending.editing?'Send / retry':'Retry send'):'Send';
  $('draft').disabled=(busy && !readingHistory) || (!!state?.pending && !state.pending.editing);
  $('pickImage').disabled=busy || $('draft').disabled;
  $('pickFile').disabled=busy || $('draft').disabled;
  $('removeImage').disabled=busy || $('draft').disabled;
  $('removeImage').hidden=!state?.draftImage;
  $('photoPreview').hidden=!state?.draftImage;
  $('selectedPhoto').hidden=!state?.draftImage;
  $('photoPreview').src=state?.draftImage?'data:image/jpeg;base64,'+state.draftImage.data:'';
  const fileRows=(state?.draftFiles || []).map((file,index)=>{
    const row=document.createElement('div'),label=document.createElement('span'),remove=document.createElement('button');
    label.textContent=`${file.name} (${file.size.toLocaleString()} bytes)`;remove.textContent='Remove';remove.disabled=busy || $('draft').disabled;
    remove.onclick=()=>run(async()=>{const old=state.draftFiles;state.draftFiles=old.filter((_,i)=>i!==index);try{await save();}catch(e){state.draftFiles=old;throw e;}});
    row.append(label,remove);return row;
  });$('selectedFiles').replaceChildren(...fileRows);$('selectedFiles').hidden=!fileRows.length;
  $('pending').textContent=state?.pending?`Send ${state.pending.id}: ${sendStage || pendingLabel(state.pending)}. Recovery always checks this exact ID first.`:state?.lastSend?`Last submission: ${state.lastSend.status}. Not a read receipt.`:'';
  $('pendingNotice').hidden=!state?.pending;
  $('pendingNotice').textContent=state?.pending?`Pending send: ${sendStage || pendingLabel(state.pending)} — details`:'';
  $('recover').hidden=true;
  $('clearPending').hidden=!state?.pending;
  $('clearPending').textContent='Edit pending message';
  $('count').textContent=`${messageBytes($('draft').value)} / 3000 bytes (including envelope)`;
  $('send').disabled=busy || (state?.pending?state.pending.status!=='outcome uncertain':
    (!$('draft').value.trim() && !state?.draftImage && !state?.draftFiles?.length) || messageBytes($('draft').value)>(state?.draftFiles?.length?1900:state?.draftImage?2800:3000));
  if(!key){document.querySelectorAll('button').forEach(b=>b.disabled=true);$('retryDevice').disabled=busy;}
}
async function save(){state.buddies=rememberBuddy(state.buddies || [],state);const value=packState({...state,keyFingerprint:key.fingerprint,session:transport.session});
  const operation=saveQueue.catch(()=>{}).then(()=>native.saveState({value}));saveQueue=operation;await operation;}
async function run(work,{preserveComposer=work===loadHistory || work===loadOlder}={}){if(busy)return;busy=true;readingHistory=preserveComposer;
  if(!preserveComposer)$('draft').disabled=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);
  try{await work();failures=0;}catch(e){failures++;message((e.message || 'Operation failed.')+(state?.pending?' Any pending text is retained; recovery must check its original ID.':''),true);}
  finally{busy=false;readingHistory=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);render();schedule();
    const queued=queuedOlder;queuedOlder=null;
    if(queued && !failures && screen==='chat' && !document.hidden && queued===buddyKey(state) && $('messages').scrollTop<=48 && hasOlder)await run(loadOlder);
      if(queuedLive && !failures && ['chat','home'].includes(screen) && !document.hidden)await run(refreshLive,{preserveComposer:true});
  }}
$('loginForm').onsubmit=e=>{e.preventDefault();run(async()=>{
  screen='home';unreadCounts.clear();pushToken=null;readTrackerKey='';historyLoaded=false;olderCursor=null;hasOlder=false;
  const origin=$('origin').value.trim();
  const retained=state?{...state,session:transport?.session || state.session}:expiredState;
  transport=new MessengerTransport(origin,{fetchImpl:nativeFetch(native,origin),timeoutMs:35000});
  friendUI?.clear();clearTimeout(draftTimer);await saveQueue.catch(()=>{});expiredState=retained;state=null;views.clear();profiles.clear();avatars.clear();$('messages').replaceChildren();cursor=[0,''];events=[];$('draft').value='';
  try{await transport.login($('email').value.trim(),$('password').value,'IAM Android');}finally{$('password').value='';}
  state=restoreAfterLogin(retained,transport.origin,transport.session,key.fingerprint);
  try{await save();expiredState=null;}catch(e){state=null;await transport.logout().catch(()=>{});throw e;}
  message(state.buddies?.length?'Signed in. Saved contacts and drafts restored. Link Desktop again to approve this login where needed.':'Signed in. Link your desktop from the buddy list to get started.');
});};
$('accountAction').onchange=()=>{
  const action=$('accountAction').value,code=['verify','reset'].includes(action),password=['register','reset'].includes(action);
  $('accountEmailField').hidden=code;$('accountEmail').required=!code;
  $('accountCodeField').hidden=!code;$('accountCode').required=code;
  $('accountPasswordField').hidden=$('accountConfirmField').hidden=!password;
  $('accountPassword').required=$('accountConfirm').required=password;
  $('accountPassword').value=$('accountConfirm').value=$('accountCode').value='';$('accountStatus').textContent='';
};
$('accountForm').onsubmit=e=>{e.preventDefault();run(async()=>{
  if(transport?.session)throw new Error('Sign out before changing account access.');
  const action=$('accountAction').value;
  if(['register','reset'].includes(action) && $('accountPassword').value!==$('accountConfirm').value)throw new Error('Passwords must match.');
  const origin=$('origin').value.trim(),account=new MessengerTransport(origin,{fetchImpl:nativeFetch(native,origin),timeoutMs:35000});
  $('accountStatus').textContent='Working…';
  try{
    await account.accountAction(action,{email:$('accountEmail').value.trim(),password:$('accountPassword').value,token:$('accountCode').value.trim()});
    $('accountStatus').textContent=action==='verify'?'Email verified. Sign in above; agent access needs separate approval.':action==='reset'?'Password reset. Sign in above with your new password.':'If eligible, an email has been sent. Check your inbox, including spam. Use its link or paste its code here.';
  }catch(error){$('accountStatus').textContent='Not confirmed. Check the status above before trying again.';throw error;}
  finally{$('accountPassword').value=$('accountConfirm').value=$('accountCode').value='';}
});};
$('retryDevice').onclick=()=>run(start);
async function claimCode(code){
  const invitation=parseInvitation(code,transport.session.homeServer);
  const pairing=await transport.claimInvitation(invitation.installation,invitation.challenge,key.publicKey);
  state.accountPairing=pairing;await save();$('pairingCode').value='';
  message('Now review the scanned phone on Desktop. Compare the fingerprint below, approve, then check account pairing approval here.');
}
$('claimPairing').onclick=()=>run(()=>claimCode($('pairingCode').value.trim()));
$('scanPairing').onclick=()=>run(async()=>{const value=await native.scanPairing();if(!value.cancelled)await claimCode(value.code);});
$('checkAccountPairing').onclick=()=>run(async()=>{
  if(!state.accountPairing)throw new Error('Scan or paste a Desktop invitation first.');
  const result=await transport.pairingStatus(state.accountPairing.installation,state.accountPairing.challenge);
  if(result.state!=='approved'){message('Waiting for Desktop approval.');return;}
  const previous=state;state=enrollConversations(state,state.accountPairing,result);
  try{await save();}catch(error){state=previous;throw error;}
  screen='home';readTrackerKey='';message('Approved conversations added to your buddy list. Existing drafts and pending send IDs were preserved.');
});
$('request').onclick=()=>run(async()=>{
  if(state.pairing)throw new Error('Pairing already requested. Check its status or sign out to start over.');
  const installation=$('installation').value.trim(),conversation=$('conversation').value.trim();
  if(!installation || !conversation)throw new Error('Installation and conversation are required.');
  if((state.buddies || []).length>=64)throw new Error('This pilot supports up to 64 saved conversations.');
  if((state.buddies || []).some(item=>buddyKey(item)===buddyKey({installation,conversation})))throw new Error('This conversation is already saved. Select it from the buddy list.');
  const pairing=await transport.requestPairing(installation,key.publicKey);
  state={...state,installation,conversation,pairing};delete state.displayName;await save();
  message('On Desktop, open this conversation’s Mobile pairing screen. Paste this request and compare the fingerprint before approving.');
});
$('check').onclick=()=>run(async()=>{
  const result=await transport.pairingStatus(state.installation,state.pairing.challenge);
  if(result.state==='approved'){state.grantId=result.grantId;state.grantExpires=result.expiresAt;await save();screen='chat';await loadOpenedChat();}
  else message('Waiting for your local Desktop confirmation.');
});
function drawHistory(){
  previews.reset();
  const list=$('messages'),oldTop=list.scrollTop;
  const edge=list.getBoundingClientRect?.().top || 0;
  const anchor=[...list.children].find(row=>row.getBoundingClientRect?.().bottom>edge);
  const anchorId=anchor?.dataset?.eventId,anchorOffset=anchor?anchor.getBoundingClientRect().top-edge:0;
  const fragment=document.createDocumentFragment();
  for(const item of events){const article=document.createElement('article');article.className=item.sender;article.dataset.eventId=item.id;
    if(item.sender==='resident')article.dataset.readEvent=item.id;
    const label=document.createElement('small');label.className='message-meta';
    const stamp=document.createElement('time');stamp.dataset.epoch=item.created;stamp.textContent=globalThis.IAMMessageTime.format(item.created);label.append(stamp);
    const body=document.createElement('div');globalThis.IAMMarkdown.render(body,item.body);article.append(body);
    if(item.truncated){const button=document.createElement('button');button.textContent='Read full message';
      button.onclick=()=>run(()=>openFullText(item),{preserveComposer:true});article.append(button);}
    if(item.sender==='owner'){
      const delivery=item.delivery || (item.state==='replied'?'replied':item.state==='accepted'?'received':'sent');
      const receipt=document.createElement('span');receipt.className='delivery-marker';
      const symbols={sent:'',received:'•',no_reply:'!',replied:'✓'};
      const labels={sent:'Sent',received:'Received by session; not a read receipt',no_reply:'Turn ended without reply',replied:'Turn ended with reply'};
      receipt.textContent=symbols[delivery];receipt.title=labels[delivery];receipt.setAttribute?.('aria-label',labels[delivery]);receipt.setAttribute?.('role','img');label.append(receipt);
      if(['failed','held'].includes(item.state)){const warning=document.createElement('small');warning.textContent='Delivery needs attention — see Help';article.append(warning);}
    }
    if(item.imageCount){
      const current=transport,selected=buddyKey(state),grant=state.grantId,transfer=scopedAttachment();
      const active=()=>screen==='chat' && transport===current && buddyKey(state)===selected && state.grantId===grant;
      previews.render(article,Array.from({length:Math.min(4,item.imageCount)},(_,index)=>({
        key:JSON.stringify([current.session?.accountId,selected,grant,item.id,index]),label:`Image ${index+1}`,active,
        load:async()=>{const image=await downloadImage(item.id,index,transfer);await native.validateImage({data:image.data});return image;},
        open:async image=>{if(!active())return;openedImage=image;$('viewImage').src=`data:${image.mime};base64,${image.data}`;$('imageViewer').showModal();}
      })));
    }
    for(let index=0;index<(item.documents?.length || 0);index++){
      const fileMeta=item.documents[index],button=document.createElement('button');
      button.textContent=`Download ${fileMeta.name} (${fileMeta.size.toLocaleString()} bytes)`;
      button.onclick=()=>run(async()=>{
        message('Downloading document securely…');
        const file=await downloadDocument(item.id,index,scopedAttachment());
        const result=await native.saveAttachment(file);
        message(result.cancelled?'Save cancelled.':'Document saved.');
      },{preserveComposer:true});article.append(button);
    }
    article.append(label);
    fragment.append(article);
  }
  $('messages').replaceChildren(fragment);
  if(following)list.scrollTop=list.scrollHeight;
  else {
    list.scrollTop=oldTop;
    const restored=[...list.children].find(row=>row.dataset?.eventId===anchorId);
    if(restored && anchorId)list.scrollTop+=restored.getBoundingClientRect().top-edge-anchorOffset;
  }
}
async function loadHistory(){
  if(screen!=='chat' || !state?.grantId)return;
  if(historyLoaded && !following && events.length>=1000){if(recoveryDue(state.pending))await recoverSend();return;}
  const initial=!historyLoaded;
  const page=await transport.historyPage(state.installation,state.conversation,state.grantId,nativeSigner(native),initial?{before:null,limit:50}:{after:cursor,limit:50});
  if(confirmedPending(state.pending,page.events))await acceptReceipt(page.events.find(item=>item.sender==='owner' && item.requestId===state.pending.id));
  const trimmed=events.length+page.events.length>1000;
  events=mergeEvents(initial?events.filter(item=>item.localReceipt):events,page.events);if(page.events.length)drawHistory();
  if(initial){historyLoaded=true;olderCursor=page.after;hasOlder=page.more;const newest=page.events.at(-1);cursor=newest?[newest.created,newest.id]:[0,''];catchingUp=false;}
  else {cursor=page.after;catchingUp=page.more;}
  if(trimmed && events.length){olderCursor=[events[0].created,events[0].id];hasOlder=true;}
  message(!events.length?'No messages in the permitted history yet.':hasOlder?'Connected. Scroll up to load older messages.':`Connected - checked ${new Date().toLocaleTimeString()}. Updates while this app is visible.`);
  if(recoveryDue(state.pending))await recoverSend();
}
async function loadOlder(){
  if(screen!=='chat')return;
  if(!historyLoaded || !hasOlder || !olderCursor)return;
  loadingOlder=true;
  try{
  const page=await transport.historyPage(state.installation,state.conversation,state.grantId,nativeSigner(native),{before:olderCursor,limit:50});
  const trimmed=events.length+page.events.length>1000;
  following=false;events=mergeEvents(events,page.events,1000,true);olderCursor=page.after;hasOlder=page.more;
  if(trimmed && events.length){const newest=events.at(-1);cursor=[newest.created,newest.id];catchingUp=true;}
  if(page.events.length)drawHistory();
  message(hasOlder?'Older messages loaded. Scroll up for more.':'Beginning of permitted history.');
  }finally{loadingOlder=false;}
}
$('history').onclick=()=>run(async()=>{if(screen!=='chat')await switchBuddy(state);else await loadOpenedChat();});
$('checkSent').onclick=()=>run(async()=>{historyLoaded=false;await loadHistory();},{preserveComposer:true});
async function acceptReceipt(result){
  if(['blocked','failed','held'].includes(result.state)){state.pending.status=result.state;await save();}
  else {const before={...state},pending=state.pending;state.lastSend={id:pending.id,status:result.state};delete state.pending;delete state.draftImage;delete state.draftFiles;state.draft='';
    try{await save();$('draft').value='';}catch(e){state=before;throw e;}
    const id=pending.id+':request',existing=events.find(item=>item.id===id);
    events=mergeEvents(events,[{id,requestId:pending.id,sender:'owner',body:pending.body,
      created:existing?.created ?? pending.createdAt/1000,state:result.state,truncated:false,imageCount:pending.image?1:(pending.files || []).filter(f=>f.kind==='image').length,
      documents:(pending.files || []).filter(f=>f.kind==='document').map(f=>({name:f.name,size:f.size})),localReceipt:true}]);
    drawHistory();}
}
async function recoverSend(){
  if(!state?.pending)return;
  const pending=state.pending;
  pending.recoveryAttempts=(pending.recoveryAttempts || 0)+1;
  pending.lastRecoveryAt=Date.now();
  await save(); // Bound automatic recovery across reopen, even if the lookup fails.
  sendStage='checking local delivery';render();
  try{
    const result=await transport.lookupSend(state.installation,state.conversation,state.grantId,pending.id,pending.body,nativeSigner(native),pending.image,pending.files);
    if(result.found){await acceptReceipt(result);message('Original message found locally: '+result.state+'. No resend performed.');return;}
    if(['blocked','failed','held'].includes(pending.status))throw new Error('Recovery conflict; this message needs review.');
    sendStage='not found locally; retrying the original ID';render();
    await uploadPending(pending);
    const receipt=await transport.sendText(state.installation,state.conversation,state.grantId,pending.id,pending.body,nativeSigner(native),pending.image,pending.files);
    await acceptReceipt(receipt);message('Recovery receipt: '+receipt.state+'. Original message ID preserved.');
  }finally{sendStage='';}
}
$('recover').onclick=()=>run(recoverSend);
$('send').onclick=()=>run(async()=>{
  const body=$('draft').value.trim()?$('draft').value:state.draftFiles?.length?'Attachments':state.draftImage?'Photo':'';
  if(!body.trim() || messageBytes(body)>(state.draftFiles?.length?1900:state.draftImage?2800:3000))
    throw new Error('Enter a message within the 3 KB pilot limit.');
  if(state.pending){
    if(!state.pending.editing || (body===state.pending.body && (state.draftImage?.sha256 || '')===(state.pending.image?.sha256 || '') &&
      JSON.stringify(manifest(state.draftFiles || []))===JSON.stringify(manifest(state.pending.files || [])))){
      await recoverSend();return;
    }
    if(!confirm('The original message may already have arrived. Send this changed text or photo as a new message with a new ID?'))return;
  }
  state.pending={id:crypto.randomUUID(),status:'outcome uncertain',body,createdAt:Date.now(),...(state.draftImage?{image:state.draftImage}:{}),...(state.draftFiles?.length?{files:state.draftFiles}:{})};
  sendStage='saving and submitting';
  render();
  try{
    await save(); // Durable before any submission; recovery checks the same ID.
    await uploadPending(state.pending);
    const result=await transport.sendText(state.installation,state.conversation,state.grantId,state.pending.id,body,nativeSigner(native),state.pending.image,state.pending.files);
    await acceptReceipt(result);
    message('Submission receipt: '+result.state+'. This is not a read receipt. Load history for the intentional reply.');
  }catch(e){await save();throw e;}finally{sendStage='';}
});
$('clearPending').onclick=()=>run(async()=>{
  const before={...state};state.pending={...state.pending,editing:true};state.draft=$('draft').value;
  try{await save();message('Automatic recovery paused while editing. Unchanged content retries the original ID; changed content requires confirmation for a new message.');}catch(e){state=before;throw e;}
});
$('logout').onclick=()=>run(async()=>{
  previews.clear();
  if(!confirm('Sign out and erase saved local drafts, selected files and pending-send recovery records on this phone? Messages already submitted are not cancelled.'))return;
  queuedLive=false;live?.stop();
  screen='home';
  unreadCounts.clear();profiles.clear();avatars.clear();pushToken=null;readTrackerKey='';historyLoaded=false;olderCursor=null;hasOlder=false;
  closeImage();closeText();
  // Delete durable credentials first: a failed server request must not resurrect login.
  friendUI?.clear();clearTimeout(draftTimer);await saveQueue.catch(()=>{});await native.clearState();state=null;expiredState=null;views.clear();$('messages').replaceChildren();cursor=[0,''];events=[];$('draft').value='';
  try{await transport.logout();message('Signed out; server token revoked.');}
  catch{message('Signed out locally. Server revocation could not be confirmed; revoke the device locally if needed.');}
});
async function start(){
  if(!Capacitor.isNativePlatform())throw new Error('This preview requires the Android app; no browser key-storage fallback exists.');
  key=await native.getKey();
  if(!live && native.openStream && native.addListener){
    live=new LiveConversation({native,subscribe:(scope,id)=>scope.friends?scope.transport.friends('listen',{id}):scope.groups?refreshRoster(scope.groups,id,scope.transport):scope.transport.syncEvents(scope.installation,scope.conversation,scope.grantId,[],nativeSigner(native),null,id),
      changed:scope=>{if(scope.friends){if($('friendships').open)friendUI?.refresh();else refreshFriendSummary();return;}if(['chat','home'].includes(screen) && !document.hidden && scope.key===live.scope?.key){queuedLive=true;if(!busy)run(refreshLive,{preserveComposer:true});}},status:()=>{drawLiveStatus();schedule();}});
    await live.initialize();
  }
  const saved=(await native.loadState()).value;
  if(saved){const value=unpackState(saved);
    if(value.session?.expiresAt>Date.now() && value.keyFingerprint===key.fingerprint){
      state=value;state.buddies=rememberBuddy(value.buddies || [],value);transport=new MessengerTransport(value.origin,{fetchImpl:nativeFetch(native,value.origin),timeoutMs:35000});transport.restoreSession(value.session);
      $('installation').value=value.installation || '';$('conversation').value=value.conversation || '';
      $('draft').value=value.pending?.editing?(value.draft || ''):(value.pending?.body || value.draft || '');
      $('settings').open=false;
      message('Saved login restored. Access is rechecked by the server on every request.');
    }else if(value.keyFingerprint===key.fingerprint){
      expiredState=value;$('origin').value=value.origin;
      message('Saved login expired. Sign in to the same account to recover saved contacts and drafts. Desktop access may need fresh approval.');
    }else{await native.clearState();message('Device key changed. Sign in and pair this login again.');}
  }else message('Development preview. Public mobile access must be enabled by your system owner before signing in.');
  render();
}
run(start);
document.addEventListener('visibilitychange',()=>{clearTimeout(timer);queuedLive=false;updateLive();if(screen==='chat' && !document.hidden && state?.grantId)run(loadHistory);});
$('draft').addEventListener('input',()=>{render();if(!state)return;state.draft=$('draft').value;clearTimeout(draftTimer);
  draftTimer=setTimeout(()=>save().catch(()=>message('Draft could not be saved. Keep the app open and copy your text before leaving.')),300);});
$('latest').onclick=()=>{if(screen!=='chat')return;following=true;$('messages').scrollTop=$('messages').scrollHeight;if(!busy){historyLoaded=false;run(loadHistory);}};
$('messages').addEventListener('scroll',()=>{const list=$('messages');following=list.scrollTop+list.clientHeight>=list.scrollHeight-48;
  if(list.scrollTop>48){queuedOlder=null;return;}
  if(screen==='chat' && historyLoaded && !loadingOlder && hasOlder && !document.hidden){
    following=false;
    if(busy)queuedOlder=buddyKey(state);else run(loadOlder);
  }
},{passive:true});
function resizeViewport(){
  fitDraft($('draft'));
  const height=window.visualViewport?.height || window.innerHeight;
  if(height>0)document.documentElement.style.setProperty('--visible-height',`${height}px`);
  if(following)$('messages').scrollTop=$('messages').scrollHeight;
}
window.visualViewport?.addEventListener('resize',resizeViewport);
window.addEventListener('resize',resizeViewport);
resizeViewport();
setInterval(()=>globalThis.IAMMessageTime.refresh(document),30000);
setInterval(checkVisibleReads,750);
setInterval(checkUnreadCounts,15000);
let checkingNotificationTarget=false;
setInterval(async()=>{
  if(document.hidden || busy || checkingNotificationTarget || !state?.buddies)return;
  checkingNotificationTarget=true;
  try{
    const target=await native.notificationTarget();
    if(typeof target.id!=='string' || !target.id)return;
    const buddy=state.buddies.find(b=>b.push?.id===target.id);
    if(buddy && !busy)await run(()=>switchBuddy(buddy));
  }catch{}finally{checkingNotificationTarget=false;}
},1000);
