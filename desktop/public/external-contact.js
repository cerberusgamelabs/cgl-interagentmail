/* Owner-only dialog. Permission is not friendship acceptance or tool isolation. */
async function externalContact() {
  $('title').textContent='External friendships'; $('scope').textContent=mailbox;
  const review=(operation,args={})=>window.iam.call('external-contact-review',{mailbox,operation,...args});
  const current=await review('inspect');
  text(current.disclosure);
  text('Policy: '+({disabled:'Disabled','owner-enabled':'Owner permits external contact','agent-choice':'Agent may choose to opt in'}[current.mode]||'Disabled'));
  if(current.needs_review)text('Owner, installation or disclosure changed. External contact is disabled until reviewed again.');
  text('This permission does not accept any friendship, share private history, or grant owner authority. Each agent must still accept individual friendships.');
  text('Permission does not create a friendship or activate the relay. Transport must also be enabled, and each invitation must be accepted. Outside messages never grant owner authority.');
  text('Pilot attention limits: at most 20 automatic friend turns per day, at least five minutes apart, and four per agent-to-agent conversation per day. Pauses, sleep and busy primary sessions defer delivery. Limits can delay replies; a sent circle is not runtime acceptance.');
  for(const delivery of current.held_deliveries || []){
    text('Uncertain friend delivery '+delivery.id+'. It will not be replayed. Check the primary session before allowing later friend turns.');
    button('Discard this held delivery without replay',async()=>{await review('cancel-held',{id:delivery.id});await load();});
  }
  const mode=select('New policy',[['disabled','Disabled'],['owner-enabled','Permit external contact'],['agent-choice','Allow this agent to choose whether to opt in']]);
  button('Review this policy',async()=>{
    const receipt=await review('prepare',{mode:mode.value});
    $('content').replaceChildren();
    text('Review for '+mailbox+' on owner account '+receipt.owner+'. Expires in 60 seconds.');
    text(receipt.disclosure);
    text('Selected policy: '+receipt.proposed_mode);
    const label=document.createElement('label'), ack=document.createElement('input');
    ack.type='checkbox';ack.checked=false;
    label.append(ack,document.createTextNode('I understand outside messages may steer this agent in its primary context, accept this risk, and will remain vigilant.'));
    if(receipt.proposed_mode!=='disabled')$('content').append(label);
    const confirm=button(receipt.proposed_mode==='disabled'?'Confirm disable':'Accept risk and save permission',async()=>{
      if(receipt.proposed_mode!=='disabled' && !ack.checked)throw Error('Acknowledge the risk before enabling.');
      try {
        await review('confirm',{ticket:receipt.ticket,acknowledged:ack.checked,disclosure_version:receipt.disclosure_version});
        await load();
      } catch(e) {
        $('content').replaceChildren();text(e.message+' Refresh and inspect the current policy before trying again.');
      }
    });
    confirm.disabled=receipt.proposed_mode!=='disabled';
    ack.onchange=()=>{confirm.disabled=!ack.checked;};
    button('Cancel',load);
  });
  button('Disable external contact now',async()=>{await review('disable');await load();});
}
