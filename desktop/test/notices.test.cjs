const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
test('notices are intentional separate UI, inert text, with explicit dismissal', () => {
  const ui = fs.readFileSync(path.join(__dirname, '../public/app.js'), 'utf8');
  assert.match(ui, /Infrastructure alert/);
  assert.match(ui, /Agent notice/);
  assert.match(ui, /body.textContent = item.body/);
  assert.match(ui, /dismiss-notice/);
  assert.match(ui, /previousNotices/);
  assert.match(ui, /blank.textContent = 'Choose/);
  assert.match(ui, /decision, content/);
});
