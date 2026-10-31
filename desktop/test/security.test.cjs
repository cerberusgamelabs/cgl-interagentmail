const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
test('desktop has isolated renderer, local authenticated serving and narrow IPC', () => {
  const main=fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8');
  assert.match(main,/listen\(0,'127\.0\.0\.1'/);
  assert.match(main,/contextIsolation: true, sandbox: true/);
  assert.match(main,/nodeIntegration: false/);
  assert.match(main,/req.headers\['x-iam-desktop'\] !== token/);
  assert.match(main,/senderFrame !== event.sender.mainFrame/);
  assert.doesNotMatch(main,/shell\.openExternal|executeJavaScript\(.*body/);
  const ui=fs.readFileSync(path.join(__dirname,'../public/app.js'),'utf8');
  assert.doesNotMatch(ui,/innerHTML|outerHTML|insertAdjacentHTML/);
  assert.match(ui,/IAMMarkdown.render\(bubble, text\)/);
  const markdown=fs.readFileSync(path.join(__dirname,'../public/markdown.js'),'utf8');
  assert.doesNotMatch(markdown,/innerHTML|outerHTML|insertAdjacentHTML/);
});

test('routine delivery states use accessible receipts and leave exceptional states visible', () => {
  const ui = fs.readFileSync(path.join(__dirname, '../public/app.js'), 'utf8');
  const symbols = require('node:vm').runInNewContext('(' + ui.match(/const symbols = (\{[^\n]+\});/)[1] + ')');
  assert.equal(symbols.queued, '');
  assert.equal(symbols.submitting, '·');
  assert.equal(symbols.accepted, '•');
  assert.equal(symbols.replied, '✓');
  assert.match(ui, /Accepted by session \(not a read receipt\)/);
  assert.match(ui, /message\.reply != null \? 'replied' : message\.error === 'Turn finished; no reply published\.'/);
  assert.match(ui, /'no-reply': 'Turn finished; no reply published\.'/);
  assert.match(ui, /if \(message\.error \|\| !\['queued', 'submitting', 'accepted', 'replied'\]\.includes\(message\.state\)\)/);
  assert.match(ui, /receipt\.setAttribute\('aria-label', labels\[delivery\]\)/);
});
