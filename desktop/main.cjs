const { app, BrowserWindow, ipcMain, session, Menu, Tray, nativeImage, clipboard, screen, dialog, shell, crashReporter, powerMonitor } = require('electron');
const express = require('express');
const { spawn } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const { createInterface } = require('node:readline');
const path = require('node:path');
const fs = require('node:fs');
const windowState = require('./window-state.cjs');
const {createBackendRpc} = require('./backend-rpc.cjs');
const {saveAttachment} = require('./attachment-save.cjs');
const {contextMenuItems} = require('./context-menu.cjs');

const root = path.resolve(__dirname, '..');
const appIcon = path.join(__dirname, 'assets', 'iam-messenger.ico');
app.setAppUserModelId('xyz.cerberusgamelabs.iam-messenger');
const token = randomBytes(32).toString('hex');
const windows = new Map();
let screenLocked = false;
const controlWindows = new Map();
const viewerImages = new Map();
const viewerDocuments = new Map();
const humanFriends = new Map();
const controlKinds = new Set(['status', 'compact', 'stop', 'model', 'plan', 'permissions', 'more', 'pairing', 'external-contact', 'friends']);
let backend, backendRpc, server, origin, tray, stopping = false;
const smoke = process.argv.includes('--smoke-test');
const layoutFile = path.join(root, 'run', 'desktop-windows.json');
let restoring = true;
function saveWindows() {
  if (smoke || restoring) return;
  try { windowState.write(layoutFile, [...windows].filter(([, w]) => !w.isDestroyed()).map(([mailbox, w]) =>
    ({mailbox, bounds: w.getNormalBounds(), maximized: w.isMaximized()}))); }
  catch { console.error('Could not save desktop window layout.'); }
}
if (!app.requestSingleInstanceLock()) { app.exit(0); return; }
const diagnostics=require('./diagnostics.cjs').installDiagnostics({app,crashReporter,directory:path.join(root,'run','desktop-crashes')});
app.on('second-instance', () => windowFor('buddies'));
app.on('web-contents-created', (_event, contents) => {
  contents.on('context-menu', (_event, params) => {
    if (!origin || !contents.getURL().startsWith(origin + '/')) return;
    const win = BrowserWindow.fromWebContents(contents);
    if (!win || win.isDestroyed()) return;
    // Download only a main-process-owned viewer attachment. Never use srcURL,
    // linkURL, or renderer-provided paths from a context-menu event.
    const image = viewerImages.get(contents.id), document = viewerDocuments.get(contents.id);
    const item = image || document;
    const download = item ? async () => {
      try { await saveAttachment(dialog, win, item, Boolean(image)); }
      catch { if (!win.isDestroyed()) await dialog.showMessageBox(win, {type:'error', message:'Could not save the attachment. Check the destination and try again.'}); }
    } : undefined;
    Menu.buildFromTemplate(contextMenuItems(params, download)).popup({window:win});
  });
});

function rpc(action, args = {}) {
  if (stopping || !backendRpc) return Promise.reject(new Error('IAM Desktop backend is unavailable'));
  return backendRpc.request(action, args);
}
function windowFor(mailbox, saved) {
  if (!origin) return;
  if (windows.has(mailbox)) { const w = windows.get(mailbox); w.show(); w.focus(); return w; }
  const buddy = mailbox === 'buddies';
  const win = new BrowserWindow({icon: appIcon, width: buddy ? 370 : 500, height: buddy ? 690 : 800,
    minWidth: 320, minHeight: 400, title: buddy ? 'IAM • Buddy List' : `IAM • ${mailbox}`,
    backgroundColor: '#171923', show: !smoke,
    ...windowState.fit(saved?.bounds, screen.getAllDisplays().map(d => d.workArea)),
    webPreferences: {preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false,
      contextIsolation: true, sandbox: true, webSecurity: true, autoplayPolicy: 'no-user-gesture-required'}});
  win.webContents.setWindowOpenHandler(() => ({action:'deny'}));
  win.webContents.on('will-navigate', (event) => event.preventDefault());
  win.on('close', event => { if (buddy && !stopping) { event.preventDefault(); win.hide(); } });
  const windowId = String(win.id);
  const trackWindow = opened => rpc('chat-window', {mailbox, window_id: windowId, opened});
  const registeredWindow = buddy ? Promise.resolve() : trackWindow(true).catch(error => {
    diagnostics.event('chat-window-register-failed', {errorType:error.name});
  });
  win.on('closed', () => {
    windows.delete(mailbox);
    if (!stopping) {
      saveWindows();
      if (!buddy) registeredWindow.then(() => trackWindow(false)).catch(error => {
        diagnostics.event('chat-window-release-failed', {errorType:error.name});
      });
    }
  });
  for (const event of ['moved', 'resized', 'maximize', 'unmaximize']) win.on(event, () => { if (!stopping) saveWindows(); });
  windows.set(mailbox, win);
  win.loadURL(`${origin}/?mailbox=${encodeURIComponent(mailbox)}${saved ? '&restored=1' : ''}`);
  if (saved?.maximized) win.maximize();
  saveWindows();
  return win;
}

