const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
test('Windows icon contains all expected PNG sizes', () => {
  const assets = path.join(__dirname, '../assets');
  const ico = fs.readFileSync(path.join(assets, 'iam-messenger.ico'));
  const sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256];
  assert.equal(ico.readUInt16LE(2), 1);
  assert.equal(ico.readUInt16LE(4), sizes.length);
  sizes.forEach((size, index) => {
    const entry = 6 + 16 * index;
    assert.equal(ico[entry] || 256, size);
    assert.equal(ico[entry + 1] || 256, size);
    const png = fs.readFileSync(path.join(assets, `iam-messenger-${size}.png`));
    assert.equal(png.readUInt32BE(16), size);
    assert.equal(png.readUInt32BE(20), size);
    const start = ico.readUInt32LE(entry + 12);
    assert.deepEqual(ico.subarray(start, start + ico.readUInt32LE(entry + 8)), png);
  });
});
test('all desktop windows and tray use the approved icon', () => {
  const source = fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8');
  assert.equal((source.match(/new BrowserWindow\(/g) || []).length, 4);
  assert.equal((source.match(/new BrowserWindow\(\{icon: appIcon/g) || []).length, 4);
  assert.ok(source.includes('new Tray(appIcon)'));
});
