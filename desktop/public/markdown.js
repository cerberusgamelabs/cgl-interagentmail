// Deliberately small Markdown subset. Never parses HTML or creates links/images.
(function (scope) {
  function inline(parent, text, doc, depth = 0) {
    if (depth > 8) { parent.append(doc.createTextNode(text)); return; }
    const pattern = /(`+)([^`\n]+?)\1|\*\*([^\n]+?)\*\*|(?<![\p{L}\p{N}_])__([^\n]+?)__(?![\p{L}\p{N}_])|\*([^*\n]+?)\*|(?<![\p{L}\p{N}_])_([^_\n]+?)_(?![\p{L}\p{N}_])/gu;
    let offset = 0, match;
    while ((match = pattern.exec(text))) {
      parent.append(doc.createTextNode(text.slice(offset, match.index)));
      const code = match[2] !== undefined;
      const node = doc.createElement(code ? 'code' : match[3] !== undefined || match[4] !== undefined ? 'strong' : 'em');
      const content = match[2] ?? match[3] ?? match[4] ?? match[5] ?? match[6];
      if (code) node.textContent = content;
      else inline(node, content, doc, depth + 1);
      parent.append(node); offset = pattern.lastIndex;
    }
    parent.append(doc.createTextNode(text.slice(offset)));
  }
  function render(parent, text, doc = document) {
    parent.replaceChildren();
    const lines = String(text).replace(/\r\n?/g, '\n').split('\n');
    let i = 0;
    const fence = line => /^\s{0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    const list = line => /^\s{0,3}(?:([-+*])|(\d{1,9})[.)])\s+(.+)$/.exec(line);
    while (i < lines.length) {
      if (!lines[i].trim()) { i++; continue; }
      const start = fence(lines[i]);
      if (start) {
        // Be forgiving of a whole code span pasted on the opening-fence line.
        // Otherwise its contents would be discarded as a language label.
        const sameLine = new RegExp('^(.+?)' + start[1][0] + '{' + start[1].length + ',}\\s*$').exec(start[2]);
        if (sameLine) {
          const pre = doc.createElement('pre'), code = doc.createElement('code');
          code.textContent = sameLine[1]; pre.append(code); parent.append(pre); i++; continue;
        }
        const header = start[2], label = header.trim();
        // This renderer supports simple language hints, not arbitrary info strings.
        // Prose on the opening line must remain visible and inert.
        const languageHint = !label || /^[A-Za-z][A-Za-z0-9_+.#-]{0,31}$/.test(label);
        const body = languageHint ? [] : [header]; let closed = false; i++;
        while (i < lines.length) {
          const end = fence(lines[i]);
          if (end && end[1][0] === start[1][0] && end[1].length >= start[1].length && !end[2].trim()) { i++; closed = true; break; }
          body.push(lines[i++]);
        }
        if (!closed && languageHint && label && !body.some(line => line.trim())) body.unshift(header);
        const pre = doc.createElement('pre'), code = doc.createElement('code');
        code.textContent = body.join('\n'); pre.append(code); parent.append(pre); continue;
      }
      const first = list(lines[i]);
      if (first) {
        const ordered = first[2] !== undefined, node = doc.createElement(ordered ? 'ol' : 'ul');
        if (ordered) node.start = Number(first[2]);
        while (i < lines.length) {
          const item = list(lines[i]);
          if (!item || (item[2] !== undefined) !== ordered) break;
          const li = doc.createElement('li'); inline(li, item[3], doc); node.append(li); i++;
        }
        parent.append(node); continue;
      }
      const paragraph = [lines[i++]];
      while (i < lines.length && lines[i].trim() && !fence(lines[i]) && !list(lines[i])) paragraph.push(lines[i++]);
      const p = doc.createElement('p'); inline(p, paragraph.join('\n'), doc); parent.append(p);
    }
  }
  scope.IAMMarkdown = {render};
  if (typeof module !== 'undefined') module.exports = {render};
})(globalThis);
