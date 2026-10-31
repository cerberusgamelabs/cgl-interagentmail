import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {messageBytes,retryDelay,mergeEvents,confirmedPending,recoveryDue,pendingLabel} from '../conversation.mjs';
import {buddyKey,buddyName,updateBuddyName,rememberBuddy,selectBuddy,restoreAfterLogin} from '../buddies.mjs';
import {fitDraft} from '../composer.mjs';
import {imagePreviews} from '../image-grid.mjs';
import {parseInvitation,enrollConversations} from '../pairing.mjs';
import {packState,unpackState} from '../protected-state.mjs';
import {prepareAttachment,addAttachment,uploadAttachments,manifest} from '../uploads.mjs';
import {publicProfile,downloadAvatar} from '../profiles.mjs';
import {installEdgeBack} from '../edge-back.mjs';
const source=(await readFile(new URL('../app.mjs',import.meta.url),'utf8')).replace(/^import .*;\r?\n/gm,'');
async function harness({pending=null,sendError=false,failSave=0,lookup={found:true,state:'queued'},lookupError=false,restore=null,uploadError=false,home=false}={}){
  if(pending && pending.createdAt===undefined)pending={...pending,createdAt:Date.now()-8000};
  const elements=new Map(),timers=new Map(),intervals=new Map(),listeners={},writes=[],calls=[],queries=[];
  function element(){return {value:'',textContent:'',dataset:{},scrollTop:0,scrollHeight:600,clientHeight:200,children:[],addEventListener(name,fn){this[name]=fn;},append(...x){this.children.push(...x);},replaceChildren(...x){this.children=x.flatMap(v=>v.fragment?v.children:[v]);},scrollIntoView(){}};}
  const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
  const saved={origin:'https://example.test',installation:'node',conversation:'friend',grantId:'grant',keyFingerprint:'key',draft:'saved draft',pending,
    session:{accountId:'owner',deviceId:'phone',expiresAt:Date.now()+600000}};
  let saveCount=0;
  const native={setConversationBack:async()=>{},pickPhoto:async()=>({data:'/9j/AA==',sha256:'a'.repeat(64)}),getKey:async()=>({fingerprint:'key'}),loadState:async()=>({value:JSON.stringify(restore || saved)}),saveState:async({value})=>{if(++saveCount===failSave)throw Error('storage unavailable');writes.push(JSON.parse(value));},clearState:async()=>writes.push(null)};
  let page={events:[],after:[0,''],more:false};
  class Transport{restoreSession(s){this.session=s;}async requestPairing(){calls.push('pair');return {challenge:'fresh'};}async historyPage(...args){calls.push('read');queries.push(args[4]);return page;}async lookupSend(){calls.push('lookup');if(lookupError)throw Error('unknown');return lookup;}async sendText(...args){calls.push('send');this.lastSent=args;if(sendError)throw Error('offline');return {state:'queued'};}async logout(){this.session=null;}}
  const document={hidden:false,body:{classList:{toggle(){}}},getElementById:get,querySelectorAll:()=>[],createElement:element,createDocumentFragment:()=>({...element(),fragment:true}),documentElement:{scrollHeight:100,style:{setProperty(){}}},addEventListener:(n,f)=>listeners[n]=f};
  const context=vm.createContext({registerPlugin:()=>native,Capacitor:{isNativePlatform:()=>true},MessengerTransport:Transport,nativeFetch:()=>{},nativeSigner:()=>{},
    imagePreviews,installEdgeBack,publicProfile,downloadAvatar,packState,unpackState,prepareAttachment,addAttachment,uploadAttachments,manifest,parseInvitation,enrollConversations,fitDraft,messageBytes,retryDelay,mergeEvents,confirmedPending,recoveryDue,pendingLabel,buddyKey,buddyName,updateBuddyName,rememberBuddy,selectBuddy,restoreAfterLogin,document,window:{innerHeight:100,scrollY:0,scrollTo(x,y){this.scrollY=y;},addEventListener(n,f){listeners[n]=f;}},
    uploadImage:async image=>{calls.push('upload');if(uploadError)throw Error('upload interrupted');},downloadImage:async()=>{},downloadText:async()=>'<literal full message>',downloadDocument:async()=>({name:'notes.txt',data:'YWJj'}),
    IAMMessageTime:{format:()=> 'Today - 1:00 PM',refresh(){}},IAMReadVisible:{tracker:()=>async()=>{}},setInterval:(fn,ms)=>{intervals.set(ms,fn);return ms;},
    IAMMarkdown:{render(el,text){el.textContent=text;}},TextEncoder,Date,JSON,crypto:{randomUUID:()=> '00000000-0000-4000-8000-000000000000'},confirm:()=>true,
    setTimeout:(fn,ms)=>{const id=timers.size+1;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id)});
  vm.runInContext(source,context);
  const settle=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};await settle();
  if(!home)vm.runInContext("screen='chat';render();schedule()",context);
  return {get,timers,intervals,listeners,writes,calls,queries,document,context,settle,setPage:p=>page=p,read:()=>vm.runInContext('run(loadHistory)',context)};
}

test('message time and delivery marker follow the body in a footer',async()=>{
  const h=await harness();
  vm.runInContext("events=[{id:'out',sender:'owner',body:'Hello',created:123,delivery:'replied'},{id:'in',sender:'resident',body:'Hi',created:124}];drawHistory()",h.context);
  const [out,incoming]=h.get('messages').children;
  assert.equal(out.children[0].textContent,'Hello');
  const footer=out.children.at(-1);
  assert.equal(footer.className,'message-meta');
  assert.equal(footer.children[0].textContent,'Today - 1:00 PM');
  assert.equal(footer.children[1].className,'delivery-marker');
  assert.equal(incoming.children.at(-1).className,'message-meta');
  assert.equal(incoming.dataset.readEvent,'in');
});

