'use strict';
// One recovery poll for the application, never one poll per window.
function createUpdateFeed(fetch, publish, {interval=2000}={}) {
  let pending=null, latest=null, signature='', timer=null, stopped=false;
  async function poll() {
    if (pending) return pending;
    pending=(async()=>{
      const value=await fetch();
      const next=JSON.stringify(value);
      latest=value;
      if (!stopped && next!==signature) {signature=next;publish(value);}
      return value;
    })();
    try {return await pending;} finally {pending=null;}
  }
  async function tick() {
    try {await poll();} catch { /* Keep last view; next poll reconciles durable state. */ }
    if (!stopped) timer=setTimeout(tick,interval);
  }
  return {
    snapshot:()=>latest ? Promise.resolve(latest) : poll(),
    refresh:poll,
    start(){if(timer!==null)return;stopped=false;timer=setTimeout(tick,0);},
    stop(){stopped=true;clearTimeout(timer);timer=null;}
  };
}
module.exports={createUpdateFeed};
