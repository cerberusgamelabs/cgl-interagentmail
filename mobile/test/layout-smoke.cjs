// Isolated, hidden Electron renderer; never connects to a Messenger session.
const {app,BrowserWindow}=require('electron');
const path=require('node:path');
const fs=require('node:fs');
const os=require('node:os');
const report=require('./layout-report.cjs').reporter({exit:code=>app.exit(code)});
app.setPath('userData',fs.mkdtempSync(path.join(os.tmpdir(),'iam-layout-')));
app.whenReady().then(async()=>{
  const win=new BrowserWindow({show:false,width:400,height:800,useContentSize:true,webPreferences:{contextIsolation:true,nodeIntegration:false}});
  await win.loadFile(path.resolve(__dirname,'../dist/index.html'));
  for(const [width,height,long,photo,retry] of [[400,800,false],[400,400,false],[320,360,false],[800,320,false],[400,400,true],[320,360,false,true,true]]){
    win.setContentSize(width,height);
    await win.webContents.executeJavaScript(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
    const result=await win.webContents.executeJavaScript(`(async()=>{
      const by=id=>document.getElementById(id);
      document.body.classList.add('chat-ready');by('login').hidden=true;by('paired').hidden=false;by('composer').hidden=false;by('messages').hidden=false;
      by('buddyPanel').hidden=true;by('settings').hidden=true;by('backHome').hidden=true;by('latest').hidden=false;by('connectionSettings').hidden=true;
      by('profileHeader').hidden=false;by('profileLines').hidden=false;by('openHelp').hidden=false;by('chatContext').hidden=false;
      by('chatAvatar').textContent='R';by('chatPresence').textContent='available';by('chatStatus').textContent='A chosen status';by('chatContext').textContent='Context ~66% left';
      by('settings').open=false;by('status').textContent='History updated';by('activeConversation').textContent='Conversation: Test';
      by('liveStatus').hidden=false;by('liveStatus').textContent='Live updates reconnecting; using periodic refresh';
      by('pending').textContent='';by('recover').hidden=true;by('clearPending').hidden=true;
      by('send').textContent=${JSON.stringify(retry?'Retry send':'Send')};
      by('selectedPhoto').hidden=${!photo};by('photoPreview').hidden=${!photo};by('removeImage').hidden=${!photo};by('photoPreview').src='mark.png';
      by('draft').disabled=false;by('draft').value=${long?JSON.stringify('Several lines of text to exercise the growing composer. '.repeat(20)):JSON.stringify('Hello')};
      const {fitDraft}=await import('./composer.mjs');fitDraft(by('draft'));
      by('messages').replaceChildren(...Array.from({length:60},(_,i)=>{const row=document.createElement('article');row.textContent='Test message '+i+' — a reply in a long conversation.';return row;}));
      const sample=by('messages').lastElementChild;
      const meta=document.createElement('small');meta.className='message-meta';
      const stamp=document.createElement('time');stamp.textContent='Today - 11:59 PM';
      const marker=document.createElement('span');marker.className='delivery-marker';marker.textContent='✓';meta.append(stamp,marker);sample.append(meta);
      by('messages').scrollTop=by('messages').scrollHeight;
      const bubbleRect=sample.getBoundingClientRect(),metaRect=meta.getBoundingClientRect(),historyRect=by('messages').getBoundingClientRect();
      if(metaRect.right>bubbleRect.right || metaRect.left<bubbleRect.left || metaRect.top>=bubbleRect.bottom || metaRect.bottom<=bubbleRect.bottom || metaRect.bottom>historyRect.bottom+1)throw Error('Timestamp tab clipping or placement');
      const input=by('draft').getBoundingClientRect(),composer=by('composer').getBoundingClientRect(),send=by('send').getBoundingClientRect();
      return {height:innerHeight,inputHeight:input.height,sameRow:Math.abs(input.bottom-send.bottom)<2 && input.right<=send.left && by('pickImage').getBoundingClientRect().right<=input.left,composerHeight:composer.height,inputBottom:input.bottom,composerBottom:composer.bottom,sendBottom:send.bottom,pageHeight:document.documentElement.scrollHeight,historyHeight:by('messages').clientHeight,historyScroll:by('messages').scrollHeight};
    })()`);
    if(result.inputBottom>height || result.composerBottom>height+1 || result.sendBottom>height || result.historyHeight<=0 || result.historyScroll<=result.historyHeight || result.pageHeight>height+1)throw Error(JSON.stringify({width,height,...result}));
    if(!result.sameRow || result.inputHeight>120 || (!long && !photo && result.composerHeight>110))throw Error('Composer not compact: '+JSON.stringify(result));
    report.record({width,height,...result});
  }
  win.setContentSize(320,400);
  const home=await win.webContents.executeJavaScript(`(()=>{
    const by=id=>document.getElementById(id);document.body.classList.remove('chat-ready');
    by('messages').hidden=true;by('composer').hidden=true;by('buddyPanel').hidden=false;
    by('settings').hidden=true;by('backHome').hidden=true;by('latest').hidden=true;
    by('buddies').replaceChildren(...Array.from({length:64},(_,i)=>{const b=document.createElement('button');b.textContent='Saved conversation '+i+' — installation';return b;}));
    const c=by('controls');return {scroll:c.scrollHeight>c.clientHeight,composer:by('composer').getBoundingClientRect().height,history:by('messages').getBoundingClientRect().height,page:document.documentElement.scrollHeight,height:innerHeight};
  })()`);
  if(!home.scroll || home.composer || home.history || home.page>home.height+1)throw Error('Buddy home layout: '+JSON.stringify(home));
  report.record({buddyHome:home});
  const account=await win.webContents.executeJavaScript(`(()=>{
    const by=id=>document.getElementById(id);by('login').hidden=false;by('paired').hidden=true;by('accountHelp').open=true;
    const c=by('controls');c.scrollTop=c.scrollHeight;
    const button=by('accountForm').querySelector('button').getBoundingClientRect();
    return {scroll:c.scrollHeight>c.clientHeight,buttonBottom:button.bottom,page:document.documentElement.scrollHeight,height:innerHeight,width:document.documentElement.scrollWidth,viewport:innerWidth};
  })()`);
  if(!account.scroll || account.buttonBottom>account.height+1 || account.page>account.height+1 || account.width>account.viewport)throw Error('Account layout: '+JSON.stringify(account));
  report.record({accountForm:account});
  const fullText=await win.webContents.executeJavaScript(`(()=>{
    const modal=document.getElementById('textViewer'),body=document.getElementById('fullText');
    body.textContent='<script>literal text</script>'+ 'long content '.repeat(10000);modal.showModal();
    const rect=modal.getBoundingClientRect(),button=document.getElementById('closeText').getBoundingClientRect();
    const result={width:innerWidth,height:innerHeight,left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,buttonBottom:button.bottom,scroll:modal.scrollHeight>modal.clientHeight};
    modal.close();return result;
  })()`);
  if(fullText.left<0 || fullText.right>fullText.width+1 || fullText.top<0 || fullText.bottom>fullText.height+1 || fullText.buttonBottom>fullText.height || !fullText.scroll)throw Error('Full text layout: '+JSON.stringify(fullText));
  report.record({fullText});
  const help=await win.webContents.executeJavaScript(`(()=>{
    const modal=document.getElementById('chatHelp');modal.showModal();
    const rect=modal.getBoundingClientRect(),close=document.getElementById('closeHelp').getBoundingClientRect();
    const result={left:rect.left,right:rect.right,bottom:rect.bottom,width:innerWidth,height:innerHeight,closeBottom:close.bottom};modal.close();return result;
  })()`);
  if(help.left<0 || help.right>help.width+1 || help.bottom>help.height+1 || help.closeBottom>help.height)throw Error('Help layout: '+JSON.stringify(help));
  report.record({help});
  for(const [width,height] of [[320,360],[400,800],[800,320]]){
  win.setContentSize(width,height);
  const friends=await win.webContents.executeJavaScript(`(async()=>{
    const {createFriendships}=await import('./friendships.mjs');
    const modal=document.getElementById('friendships'),state={};
    const transport={session:{},friends:async op=>op==='roster'?{self:{enabled:true,name:'Test'},relationships:[{id:'test',state:'active',revision:1,peer:{name:'A long chosen friend name'}}]}:{events:[{id:'message',own:true,text:'Full friend message '.repeat(300),created:1,delivery:'received'}],more:false}};
    const ui=createFriendships({dialog:modal,document,getTransport:()=>transport,getState:()=>state,save:async()=>{}});
    await ui.open();await [...modal.querySelectorAll('button')].find(b=>b.textContent.startsWith('Chat with')).onclick();
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    const rect=modal.getBoundingClientRect(),composer=modal.querySelector('textarea'),history=modal.querySelector('.friend-history');history.scrollTop=history.scrollHeight;
    const input=composer.getBoundingClientRect(),send=[...modal.querySelectorAll('button')].find(b=>b.textContent==='Send').getBoundingClientRect();
    const bubble=history.querySelector('article').getBoundingClientRect(),meta=history.querySelector('.message-meta').getBoundingClientRect();
    const help=modal.querySelector('.friend-help');
    const result={left:rect.left,right:rect.right,bottom:rect.bottom,width:innerWidth,height:innerHeight,inputBottom:input.bottom,sendBottom:send.bottom,
      pageScroll:modal.scrollHeight>modal.clientHeight+1,historyHeight:history.clientHeight,historyScroll:history.scrollHeight>history.clientHeight,
      helpHidden:help.hidden,sameRow:Math.abs(input.bottom-send.bottom)<2,
      footer:meta.right<=bubble.right && meta.left>=bubble.left && meta.top<bubble.bottom && meta.bottom>bubble.bottom && meta.bottom<=history.getBoundingClientRect().bottom+1};
    await ui.close();return result;
  })()`);
  if(friends.left<0 || friends.right>friends.width+1 || friends.bottom>friends.height+1 || friends.inputBottom>friends.height || friends.sendBottom>friends.height || friends.pageScroll || !friends.historyHeight || !friends.historyScroll || !friends.helpHidden || !friends.sameRow || !friends.footer)throw Error('Friend layout: '+JSON.stringify(friends));
  report.record({friends});
  }
  const grids=await win.webContents.executeJavaScript(`(async()=>{
    const {imagePreviews}=await import('./image-grid.mjs');const results=[];
    for(let count=1;count<=4;count++){
      const parent=document.createElement('div');parent.style.cssText='position:fixed;inset:0 auto auto 0;width:280px;z-index:9999';document.body.append(parent);
      imagePreviews({document,Observer:null}).render(parent,Array.from({length:count},(_,i)=>({key:String(i),label:'Preview',active:()=>true,load:async()=>{},open:async()=>{}})));
      const r=[...parent.querySelectorAll('button')].map(e=>{const b=e.getBoundingClientRect();return {x:b.x,y:b.y,w:b.width,h:b.height};});
      const near=(a,b)=>Math.abs(a-b)<1;
      const okay=count===1?near(r[0].w,280):count===2?near(r[0].y,r[1].y)&&r[1].x>r[0].x:count===3?near(r[0].w,280)&&r[1].y>r[0].y&&near(r[1].y,r[2].y)&&r[2].x>r[1].x:near(r[0].y,r[1].y)&&near(r[2].y,r[3].y)&&r[2].y>r[0].y&&near(r[0].x,r[2].x);
      results.push({count,okay,rects:r});parent.remove();
    }return results;
  })()`);
  if(grids.some(g=>!g.okay))throw Error('Image grids: '+JSON.stringify(grids));report.record({grids});
  app.exit(0);
}).catch(error=>report.fail(error));