test('expired login retains encrypted state without exposing contacts before authentication',async()=>{
  const restore={origin:'https://example.test',keyFingerprint:'key',session:{accountId:'owner',homeServer:'relay',deviceId:'old',expiresAt:1},buddies:[{conversation:'private',draft:'keep'}]};
  const h=await harness({restore,home:true});
  assert.equal(vm.runInContext('state',h.context),null);
  assert.equal(vm.runInContext('expiredState.buddies[0].draft',h.context),'keep');
  assert.deepEqual(h.writes,[]);assert.deepEqual(h.calls,[]);
  assert.match(h.get('status').textContent,/expired/);
});

test('same-account reauthentication restores contacts without old-device grants',async()=>{
  const restore={origin:'https://example.test',keyFingerprint:'key',session:{accountId:'owner',homeServer:'relay',deviceId:'old',expiresAt:1},buddies:[{installation:'node',conversation:'friend',grantId:'old-grant',draft:'keep',pending:{id:'original'}}]};
  const h=await harness({restore,home:true});
  vm.runInContext("MessengerTransport.prototype.login=async function(){this.origin='https://example.test';this.session={accountId:'owner',homeServer:'relay',deviceId:'new',expiresAt:Date.now()+60000};}",h.context);
  h.get('loginForm').onsubmit({preventDefault(){}});await h.settle();
  assert.equal(vm.runInContext('state.buddies[0].draft',h.context),'keep');
  assert.equal(vm.runInContext('state.buddies[0].pending.id',h.context),'original');
  assert.equal(vm.runInContext('state.buddies[0].grantId',h.context),undefined);
  assert.equal(h.writes.includes(null),false);assert.deepEqual(h.calls,[]);
});

test('failed reauthentication does not erase saved contacts or display them',async()=>{
  const restore={origin:'https://example.test',keyFingerprint:'key',session:{accountId:'owner',homeServer:'relay',deviceId:'old',expiresAt:1},buddies:[{conversation:'private',draft:'keep'}]};
  const h=await harness({restore,home:true});
  vm.runInContext("MessengerTransport.prototype.login=async function(){throw Error('Sign-in rejected');}",h.context);
  h.get('loginForm').onsubmit({preventDefault(){}});await h.settle();
  assert.equal(vm.runInContext('state',h.context),null);
  assert.equal(vm.runInContext('expiredState.buddies[0].draft',h.context),'keep');
  assert.deepEqual(h.writes,[]);assert.deepEqual(h.calls,[]);
});

test('original selection is persisted before sending and removal preserves text',async()=>{
  const h=await harness();
  vm.runInContext("native.pickAttachment=async()=>({kind:'document',name:'notes.txt',data:'YWJj'})",h.context);
  await h.get('pickFile').onclick();
  assert.equal(vm.runInContext('state.draftFiles[0].name',h.context),'notes.txt');
  assert.equal(h.calls.includes('send'),false);
  const stored=unpackState(JSON.stringify(h.writes.at(-1)));
  assert.equal(stored.draftFiles[0].data,'YWJj');
  h.get('draft').value='keep text';
  await h.get('selectedFiles').children[0].children[1].onclick();
  assert.equal(vm.runInContext('state.draftFiles.length',h.context),0);
  assert.equal(h.get('draft').value,'keep text');
});

test('failed attachment vault write rolls selection back without sending',async()=>{
  const h=await harness();
  vm.runInContext("native.pickAttachment=async()=>({kind:'document',name:'notes.txt',data:'YWJj'});native.saveState=async()=>{throw Error('storage full')}",h.context);
  await h.get('pickFile').onclick();
  assert.equal(vm.runInContext('state.draftFiles?.length || 0',h.context),0);
  assert.equal(h.calls.includes('send'),false);
  assert.match(h.get('status').textContent,/storage full/);
});

test('full message viewer is literal, preserves draft and Back returns to the chat',async()=>{
  const h=await harness();
  h.get('textViewer').showModal=function(){this.open=true;};h.get('textViewer').close=function(){this.open=false;};
  h.get('draft').value='keep draft';
  await vm.runInContext("run(()=>openFullText({id:'r:reply'}),{preserveComposer:true})",h.context);
  assert.equal(h.get('fullText').textContent,'<literal full message>');
  assert.equal(h.get('textViewer').open,true);assert.equal(h.get('draft').value,'keep draft');
  vm.runInContext("readTracker=()=>{throw Error('covered messages must not be read')};checkVisibleReads()",h.context);
  h.listeners.iamBack();assert.equal(h.get('textViewer').open,false);
  assert.equal(h.get('fullText').textContent,'');assert.equal(vm.runInContext('screen',h.context),'chat');
  assert.equal(h.calls.includes('send'),false);
});

test('Help preserves draft and blocks read acknowledgements while covering history',async()=>{
  const h=await harness();
  h.get('chatHelp').showModal=function(){this.open=true;};h.get('chatHelp').close=function(){this.open=false;};
  h.get('draft').value='draft remains';h.get('openHelp').onclick();
  vm.runInContext("readTracker=()=>{throw Error('must not acknowledge covered history')};checkVisibleReads()",h.context);
  assert.equal(h.get('chatHelp').open,true);h.listeners.iamBack();
  assert.equal(h.get('chatHelp').open,false);assert.equal(h.get('draft').value,'draft remains');
  assert.equal(h.get('backHome').hidden,true);assert.equal(h.get('connectionSettings').hidden,true);
});

test('buddy home refreshes the authorized roster together without fetching messages',async()=>{
  const h=await harness({home:true});
  vm.runInContext("state.buddies=[{installation:'node',conversation:'friend',grantId:'grant',grantExpires:Date.now()/1000+1000}];transport.syncEvents=async(...args)=>{globalThis.rosterArgs=args;return {roster:[{conversation:'friend',displayName:'Chosen name',unread:2,profile:{status:'Hello'}}]}}",h.context);
  await vm.runInContext('refreshRoster()',h.context);
  assert.equal(vm.runInContext('state.buddies[0].displayName',h.context),'Chosen name');
  assert.equal(vm.runInContext('rosterArgs[7][0].grant',h.context),'grant');
  assert.equal(h.calls.includes('read'),false);assert.equal(h.calls.includes('send'),false);
});

