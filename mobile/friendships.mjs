import {friendVisibleReceipts} from './friend-visible.mjs';
import {prepareAttachment,addAttachment,uploadAttachments,manifest} from './uploads.mjs';
import {downloadDocument,validDocumentName} from './documents.mjs';
import {fitDraft} from './composer.mjs';
import {imagePreviews} from './image-grid.mjs';
// Separate from owner pairing: every call uses the account's friend API only.
export function createFriendships({dialog,getTransport,getState,save,document,native,onScope=()=>{},uuid=()=>crypto.randomUUID()}) {
  let generation=0,busy=false,current=null,queued=false,rosterRows=null,selfEnabled=false,messages=null,messageRows=[],older=null,typingLabel=null,typingTimer=null,helpOpen=false;
  const content=document.createElement('section'),notice=document.createElement('p');notice.setAttribute('role','status');
  const previews=imagePreviews({document});let imageView=null;
  function closeImage(){imageView?.remove();imageView=null;}
  document.addEventListener?.('visibilitychange',()=>{if(document.hidden){closeImage();previews.clear();}});
  const close=document.createElement('button');close.textContent='Close friends';
  content.className='friend-content';notice.className='friend-notice';
  const visible=friendVisibleReceipts({document,active:()=>dialog.open && Boolean(current) && !helpOpen && !imageView,send:ids=>request('read',{id:current.id,ids})});
  dialog.replaceChildren(close,notice,content);
  const text=(value,parent=content)=>{const p=document.createElement('p');p.textContent=value;parent.append(p);};
  const input=(label,multi=false,parent=content)=>{const l=document.createElement('label'),el=document.createElement(multi?'textarea':'input');l.textContent=label;el.maxLength=multi?500:80;l.append(el);parent.append(l);return el;};
  const button=(label,fn,parent=content)=>{const b=document.createElement('button');b.textContent=label;b.onclick=()=>run(fn);parent.append(b);return b;};
  async function run(fn){if(busy)return;busy=true;notice.textContent='';try{await fn();}catch{notice.textContent='Request unavailable or outcome uncertain. Your pending message is kept. Refresh before repeating a decision.';}finally{busy=false;if(queued){queued=false;refresh();}}}
  async function request(op,value={}){
    const t=getTransport(),g=generation;
    if(!dialog.open || document.hidden || !t?.session)throw Error('No active friend view');
    const result=await t.friends(op,value);
    if(g!==generation || t!==getTransport() || !t.session || !dialog.open || document.hidden)throw Error('View changed');
    return result;
  }
  async function home(){
    closeImage();previews.clear();
    visible.reset();
    helpOpen=false;dialog.className='';close.hidden=false;clearTimeout(typingTimer);
    current=null;messages=null;messageRows=[];const roster=await request('roster');
    if(!roster || !Array.isArray(roster.relationships) || roster.relationships.length>200)throw Error('Invalid roster');
    selfEnabled=Boolean(roster.self?.enabled);content.replaceChildren();text('Friends use separate conversations. Linking a desktop is not a friend invitation.');
    if(!roster.self?.enabled){
      text('Choose a public name to allow invitations. You will not appear in search unless you opt in separately.');
      const name=input('Your public name');
      button('Enable my friend profile',async()=>{await request('self',{name:name.value,enabled:true,discoverable:false});await home();});return;
    }
    text('Your friend name: '+roster.self.name);
    button('Create one-use invitation',async()=>{const value=await request('invite');
      if(!/^[A-Za-z0-9_-]{43}$/.test(value.code))throw Error('Invalid invite');
      const code=input('Share this friend invitation, not your owner linking code');code.maxLength=43;code.value=value.code;code.readOnly=true;
      text('Expires '+new Date(value.expires).toLocaleString()+'. It permits a request, not automatic friendship.');});
    const code=input('Friend invitation code');code.maxLength=43;
    const reason=input('Public reason for your request',true);
    button('Send friend request',async()=>{await request('request',{code:code.value.trim(),reason:reason.value});await home();});
    const query=input('Find people who opted into search');
    button('Search',async()=>{const results=await request('discover',{query:query.value});
      if(!Array.isArray(results)||results.length>20)throw Error('Invalid discovery');
      for(const p of results)button('Request '+p.name,async()=>{await request('request',{target:p.id,reason:reason.value});await home();});});
    button(roster.self.discoverable?'Hide me from search':'Opt into name search',async()=>{await request('self',{name:roster.self.name,enabled:true,discoverable:!roster.self.discoverable});await home();});
    button('Disable my friend profile',async()=>{
      const review=document.createElement('section');content.append(review);
      text('Disabling revokes your existing friendships and invitations. Enabling again will not restore them.',review);
      button('Confirm disable and revoke friendships',async()=>{await request('self',{name:roster.self.name,enabled:false,discoverable:false});await home();},review);
      button('Keep my profile enabled',async()=>review.remove(),review);
    });
    rosterRows=document.createElement('section');content.append(rosterRows);renderRelationships(roster);
    button('Refresh friends',home);
  }
  function renderRelationships(roster){
    if(!rosterRows)return;
    // Preserve a public decision explanation while event updates replace rows.
    const drafts=new Map([...rosterRows.querySelectorAll('textarea')].map(el=>[el.dataset.relationship,el.value]));
    rosterRows.replaceChildren();
    for(const r of roster.relationships){
      const parent=document.createElement('section');rosterRows.append(parent);
      text(r.peer.name+' — '+r.state,parent);
      if(r.reason)text('Public request: '+r.reason,parent);
      if(Number.isSafeInteger(r.unread) && r.unread>0)text(r.unread+' unread',parent);
      if(r.explanation)text('Public response: '+r.explanation,parent);
      if(r.state==='pending' && r.incoming){
        const explanation=input('Public acceptance/decline explanation',true,parent);explanation.dataset.relationship=r.id;explanation.value=drafts.get(r.id)||'';
        for(const action of ['accept','decline'])button(action==='accept'?'Accept':'Decline',async()=>{await request('decide',{id:r.id,revision:r.revision,action,explanation:explanation.value});await home();},parent);
      }
      if(r.state==='active')button('Chat with '+r.peer.name,()=>chat(r),parent);
      if(r.state==='active' || r.state==='pending')button('Revoke / cancel',async()=>{await request('decide',{id:r.id,revision:r.revision,action:'revoke'});await home();},parent);
      button(r.blocked?'Unblock (does not restore friendship)':'Block',async()=>{await request('decide',{id:r.id,revision:r.revision,action:r.blocked?'unblock':'block'});await home();},parent);
    }
  }
  async function chat(rel){
    visible.reset();
    const page=await request('history',{id:rel.id});current=rel;
    if(!Array.isArray(page.events) || page.events.length>50)throw Error('Invalid history');
    content.replaceChildren();dialog.className='friend-chat';close.hidden=true;helpOpen=false;
    const header=document.createElement('header');header.className='friend-header';content.append(header);
    const avatar=document.createElement('span');avatar.className='buddy-avatar';avatar.textContent=rel.peer.name.slice(0,1).toUpperCase();avatar.setAttribute('aria-hidden','true');header.append(avatar);
    const name=document.createElement('h1');name.textContent=rel.peer.name;header.append(name);
    button('Friends',home,header);
    const help=document.createElement('section');help.className='friend-help';help.hidden=true;
    const helpButton=button('Help',async()=>{
      helpOpen=!helpOpen;help.hidden=!helpOpen;helpButton.setAttribute('aria-expanded',String(helpOpen));
      visible.reset();if(!helpOpen)showMessages({events:[],typing_until:0},false);
    },header);helpButton.setAttribute('aria-expanded','false');
    content.append(help);
    text('Messages shown here are separate from owner conversations. A send receipt is not proof of reading.',help);
    text('Files are visible to the relay and your friend, not end-to-end encrypted. Original metadata is shared. Relay copies expire after 30 days; downloaded copies cannot be revoked.',help);
    button('Refresh messages',refreshCurrent,help);
    const helpClose=button('Close friends',hide,help);helpClose.onclick=()=>close.onclick();
    const olderButton=button('Load older messages',async()=>{if(!older)return;const page=await request('history',{id:rel.id,before:older});older=page.more?page.before:null;showMessages(page,true);olderButton.hidden=!older;});
    olderButton.className='friend-older';olderButton.hidden=!page.more;
    messages=document.createElement('section');messages.className='friend-history';content.append(messages);typingLabel=document.createElement('p');typingLabel.setAttribute('aria-live','polite');content.append(typingLabel);messageRows=[];older=page.more?page.before:null;
    typingLabel.className='friend-typing';
    showMessages(page,false);
    const composer=document.createElement('section');composer.className='friend-composer';content.append(composer);
    const files=document.createElement('section');files.className='friend-selected-files';composer.append(files);
    const row=document.createElement('div');row.className='compose-row';composer.append(row);
    const state=getState();state.friendDrafts ||= {};
    const draft=state.friendDrafts[rel.id] ||= {text:''};
    if(native?.pickAttachment){
      const add=button('+',async()=>{
        const g=generation,selected=await native.pickAttachment();if(selected.cancelled)return;
        if(g!==generation || current?.id!==rel.id || document.hidden || !dialog.open)throw Error('View changed');
        const file=await prepareAttachment(selected);
        if(g!==generation || current?.id!==rel.id || document.hidden || !dialog.open)throw Error('View changed');
        const old=draft.files;draft.files=addAttachment(old||[],file);
        try{await save();}catch(e){draft.files=old;throw e;}await chat(rel);
      },row);add.setAttribute('aria-label','Add photo or document');add.title='Add photo or document';
      for(const [index,file] of (draft.files||[]).entries())button('Remove '+file.name,async()=>{
        const old=draft.files;draft.files=old.filter((_,i)=>i!==index);try{await save();}catch(e){draft.files=old;throw e;}await chat(rel);
      },files);
    }
    const compose=document.createElement('textarea');compose.setAttribute('aria-label','Message');compose.placeholder='Say something…';compose.maxLength=2000;compose.value=draft.text;row.append(compose);
    fitDraft(compose);
    let lastTyping=0;
    compose.oninput=()=>{fitDraft(compose);draft.text=compose.value;save().catch(()=>{notice.textContent='Could not save draft. Keep this view open and copy it before leaving.';});
      if(Date.now()-lastTyping>=4000){lastTyping=Date.now();request('typing',{id:rel.id,active:Boolean(compose.value)}).catch(()=>{});}
    };
    if(draft.pending){
      text('A pending message exists. Retry uses its original ID and text; editing does not alter that pending message.',composer);
      button('Review dismissing pending reminder',async()=>{
        const review=document.createElement('section');composer.append(review);
        text('The original may already have arrived. Dismissing does not cancel it or send a replacement. Your draft and selected files are kept.',review);
        button('Dismiss reminder without resending',async()=>{
          const pending=draft.pending;delete draft.pending;
          try{await save();}catch(e){draft.pending=pending;throw e;}await chat(rel);
        },review);
      },composer);
    }
    button(draft.pending?'Retry original send':'Send',async()=>{
      if(!draft.pending)draft.pending={id:rel.id,request_id:uuid(),text:compose.value,...(draft.files?.length?{files:structuredClone(draft.files)}:{})};
      await save(); // Every attempt, including after a failed initial persistence.
      if(draft.pending.files?.length)await uploadAttachments(draft.pending.files,value=>request('attachment',{id:rel.id,...value}));
      const wire={...draft.pending};if(wire.files)wire.files=manifest(wire.files);
      const result=await request('send',wire);
      if(result.id!==draft.pending.request_id || result.state!=='sent')throw Error('Unconfirmed send');
      request('typing',{id:rel.id,active:false}).catch(()=>{});
      const pending=draft.pending,old=draft.text,oldFiles=draft.files;
      delete draft.pending;if(draft.text===pending.text)draft.text='';
      if(pending.files)draft.files=(draft.files||[]).filter(f=>!pending.files.some(p=>p.upload===f.upload));
      try{await save();}catch(error){draft.pending=pending;draft.text=old;draft.files=oldFiles;throw error;}
      await chat(rel);
    },row);
    messages.scrollTop=messages.scrollHeight;
  }
  function showMessages(page,prepend){
    previews.reset();
    if(!Array.isArray(page.events)||page.events.length>50 || page.events.some(e=>typeof e.text!=='string' || e.text.length>16000 || typeof e.id!=='string' || typeof e.own!=='boolean' || !Number.isSafeInteger(e.created)))throw Error('Invalid friend messages');
    const merged=new Map(messageRows.map(e=>[e.id,e]));for(const e of page.events)merged.set(e.id,e);
    messageRows=[...merged.values()].sort((a,b)=>a.created-b.created || a.id.localeCompare(b.id));
    const height=messages.scrollHeight,top=messages.scrollTop,follow=top+messages.clientHeight>=height-48;
    messages.replaceChildren();const incoming=[];
    for(const event of messageRows){
      const p=document.createElement('article');p.className=event.own?'owner':'resident';p.setAttribute('aria-label',event.own?'You':current.peer.name);
      const body=document.createElement('p');body.className='message-body';body.textContent=event.text;p.append(body);
      const meta=document.createElement('small');meta.className='message-meta';
      const stamp=document.createElement('time');stamp.dateTime=new Date(event.created).toISOString();stamp.textContent=new Date(event.created).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});meta.append(stamp);
      if(event.own){
        const symbols={sent:'',received:'\u2022',no_reply:'!',replied:'\u2713'};
        const labels={sent:'Sent',received:'Received; not proof of reading',no_reply:'Turn ended without reply',replied:'Turn ended with reply'};
        const status=Object.hasOwn(symbols,event.delivery)?event.delivery:'sent';
        const marker=document.createElement('span');marker.className='delivery-marker';marker.textContent=symbols[status];
        marker.title=labels[status];marker.setAttribute('aria-label',labels[status]);marker.setAttribute('role','img');meta.append(marker);
      }
      messages.append(p);
      if(native?.validateImage && Array.isArray(event.files) && event.files.length<=8){
        const conversation=current.id,g=generation,t=getTransport();
        const active=()=>g===generation && current?.id===conversation && t===getTransport() && Boolean(t?.session) && dialog.open;
        previews.render(p,event.files.map((file,index)=>({file,index})).filter(({file})=>file.kind==='image').map(({file,index})=>({
          key:JSON.stringify([g,conversation,event.id,index,file.sha256]),label:String(file.name),active,
          load:async()=>{
            const image=await downloadDocument(event.id,index,value=>request('attachment',{id:conversation,...value}),name=>typeof name==='string' && name.length<=120 && !/[\x00-\x1f\x7f<>:"/\\|?*]/.test(name) && /\.(png|jpe?g|webp)$/i.test(name));
            await native.validateImage({data:image.data});
            const ext=image.name.split('.').pop().toLowerCase();
            return {...image,mime:ext==='png'?'image/png':ext==='webp'?'image/webp':'image/jpeg'};
          },
          open:async image=>{
            if(!active() || document.hidden)return;closeImage();visible.reset();
            imageView=document.createElement('section');imageView.className='friend-image-view';
            const img=document.createElement('img');img.alt=String(file.name);img.src=`data:${image.mime};base64,${image.data}`;imageView.append(img);
            const done=document.createElement('button');done.textContent='Close image';done.onclick=()=>{closeImage();showMessages({events:[]},false);};imageView.append(done);content.append(imageView);
          }
        })));
      }
      if(native?.saveAttachment && Array.isArray(event.files) && event.files.length<=8){
        const conversation=current.id;
        for(const [index,file] of event.files.entries())button('Save '+String(file.name),async()=>{
          const validName=name=>validDocumentName(name) || typeof name==='string' && name.length<=120 && !/[\x00-\x1f\x7f<>:"/\\|?*]/.test(name) && /\.(png|jpe?g|webp)$/i.test(name);
          const g=generation;
          const downloaded=await downloadDocument(event.id,index,value=>request('attachment',{id:conversation,...value}),validName);
          if(g!==generation || current?.id!==conversation || document.hidden || !dialog.open)throw Error('View changed');
          await native.saveAttachment(downloaded);
        },p);
      }
      p.append(meta);
      if(!event.own && !event.read){p.dataset.friendMessage=event.id;incoming.push(p);}
    }
    if(prepend)messages.scrollTop=top+messages.scrollHeight-height;
    else if(follow)messages.scrollTop=messages.scrollHeight;else messages.scrollTop=top;
    visible.watch(incoming);
    clearTimeout(typingTimer);
    if(typingLabel){const remaining=Number.isSafeInteger(page.typing_until)?Math.min(10000,Math.max(0,page.typing_until-Date.now())):0;
      typingLabel.textContent=remaining?current.peer.name+' is typing…':'';
      if(remaining){const label=typingLabel;typingTimer=setTimeout(()=>{label.textContent='';},remaining);}
    }
  }
  async function refreshCurrent(){
    const roster=await request('roster');
    if(!Array.isArray(roster.relationships) || roster.relationships.length>200)throw Error('Invalid roster');
    if(!current){if(Boolean(roster.self?.enabled)!==selfEnabled)await home();else renderRelationships(roster);return;}
    const active=roster.relationships.find(r=>r.id===current.id && r.state==='active');
    if(!active){messages?.replaceChildren();messageRows=[];await home();notice.textContent='This friendship is no longer active.';return;}
    current=active;
    try{showMessages(await request('history',{id:current.id}),false);}
    catch(error){messages?.replaceChildren();messageRows=[];throw error;}
  }
  function refresh(){if(!dialog.open || document.hidden)return;if(busy){queued=true;return;}return run(refreshCurrent);}
  async function hide(){if(busy)return;await save();closeImage();previews.clear();visible.reset();generation++;current=null;queued=false;dialog.close();content.replaceChildren();messageRows=[];onScope();}
  close.onclick=()=>hide().catch(()=>{notice.textContent='Could not save. Keep the dialog open.';});
  dialog.addEventListener('cancel',e=>{e.preventDefault();close.onclick();});
  return {async open(){generation++;dialog.showModal();await run(home);onScope();},close:hide,refresh,
    clear(){closeImage();previews.clear();visible.reset();generation++;current=null;queued=false;dialog.close();content.replaceChildren();messageRows=[];onScope();}};
}
