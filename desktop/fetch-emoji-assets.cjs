// Maintainer-only asset import; never used at application runtime.
const fs = require('node:fs');
const path = require('node:path');
const {entries} = require('./public/emoji.js');
const base = 'https://raw.githubusercontent.com/jdecked/twemoji/v16.0.1/';
const target = path.join(__dirname, 'public', 'emoji');
async function main() {
  fs.mkdirSync(target, {recursive: true});
  for (const [emoji] of entries) {
    const name = [...emoji].filter(c => c !== '\ufe0f').map(c => c.codePointAt(0).toString(16)).join('-') + '.png';
    const response = await fetch(base + 'assets/72x72/' + name);
    if (!response.ok) throw new Error(`${name}: ${response.status}`);
    const data = Buffer.from(await response.arrayBuffer());
    if (data.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') throw new Error('Invalid PNG');
    fs.writeFileSync(path.join(target, name), data);
  }
  const license = await fetch(base + 'LICENSE-GRAPHICS');
  if (!license.ok) throw new Error('License download failed');
  fs.writeFileSync(path.join(target, 'LICENSE-GRAPHICS.txt'), await license.text());
  console.log(`Imported ${entries.length} local PNGs and graphics license from Twemoji v16.0.1`);
}
main().catch(error => {console.error(error); process.exitCode = 1;});
