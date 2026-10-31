// Receipt means delivered to a visible foreground view, not comprehension.
export function friendVisibleReceipts({document,active,send,Observer=globalThis.IntersectionObserver}){
  let epoch=0,observer=null,pending=new Set(),done=new Set(),sending=false;
  const eligible=()=>active() && !document.hidden && document.hasFocus?.()===true;
  async function flush(){
    if(sending || !eligible() || !pending.size)return;
    const version=epoch,ids=[...pending].slice(0,50);ids.forEach(id=>pending.delete(id));sending=true;
    try{await send(ids);if(version===epoch)ids.forEach(id=>done.add(id));}
    catch{/* A later observed visibility transition may retry; never a timer loop. */}
    finally{sending=false;if(pending.size)queueMicrotask(flush);}
  }
  function reset(){epoch++;observer?.disconnect();observer=null;pending.clear();done.clear();}
  function watch(rows){
    observer?.disconnect();pending.clear();
    if(!Observer)return; // No visibility evidence: do not fabricate a receipt.
    const version=epoch;
    observer=new Observer(entries=>{
      if(version!==epoch || !eligible())return;
      for(const entry of entries){const id=entry.target.dataset.friendMessage;
        if(entry.isIntersecting && entry.intersectionRatio>=0.5 && id && !done.has(id))pending.add(id);
        else if(id)pending.delete(id);
      }
      queueMicrotask(flush);
    },{threshold:[0,0.5]});
    for(const row of rows)observer.observe(row);
  }
  return {reset,watch};
}
