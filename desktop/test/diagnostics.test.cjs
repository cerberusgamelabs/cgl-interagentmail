const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {EventEmitter}=require('node:events');
const {createDiagnostics,installDiagnostics}=require('../diagnostics.cjs');
function fixture(t){const dir=fs.mkdtempSync(path.join(os.tmpdir(),'iam-diagnostics-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));return dir;}
function rows(dir){return fs.readFileSync(path.join(dir,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse);}
test('detects unclean previous run, distinguishes clean shutdown',t=>{
  const dir=fixture(t);createDiagnostics(dir).start();
  const second=createDiagnostics(dir);second.start();second.cleanExit();createDiagnostics(dir).start();
  assert.equal(rows(dir).filter(r=>r.event==='previous-session-unclean').length,1);
  assert.equal(rows(dir).filter(r=>r.event==='clean-exit').length,1);
});
test('metadata projection excludes sensitive payloads and untrusted strings',t=>{
  const dir=fixture(t),log=createDiagnostics(dir);
  log.event('backend-exit',{exitCode:1,body:'PRIVATE',token:'PRIVATE',stack:'PRIVATE',url:'PRIVATE',errorType:'PRIVATE',reason:'PRIVATE'});
  const text=fs.readFileSync(path.join(dir,'events.jsonl'),'utf8');assert.ok(!text.includes('PRIVATE'));
  assert.equal(rows(dir)[0].exitCode,1);assert.equal(rows(dir)[0].reason,'unknown');
});
test('rotates bounded logs and tolerates unwritable destination',t=>{
  const dir=fixture(t),log=createDiagnostics(dir,{maxBytes:300,archives:3});
  for(let i=0;i<25;i++)log.event('test-event');
  assert.ok(fs.readdirSync(dir).length<=4);
  for(const file of fs.readdirSync(dir))assert.ok(fs.statSync(path.join(dir,file)).size<=300);
  const file=path.join(dir,'not-a-directory');fs.writeFileSync(file,'x');
  assert.equal(createDiagnostics(file).event('test-event'),false);
});
test('hooks native renderer/backend lifecycle without swallowing fatal exceptions',t=>{
  const dir=fixture(t),app=new EventEmitter(),proc=new EventEmitter(),wc=new EventEmitter();wc.id=7;
  let options;app.setPath=(key,value)=>{assert.equal(key,'crashDumps');assert.equal(value,path.join(dir,'dumps'));};
  installDiagnostics({app,processObject:proc,directory:dir,crashReporter:{start:value=>options=value}});
  assert.equal(options.uploadToServer,false);assert.equal(options.submitURL,undefined);
  assert.equal(proc.listenerCount('uncaughtException'),0);assert.equal(proc.listenerCount('unhandledRejection'),0);
  proc.emit('uncaughtExceptionMonitor',new TypeError('PRIVATE'),'uncaughtException');
  app.emit('web-contents-created',{},wc);wc.emit('unresponsive');wc.emit('responsive');
  app.emit('render-process-gone',{},wc,{reason:'crashed',exitCode:9});
  app.emit('child-process-gone',{}, {type:'GPU',reason:'oom',exitCode:4,name:'PRIVATE'});
  app.emit('before-quit');app.emit('quit');proc.emit('exit',0);
  assert.ok(rows(dir).some(r=>r.event==='renderer-exit' && r.reason==='crashed'));
  assert.ok(rows(dir).some(r=>r.event==='child-exit' && r.type==='GPU'));
  assert.ok(!fs.readFileSync(path.join(dir,'events.jsonl'),'utf8').includes('PRIVATE'));
  assert.equal(JSON.parse(fs.readFileSync(path.join(dir,'session.json'))).clean,true);
});
test('crash reporter startup failure leaves diagnostics usable',t=>{
  const dir=fixture(t),app=new EventEmitter(),proc=new EventEmitter();app.setPath=()=>{};
  installDiagnostics({app,processObject:proc,directory:dir,crashReporter:{start(){throw Error('private');}}});
  assert.ok(rows(dir).some(r=>r.event==='local-crash-reporter-unavailable'));
});
