const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {EventEmitter}=require('node:events');
const {spawnSync}=require('node:child_process');
const {runtimeRoot,preserveLegacyState}=require('../runtime-paths.cjs');
const {bootstrap,watchStartup}=require('../backend-startup.cjs');
const {project}=require('../diagnostics.cjs');
function fixture(t){const p=fs.mkdtempSync(path.join(os.tmpdir(),'iam startup '));t.after(()=>fs.rmSync(p,{recursive:true,force:true}));return p;}
test('runtime root is explicit, absolute and supports spaces',t=>{
  const p=fixture(t);assert.equal(runtimeRoot({IAM_DESKTOP_DATA_ROOT:p}),p);
  assert.throws(()=>runtimeRoot({}));assert.throws(()=>runtimeRoot({IAM_DESKTOP_DATA_ROOT:'relative'}));
});
test('legacy state copies only selected files, keeps originals and never overwrites',t=>{
  const p=fixture(t),old=path.join(p,'package'),data=path.join(p,'data');fs.mkdirSync(path.join(old,'run'),{recursive:true});
  fs.writeFileSync(path.join(old,'run','remote-desktop.secure'),'encrypted');
  fs.writeFileSync(path.join(old,'run','desktop-control.json'),'old token');
  preserveLegacyState(old,data);assert.equal(fs.readFileSync(path.join(data,'run','remote-desktop.secure'),'utf8'),'encrypted');
  assert.ok(!fs.existsSync(path.join(data,'run','desktop-control.json')));
  fs.writeFileSync(path.join(data,'run','remote-desktop.secure'),'new');preserveLegacyState(old,data);
  assert.equal(fs.readFileSync(path.join(data,'run','remote-desktop.secure'),'utf8'),'new');
  assert.ok(fs.existsSync(path.join(old,'run','remote-desktop.secure')));
});
test('stderr parsing handles chunks and projects away private fields',()=>{
  const stream=new EventEmitter(),events=[];watchStartup(stream,{event:(name,value)=>events.push({name,...project(value)})});
  stream.emit('data',Buffer.from('private raw error\nIAM_STARTUP_DIAG'));
  stream.emit('data',Buffer.from('NOSTIC '+JSON.stringify({pythonError:'RuntimeError',pythonFile:'iam_desktop.py',lineNumber:42,password:'SECRET'})+'\n'));
  assert.deepEqual(events,[{name:'backend-startup-failed',pythonError:'RuntimeError',pythonFile:'iam_desktop.py',lineNumber:42}]);
});
test('Python bootstrap reports sanitized real exceptions and propagates successful exit',t=>{
  const p=fixture(t),python=process.env.IAM_TEST_PYTHON||path.resolve(__dirname,'../../.venv/Scripts/python.exe');
  fs.writeFileSync(path.join(p,'iam_desktop.py'),"raise RuntimeError('SECRET password and body')\n");
  const failed=spawnSync(python,['-X','utf8','-c',bootstrap],{cwd:p,encoding:'utf8'});
  assert.equal(failed.status,1);assert.ok(!failed.stderr.includes('SECRET'));
  const record=JSON.parse(failed.stderr.trim().slice(23));assert.equal(record.pythonError,'RuntimeError');assert.equal(record.lineNumber,1);
  fs.writeFileSync(path.join(p,'iam_desktop.py'),"print('ready')\n");
  const ok=spawnSync(python,['-X','utf8','-c',bootstrap],{cwd:p,encoding:'utf8'});
  assert.equal(ok.status,0);assert.equal(ok.stdout.trim(),'ready');assert.equal(ok.stderr,'');
});
