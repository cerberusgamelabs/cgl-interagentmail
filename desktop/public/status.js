(function(scope) {
  const missing = 'Not reported';
  function permissionLabel(settings, registered) {
    const sandbox = settings?.sandboxPolicy?.type;
    const approval = settings?.approvalPolicy;
    if (!sandbox) return `${registered?.sandbox || missing} (IAM registered; live policy unavailable)`;
    const names = {dangerFullAccess:'Full Access', workspaceWrite:'Workspace', readOnly:'Read Only'};
    const name = names[sandbox] || sandbox;
    return approval === 'never' && sandbox === 'dangerFullAccess' ? name : `${name} · approvals: ${typeof approval === 'string' ? approval : 'custom / unreported'}`;
  }
  function percentage(value) {return typeof value === 'number' && Number.isFinite(value) ? Math.max(0,Math.min(100,value)) : null;}
  function render(root, data, doc = document) {
    root.replaceChildren();root.classList.add('status-content');
    function el(tag, className, text) {const node=doc.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
    const card=el('section','status-card'), hero=el('div','status-hero');
    hero.append(el('span','status-monogram',(data.name||data.mailbox||'?').slice(0,1)));
    const heading=el('div');heading.append(el('h2','',data.name||data.mailbox),el('p','status-caption','OpenAI Codex · IAM Messenger'));
    hero.append(heading,el('span','status-pill',data.state||'Unknown'));card.append(hero);
    const fields=el('dl','status-fields');
    function row(label,value,mono=false){fields.append(el('dt','',label),el('dd',mono?'status-mono':'',value===null||value===undefined||value===''?missing:String(value)));}
    row('Server',data.server,true);
    row('Model',data.model?`${data.model}${data.effort?' · '+data.effort+' reasoning':''}`:null);
    row('Provider',data.provider);
    row('Directory',data.directory,true);
    row('Permissions',permissionLabel(data.observedSettings,data.registeredPolicy));
    row('Instructions',Array.isArray(data.instructions)?(data.instructions.length?data.instructions.join('\n'):'None reported'):null,true);
    const account=data.account||{};
    row('Account',[account.email,account.planType?`(${account.planType})`:account.type].filter(Boolean).join(' '));
    row('Thread name',data.threadName);
    row('Mode',data.observedSettings?.mode ? (data.observedSettings.mode==='plan'?'Plan':'Default'):null);
    row('Session',data.thread,true);
    if(data.linkedMailboxes?.length>1)row('Shared with',data.linkedMailboxes.join(', '));
    card.append(fields);root.append(card);
    const usage=el('section','status-card status-usage');
    usage.append(el('h2','','Usage'));
    function meter(label,value,detail){
      const block=el('div','status-meter'), line=el('div','status-meter-label'), pct=percentage(value);
      line.append(el('span','',label),el('strong','',pct===null?missing:`${Math.round(pct)}% left`));block.append(line);
      if(pct!==null){const progress=el('progress');progress.max=100;progress.value=pct;progress.setAttribute('aria-label',`${label}: ${Math.round(pct)} percent remaining`);block.append(progress);}
      if(detail)block.append(el('small','',detail));usage.append(block);
    }
    const context=data.context;
    if(context?.state==='compacting')usage.append(el('p','status-compacting','Compacting…'));
    else {
      const detail=context?.usedTokens!=null&&context?.windowTokens!=null?`${Number(context.usedTokens).toLocaleString()} used / ${Number(context.windowTokens).toLocaleString()} tokens`: 'Estimate from the latest usage event';
      meter('Context window',context?.remainingPercent,context?(context.stale ? `Last known: ${detail}` : detail):'Awaiting a usage report from this connection');
    }
    const weekly=data.weekly;
    meter('Weekly limit',weekly?.remainingPercent,weekly?.resetsAt?`Resets ${new Date(weekly.resetsAt*1000).toLocaleString()}`:null);
    const credits=data.credits||{};
    usage.append(el('p','status-credits',`Credits: ${credits.unlimited===true?'Unlimited':credits.balance??missing}`));
    root.append(usage);
    const time=data.checkedAt?new Date(data.checkedAt*1000).toLocaleTimeString():null;
    root.append(el('p','status-footnote',`${time?'Checked '+time+'. ':''}Session settings reflect the latest observation. Missing fields are not inferred. No transcript or private notes are displayed.`));
  }
  if(typeof module!=='undefined')module.exports={render,permissionLabel,percentage};
  if(scope)scope.IAMStatus={render};
})(typeof window!=='undefined'?window:null);
