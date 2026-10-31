const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {saveAttachment} = require('../attachment-save.cjs');

test('photo download preserves bytes, strips paths and uses MIME extension', async () => {
  const bytes = Buffer.from('fixture-original-image'); let options, written;
  const dialog = {showSaveDialog: async (parent, value) => { assert.equal(parent, 'viewer'); options = value; return {filePath:'C:/chosen/photo.png'}; }};
  assert.equal(await saveAttachment(dialog, 'viewer', {name:'../../picture.exe', data:'data:image/png;base64,'+bytes.toString('base64')}, true,
    async (target, data) => {written = {target,data};}), true);
  assert.equal(options.defaultPath, 'picture.exe.png');
  assert.equal(written.target, 'C:/chosen/photo.png');
  assert.deepEqual(written.data, bytes);
});

test('document cancel performs no write and never opens external content', async () => {
  let writes = 0;
  const result = await saveAttachment({showSaveDialog:async()=>({canceled:true,filePath:'ignored'})}, null,
    {name:'note.txt',data:Buffer.from('hello').toString('base64')}, false, async()=>{writes++;});
  assert.equal(result, false); assert.equal(writes, 0);
});

test('document save retains exact bytes and cleans reserved filenames', async () => {
  const bytes = Buffer.from([0,1,2,255]); let suggested;
  await saveAttachment({showSaveDialog:async (_p,opts)=>{suggested=opts.defaultPath;return {filePath:'C:/chosen/doc.pdf'};}},null,
    {name:'C:\\private\\CON.pdf',data:bytes.toString('base64')},false,async (_p,data)=>assert.deepEqual(data,bytes));
  assert.equal(suggested,'attachment-CON.pdf');
});

test('unsupported or oversized data cannot open save picker', async () => {
  const dialog = {showSaveDialog:async()=>assert.fail('picker must not open')};
  await assert.rejects(saveAttachment(dialog,null,{name:'x.exe',data:'YQ=='}));
  await assert.rejects(saveAttachment(dialog,null,{name:'x.svg',data:'data:image/svg+xml;base64,YQ=='},true));
  await assert.rejects(saveAttachment(dialog,null,{name:'x.txt',data:Buffer.alloc(4000001).toString('base64')}));
});

test('write failure reaches caller without reporting success', async () => {
  await assert.rejects(saveAttachment({showSaveDialog:async()=>({filePath:'chosen.txt'})},null,
    {name:'x.txt',data:'YQ=='},false,async()=>{throw new Error('disk full');}),/disk full/);
});

test('viewer saves use main-process owned attachments, not renderer supplied bytes', () => {
  const main = fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8');
  for (const [action,map] of [['viewer-image-save','viewerImages'],['viewer-document-save','viewerDocuments']]) {
    const block = main.slice(main.indexOf(`if (action === '${action}')`));
    const handler = block.slice(0,block.indexOf('\n    }'));
    assert.ok(handler.includes(`${map}.get(event.sender.id)`));
    assert.ok(handler.includes('saveAttachment('));
    assert.ok(!handler.includes('args.'));
  }
  for (const name of ['viewer','document']) {
    const js = fs.readFileSync(path.join(__dirname,`../public/${name}.js`),'utf8');
    assert.match(js,/button.disabled = true/);
    assert.match(js,/finally \{ button.disabled = false; \}/);
  }
});
