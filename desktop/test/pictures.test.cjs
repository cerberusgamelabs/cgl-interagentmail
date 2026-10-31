const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
test('picture viewer only resolves stored references and isolates each viewer', () => {
  const main = fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8');
  assert.match(main, /sender.searchParams.get\('mailbox'\) !== args.mailbox/);
  assert.match(main, /viewerImages.get\(event.sender.id\)/);
  assert.match(main, /viewerImages.delete\(viewerId\)/);
  assert.match(main, /size.width \* size.height > 40000000/);
  const viewer = fs.readFileSync(path.join(__dirname, '../public/viewer.js'), 'utf8');
  assert.doesNotMatch(viewer, /location.search|innerHTML|file:\/\//);
  assert.match(viewer, /call\('viewer-image'\)/);
});
