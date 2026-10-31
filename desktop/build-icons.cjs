// Run with: desktop/node_modules/.bin/electron desktop/build-icons.cjs
// Mechanical resizing only: preserves the owner-approved artwork.
const {app, nativeImage} = require('electron');
const fs = require('node:fs');
const path = require('node:path');
app.whenReady().then(() => {
  const directory = path.join(__dirname, 'assets');
  const source = nativeImage.createFromPath(path.join(directory, 'iam-messenger-source.png'));
  if (source.isEmpty()) throw new Error('Approved icon source is missing or unreadable');
  const frames = [16, 20, 24, 32, 40, 48, 64, 128, 256].map(size => {
    const bytes = source.resize({width: size, height: size, quality: 'best'}).toPNG();
    fs.writeFileSync(path.join(directory, `iam-messenger-${size}.png`), bytes);
    return {size, bytes};
  });
  const header = Buffer.alloc(6 + 16 * frames.length);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(frames.length, 4);
  let offset = header.length;
  frames.forEach(({size, bytes}, index) => {
    const entry = 6 + index * 16;
    header[entry] = header[entry + 1] = size === 256 ? 0 : size;
    header.writeUInt16LE(1, entry + 4);
    header.writeUInt16LE(32, entry + 6);
    header.writeUInt32LE(bytes.length, entry + 8);
    header.writeUInt32LE(offset, entry + 12);
    offset += bytes.length;
  });
  fs.writeFileSync(path.join(directory, 'iam-messenger.ico'), Buffer.concat([header, ...frames.map(f => f.bytes)]));
  console.log('Built nine PNG sizes and the multi-resolution Windows icon.');
  app.quit();
}).catch(error => { console.error(error); app.exit(1); });