test('friend foreground uses its own stream without requiring an owner grant; background releases it',async()=>{
  const h=await harness({home:true});
  h.get('friendships').open=true;
  vm.runInContext("state.buddies=[];state.grantId=null;live={setScope(value){globalThis.selectedScope=value}};updateLive()",h.context);
  assert.equal(vm.runInContext('selectedScope.friends',h.context),true);
  assert.equal(vm.runInContext('selectedScope.grantId',h.context),undefined);
  h.document.hidden=true;vm.runInContext('updateLive()',h.context);
  assert.equal(vm.runInContext('selectedScope',h.context),null);
  assert.equal(h.calls.includes('read'),false);
});

test('document download asks native Save As and preserves draft without sending',async()=>{
  const h=await harness();h.get('draft').value='keep this';
  vm.runInContext("native.saveAttachment=async file=>{globalThis.exported=file;return {saved:true};}",h.context);
  h.setPage({events:[{id:'r:reply',sender:'resident',body:'file',created:1,documents:[{name:'notes.txt',size:3}]}],after:[1,'r:reply'],more:false});
  await h.read();
  const button=h.get('messages').children[0].children.find(el=>el.textContent?.startsWith('Download notes.txt'));
  assert.ok(button);await button.onclick();
  assert.equal(vm.runInContext('exported.name',h.context),'notes.txt');
  assert.equal(h.get('draft').value,'keep this');assert.equal(h.calls.includes('send'),false);
});

test('a full message that finishes after backgrounding is not displayed',async()=>{
  const h=await harness();
  const pending=vm.runInContext("downloadText=()=>new Promise(r=>globalThis.finishText=r);run(()=>openFullText({id:'r:reply'}))",h.context);
  h.document.hidden=true;vm.runInContext("finishText('private text')",h.context);await pending;
  assert.equal(h.get('fullText').textContent,'');assert.match(h.get('status').textContent,/view changed/);
});

test('stream status distinguishes live from fallback and hides outside chat',async()=>{
  const h=await harness();
  assert.equal(h.get('liveStatus').textContent,'Using periodic refresh');
  vm.runInContext("live={connected:true,phase:'live',setScope(){}};schedule()",h.context);
  assert.equal(h.get('liveStatus').textContent,'Live updates connected');
  assert.equal(h.timers.size,0);
  vm.runInContext("live.connected=false;live.phase='retrying';schedule()",h.context);
  assert.match(h.get('liveStatus').textContent,/reconnecting; using periodic refresh/);
  assert.equal(h.timers.size,1);
  vm.runInContext("screen='home';schedule()",h.context);
  assert.equal(h.get('liveStatus').hidden,true);assert.equal(h.timers.size,0);
});

test('synchronous connecting status cannot leave two fallback timers scheduled',async()=>{
  const h=await harness();
  vm.runInContext("live={connected:false,phase:'connecting',setScope(){if(!this.reported){this.reported=true;schedule();}}};schedule()",h.context);
  assert.equal(h.timers.size,1);
});

test('event during a fetch drains immediately without waiting for polling and preserves typing',async()=>{
  const h=await harness();
  vm.runInContext("live={connected:true,setScope(){}};transport.syncEvents=async()=>({unread:0,subscriptions:[]});transport.historyPage=()=>new Promise(r=>globalThis.finish=r);run(loadHistory)",h.context);
  h.get('draft').value='typing during event';
  vm.runInContext("queuedLive=true;transport.historyPage=async()=>{globalThis.refetched=true;return {events:[],after:[1,'x'],more:false}};finish({events:[],after:[0,''],more:false})",h.context);
  await h.settle();
  assert.equal(vm.runInContext('refetched',h.context),true);
  assert.equal(h.get('draft').value,'typing during event');
  assert.equal(h.timers.size,0);assert.equal(h.calls.includes('send'),false);
});

test('existing buddy learns its chosen name during sync without re-pairing',async()=>{
  const h=await harness({home:true});
  await vm.runInContext("transport.syncEvents=async()=>({unread:0,subscriptions:[],displayName:'Sophie Vale'}); syncUnread(state.buddies[0])",h.context);
  assert.equal(h.writes.at(-1).buddies[0].displayName,'Sophie Vale');
  assert.equal(h.writes.at(-1).buddies[0].conversation,'friend');
  assert.equal(h.writes.at(-1).buddies[0].grantId,'grant');
  assert.equal(h.writes.at(-1).draft,'saved draft');assert.equal(h.calls.includes('pair'),false);
});

test('restored login opens buddy home without loading or marking chat read',async()=>{
  const h=await harness({home:true});
  assert.equal(h.get('buddyPanel').hidden,false);assert.equal(h.get('messages').hidden,true);
  assert.equal(h.get('composer').hidden,true);assert.equal(h.timers.size,0);
  h.document.hidden=false;h.listeners.visibilitychange();await h.read();
  assert.equal(h.calls.length,0);
  vm.runInContext("readTracker=()=>{throw Error('must not read')};checkVisibleReads()",h.context);
  assert.equal(h.get('buddies').children.length,1);
  h.get('buddies').children[0].onclick();await h.settle();
  assert.equal(h.get('messages').hidden,false);assert.equal(h.get('buddyPanel').hidden,true);
});

test('approved account pairing reaches buddy home and preserves current pending state',async()=>{
  const h=await harness({home:true});
  vm.runInContext("state.accountPairing={installation:'node',challenge:'test'};transport.pairingStatus=async()=>({state:'approved',grantId:'new',expiresAt:9999999999,conversations:['friend','second']})",h.context);
  h.get('checkAccountPairing').onclick();await h.settle();
  assert.equal(h.get('buddies').children.length,2);assert.equal(h.get('messages').hidden,true);
  assert.equal(h.writes.at(-1).draft,'saved draft');assert.equal(h.writes.at(-1).grantId,'new');
  assert.equal(h.writes.at(-1).accountPairing,undefined);
});

test('live connected chat disables routine history polling but retains recovery and catch-up',async()=>{
  const h=await harness();
  vm.runInContext('live={connected:true,setScope(){},stop(){}};schedule()',h.context);
  assert.equal(h.timers.size,0);
  vm.runInContext('catchingUp=true;schedule()',h.context);assert.equal(h.timers.size,1);
  vm.runInContext('catchingUp=false;live.connected=false;schedule()',h.context);assert.equal(h.timers.size,1);
  h.get('backHome').onclick();await h.settle();assert.equal(h.timers.size,0);
});

