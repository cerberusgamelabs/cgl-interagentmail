// Isolated hidden renderer: no Messenger backend, sessions, or network.
const {app,BrowserWindow,session}=require('electron');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const scratch=fs.mkdtempSync(path.join(os.tmpdir(),'iam-history-smoke-'));
app.setPath('userData',scratch);
app.whenReady().then(async()=>{
  session.defaultSession.webRequest.onBeforeRequest((details,done)=>done({cancel:!details.url.startsWith('data:')}));
  const win=new BrowserWindow({show:false,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false}});
  await win.loadURL('data:text/html,<html><body><div id="messages"></div></body></html>');
  const helper=fs.readFileSync(path.join(__dirname,'../public/history-view.js'),'utf8');
  const css=fs.readFileSync(path.join(__dirname,'../public/style.css'),'utf8');
  const result=await win.webContents.executeJavaScript(`(async()=>{
    ${helper}
    const style=document.createElement('style');style.textContent=${JSON.stringify(css)};document.head.append(style);
    const list=document.getElementById('messages');list.style.cssText='height:300px;min-height:0;overflow:auto;display:block;flex:none';
    let builds=0;const pictures=[];
    const update=IAMHistoryView.create(list,m=>{
      builds++;const node=document.createElement('section');node.className='history-message';
      if(m.image){const button=document.createElement('button');button.className='shared-picture';button.textContent='Loading';node.append(button);pictures.push(button);}
      else {node.style.height='100px';node.textContent=m.id;}
      return node;
    });
    const rows=Array.from({length:12},(_,i)=>({id:String(i),image:i===1}));update(rows);
    list.scrollTop=80;const before=list.scrollTop,oldNode=list.children[1],oldHeight=list.scrollHeight;
    update([...rows,{id:'12'}]);
    if(list.scrollTop!==before || list.children[1]!==oldNode || builds!==13)throw Error('Refresh changed reader position or image identity');
    const img=new Image();img.src='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=';
    await img.decode();pictures[0].replaceChildren(img);
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    if(Math.abs(list.scrollTop-before)>1 || list.scrollHeight!==oldHeight+100)throw Error('Late image changed layout or scroll');
    list.scrollTop=list.scrollHeight;update([...rows,{id:'12'},{id:'13'}]);
    if(Math.abs(list.scrollHeight-list.clientHeight-list.scrollTop)>1)throw Error('Bottom follow failed');
    return {unchangedImageRetained:true,lateImageStable:true,readingPositionPreserved:true,bottomFollow:true};
  })()`);
  console.log(JSON.stringify(result));win.destroy();app.quit();
}).catch(error=>{console.error(error);app.exit(1);});
app.on('quit',()=>{try{fs.rmSync(scratch,{recursive:true,force:true});}catch{}});
