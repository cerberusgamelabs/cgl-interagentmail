const query = new URLSearchParams(location.search), mailbox = query.get('mailbox'), kind = query.get('kind');
const $ = id => document.getElementById(id);
const call = (operation, args = {}) => window.iam.call('session-control', {mailbox, operation, ...args});
const labels = {status:'Session status', compact:'Compact context', stop:'Stop current turn', model:'Model and reasoning', plan:'Planning mode', permissions:'Permissions', more:'More session controls'};
$('title').textContent = labels[kind] || 'Session controls';
$('close').onclick = () => window.close();
let busy = false;
function text(value) { const p = document.createElement('p'); p.textContent = value; $('content').append(p); }
function show(value) { const p = document.createElement('pre'); p.textContent = JSON.stringify(value, null, 2); $('content').append(p); }
function button(label, action) { const b = document.createElement('button'); b.type = 'button'; b.textContent = label; b.onclick = async () => {if(busy)return; busy=true; b.disabled=true; try{await action();}catch(e){$('notice').textContent=e.message;}finally{busy=false;b.disabled=false;}}; $('content').append(b); return b; }
function select(label, entries) {const l=document.createElement('label'), s=document.createElement('select'); l.append(document.createTextNode(label)); for(const [value,name] of entries){const o=document.createElement('option');o.value=value;o.textContent=name;s.append(o);} l.append(s);$('content').append(l);return s;}
async function prepare(action, args = {}) {
  const receipt = await call('prepare', {kind:action, ...args});
  $('content').replaceChildren();
  text('Confirm this action on the exact session below. Confirmation expires in 60 seconds.');
  show({action:receipt.kind, thread:receipt.thread, parameters:receipt.parameters});
  if(action==='stop') text('Interrupts only this observed turn. Already-completed actions are not undone.');
  if(action==='compact') text('Summarizes earlier context; this changes what the session retains verbatim.');
  const confirm=button('Confirm',async()=>{
    confirm.disabled=true;
    try {const result=await call('confirm',{ticket:receipt.ticket}); $('content').replaceChildren();text(result.note);}
    catch(e){$('content').replaceChildren();text(e.message);}
    // Never automatically replay an uncertain operation.
  });
  button('Cancel',load);
}
async function load() {
  $('notice').textContent='Loading…'; $('content').replaceChildren();
  try {
    if(kind==='more') {
      $('scope').textContent=mailbox;
      for(const item of ['model','plan','permissions']) button(labels[item],()=>window.iam.call('control-window',{mailbox,kind:item}));
      button('Pair a mobile device',()=>window.iam.call('control-window',{mailbox,kind:'pairing'}));
      button('External friendships and risk',()=>window.iam.call('control-window',{mailbox,kind:'external-contact'}));
      button('Friend requests (agent decides)',()=>window.iam.call('control-window',{mailbox,kind:'friends'}));
      button('Copy latest desktop reply',async()=>{await window.iam.call('copy-published',{mailbox});$('notice').textContent='Published reply copied.';});
      $('notice').textContent='';return;
    }
    if(kind==='friends') {await desktopFriends();$('notice').textContent='';return;}
    if(kind==='external-contact') {await externalContact();$('notice').textContent='';return;}
    if(kind==='pairing') {
      if(mailbox==='buddies') {await accountPairing();$('notice').textContent='';return;}
      $('title').textContent='Mobile pairing'; $('scope').textContent=mailbox;
      text('Mobile pilot: availability is controlled by your system owner. Only approve requests from your own phone after comparing its key fingerprint. Pairing does not enable unfinished messaging features.');
      button('Review paired device grants',async()=>{
        const grants=await window.iam.call('pairing-review',{mailbox,operation:'grants'});
        $('content').replaceChildren();
        if(!grants.length)text('No active grants for this conversation.');
        for(const grant of grants) {
          show(grant);
          button('Review revocation for '+grant.principal.device_id,async()=>{
            const receipt=await window.iam.call('pairing-review',{mailbox,operation:'prepare-revoke',grant_id:grant.grant_id});
            $('content').replaceChildren();show(receipt);
            text('Revoke this entire grant for the listed conversations. Downloaded copies cannot be erased.');
            button('Confirm revocation',async()=>{
              const result=await window.iam.call('pairing-review',{mailbox,operation:'confirm-revoke',ticket:receipt.ticket});
              $('content').replaceChildren();text(result.note);
            });
            button('Cancel',load);
          });
        }
        button('Back',load);
      });
      const label=document.createElement('label'), input=document.createElement('input');
      label.textContent='Pairing request from your phone'; input.autocomplete='off'; input.maxLength=128; label.append(input); $('content').append(label);
      const history=select('Conversation history',[['new','Only messages from now'],['existing','Include existing Messenger history']]);
      const authority=select('Device authority',[['conversation','Conversation only (legacy prototype)'],['owner','Trusted owner phone — existing session permissions']]);
      button('Review exact account and phone',async()=>{
        const receipt=await window.iam.call('pairing-review',{mailbox,operation:'prepare',challenge:input.value.trim(),include_history:history.value==='existing',owner_access:authority.value==='owner'});
        $('content').replaceChildren();
        text(receipt.owner_access?'Compare the key fingerprint with your phone. This trusts that specific device as you, using the existing session permissions. A compromised phone could act with your authority.':'Conversation-only legacy grant; no owner authority.');
        show({home:receipt.identity.home_server,account:receipt.identity.account_id,device:receipt.identity.device_id,installation:receipt.installation,conversation:receipt.conversation,ownerAuthority:receipt.owner_access,keyFingerprint:receipt.key_fingerprint,existingHistory:receipt.include_history,expires:new Date(receipt.expires_at*1000).toLocaleString()});
        const confirm=button('Approve this device',async()=>{
          confirm.disabled=true;
          try{const result=await window.iam.call('pairing-review',{mailbox,operation:'confirm',ticket:receipt.ticket});$('content').replaceChildren();text(result.note);}
          catch(e){$('content').replaceChildren();text(e.message+' Refresh to review again; do not retry an uncertain approval.');}
        });
        button('Cancel',load);
      });
      $('notice').textContent='';return;
    }
    const data=await call('inspect',{details:kind==='status'});
    $('scope').textContent=`${mailbox} · ${data.state} · ${data.linkedMailboxes.length>1?'Shared with '+data.linkedMailboxes.join(', '):'Dedicated session'}`;
    if ((data.activeFlags || []).includes('waitingOnApproval')) text('This turn is waiting on approval. If no prompt appears in the buddy list, Messenger does not currently have a live actionable approval request. Stop interrupts the turn; it does not approve the action.');
    if(kind==='status') IAMStatus.render($('content'),data);
    else if(kind==='compact'||kind==='stop') {
      text(kind==='compact'?'Compact this existing session without creating a replacement. Available only while idle.':'Request interruption of the observed active turn. This does not stop IAM or the app-server.');
      const b=button('Review '+kind,()=>prepare(kind));
      b.disabled=kind==='compact'?data.state!=='idle':data.state!=='active'||!data.knownTurn;
      if(b.disabled) text(kind==='stop'?'No safely identified active turn. Refresh after Desktop observes a turn start.':'Wait until the session is idle, then refresh.');
    } else if(kind==='permissions') {
      show({registered:data.registeredPolicy, observed:data.observedSettings});
      text('Inspection and reapply only. Editing the registered policy remains in IAM setup so mail and desktop delivery cannot silently disagree. Observed settings may lag changes from another client.');
      const b=button('Review reapply registered policy',()=>prepare('permissions'));b.disabled=data.state!=='idle';
    } else if(kind==='model') {
      const models=await call('models');
      const model=select('Model',models.map(m=>[m.model,m.displayName||m.model]));
      if(models.some(m=>m.model===data.model))model.value=data.model;
      const effort=select('Reasoning effort',[]);
      function efforts(){effort.replaceChildren();const m=models.find(m=>m.model===model.value);for(const e of m?.supportedReasoningEfforts||[]){const o=document.createElement('option');o.value=e.reasoningEffort;o.textContent=e.reasoningEffort;effort.append(o);}effort.value=m?.defaultReasoningEffort||effort.value;}
      model.onchange=efforts;efforts();if(model.value===data.model&&[...effort.options].some(o=>o.value===data.effort))effort.value=data.effort;
      text('Updates this session’s subsequent turns; does not send a chat message. Shared mailboxes use the same session.');
      const b=button('Review model change',()=>prepare('model',{model:model.value,effort:effort.value}));b.disabled=data.state!=='idle';
    } else if(kind==='plan') {
      text('Choose how subsequent turns collaborate. Plan mode is a workflow setting, not a replacement for sandbox permissions.');
      const mode=select('Mode',[['default','Default'],['plan','Plan first']]);mode.value=data.observedSettings?.mode||'default';
      const b=button('Review mode change',()=>prepare('plan',{mode:mode.value}));b.disabled=data.state!=='idle';
    }
    $('notice').textContent='';
  } catch(e){$('notice').textContent=e.message;}
}
$('refresh').onclick=()=>{if(!busy)load();};
window.iam.call('theme',{}).then(value=>document.documentElement.dataset.theme=value).catch(()=>{});
load();