test('onboarding form switches fields and clears secrets without changing saved chats',async()=>{
  const h=await harness({home:true});
  h.get('accountPassword').value='secret';h.get('accountCode').value='code';
  h.get('accountAction').value='forgot';h.get('accountAction').onchange();
  assert.equal(h.get('accountEmail').required,true);assert.equal(h.get('accountCodeField').hidden,true);
  assert.equal(h.get('accountPasswordField').hidden,true);assert.equal(h.get('accountPassword').value,'');
  h.get('accountAction').value='reset';h.get('accountAction').onchange();
  assert.equal(h.get('accountEmailField').hidden,true);assert.equal(h.get('accountCode').required,true);
  assert.equal(h.get('accountPassword').required,true);assert.equal(h.get('accountCode').value,'');
  // An existing session cannot accidentally be replaced through recovery.
  h.get('accountForm').onsubmit({preventDefault(){}});await h.settle();
  assert.match(h.get('status').textContent,/Sign out/);assert.equal(h.writes.length,0);
});

test('onboarding submission does not persist codes/passwords or sign in implicitly',async()=>{
  const h=await harness({home:true});
  vm.runInContext("transport=null;MessengerTransport.prototype.accountAction=async function(action,body){globalThis.submitted={action,body};return {accepted:true};}",h.context);
  h.get('accountAction').value='register';h.get('origin').value='https://example.test';h.get('accountEmail').value='a@example.test';
  h.get('accountPassword').value=h.get('accountConfirm').value='long-password-only';
  h.get('accountForm').onsubmit({preventDefault(){}});await h.settle();
  assert.equal(h.context.submitted.action,'register');assert.equal(h.get('accountPassword').value,'');assert.equal(h.get('accountConfirm').value,'');
  assert.equal(h.writes.length,0);assert.match(h.get('accountStatus').textContent,/If eligible/);
  assert.equal(vm.runInContext('transport',h.context),null);
});

test('return home preserves draft and scroll, stops history polling and permits reopening same chat',async()=>{
  const h=await harness();h.get('draft').value='keep me';h.get('draft').input();
  h.get('messages').scrollTop=123;h.get('messages').scroll();
  h.get('backHome').onclick();await h.settle();
  assert.equal(h.get('composer').hidden,true);assert.equal(h.timers.size,0);
  assert.equal(h.writes.at(-1).draft,'keep me');
  h.get('messages').scrollTop=0; // A hidden browser scroller may lose its geometry.
  h.get('buddies').children[0].onclick();await h.settle();
  assert.equal(h.get('draft').value,'keep me');assert.equal(h.get('messages').scrollTop,123);
  assert.equal(h.get('composer').hidden,false);
});

test('failed draft persistence prevents leaving the conversation',async()=>{
  const h=await harness({failSave:1});h.get('backHome').onclick();await h.settle();
  assert.equal(vm.runInContext('screen',h.context),'chat');assert.equal(h.get('composer').hidden,false);
});

test('buddy search is local and shows an explicit no-results state',async()=>{
  const h=await harness({home:true});h.get('buddySearch').value='missing';h.get('buddySearch').input();
  assert.equal(h.get('buddies').children.length,0);assert.equal(h.get('emptyBuddies').hidden,false);
  h.get('buddySearch').value='FRIEND';h.get('buddySearch').input();
  assert.equal(h.get('buddies').children.length,1);assert.equal(h.calls.length,0);
});

test('opening a buddy immediately loads newest history without firing the poll timer',async()=>{
  const h=await harness({home:true});
  h.setPage({events:[{id:'latest',created:5,sender:'resident',body:'hello'}],after:[5,'latest'],more:true});
  h.get('buddies').children[0].onclick();await h.settle();
  assert.deepEqual(h.calls,['read']);assert.equal(h.queries[0].before,null);assert.equal(h.queries[0].limit,50);
  assert.equal(vm.runInContext('events[0].id',h.context),'latest');
  assert.equal(h.get('messages').scrollTop,h.get('messages').scrollHeight);
});

test('opening shows loading then empty state and permits typing while navigation is locked',async()=>{
  const h=await harness({home:true});
  vm.runInContext('transport.historyPage=()=>new Promise(resolve=>globalThis.finishOpen=resolve)',h.context);
  h.get('buddies').children[0].onclick();await h.settle();
  assert.match(h.get('status').textContent,/Loading conversation/);
  assert.equal(h.get('draft').disabled,false);assert.equal(h.get('backHome').disabled,true);
  h.get('draft').value='still typing';h.get('draft').input();
  h.listeners.iamBack();await h.settle();assert.equal(vm.runInContext('screen',h.context),'chat');
  vm.runInContext("finishOpen({events:[],after:[0,''],more:false})",h.context);await h.settle();
  assert.match(h.get('status').textContent,/No messages/);assert.equal(h.get('draft').value,'still typing');
  h.listeners.iamBack();await h.settle();assert.equal(vm.runInContext('screen',h.context),'home');
});

test('failed initial history retains chat and draft; scheduled retry still uses newest page',async()=>{
  const h=await harness({home:true});
  vm.runInContext("transport.historyPage=async()=>{throw Error('offline')}",h.context);
  h.get('buddies').children[0].onclick();await h.settle();
  assert.match(h.get('status').textContent,/offline/);assert.equal(h.get('draft').value,'saved draft');
  assert.equal(vm.runInContext('historyLoaded',h.context),false);
  vm.runInContext("transport.historyPage=async(...args)=>{globalThis.retryQuery=args[4];return {events:[],after:[0,''],more:false}}",h.context);
  await [...h.timers.values()].find(t=>t.ms>=8000).fn();
  assert.equal(vm.runInContext('retryQuery.before',h.context),null);
  assert.match(h.get('status').textContent,/No messages/);
});

