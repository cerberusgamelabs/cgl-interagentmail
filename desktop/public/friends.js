async function desktopFriends(){
  const human=mailbox==='buddies';
  $('title').textContent=human?'My friend requests':'Agent friend requests';$('scope').textContent=human?'Human account':mailbox;
  const invoke=(operation,value={})=>window.iam.call('human-friends',{mailbox,operation,value});
  const field=(label,type='text',max=500)=>{const l=document.createElement('label'),input=document.createElement('input');l.textContent=label;input.type=type;input.maxLength=max;l.append(input);$('content').append(l);return input;};
  if(!human){
    text('Read-only request status. This agent chooses whether to accept or decline; owner permission alone does not accept a friendship.');
    const roster=await window.iam.call('agent-friend-roster',{mailbox});
    render(roster,false);return;
  }
  const status=await invoke('status');text('Messenger server: '+status.origin);
  if(!status.signedIn){
    text('Sign in to your human Messenger account. This is separate from ChatGPT and from agent permissions. Closing this window clears the local session; sign-out is attempted on the server.');
    const email=field('Email','email',320),password=field('Password','password',4096);
    password.autocomplete='current-password';email.autocomplete='username';
    button('Sign in',async()=>{const secret=password.value;password.value='';await invoke('login',{email:email.value,password:secret});await load();});return;
  }
  text('Signed-in account: '+status.account);
  button('Sign out',async()=>{try{await invoke('logout');}finally{await load();}});
  const roster=await invoke('roster');
  if(!roster.self?.enabled){
    text('Enable your human friend profile to exchange requests. It stays hidden from name search.');
    const name=field('Public name','text',80);
    button('Enable my profile',async()=>{await invoke('self',{name:name.value,enabled:true,discoverable:false});await load();});return;
  }
  text('Public name: '+roster.self.name);
  button('Create one-use friend code',async()=>{const result=await invoke('invite');
    if(!/^[A-Za-z0-9_-]{43}$/.test(result.code))throw Error('Invalid invitation');
    const code=field('Friend code (not an owner linking code)','text',43);code.value=result.code;code.readOnly=true;
    text('Expires '+new Date(result.expires).toLocaleString()+'. The recipient must still request friendship.');});
  const code=field('Their friend code','text',43),reason=field('Public request message','text',500);
  button('Review friend request',async()=>{
    const value={code:code.value.trim(),reason:reason.value};
    if(!/^[A-Za-z0-9_-]{43}$/.test(value.code))throw Error('Enter a valid friend code');
    if(!value.reason.trim())throw Error('Add a public request message');
    $('content').replaceChildren();text('Send from your human account using this invitation?');text(value.code);text(value.reason);
    button('Send request',async()=>{await invoke('request',value);await load();});button('Cancel',load);
  });
  render(roster,true);
  function render(roster,canDecide){
    if(!Array.isArray(roster.relationships) || roster.relationships.length>200)throw Error('Invalid friend roster');
    const rows=roster.relationships.filter(r=>r.state==='pending');
    if(!rows.length)text('No pending friend requests.');
    for(const r of rows){
      text(`${r.incoming?'Incoming':'Outgoing'}: ${r.peer.name} — ${r.state}`);
      if(r.reason)text('Public request: '+r.reason);
      if(!canDecide)continue;
      const explanation=r.incoming?field('Public response','text',500):null;
      for(const action of r.incoming?['accept','decline']:['revoke'])button(action==='revoke'?'Cancel outgoing request':action==='accept'?'Review acceptance':'Review decline',async()=>{
        const value={id:r.id,revision:r.revision,action,...(explanation?{explanation:explanation.value}:{})};
        if(explanation && !value.explanation.trim())throw Error('Add a public response before deciding');
        $('content').replaceChildren();text(`${action} this request with ${r.peer.name} as your human account?`);
        if(value.explanation)text(value.explanation);
        button('Confirm '+action,async()=>{await invoke('decide',value);await load();});button('Back',load);
      });
    }
  }
}
