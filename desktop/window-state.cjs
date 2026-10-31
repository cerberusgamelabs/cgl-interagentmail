const fs = require('node:fs');

// UI metadata only: never persist drafts, messages, tokens or confirmation dialogs.
function clean(entries) {
  if (!Array.isArray(entries)) return [];
  const seen = new Set();
  return entries.filter(e => e && typeof e.mailbox === 'string' && e.mailbox.length <= 200 && !seen.has(e.mailbox) && seen.add(e.mailbox))
    .slice(0, 200).map(e => ({mailbox: e.mailbox, bounds: e.bounds &&
      ['x','y','width','height'].every(k => Number.isFinite(e.bounds[k])) ?
      Object.fromEntries(['x','y','width','height'].map(k => [k, Math.round(e.bounds[k])])) : undefined,
      maximized: e.maximized === true}));
}
function read(file) {
  try { return clean(JSON.parse(fs.readFileSync(file, 'utf8')).windows); } catch { return []; }
}
function write(file, entries) {
  const temp = file + '.tmp';
  fs.writeFileSync(temp, JSON.stringify({version: 1, windows: clean(entries)}), {mode: 0o600});
  fs.renameSync(temp, file);
}
function fit(bounds, displays) {
  if (!bounds || !displays.length) return {};
  const area = displays.find(a => bounds.x < a.x+a.width && bounds.x+bounds.width > a.x &&
    bounds.y < a.y+a.height && bounds.y+bounds.height > a.y) || displays[0];
  const width = Math.min(Math.max(320, bounds.width), area.width);
  const height = Math.min(Math.max(400, bounds.height), area.height);
  return {width, height, x: Math.max(area.x, Math.min(bounds.x, area.x+area.width-width)),
    y: Math.max(area.y, Math.min(bounds.y, area.y+area.height-height))};
}
module.exports = {clean, read, write, fit};
