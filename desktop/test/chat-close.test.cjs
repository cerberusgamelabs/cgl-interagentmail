const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('chat lifecycle is main-owned and close follows registration', () => {
  const source = fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8');
  assert.match(source, /window_id: windowId, opened/);
  assert.match(source, /registeredWindow\.then\(\(\) => trackWindow\(false\)\)/);
  assert.match(source, /if \(action === 'chat-window'\) throw new Error/);
  assert.match(source, /buddy \? Promise\.resolve\(\) : trackWindow\(true\)/);
});

test('cached context is labeled last-known in chat and status', () => {
  const app = fs.readFileSync(path.join(__dirname, '../public/app.js'), 'utf8');
  const status = fs.readFileSync(path.join(__dirname, '../public/status.js'), 'utf8');
  assert.match(app, /context\?\.stale.*last known/);
  assert.match(status, /context\.stale.*Last known/);
});