test('cached chat renders immediately while its forward update is still pending',async()=>{
  const h=await harness();
  h.setPage({events:[{id:'cached',created:7,sender:'resident',body:'cached text'}],after:[7,'cached'],more:false});await h.read();
  h.get('messages').scrollTop=110;h.get('messages').scroll();
  h.get('backHome').onclick();await h.settle();
  vm.runInContext('transport.historyPage=(...args)=>{globalThis.openQuery=args[4];return new Promise(resolve=>globalThis.finishOpen=resolve)}',h.context);
  h.get('buddies').children[0].onclick();await h.settle();
  assert.equal(h.get('messages').hidden,false);assert.equal(h.get('messages').children[0].dataset.eventId,'cached');
  assert.equal(h.get('messages').scrollTop,110);assert.equal(vm.runInContext('openQuery.after[1]',h.context),'cached');
  vm.runInContext("finishOpen({events:[],after:[7,'cached'],more:false})",h.context);await h.settle();
  assert.equal(h.get('messages').scrollTop,110);
});

test('history entry from settings loads once and approved pairing loads immediately',async()=>{
  const h=await harness({home:true});h.get('history').onclick();await h.settle();
  assert.deepEqual(h.calls,['read']);
  vm.runInContext("screen='setup';state.pairing={challenge:'c'};transport.pairingStatus=async()=>({state:'approved',grantId:'new',expiresAt:9999999999})",h.context);
  h.get('check').onclick();await h.settle();assert.deepEqual(h.calls,['read','read']);
  assert.equal(vm.runInContext('screen',h.context),'chat');
});

test('native Back follows durable navigation and cannot interrupt an in-flight send',async()=>{
  const h=await harness();vm.runInContext('busy=true',h.context);
  h.listeners.iamBack();await h.settle();assert.equal(vm.runInContext('screen',h.context),'chat');
  vm.runInContext('busy=false',h.context);h.listeners.iamBack();await h.settle();
  assert.equal(vm.runInContext('screen',h.context),'home');assert.equal(h.writes.at(-1).draft,'saved draft');
});

test('settings are not a read surface and late read signing is rejected after leaving chat',async()=>{
  const h=await harness();h.get('connectionSettings').onclick();await h.settle();
  assert.equal(h.get('settings').hidden,false);assert.equal(h.get('messages').hidden,true);
  h.document.hasFocus=()=>true;
  vm.runInContext('transport.syncEvents=async(a,b,c,ids,signer)=>{await signer();return {unread:0}}',h.context);
  await assert.rejects(vm.runInContext("syncUnread(state,['event'])",h.context),/no longer active/);
});

test('notification tap opens only its exact enrolled conversation from home',async()=>{
  const h=await harness({home:true});
  vm.runInContext("native.notificationTarget=async()=>({id:'unknown'})",h.context);
  await h.intervals.get(1000)();assert.equal(h.calls.length,0);
  assert.equal(vm.runInContext('screen',h.context),'home');
  vm.runInContext("state.buddies[0].push={id:'known'};native.notificationTarget=async()=>({id:'known'})",h.context);
  await h.intervals.get(1000)();assert.deepEqual(h.calls,['read']);
  assert.equal(vm.runInContext('screen',h.context),'chat');assert.equal(h.get('composer').hidden,false);
});
test('restores draft, polls only visible app and merges duplicate history',async()=>{
  const h=await harness();assert.equal(h.get('draft').value,'saved draft');assert.equal([...h.timers.values()][0].ms,8000);
  h.document.hidden=true;h.listeners.visibilitychange();assert.equal(h.timers.size,0);
  h.setPage({events:[{id:'1',requestId:'r',created:1,sender:'resident',body:'hello'}],after:[1,'1'],more:false});
  h.document.hidden=false;h.listeners.visibilitychange();await h.settle();await h.read();
  assert.equal(vm.runInContext('events.length',h.context),1);assert.equal(h.calls.filter(x=>x==='send').length,0);
});

test('in-flight refresh preserves typing focus, selection and draft without scrolling',async()=>{
  const h=await harness();const draft=h.get('draft');let disabled=false,blurred=false,scrolls=0;
  Object.defineProperty(draft,'disabled',{get:()=>disabled,set:value=>{disabled=value;if(value)blurred=true;}});
  draft.value='typing';draft.selectionStart=2;draft.selectionEnd=4;h.document.activeElement=draft;
  h.get('composer').scrollIntoView=()=>scrolls++;
  vm.runInContext('transport.historyPage=()=>new Promise(resolve=>globalThis.finishRead=resolve)',h.context);
  const refresh=h.read();await h.settle();
  assert.equal(disabled,false);
  draft.value='still typing';draft.input();
  assert.equal(disabled,false);
  vm.runInContext("finishRead({events:[{id:'new',created:1,sender:'resident',body:'reply'}],after:[1,'new'],more:false})",h.context);
  await refresh;
  assert.equal(blurred,false);assert.equal(draft.value,'still typing');
  assert.equal(draft.selectionStart,2);assert.equal(draft.selectionEnd,4);assert.equal(scrolls,0);
});

test('history has independent scrolling and latest only moves that scroller',async()=>{
  const h=await harness();const list=h.get('messages');
  list.scrollTop=120;list.scroll();
  assert.equal(vm.runInContext('following',h.context),false);
  h.setPage({events:[{id:'one',created:1,sender:'resident',body:'reply'}],after:[1,'one'],more:false});
  await h.read();assert.equal(list.scrollTop,120);
  h.get('latest').onclick();assert.equal(list.scrollTop,list.scrollHeight);
  assert.equal(vm.runInContext('window.scrollY',h.context),0);
});