async function accountPairing() {
  $('title').textContent='Connect phone';$('scope').textContent='Account-based owner pairing';
  const pairing=(operation,args={})=>window.iam.call('pairing-review',{mailbox,operation,...args});
  const roster=await pairing('roster');
  text('Sign in on your phone to this account. Choose conversations, scan the QR, then compare the phone fingerprint before approving. New conversations are not added automatically.');
  show({home:roster.home,account:roster.account,installation:roster.installation});
  button('Review or revoke paired phones',async()=>{
    const grants=await pairing('grants');$('content').replaceChildren();
    if(!grants.length)text('No active phone grants.');
    for(const grant of grants){
      show(grant);
      button('Review revocation',async()=>{
        const receipt=await pairing('prepare-revoke',{grant_id:grant.grant_id});$('content').replaceChildren();show(receipt);
        text('Revoke this entire grant for every listed conversation. Already downloaded copies remain on the phone.');
        button('Confirm revocation',async()=>{const result=await pairing('confirm-revoke',{ticket:receipt.ticket});$('content').replaceChildren();text(result.note);});
        button('Cancel',load);
      });
    }
    button('Back',load);
  });
  const checks=[];
  button('Select all current conversations',async()=>{if(checks.length>64)throw new Error('Select at most 64 conversations.');for(const c of checks)c.checked=true;});
  const choices=document.createElement('div');choices.className='pairing-choices';$('content').append(choices);
  for(const name of roster.conversations){
    const label=document.createElement('label'),check=document.createElement('input');
    check.type='checkbox';check.value=name;label.append(check,document.createTextNode(name));choices.append(label);checks.push(check);
  }
  const history=select('History consent',[['new','Only messages from now'],['existing','Include existing Messenger history']]);
  button('Create five-minute invitation',async()=>{
    const invitation=await pairing('invite',{conversations:checks.filter(c=>c.checked).map(c=>c.value),include_history:history.value==='existing'});
    $('content').replaceChildren();
    text('Scan with Connect desktop in the signed-in phone app. Or paste the complete manual code. This is not an approval.');
    const image=document.createElement('img');image.src=invitation.qr;image.alt='Single-use phone pairing QR';image.width=280;image.height=280;$('content').append(image);
    const code=document.createElement('textarea');code.readOnly=true;code.value=invitation.code;code.setAttribute('aria-label','Manual pairing code');$('content').append(code);
    show({conversations:invitation.conversations,existingHistory:invitation.include_history,expires:new Date(invitation.expires_at*1000).toLocaleString()});
    button('Review scanned phone',async()=>{
      const receipt=await pairing('prepare-invite',{challenge:invitation.challenge});
      $('content').replaceChildren();
      text('Compare this fingerprint with your phone. This device can act as you with the existing agent session permissions. Approve only your own phone.');
      show({account:receipt.identity.account_id,device:receipt.identity.device_id,keyFingerprint:receipt.key_fingerprint,conversations:receipt.conversations,existingHistory:receipt.include_history,expires:new Date(receipt.expires_at*1000).toLocaleString()});
      button('Approve this account and phone',async()=>{
        const result=await pairing('confirm-invite',{ticket:receipt.ticket});$('content').replaceChildren();text(result.note);
      });
      button('Cancel invitation',async()=>{await pairing('cancel-invite',{challenge:invitation.challenge});await load();});
    });
    button('Cancel invitation',async()=>{await pairing('cancel-invite',{challenge:invitation.challenge});await load();});
  });
}
