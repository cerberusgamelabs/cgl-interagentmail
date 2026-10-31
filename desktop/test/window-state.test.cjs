const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const state = require('../window-state.cjs');

test('window metadata round-trips without message bodies or confirmation state', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'iam-window-test-'));
  try {
    const file = path.join(dir, 'windows.json');
    const entries = [{mailbox: 'One', bounds: {x: 20, y: 40, width: 650, height: 630}, maximized: true,
      body: 'private draft', ticket: 'never restore'}, {mailbox: 'One'}, null];
    state.write(file, entries);
    assert.deepEqual(state.read(file), [{mailbox: 'One', bounds: {x: 20, y: 40, width: 650, height: 630}, maximized: true}]);
    assert.doesNotMatch(fs.readFileSync(file, 'utf8'), /private draft|ticket/);
    fs.writeFileSync(file, 'broken');
    assert.deepEqual(state.read(file), []);
  } finally { fs.rmSync(dir, {recursive: true}); }
});

test('restored geometry fits remaining displays and rejects malformed bounds', () => {
  const screen = [{x: 0, y: 0, width: 1920, height: 1080}];
  assert.deepEqual(state.fit({x: 2500, y: -500, width: 650, height: 630}, screen),
    {x: 1270, y: 0, width: 650, height: 630});
  assert.deepEqual(state.fit({x: 0, y: 0, width: 8000, height: 5000}, screen),
    {x: 0, y: 0, width: 1920, height: 1080});
  assert.equal(state.clean([{mailbox: 'One', bounds: {x: 'invalid'}}])[0].bounds, undefined);
});

test('restoration validates roster, never restores control dialogs, and reconnects through the guarded path', () => {
  const main = fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8');
  const ui = fs.readFileSync(path.join(__dirname, '../public/app.js'), 'utf8');
  assert.match(main, /allowed\.has\(saved.mailbox\)/);
  assert.match(main, /\[\.\.\.windows\]/);
  assert.doesNotMatch(main, /\[\.\.\.controlWindows\]/);
  assert.match(ui, /IAMReconnect\.controller\(call, mailbox/);
  assert.doesNotMatch(ui, /get\('restored'\) !== '1'/);
  assert.match(main, /if \(!stopping\) saveWindows\(\)/);
  assert.match(main, /stopping=true; backendRpc\?\.stop\(\); saveWindows\(\)/);
});
