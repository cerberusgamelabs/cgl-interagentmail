const path = require('node:path');
const fs = require('node:fs');

function attachment(item, image) {
  let name = path.win32.basename(String(item.name || 'attachment')).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').replace(/[. ]+$/, '');
  if (!name || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name)) name = 'attachment-' + name;
  let encoded = item.data, extensions;
  if (image) {
    const match = /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$/.exec(encoded);
    if (!match) throw new Error('Unsupported image');
    extensions = match[1] === 'jpeg' ? ['jpg', 'jpeg'] : [match[1]];
    encoded = match[2];
    if (!extensions.includes(path.extname(name).slice(1).toLowerCase())) name += '.' + extensions[0];
  } else {
    const ext = path.extname(name).slice(1).toLowerCase();
    if (!['txt','md','csv','json','log','pdf','docx','xlsx','pptx'].includes(ext)) throw new Error('Unsupported document');
    extensions = [ext];
  }
  if (typeof encoded !== 'string' || encoded.length > 5500000 || !/^[A-Za-z0-9+/=]*$/.test(encoded)) throw new Error('Invalid attachment');
  const bytes = Buffer.from(encoded, 'base64');
  if (bytes.length > 4000000) throw new Error('Attachment exceeds 4 MB');
  return {name, bytes, extensions};
}

async function saveAttachment(dialog, parent, item, image = false, writeFile = fs.promises.writeFile) {
  const {name, bytes, extensions} = attachment(item, image);
  const selected = await dialog.showSaveDialog(parent, {
    title: image ? 'Download shared photo' : 'Download shared document',
    defaultPath: name, filters: [{name: image ? 'Image' : 'Document', extensions}],
  });
  if (selected.canceled || !selected.filePath) return false;
  await writeFile(selected.filePath, bytes);
  return true;
}

module.exports = {saveAttachment};
