/* Local-only emoji picker. No remote assets, tracking, or message submission. */
(function () {
  const entries = [
    ['😀','grinning happy smile'], ['😄','smile happy laugh'], ['😁','grin beam'], ['😂','laugh tears funny'],
    ['🤣','rolling laughing'], ['😊','blush happy smile'], ['😉','wink'], ['😍','love heart eyes'],
    ['🥰','love hearts'], ['😘','kiss'], ['😎','cool sunglasses'], ['🤓','nerd glasses'],
    ['🤔','thinking'], ['🙃','upside down'], ['😅','sweat relief'], ['🥹','happy tears touched'],
    ['😭','cry sob'], ['😢','sad tear'], ['😮','surprised'], ['😴','sleep tired'],
    ['😤','frustrated'], ['😬','grimace'], ['🫠','melting'], ['🤗','hug'],
    ['👋','wave hello'], ['👍','thumbs up yes'], ['👎','thumbs down no'], ['👏','clap applause'],
    ['🙌','celebrate hands'], ['🙏','thanks please'], ['🤞','fingers crossed luck'], ['🫶','heart hands'],
    ['❤️','red heart love'], ['🧡','orange heart'], ['💛','yellow heart'], ['💚','green heart'],
    ['💙','blue heart'], ['💜','purple heart'], ['🖤','black heart'], ['💕','two hearts'],
    ['✨','sparkles'], ['🎉','party celebration'], ['🎊','confetti'], ['🔥','fire'],
    ['🌈','rainbow'], ['🌙','moon night'], ['☀️','sun morning'], ['🌻','sunflower'],
    ['🦊','fox'], ['🐈','cat'], ['🐶','dog'], ['🐉','dragon'],
    ['☕','coffee tea'], ['🍵','tea'], ['🍰','cake'], ['🍕','pizza'],
    ['🎨','art paint'], ['🎵','music'], ['🎮','game controller'], ['💻','computer code'],
    ['🛠️','tools repair'], ['🐛','bug'], ['🚀','rocket'], ['✅','check done']
  ];
  const assetNames = new Map(entries.map(([emoji]) => [emoji.replace(/\ufe0f/g, ''),
    [...emoji].filter(c => c !== '\ufe0f').map(c => c.codePointAt(0).toString(16)).join('-')]));
  function asset(emoji) {
    const name = assetNames.get(emoji.replace(/\ufe0f/g, ''));
    return name ? 'emoji/' + name + '.png' : null;
  }
  function image(emoji, doc = document) {
    const img = doc.createElement('img'); img.className = 'emoji-art'; img.src = asset(emoji);
    img.alt = emoji; img.draggable = false;
    img.onerror = () => img.replaceWith(doc.createTextNode(emoji));
    return img;
  }
  function decorate(root) {
    // Whole graphemes only: never split skin tones, flags or joined sequences.
    const doc = root.ownerDocument;
    const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
    const segmenter = new Intl.Segmenter(undefined, {granularity: 'grapheme'});
    for (const node of nodes) {
      if (node.parentElement.closest('code, pre, textarea')) continue;
      const parts = [...segmenter.segment(node.textContent)].map(part => part.segment);
      if (!parts.some(asset)) continue;
      const fragment = doc.createDocumentFragment();
      for (const part of parts) fragment.append(asset(part) ? image(part, doc) : doc.createTextNode(part));
      node.replaceWith(fragment);
    }
  }
  function replacement(value, start, end, emoji, maxLength) {
    const next = value.slice(0, start) + emoji + value.slice(end);
    return maxLength >= 0 && next.length > maxLength ? null : {value: next, caret: start + emoji.length};
  }
  function mount(compose, input, notice, toolbar) {
    const button = document.createElement('button'); button.type = 'button'; button.id = 'emojiToggle';
    button.append(image('😊')); button.title = 'Choose an emoji'; button.setAttribute('aria-label', 'Choose an emoji');
    button.setAttribute('aria-expanded', 'false'); button.setAttribute('aria-controls', 'emojiPicker');
    const panel = document.createElement('section'); panel.id = 'emojiPicker'; panel.hidden = true;
    panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', 'Emoji picker');
    const top = document.createElement('div'); top.className = 'emoji-top';
    const search = document.createElement('input'); search.type = 'search'; search.placeholder = 'Find an emoji…'; search.setAttribute('aria-label', 'Search emojis');
    const close = document.createElement('button'); close.type = 'button'; close.textContent = '×'; close.setAttribute('aria-label', 'Close emoji picker');
    const grid = document.createElement('div'); grid.className = 'emoji-grid';
    const credit = document.createElement('small'); credit.className = 'emoji-credit'; credit.textContent = 'Twemoji · CC BY 4.0';
    credit.title = 'Twitter and contributors · github.com/jdecked/twemoji · creativecommons.org/licenses/by/4.0/';
    top.append(search, close); panel.append(top, grid, credit); compose.append(panel); toolbar.append(button);
    let selection = [0, 0];
    function hide(restore = false) { panel.hidden = true; button.setAttribute('aria-expanded', 'false'); if (restore) button.focus(); }
    function render() {
      grid.replaceChildren();
      const matches = entries.filter(([emoji, name]) => (emoji + ' ' + name).includes(search.value.trim().toLowerCase()));
      for (const [emoji, name] of matches) {
        const item = document.createElement('button'); item.type = 'button'; item.append(image(emoji)); item.title = name; item.setAttribute('aria-label', name);
        item.onclick = () => {
          const next = replacement(input.value, ...selection, emoji, input.maxLength);
          if (!next) { notice('This emoji would exceed the message length limit.'); return; }
          input.value = next.value; hide(); input.focus(); input.setSelectionRange(next.caret, next.caret);
          input.dispatchEvent(new Event('input', {bubbles: true}));
        };
        grid.append(item);
      }
      if (!matches.length) { const empty = document.createElement('p'); empty.textContent = 'No matching emojis'; grid.append(empty); }
    }
    button.onclick = () => {
      if (!panel.hidden) { hide(true); return; }
      selection = [input.selectionStart, input.selectionEnd]; search.value = ''; render();
      panel.hidden = false; button.setAttribute('aria-expanded', 'true'); search.focus();
    };
    close.onclick = () => hide(true); search.oninput = render;
    panel.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); hide(true); }
      if (event.key === 'Enter' && event.target === search) event.preventDefault();
    });
    document.addEventListener('pointerdown', event => { if (!panel.contains(event.target) && !button.contains(event.target)) hide(); });
    document.addEventListener('focusin', event => { if (!panel.contains(event.target) && event.target !== button) hide(); });
    compose.addEventListener('submit', () => hide());
  }
  if (typeof module !== 'undefined') module.exports = {replacement, entries, asset};
  if (typeof window !== 'undefined') window.IAMEmoji = {mount, decorate};
})();