test('history preserves the visible message anchor when earlier rows change height',async()=>{
  const h=await harness();const list=h.get('messages');list.scrollTop=120;
  list.getBoundingClientRect=()=>({top:50});
  list.children=[{dataset:{eventId:'anchor'},getBoundingClientRect:()=>({top:40,bottom:140})}];
  const create=h.document.createElement;
  h.document.createElement=()=>{const row=create();row.getBoundingClientRect=()=>({top:80,bottom:180});return row;};
  vm.runInContext('following=false',h.context);
  h.setPage({events:[{id:'anchor',created:1,sender:'resident',body:'updated'}],after:[1,'anchor'],more:false});
  await h.read();assert.equal(list.scrollTop,160);
});
test('successful send clears draft only after durable receipt and unlocks composer',async()=>{
  const h=await harness();h.get('draft').value='hello';h.get('send').onclick();await h.settle();
  assert.deepEqual(h.calls,['send']);assert.equal(h.writes[0].pending.body,'hello');
  assert.equal(h.writes.at(-1).lastSend.status,'queued');assert.equal(h.get('draft').value,'');assert.equal(h.get('draft').disabled,false);
  assert.equal(h.get('messages').children.length,1);
  assert.equal(vm.runInContext('events[0].body',h.context),'hello');
  assert.equal(vm.runInContext('events[0].state',h.context),'queued');
});

test('loads newest chunk, polls forward only, and loads older on upward scroll',async()=>{
  const h=await harness();
  h.setPage({events:[{id:'new1',sender:'resident',body:'new1',created:100},{id:'new2',sender:'resident',body:'new2',created:101}],after:[100,'new1'],more:true});
  await h.read();
  assert.equal(h.queries[0].before,null);assert.equal(h.queries[0].limit,50);
  assert.equal(vm.runInContext('catchingUp',h.context),false);
  h.setPage({events:[],after:[101,'new2'],more:false});await h.read();
  assert.equal(h.queries[1].after[1],'new2');
  h.setPage({events:[{id:'old',sender:'resident',body:'old',created:50}],after:[50,'old'],more:false});
  h.get('messages').scrollTop=0;h.get('messages').scroll();await h.settle();
  assert.equal(h.queries[2].before[1],'new1');assert.equal(h.queries[2].limit,50);
  assert.equal(vm.runInContext('events.map(e=>e.id).join(",")',h.context),'old,new1,new2');
  assert.equal(vm.runInContext('cursor[1]',h.context),'new2');
  assert.equal(vm.runInContext('hasOlder',h.context),false);
});

test('receipt bubble is replaced by canonical history, not duplicated',async()=>{
  const h=await harness();h.get('draft').value='hello';h.get('send').onclick();await h.settle();
  const id='00000000-0000-4000-8000-000000000000:request';
  h.setPage({events:[{id,requestId:id.split(':')[0],sender:'owner',body:'hello',created:100,state:'accepted'}],after:[100,id],more:false});
  await h.read();assert.equal(vm.runInContext('events.length',h.context),1);
  assert.equal(vm.runInContext('events[0].created',h.context),100);
  assert.equal(vm.runInContext('events[0].state',h.context),'accepted');
});

test('top scroll during a refresh queues one older page rather than dropping it',async()=>{
  const h=await harness();
  h.setPage({events:[{id:'new',created:100,sender:'resident',body:'new'}],after:[100,'new'],more:true});await h.read();
  vm.runInContext('transport.historyPage=()=>new Promise(resolve=>globalThis.finishRead=resolve)',h.context);
  const refresh=h.read();await h.settle();
  h.get('messages').scrollTop=0;h.get('messages').scroll();h.get('messages').scroll();
  vm.runInContext("transport.historyPage=async(...args)=>{globalThis.olderQuery=args[4];return {events:[],after:[0,''],more:false}};finishRead({events:[],after:[100,'new'],more:false})",h.context);
  await refresh;
  assert.equal(vm.runInContext('olderQuery.before[1]',h.context),'new');
  assert.equal(vm.runInContext('queuedOlder',h.context),null);
});

test('moving away from top cancels deferred paging and underfilled pages can load older',async()=>{
  const h=await harness();vm.runInContext("historyLoaded=true;hasOlder=true;olderCursor=[1,'old'];busy=true",h.context);
  h.get('messages').scrollTop=0;h.get('messages').scroll();
  assert.notEqual(vm.runInContext('queuedOlder',h.context),null);
  h.get('messages').scrollTop=90;h.get('messages').scroll();
  assert.equal(vm.runInContext('queuedOlder',h.context),null);
  vm.runInContext('busy=false',h.context);
  h.get('messages').scrollHeight=100;h.get('messages').scrollTop=0;h.get('messages').scroll();await h.settle();
  assert.equal(h.queries[0].before[1],'old');
});

test('buddy rows separate identity, connection details and unread badge',async()=>{
  const h=await harness({home:true});vm.runInContext('unreadCounts.set(buddyKey(state),3);drawBuddies()',h.context);
  const row=h.get('buddies').children[0];
  assert.equal(row.children[0].textContent,'F');assert.equal(row.children[1].children[0].textContent,'friend');
  assert.equal(row.children[2].textContent,'3 unread');
  assert.match(row.children[1].children[1].textContent,/Paired/);
});

