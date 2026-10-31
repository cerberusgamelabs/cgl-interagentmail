const fs=require('node:fs');
const path=require('node:path');
const {randomUUID}=require('node:crypto');

// Deliberately omit messages, stacks, URLs, argv, mailbox names and IPC payloads.
const numbers=new Set(['exitCode','processId','webContentsId']);
const enums={reason:new Set(['clean-exit','abnormal-exit','killed','crashed','oom','launch-failed','integrity-failure']),
  type:new Set(['Browser','GPU','Utility','Zygote','Sandbox helper','Pepper Plugin','Pepper Plugin Broker','renderer']),
  signal:new Set(['SIGTERM','SIGKILL','SIGABRT','SIGSEGV','SIGINT']),
  errorType:new Set(['Error','TypeError','RangeError','SyntaxError','ReferenceError','URIError','EvalError']),
  origin:new Set(['uncaughtException','unhandledRejection'])};
function project(details={}){
  const result={};
  for(const [key,value] of Object.entries(details)){
    if(numbers.has(key) && Number.isSafeInteger(value))result[key]=value;
    if(enums[key])result[key]=enums[key].has(value)?value:'unknown';
  }
  return result;
}
function createDiagnostics(directory,{maxBytes=1024*1024,archives=3}={}){
  const sessionId=randomUUID(),log=path.join(directory,'events.jsonl'),marker=path.join(directory,'session.json');
  function event(name,details={}){
    try{
      if(!/^[a-z][a-z0-9-]{0,63}$/.test(name))return false;
      fs.mkdirSync(directory,{recursive:true});
      const line=JSON.stringify({time:new Date().toISOString(),sessionId,pid:process.pid,event:name,...project(details)})+'\n';
      if(fs.existsSync(log) && fs.statSync(log).size+Buffer.byteLength(line)>maxBytes){
        for(let i=archives;i>=1;i--){const target=log+'.'+i,source=i===1?log:log+'.'+(i-1);
          if(fs.existsSync(target))fs.unlinkSync(target);
          if(fs.existsSync(source))fs.renameSync(source,target);
        }
      }
      fs.appendFileSync(log,line,{mode:0o600});return true;
    }catch{return false;} // Diagnostics must never take down Messenger.
  }
  function mark(clean){try{fs.mkdirSync(directory,{recursive:true});fs.writeFileSync(marker,JSON.stringify({sessionId,pid:process.pid,clean}),{mode:0o600});}catch{}}
  function start(){
    try{const previous=JSON.parse(fs.readFileSync(marker,'utf8'));if(previous.clean===false)event('previous-session-unclean',{processId:previous.pid});}
    catch(error){if(error.code!=='ENOENT')event('previous-marker-unreadable');}
    mark(false);event('started');
  }
  function cleanExit(){event('clean-exit');mark(true);}
  return {event,start,cleanExit};
}
function installDiagnostics({app,crashReporter,processObject=process,directory}){
  const log=createDiagnostics(directory);log.start();
  try{
    const dumps=path.join(directory,'dumps');fs.mkdirSync(dumps,{recursive:true});
    app.setPath('crashDumps',dumps);
    crashReporter.start({uploadToServer:false,productName:'IAM Messenger'});
    log.event('local-crash-reporter-started');
  }catch{log.event('local-crash-reporter-unavailable');}
  processObject.on('uncaughtExceptionMonitor',(error,origin)=>log.event('uncaught-exception',{errorType:error?.name,origin}));
  // Monitor only: do not swallow fatal errors or change Node's rejection policy.
  processObject.on('exit',exitCode=>log.event('process-exit',{exitCode}));
  app.on('render-process-gone',(_event,contents,details)=>log.event('renderer-exit',{webContentsId:contents.id,reason:details.reason,exitCode:details.exitCode}));
  app.on('child-process-gone',(_event,details)=>log.event('child-exit',{type:details.type,reason:details.reason,exitCode:details.exitCode}));
  app.on('web-contents-created',(_event,contents)=>{
    contents.on('unresponsive',()=>log.event('renderer-unresponsive',{webContentsId:contents.id}));
    contents.on('responsive',()=>log.event('renderer-responsive',{webContentsId:contents.id}));
  });
  app.on('before-quit',()=>log.event('quit-requested'));
  app.on('quit',()=>log.cleanExit());
  return log;
}
module.exports={createDiagnostics,installDiagnostics,project};
