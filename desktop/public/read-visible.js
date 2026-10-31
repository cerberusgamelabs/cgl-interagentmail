(function(root) {
  function visibleIds(container, doc) {
    if (doc.hidden || !doc.hasFocus() || doc.querySelector('dialog[open]')) return [];
    const box = container.getBoundingClientRect();
    const viewport = doc.defaultView?.visualViewport;
    const top = Math.max(box.top, viewport?.offsetTop || 0);
    const bottom = Math.min(box.bottom, (viewport?.offsetTop || 0) + (viewport?.height || doc.defaultView?.innerHeight || Infinity));
    return [...container.querySelectorAll('[data-read-event]')].filter(node => {
      const rect = node.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && Math.min(rect.bottom,bottom)-Math.max(rect.top,top) >= Math.min(24,rect.height);
    }).map(node => node.dataset.readEvent);
  }
  function tracker(container, doc, submit) {
    const acknowledged = new Set(); let running = false;
    return async function check() {
      if (running) return;
      const ids = [...new Set(visibleIds(container,doc))].filter(id => !acknowledged.has(id)).slice(0,20);
      if (!ids.length) return;
      running = true;
      try { const result = await submit(ids); if (result !== false) ids.forEach(id => acknowledged.add(id)); }
      catch { /* Keep unread and retry on the next visible check. */ }
      finally { running = false; }
    };
  }
  const api = {visibleIds,tracker};
  if (typeof module !== 'undefined' && module.exports) module.exports=api;
  else root.IAMReadVisible=api;
})(typeof window !== 'undefined' ? window : globalThis);
