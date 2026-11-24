(function(root) {
  function pager() {
    const records = new Map();
    let oldest = null, more = true, capacity = 50;
    return {
      merge(page, older = false) {
        if (older) capacity += page.length;
        for (const row of page) records.set(`${row.notice_kind ? 'notice' : 'request'}:${row.id}`, row);
        if (older || oldest === null) {
          more = page.length === 50;
          if (page.length) oldest = [page[0].created, page[0].id];
        }
        const sorted = [...records.values()].sort((a,b) => a.created-b.created || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
        const removed = sorted.splice(0, Math.max(0, sorted.length-capacity));
        for (const row of removed) records.delete(`${row.notice_kind ? 'notice' : 'request'}:${row.id}`);
        if (removed.length) more = true;
        if (sorted.length) oldest = [sorted[0].created, sorted[0].id];
        return sorted;
      },
      get before() { return oldest; },
      get more() { return more; }
    };
  }
  function dayLabel(timestamp) {
    if(typeof timestamp!=='number' || !Number.isFinite(timestamp))return null;
    const date=new Date(timestamp*1000);if(Number.isNaN(date.getTime()))return null;
    const day=date.getDate(), suffix=day%100>=11 && day%100<=13?'th':({1:'st',2:'nd',3:'rd'}[day%10] || 'th');
    return {key:`${date.getFullYear()}-${date.getMonth()+1}-${day}`,label:`${date.toLocaleString('en-US',{month:'short'})} ${day}${suffix}, ${date.getFullYear()}`};
  }
  function create(container, build, buildDay) {
    let records = new Map();
    let initialized = false;
    return function update(messages) {
      const top = container.scrollTop;
      const follow = !initialized || container.scrollHeight - container.clientHeight - top <= 32;
      const edge = container.getBoundingClientRect().top;
      const anchor = [...container.children].find(node => node.getBoundingClientRect().bottom > edge);
      const anchorOffset = anchor ? anchor.getBoundingClientRect().top - edge : 0;
      // Build before touching live DOM, preserving it on render failure.
      const next = new Map();
      let previousDay=null;
      for (const message of messages) {
        const day=buildDay && dayLabel(message.created);
        if(day && day.key!==previousDay){
          const dividerKey=`day:${day.key}:${message.notice_kind?'notice':'request'}:${message.id}`;
          const old=records.get(dividerKey);
          next.set(dividerKey,{signature:day.label,node:old?.signature===day.label?old.node:buildDay(day.label)});
          previousDay=day.key;
        }
        const key = `${message.notice_kind ? 'notice' : 'request'}:${message.id}`;
        if (next.has(key)) throw new Error('Duplicate message identity');
        const signature = JSON.stringify(message);
        const old = records.get(key);
        const node = old?.signature === signature ? old.node : build(message);
        next.set(key, {signature, node});
      }
      let cursor = container.firstChild;
      for (const {node} of next.values()) {
        if (cursor === node) cursor = cursor.nextSibling;
        else container.insertBefore(node, cursor);
      }
      while (cursor) { const after = cursor.nextSibling; container.removeChild(cursor); cursor = after; }
      const anchorKey = [...records].find(([, value]) => value.node === anchor)?.[0];
      records = next;
      if (follow) container.scrollTop = container.scrollHeight;
      else {
        const retained = records.get(anchorKey)?.node;
        container.scrollTop = retained
          ? container.scrollTop + retained.getBoundingClientRect().top - edge - anchorOffset
          : top;
      }
      initialized = true;
    };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {create, pager, dayLabel};
  else root.IAMHistoryView = {create, pager, dayLabel};
})(typeof window !== 'undefined' ? window : globalThis);
