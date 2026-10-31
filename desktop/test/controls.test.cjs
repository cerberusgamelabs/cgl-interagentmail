const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
test('session controls use separate sandboxed windows and bind mailbox to sender', () => {
  const main = fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8');
  assert.match(main, /function controlWindow\(mailbox, kind, parent\)/);
  assert.match(main, /parent, modal: false/);
  assert.match(main, /args.mailbox !== mailbox/);
  assert.match(main, /sender.pathname !== '\/controls.html'/);
  const ui = fs.readFileSync(path.join(__dirname, '../public/controls.js'), 'utf8');
  assert.doesNotMatch(ui, /innerHTML|outerHTML|insertAdjacentHTML/);
  assert.match(ui, /call\('prepare'/);
  assert.match(ui, /call\('confirm'/);
});