function controlWindow(mailbox, kind, parent) {
  const key = mailbox + ':' + kind;
  if (controlWindows.has(key)) { const w = controlWindows.get(key); w.show(); w.focus(); return; }
  const win = new BrowserWindow({icon: appIcon, width: kind === 'status' ? 590 : 490, height: kind === 'status' ? 740 : 560, minWidth: 380, minHeight: 360,
    parent, modal: false, title: `IAM — ${kind}`, backgroundColor: '#171923',
    webPreferences: {preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true}});
  win.webContents.setWindowOpenHandler(() => ({action: 'deny'}));
  win.webContents.on('will-navigate', e => e.preventDefault());
  const contentsId=win.webContents.id;
  win.on('closed', () => {controlWindows.delete(key);humanFriends.get(contentsId)?.close();humanFriends.delete(contentsId);}); controlWindows.set(key, win);
  win.loadURL(`${origin}/controls.html?mailbox=${encodeURIComponent(mailbox)}&kind=${kind}`);
}

app.whenReady().then(async () => {
  powerMonitor.on('lock-screen',()=>{screenLocked=true;});
  powerMonitor.on('unlock-screen',()=>{screenLocked=false;});
  Menu.setApplicationMenu(null);
  session.defaultSession.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  const api = express();
  api.disable('x-powered-by');
  api.use((req, res, next) => {
    if (req.headers['x-iam-desktop'] !== token || req.headers.host !== new URL(origin).host) return res.sendStatus(403);
    res.set('Cache-Control','no-store');
    res.set('X-Content-Type-Options','nosniff');
    res.set('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; img-src data: 'self'; connect-src 'none'; base-uri 'none'; frame-ancestors 'none'");
    next();
  });
  api.post('/control/:action', (req,res) => {
    if (req.params.action === 'show') { windowFor('buddies'); return res.json({ok:true}); }
    if (req.params.action === 'stop') { res.json({ok:true}); setImmediate(()=>app.quit()); return; }
    res.sendStatus(404);
  });
  api.use(express.static(path.join(__dirname,'public'), {dotfiles:'deny'}));
  server = await new Promise(resolve => { const s=api.listen(0,'127.0.0.1',()=>resolve(s)); });
  origin = `http://127.0.0.1:${server.address().port}`;
  fs.mkdirSync(path.join(root,'run'),{recursive:true});
  fs.writeFileSync(path.join(root,'run','desktop-control.json'),JSON.stringify({url:origin,token,pid:process.pid}),{mode:0o600});
  session.defaultSession.webRequest.onBeforeRequest((details, callback) => {
    callback({cancel: !details.url.startsWith(origin + '/') && !details.url.startsWith('data:image/')});
  });
  session.defaultSession.webRequest.onBeforeSendHeaders((details, callback) => {
    if (details.url.startsWith(origin + '/')) details.requestHeaders['X-IAM-Desktop'] = token;
    callback({requestHeaders: details.requestHeaders});
  });
  const python = process.env.IAM_DESKTOP_PYTHON;
  if (!python) throw new Error('Start with iam desktop start so the configured Python runtime is used.');
  backend = spawn(python, ['-X','utf8','-u','-m','iam_desktop'], {cwd: root, windowsHide: true, stdio:['pipe','pipe','pipe']});
  backendRpc = createBackendRpc(backend);
  diagnostics.event('backend-started',{processId:backend.pid});
  backend.on('error',error=>diagnostics.event('backend-error',{errorType:error.name}));
  // Never echo backend content or transcript-bearing protocol errors into a renderer.
  backend.stderr.on('data', () => {});
  createInterface({input:backend.stdout}).on('line', line => {
    try {
      backendRpc.receive(JSON.parse(line));
    } catch {}
  });
  backend.on('exit', (exitCode,signal) => { diagnostics.event('backend-exit',{exitCode,signal}); });
  ipcMain.handle('iam:call', async (event, action, args={}) => {
    if (action === 'chat-window') throw new Error('Window lifecycle is main-process owned');
    if (event.senderFrame !== event.sender.mainFrame || !event.senderFrame.url.startsWith(origin + '/')) throw new Error('Untrusted window');
    if (Buffer.byteLength(JSON.stringify(args)) > (action === 'send' ? 12000000 : 100000)) throw new Error('Request too large');
    if(action==='human-friends' || action==='agent-friend-roster'){
      const sender=new URL(event.senderFrame.url),mailbox=sender.searchParams.get('mailbox');
      if(sender.pathname!=='/controls.html' || sender.searchParams.get('kind')!=='friends' || args.mailbox!==mailbox)throw Error('Friend requests require their dedicated window');
      if(action==='agent-friend-roster'){
        if(mailbox==='buddies')throw Error('Agent mailbox required');
        return rpc(action,{mailbox});
      }
      if(mailbox!=='buddies')throw Error('Human requests require the account window');
      if(!humanFriends.has(event.sender.id))humanFriends.set(event.sender.id,require('./human-friends.cjs').createHumanFriends());
      const client=humanFriends.get(event.sender.id),config=await rpc('human-friend-config');
      if(event.sender.isDestroyed())throw Error('Window closed');
      return client.call(config,args.operation,args.value);
    }
    if (action === 'mark-read') {
      const sender = new URL(event.senderFrame.url), win = BrowserWindow.fromWebContents(event.sender);
      if (sender.pathname !== '/' || sender.searchParams.get('mailbox') !== args.mailbox) throw new Error('Read mailbox mismatch');
      if (screenLocked || powerMonitor.getSystemIdleState(60) === 'locked' || !win || !win.isFocused() || !win.isVisible() || win.isMinimized()) return {marked:false};
      return rpc('mark-read', {mailbox:args.mailbox,event_ids:args.event_ids});
    }
    if (action === 'ensure-session' || action === 'session-readiness') {
      const sender = new URL(event.senderFrame.url);
      if (sender.pathname !== '/' || sender.searchParams.get('mailbox') !== args.mailbox) throw new Error('Session mailbox mismatch');
      if (action === 'session-readiness') return rpc(action, {mailbox: args.mailbox});
      const review = await rpc('first-session-review', {mailbox: args.mailbox});
      if (review.state === 'bound') return review;
      if (review.state !== 'confirm') throw new Error('Session review unavailable');
      const answer = await dialog.showMessageBox(BrowserWindow.fromWebContents(event.sender), {
        type: 'question', title: 'Start first conversation?',
        message: `Start ${review.name}'s first conversation?`,
        detail: `Workspace: ${review.workspace}\nSandbox: ${review.sandbox}\nApprovals: ${review.approval}\n\nThis creates and binds a new Codex session, then sends your drafted message. If this agent has used another conversation before, cancel and bind that session instead.`,
        buttons: ['Cancel', 'Start conversation'], defaultId: 0, cancelId: 0, noLink: true
      });
      if (answer.response !== 1) return {state: 'cancelled'};
      return rpc('first-session-confirm', {mailbox: args.mailbox, token: review.token});
    }
    if (action === 'viewer-document-save') {
      const item = viewerDocuments.get(event.sender.id);
      if (!item) throw new Error('No document belongs to this viewer');
      return saveAttachment(dialog, BrowserWindow.fromWebContents(event.sender), item);
    }
    if (action === 'viewer-image-save') {
      const item = viewerImages.get(event.sender.id);
      if (!item) throw new Error('No image belongs to this viewer');
      return saveAttachment(dialog, BrowserWindow.fromWebContents(event.sender), item, true);
    }
    if (action === 'viewer-document') {
      const item = viewerDocuments.get(event.sender.id);
      if (!item) throw new Error('No document belongs to this viewer');
      return {name: item.name, text: Buffer.from(item.data, 'base64').toString('utf8')};
    }
    if (['file-preview', 'file-save', 'file-open'].includes(action)) {
      const sender = new URL(event.senderFrame.url);
      if (sender.pathname !== '/' || sender.searchParams.get('mailbox') !== args.mailbox) throw new Error('Document mailbox mismatch');
      const item = await rpc('document', args);
      const bytes = Buffer.from(item.data, 'base64');
      const ext = path.extname(item.name).toLowerCase();
      const parent = BrowserWindow.fromWebContents(event.sender);
      if (action === 'file-preview') {
        if (!['.txt','.md','.csv','.json','.log'].includes(ext)) throw new Error('Preview is available for UTF-8 text documents only');
        const win = new BrowserWindow({icon: appIcon, width: 760, height: 720, title: 'IAM Document Viewer',
          webPreferences: {preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true}});
        const id = win.webContents.id; viewerDocuments.set(id, item);
        win.webContents.setWindowOpenHandler(() => ({action:'deny'}));
        win.webContents.on('will-navigate', e => e.preventDefault());
        win.on('closed', () => viewerDocuments.delete(id));
        win.loadURL(`${origin}/document.html`); return true;
      }
      if (action === 'file-save') {
        return saveAttachment(dialog, parent, item);
      }
      // Never execute arbitrary attachments or open text in an executable association.
      if (!['.pdf','.docx','.xlsx','.pptx'].includes(ext)) throw new Error('Use Preview or Save As for this document');
      const consent = await dialog.showMessageBox(parent, {type:'warning', buttons:['Cancel','Open document'], defaultId:0, cancelId:0,
        message:`Open ${item.name} in its associated application?`, detail:'External applications may process active content or contact the network. Only open documents you trust.'});
      if (consent.response !== 1) return false;
      const directory = await fs.promises.mkdtemp(path.join(app.getPath('temp'), 'iam-document-'));
      const target = path.join(directory, 'document' + ext);
      await fs.promises.writeFile(target, bytes, {flag:'wx'});
      const error = await shell.openPath(target);
      if (error) throw new Error('The associated application could not open this document');
      return true;
    }
    if (action === 'viewer-image') {
      const image = viewerImages.get(event.sender.id);
      if (!image) throw new Error('No image belongs to this viewer');
      return image;
    }
    if (action === 'published-image' || action === 'image-window' || action === 'image-save') {
      const sender = new URL(event.senderFrame.url);
      if (sender.pathname !== '/' || sender.searchParams.get('mailbox') !== args.mailbox) throw new Error('Image mailbox mismatch');
      const item = await rpc('published-image', args);
      const decoded = nativeImage.createFromDataURL(item.data), size = decoded.getSize();
      if (decoded.isEmpty() || size.width * size.height > 40000000) throw new Error('Invalid image or image exceeds 40 megapixels');
      if (action === 'published-image') return item;
      if (action === 'image-save') return saveAttachment(dialog, BrowserWindow.fromWebContents(event.sender), item, true);
      const win = new BrowserWindow({icon: appIcon, width: 900, height: 760, minWidth: 320, minHeight: 300,
        title: 'IAM Image Viewer', backgroundColor: '#171923',
        webPreferences: {preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true}});
      viewerImages.set(win.webContents.id, item);
      const viewerId = win.webContents.id;
      win.webContents.setWindowOpenHandler(() => ({action: 'deny'}));
      win.webContents.on('will-navigate', e => e.preventDefault());
      win.on('closed', () => viewerImages.delete(viewerId));
      win.loadURL(`${origin}/viewer.html`);
      return true;
    }
    if (['control-window', 'session-control', 'copy-published', 'pairing-review', 'external-contact-review'].includes(action)) {
      const sender = new URL(event.senderFrame.url);
      const mailbox = sender.searchParams.get('mailbox');
      if (!mailbox || args.mailbox !== mailbox || (mailbox === 'buddies' &&
          !(action === 'pairing-review' || action === 'control-window' && ['pairing','friends'].includes(args.kind)))) throw new Error('Control mailbox mismatch');
      if (action === 'control-window') {
        if (!controlKinds.has(args.kind)) throw new Error('Unknown control dialog');
        controlWindow(mailbox, args.kind, windows.get(mailbox)); return true;
      }
      if (sender.pathname !== '/controls.html') throw new Error('Session controls require their dedicated dialog');
      if (action === 'external-contact-review') {
        if (sender.searchParams.get('kind') !== 'external-contact') throw new Error('External contact requires its dedicated owner dialog');
        return rpc('external-contact-review', args);
      }
      if (action === 'pairing-review') {
        if (sender.searchParams.get('kind') !== 'pairing') throw new Error('Pairing requires its dedicated dialog');
        const result=await rpc('pairing-review', args);
        if(mailbox==='buddies' && args.operation==='invite') {
          const code='iam-pair:'+Buffer.from(JSON.stringify({v:1,home:result.home,installation:result.installation,challenge:result.challenge})).toString('base64url');
          return {...result,code,qr:await require('qrcode').toDataURL(code,{errorCorrectionLevel:'M',width:320,margin:4})};
        }
        return result;
      }
      if (action === 'copy-published') {
        const history = await rpc('history', {mailbox});
        const latest = history.filter(m => typeof m.reply === 'string').sort((a,b) => (b.replied_at || b.created) - (a.replied_at || a.created))[0];
        if (!latest) throw new Error('No published desktop reply to copy');
        clipboard.writeText(latest.reply); return true;
      }
      return rpc('session-control', args);
    }
    if (action === 'send' && args.images !== undefined) {
      if (!Array.isArray(args.images) || args.images.length > 4) throw new Error('Attach at most four images');
      let total = 0;
      for (const item of args.images) {
        if (!item || typeof item.data !== 'string' || item.data.length > 5500000 || !/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(item.data)) throw new Error('Unsupported image');
        const bytes = Buffer.from(item.data.split(',')[1], 'base64'); total += bytes.length;
        if (bytes.length > 4000000 || total > 8000000) throw new Error('Images: 4 MB each, 8 MB total maximum');
        const image = nativeImage.createFromBuffer(bytes), size = image.getSize();
        if (image.isEmpty() || size.width * size.height > 40000000) throw new Error('Invalid image or image dimensions too large');
      }
    }
    if (action === 'theme') {
      const file = path.join(root, 'run', 'desktop-theme.json');
      if (args.value !== undefined) {
        if (!['dark', 'light'].includes(args.value)) throw new Error('Invalid theme');
        fs.writeFileSync(file, JSON.stringify({value: args.value}));
      }
      try { const value = JSON.parse(fs.readFileSync(file, 'utf8')).value; return value === 'light' ? 'light' : 'dark'; }
      catch { return 'dark'; }
    }
    if (action === 'window') { const roster=await rpc('snapshot'); if (!roster.buddies.some(b=>b.mailbox===args.mailbox)) throw new Error('Unknown buddy'); windowFor(args.mailbox); return true; }
    if (!['snapshot','history','open','send','approval','decide','dismiss-notice'].includes(action)) throw new Error('Unsupported operation');
    return rpc(action,args);
  });
  tray = new Tray(appIcon); tray.setToolTip('IAM — Your people, one click away');
  tray.setContextMenu(Menu.buildFromTemplate([{label:'Buddy list',click:()=>windowFor('buddies')},{label:'Close desktop (IAM stays running)',click:()=>app.quit()}]));
  tray.on('click',()=>windowFor('buddies'));
  const savedWindows = smoke ? [] : windowState.read(layoutFile);
  windowFor('buddies', savedWindows.find(w => w.mailbox === 'buddies'));
  if (!smoke) try {
    const roster = await rpc('snapshot');
    const allowed = new Set(roster.buddies.map(b => b.mailbox));
    for (const saved of savedWindows) {
      if (saved.mailbox !== 'buddies' && allowed.has(saved.mailbox)) windowFor(saved.mailbox, saved);
    }
  } catch { console.error('Could not restore desktop chat windows.'); }
  restoring = false;
  saveWindows();
  if (smoke) {
    const win=windows.get('buddies');
    await new Promise(resolve=>win.webContents.once('did-finish-load',resolve));
    const result=await rpc('snapshot');
    await new Promise(resolve=>setTimeout(resolve,1500));
    fs.writeFileSync(path.join(root,'run','desktop-preview.png'),(await win.webContents.capturePage()).toPNG());
    const unauthorized = await fetch(origin);
    if (unauthorized.status !== 403) throw new Error('Unauthenticated desktop HTTP access was not rejected');
    console.log(JSON.stringify({smoke:true,buddies:result.buddies.length,loopback:server.address().address}));
    app.quit();
  }
}).catch(error=>{ diagnostics.event('startup-failed',{errorType:error.name}); console.error('Messenger startup failed; see local diagnostics.'); app.exit(1); });
app.on('window-all-closed',()=>{});
app.on('before-quit',()=>{stopping=true; backendRpc?.stop(); saveWindows(); server?.close();});
app.on('will-quit',()=>{backend?.kill(); tray?.destroy();try{const file=path.join(root,'run','desktop-control.json');if(JSON.parse(fs.readFileSync(file,'utf8')).pid===process.pid)fs.unlinkSync(file);}catch{}});
