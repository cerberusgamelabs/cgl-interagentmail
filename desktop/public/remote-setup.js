const byId=id=>document.getElementById(id);
const call=(operation,extra={})=>window.iam.call('remote-setup',{operation,...extra});
async function status(){const s=await call('status');byId('peer').replaceChildren(...s.peers.map(node=>{const option=document.createElement('option');option.value=node;option.textContent=node;return option;}));byId('status').textContent=(s.signedIn?'Signed in. ':'Sign in to your owning account. ')+(s.error || '')+` ${s.queued} retained outgoing drafts.`;}
async function action(operation){try{const result=await call(operation,{node:byId('peer').value});byId('status').textContent=result.note || result.state || (result.cancelled?'Cancelled.':'Signed out.');}catch(e){byId('status').textContent=e.message;}}
byId('login').onsubmit=async event=>{event.preventDefault();const password=byId('password').value;byId('password').value='';try{await call('login',{email:byId('email').value,password});await status();}catch(e){byId('status').textContent=e.message;}};
for(const operation of ['pair','check','logout'])byId(operation).onclick=()=>action(operation);
status().catch(e=>{byId('status').textContent=e.message;});
