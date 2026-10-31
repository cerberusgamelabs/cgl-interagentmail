const test = require('node:test');
const assert = require('node:assert/strict');
const {replacement, entries, asset} = require('../public/emoji.js');
test('every picker emoji has a bundled PNG and unknown sequences never become URLs', () => {
  const fs = require('node:fs'), path = require('node:path');
  for (const [emoji] of entries) {
    const bytes = fs.readFileSync(path.join(__dirname, '../public', asset(emoji)));
    assert.equal(bytes.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
  }
  assert.equal(asset('🥹'), 'emoji/1f979.png');
  assert.equal(asset('🫠'), 'emoji/1fae0.png');
  assert.equal(asset('🫶'), 'emoji/1faf6.png');
  assert.equal(asset('https://example.com'), null);
  assert.equal(asset('👍🏽'), null);
});
test('emoji inserts at the cursor and replaces selected text', () => {
  assert.deepEqual(replacement('Hello !', 6, 6, '😊', 100), {value: 'Hello 😊!', caret: 8});
  assert.deepEqual(replacement('Hello sad!', 6, 9, '❤️', 100), {value: 'Hello ❤️!', caret: 8});
});
test('emoji respects textarea UTF-16 length limit including selections', () => {
  assert.equal(replacement('1234', 4, 4, '😊', 5), null);
  assert.deepEqual(replacement('1234', 2, 4, '😊', 4), {value: '12😊', caret: 4});
});
test('picker is a local overlay with explicit non-submit buttons', () => {
  const fs = require('node:fs'), path = require('node:path');
  const css = fs.readFileSync(path.join(__dirname, '../public/style.css'), 'utf8');
  const js = fs.readFileSync(path.join(__dirname, '../public/emoji.js'), 'utf8');
  assert.match(css, /#emojiPicker\{position:absolute;bottom:calc\(100% \+ 10px\)/);
  assert.doesNotMatch(js, /fetch\(|innerHTML|requestSubmit/);
  assert.ok(entries.length >= 60);
  assert.equal(new Set(entries.map(e => e[0])).size, entries.length);
});
test('composer tools are small round controls in their own row above the input', () => {
  const fs = require('node:fs'), path = require('node:path');
  const css = fs.readFileSync(path.join(__dirname, '../public/style.css'), 'utf8');
  const ui = fs.readFileSync(path.join(__dirname, '../public/app.js'), 'utf8');
  assert.match(css, /#composeTools\{grid-column:1\/-1/);
  assert.match(css, /#composeTools button\{[^}]*width:28px;[^}]*border-radius:50%/);
  assert.match(css, /#attachButton::before,#attachButton::after\{[^}]*left:50%;top:50%;transform:translate\(-50%,-50%\)/);
  assert.match(css, /#attachButton::before\{width:12px;height:2px\}/);
  assert.match(css, /#attachButton::after\{width:2px;height:12px\}/);
  assert.match(ui, /attachButton.setAttribute\('aria-label', 'Attach images or documents'\)/);
});