test('scrolls during an older-page request do not enqueue a second page',async()=>{
  const h=await harness();
  vm.runInContext("historyLoaded=true;hasOlder=true;olderCursor=[1,'old'];transport.historyPage=()=>new Promise(resolve=>globalThis.finishOlder=resolve)",h.context);
  h.get('messages').scrollTop=0;h.get('messages').scroll();await h.settle();
  h.get('messages').scroll();h.get('messages').scroll();
  assert.equal(vm.runInContext('queuedOlder',h.context),null);
  vm.runInContext("finishOlder({events:[],after:[0,''],more:true})",h.context);await h.settle();
  assert.equal(vm.runInContext('busy',h.context),false);
});
test('timeout holds original text; failed lookup never resends',async()=>{
  const h=await harness({sendError:true,lookupError:true});h.get('draft').value='hello';h.get('send').onclick();await h.settle();
  assert.equal(h.get('draft').disabled,true);assert.equal(h.writes.at(-1).pending.body,'hello');
  vm.runInContext('state.pending.createdAt-=8000',h.context);
  await h.read();assert.deepEqual(h.calls,['send','read','lookup']);
  h.setPage({events:[{id:'1',requestId:'00000000-0000-4000-8000-000000000000',sender:'owner',created:1,body:'hello',state:'accepted'}],after:[1,'1'],more:false});
  await h.read();assert.equal(h.get('draft').disabled,false);assert.equal(h.calls.filter(x=>x==='send').length,1);
});
test('sign out clears credentials and cancels scheduled reads',async()=>{
  const h=await harness();h.get('logout').onclick();await h.settle();
  assert.equal(h.writes.at(-1),null);assert.equal(h.timers.size,0);assert.equal(h.get('draft').value,'');
});
test('failed pre-send storage prevents any transmission',async()=>{
  const h=await harness({failSave:1});h.get('draft').value='hello';h.get('send').onclick();await h.settle();
  assert.equal(h.calls.length,0);assert.equal(h.get('draft').value,'hello');
});
test('failed receipt persistence preserves pending ID and body',async()=>{
  const h=await harness({failSave:2});h.get('draft').value='hello';h.get('send').onclick();await h.settle();
  assert.deepEqual(h.calls,['send']);assert.equal(h.writes.at(-1).pending.body,'hello');assert.equal(h.get('draft').disabled,true);
  assert.equal(h.get('messages').children.length,0);
});
test('reopening a pending send restores text and checks before doing anything',async()=>{
  const h=await harness({pending:{id:'original',body:'do not resend',status:'outcome uncertain'}});
  assert.equal(h.get('draft').value,'do not resend');await h.read();assert.deepEqual(h.calls,['read','lookup']);assert.equal(h.get('draft').disabled,false);
});
test('explicit absence retries exact ID and body once',async()=>{
  const h=await harness({pending:{id:'original',body:'same text',status:'outcome uncertain'},lookup:{found:false}});
  await h.read();assert.deepEqual(h.calls,['read','lookup','send']);
  assert.equal(vm.runInContext('transport.lastSent[3]',h.context),'original');
  assert.equal(vm.runInContext('transport.lastSent[4]',h.context),'same text');
});
test('failed recovery is bounded across polling and reopen',async()=>{
  const h=await harness({pending:{id:'original',body:'same text',status:'outcome uncertain'},lookup:{found:false},sendError:true});
  await h.read();await h.read();assert.deepEqual(h.calls,['read','lookup','send','read']);
  assert.equal(h.writes.at(-1).pending.recoveryAttempts,1);
});
test('existing held request never resubmits or unlocks composer',async()=>{
  const h=await harness({pending:{id:'original',body:'same text',status:'outcome uncertain'},lookup:{found:true,state:'held'}});
  await h.read();await h.read();assert.deepEqual(h.calls,['read','lookup','read']);assert.equal(h.get('draft').disabled,true);
});
test('ten exhausted checks leave Send as a manual retry of the original ID',async()=>{
  const h=await harness({pending:{id:'original',body:'same text',status:'outcome uncertain',recoveryAttempts:10,createdAt:Date.now()-600001},lookup:{found:false}});
  await h.read();assert.deepEqual(h.calls,['read']);
  assert.equal(h.get('send').textContent,'Retry send');assert.equal(h.get('send').disabled,false);
  assert.match(h.get('pending').textContent,/Delivery not confirmed/);
  h.get('send').onclick();await h.settle();
  assert.deepEqual(h.calls,['read','lookup','send']);
  assert.equal(vm.runInContext('transport.lastSent[3]',h.context),'original');
});
test('every app DOM reference exists in packaged HTML',async()=>{
  const html=await readFile(new URL('../index.html',import.meta.url),'utf8');
  for(const match of source.matchAll(/\$\('([^']+)'\)/g))assert.ok(html.includes(`id="${match[1]}"`),match[1]);
});

test('editing retains draft across reopen and history cannot discard it',async()=>{
  const h=await harness({pending:{id:'original',body:'original text',status:'outcome uncertain'}});
  h.get('clearPending').onclick();await h.settle();
  h.get('draft').value='edited text';h.get('draft').input();
  await vm.runInContext('save()',h.context);
  const reopened=await harness({restore:h.writes.at(-1)});
  assert.equal(reopened.get('draft').value,'edited text');
  reopened.setPage({events:[{id:'event',requestId:'original',sender:'owner',created:1,body:'original text',state:'queued'}],after:[1,'event'],more:false});
  await reopened.read();
  assert.equal(reopened.get('draft').value,'edited text');
  assert.deepEqual(reopened.calls,['read']);
});

test('changed photo requires a fresh message ID even with unchanged caption',async()=>{
  const h=await harness({pending:{id:'original',body:'Photo',status:'outcome uncertain',image:{sha256:'b'.repeat(64)}}});
  h.get('clearPending').onclick();await h.settle();
  h.get('pickImage').onclick();await h.settle();
  h.get('send').onclick();await h.settle();
  assert.deepEqual(h.calls,['upload','send']);
  assert.equal(vm.runInContext('transport.lastSent[3]',h.context),'00000000-0000-4000-8000-000000000000');
});

test('editing unchanged content retries original; changed content gets a new ID',async()=>{
  for(const changed of [false,true]){
    const h=await harness({pending:{id:'original',body:'same text',status:'outcome uncertain'},lookup:{found:false}});
    h.get('clearPending').onclick();await h.settle();
    assert.equal(h.get('draft').disabled,false);
    await h.read();assert.deepEqual(h.calls,['read']);
    if(changed)h.get('draft').value='changed text';
    h.get('send').onclick();await h.settle();
    assert.equal(vm.runInContext('transport.lastSent[3]',h.context),changed?'00000000-0000-4000-8000-000000000000':'original');
    assert.equal(vm.runInContext('transport.lastSent[4]',h.context),changed?'changed text':'same text');
  }
});

test('legacy pairing migrates and switching preserves exact pending send without cross-chat recovery',async()=>{
  const h=await harness({lookupError:true,pending:{id:'original',body:'private first chat',status:'outcome uncertain'}});
  assert.equal(h.get('buddies').children.length,1);
  await vm.runInContext("run(()=>switchBuddy({installation:'node',conversation:'second',grantId:'other-grant'}))",h.context);
  assert.equal(h.get('draft').value,'');assert.equal(h.get('draft').disabled,false);
  assert.deepEqual(h.calls,['read']);
  assert.equal(h.writes.at(-1).buddies.find(x=>x.conversation==='friend').pending.id,'original');
  await vm.runInContext("run(()=>switchBuddy(state.buddies.find(x=>x.conversation==='friend')))",h.context);
  assert.equal(h.get('draft').value,'private first chat');assert.equal(h.get('draft').disabled,true);
  assert.deepEqual(h.calls,['read','read','lookup']);
});

test('switching preserves independent history cursor, draft, scroll and view',async()=>{
  const h=await harness();
  h.setPage({events:[{id:'one',sender:'resident',body:'first only',created:1}],after:[1,'one'],more:false});await h.read();
  vm.runInContext("$('messages').scrollTop=145;following=false",h.context);
  h.setPage({events:[],after:[0,''],more:false});
  await vm.runInContext("run(()=>switchBuddy({installation:'node',conversation:'second',grantId:'other'}))",h.context);
  assert.equal(vm.runInContext('events.length',h.context),0);
  h.get('draft').value='second draft';h.get('draft').input();
  h.setPage({events:[],after:[1,'one'],more:false});
  await vm.runInContext("run(()=>switchBuddy(state.buddies.find(x=>x.conversation==='friend')))",h.context);
  assert.equal(h.get('draft').value,'saved draft');assert.equal(vm.runInContext('events[0].body',h.context),'first only');
  assert.equal(vm.runInContext('cursor[1]',h.context),'one');assert.equal(h.get('messages').scrollTop,145);
  assert.equal(h.writes.at(-1).buddies.find(x=>x.conversation==='second').draft,'second draft');
});

test('failed save blocks switching and retains active pending message',async()=>{
  const h=await harness({failSave:1,pending:{id:'original',body:'keep',status:'outcome uncertain'}});
  await vm.runInContext("run(()=>switchBuddy({}))",h.context);
  assert.equal(vm.runInContext('state.conversation',h.context),'friend');assert.equal(h.get('draft').value,'keep');
});

test('busy operation blocks linking; buddy-list link opens Settings without clearing owner state',async()=>{
  const h=await harness({home:true,pending:{id:'original',body:'keep',status:'outcome uncertain'}});
  vm.runInContext('busy=true',h.context);h.get('addConversation').onclick();await h.settle();
  assert.equal(vm.runInContext('state.conversation',h.context),'friend');
  vm.runInContext('busy=false',h.context);h.get('addConversation').onclick();await h.settle();
  assert.equal(vm.runInContext('state.grantId',h.context),'grant');assert.equal(h.get('composer').hidden,true);
  assert.equal(vm.runInContext('state.pending.id',h.context),'original');
  assert.equal(vm.runInContext('screen',h.context),'setup');
  assert.equal(h.get('settings').hidden,false);assert.equal(h.get('settings').open,true);
  assert.equal(h.get('addConversation').onclick,h.get('connectionSettings').onclick);
  assert.deepEqual(h.calls,[]);
  assert.equal(h.writes.at(-1).buddies.length,1);
});

test('buddy-list linking save failure stays home and preserves pending ID',async()=>{
  const h=await harness({home:true,failSave:1,pending:{id:'original',body:'keep',status:'outcome uncertain'}});
  await h.get('addConversation').onclick();await h.settle();
  assert.equal(vm.runInContext('screen',h.context),'home');
  assert.equal(vm.runInContext('state.pending.id',h.context),'original');
  assert.equal(vm.runInContext('state.grantId',h.context),'grant');
  assert.deepEqual(h.calls,[]);
});

test('multiple chats survive reopen; refreshing pairing retains pending ID but removes old grant',async()=>{
  const h=await harness({pending:{id:'original',body:'retained',status:'outcome uncertain',editing:true}});
  await vm.runInContext("run(()=>switchBuddy({installation:'node',conversation:'second',grantId:'other'}))",h.context);
  const reopened=await harness({restore:h.writes.at(-1)});
  assert.equal(reopened.get('buddies').children.length,2);
  await vm.runInContext("run(()=>switchBuddy(state.buddies.find(x=>x.conversation==='friend')))",reopened.context);
  reopened.get('renewPairing').onclick();await reopened.settle();
  assert.equal(reopened.writes.at(-1).grantId,undefined);
  assert.equal(reopened.writes.at(-1).pending.id,'original');assert.equal(reopened.writes.at(-1).pairing.challenge,'fresh');
  assert.equal(reopened.get('composer').hidden,true);
});

test('photo selection stays local; upload precedes send and receipt clears photo',async()=>{
  const h=await harness();h.get('pickImage').onclick();await h.settle();
  assert.deepEqual(h.calls,[]);assert.ok(h.writes.at(-1).draftImage);
  h.get('send').onclick();await h.settle();
  assert.deepEqual(h.calls,['upload','send']);assert.equal(h.writes.at(-1).draftImage,undefined);
  assert.equal(vm.runInContext('transport.lastSent[6].sha256',h.context),'a'.repeat(64));
});

test('interrupted image upload never sends; recovery retains image and checks exact ID first',async()=>{
  const h=await harness({uploadError:true,lookup:{found:false}});
  h.get('pickImage').onclick();await h.settle();h.get('send').onclick();await h.settle();
  assert.deepEqual(h.calls,['upload']);assert.ok(h.writes.at(-1).pending.image);
  vm.runInContext('state.pending.createdAt-=8000',h.context);
  await h.read();assert.deepEqual(h.calls,['upload','read','lookup','upload']);
  assert.equal(h.get('draft').disabled,true);
});

test('image draft is isolated when switching and retained on reopening',async()=>{
  const h=await harness();h.get('pickImage').onclick();await h.settle();
  await vm.runInContext("run(()=>switchBuddy({installation:'node',conversation:'second',grantId:'other'}))",h.context);
  assert.equal(h.get('photoPreview').hidden,true);
  const next=await harness({restore:h.writes.at(-1)});
  await vm.runInContext("run(()=>switchBuddy(state.buddies.find(x=>x.conversation==='friend')))",next.context);
  assert.equal(next.get('photoPreview').hidden,false);
});
