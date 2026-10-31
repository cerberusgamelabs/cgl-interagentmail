// Only verified attachment bytes supplied by the caller; never remote image URLs.
export function imagePreviews({document,Observer=globalThis.IntersectionObserver}){
  const cache=new Map();let observer;
  const jobs=new WeakMap();
  if(Observer)observer=new Observer(entries=>{for(const e of entries)if(e.isIntersecting){observer.unobserve(e.target);jobs.get(e.target)?.();}},{rootMargin:'80px'});
  function render(parent,items){
    if(!items.length)return;
    const grid=document.createElement('div');grid.className='image-grid';grid.dataset.count=String(Math.min(4,items.length));parent.append(grid);
    for(const item of items.slice(0,4)){
      const button=document.createElement('button');button.className='image-tile';button.type='button';button.textContent='Load image';button.setAttribute('aria-label',item.label);grid.append(button);
      let loading=false,loaded=null;
      const active=()=>!document.hidden && item.active() && button.isConnected!==false;
      async function load(){
        if(loading || !active())return null;loading=true;
        try{
          let promise=cache.get(item.key);
          if(!promise){promise=item.load();cache.set(item.key,promise);while(cache.size>8)cache.delete(cache.keys().next().value);}
          const image=await promise;
          if(!active())return null;
          if(!['image/png','image/jpeg','image/webp'].includes(image.mime) || typeof image.data!=='string' || !/^[A-Za-z0-9+/=]+$/.test(image.data))throw Error('Invalid preview');
          const img=document.createElement('img');img.alt=item.label;img.src=`data:${image.mime};base64,${image.data}`;button.replaceChildren(img);loaded=image;return image;
        }catch{cache.delete(item.key);if(active())button.textContent='Retry image';return null;}
        finally{loading=false;}
      }
      jobs.set(button,load);observer?.observe(button);
      button.onclick=async()=>{const image=loaded || await load();if(image && active())await item.open(image);};
    }
  }
  return {render,reset(){observer?.disconnect();},clear(){cache.clear();observer?.disconnect();}};
}
