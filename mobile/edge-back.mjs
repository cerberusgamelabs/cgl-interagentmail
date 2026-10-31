// Only an inward gesture starting at a screen edge can navigate.
export function edgeBack({startX,startY,endX,endY,width,elapsed}){
  if(elapsed<0 || elapsed>900 || Math.abs(endY-startY)>30)return false;
  return startX<=18 && endX-startX>=64 || startX>=width-18 && startX-endX>=64;
}
export function installEdgeBack(target,{enabled,back,width=()=>globalThis.innerWidth,now=()=>Date.now()}){
  let start=null;
  target.addEventListener('pointerdown',event=>{
    start=null;
    if(!enabled() || event.isPrimary===false || event.pointerType!=='touch' || event.target?.closest?.('input,textarea,[contenteditable="true"],dialog'))return;
    if(event.clientX>18 && event.clientX<width()-18)return;
    start={id:event.pointerId,startX:event.clientX,startY:event.clientY,width:width(),time:now()};
  },{passive:true});
  target.addEventListener('pointercancel',()=>{start=null;},{passive:true});
  target.addEventListener('pointerup',event=>{
    const gesture=start;start=null;
    if(!gesture || !enabled() || event.pointerId!==gesture.id)return;
    if(edgeBack({...gesture,endX:event.clientX,endY:event.clientY,elapsed:now()-gesture.time}))back();
  },{passive:true});
}
