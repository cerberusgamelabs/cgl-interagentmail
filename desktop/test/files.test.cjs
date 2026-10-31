const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = name => fs.readFileSync(path.join(__dirname, '..', name), 'utf8');
test('document operations require mailbox-bound chat windows and explicit native actions', () => {
  const main = read('main.cjs');
  assert.match(main, /sender\.pathname !== '\/' \|\| sender\.searchParams\.get\('mailbox'\) !== args\.mailbox/);
  assert.match(main, /rpc\('document', args\)/);
  assert.match(read('attachment-save.cjs'), /dialog\.showSaveDialog/);
  assert.match(main, /dialog\.showMessageBox/);
  assert.match(main, /defaultId:0, cancelId:0/);
  assert.match(main, /\['\.pdf','\.docx','\.xlsx','\.pptx'\]\.includes\(ext\)/);
  assert.match(main, /viewerDocuments\.get\(event\.sender\.id\)/);
});
test('document previews are inert text and cards expose only intentional controls', () => {
  const viewer = read('public/document.js');
  assert.match(viewer, /textContent = item\.text/);
  assert.doesNotMatch(viewer, /innerHTML|eval\(|iframe|fetch\(/);
  const ui = read('public/app.js');
  assert.match(ui, /message\.reply_files/);
  assert.match(ui, /'file-save', 'Save As/);
  assert.match(ui, /'file-preview' : 'file-open'/);
  assert.match(ui, /JSON\.stringify\(\{body, images, files\}\)/);
});
