(function(root) {
  function create(container, build) {
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
      for (const message of messages) {
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
  if (typeof module !== 'undefined' && module.exports) module.exports = {create};
  else root.IAMHistoryView = {create};
})(typeof window !== 'undefined' ? window : globalThis);
