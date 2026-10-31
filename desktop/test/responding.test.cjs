const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
test('responding indicator is scoped, accessible, expires and reserves layout space', () => {
  const ui = fs.readFileSync(path.join(__dirname, '../public/app.js'), 'utf8');
  const css = fs.readFileSync(path.join(__dirname, '../public/style.css'), 'utf8');
  assert.match(ui, /buddy\?\.responding/);
  assert.match(ui, /setTimeout\(clearResponding, 7000\)/);
  assert.match(ui, /respondingIndicator.setAttribute\('aria-live', 'polite'\)/);
  assert.match(css, /\.responding-indicator\{height:20px/);
  assert.match(css, /prefers-reduced-motion:reduce/);
});
