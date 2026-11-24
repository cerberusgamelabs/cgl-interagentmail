const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const read=name=>fs.readFileSync(path.join(__dirname,'../public',name),'utf8');
test('buddy and settings selects share theme-aware accessible styling',()=>{
 for(const file of ['index.html','controls.html'])assert.match(read(file),/href="friendly-select.css"/);
 const css=read('friendly-select.css');
 for(const pattern of [/data-theme="light"/,/:focus-visible/,/:disabled/,/prefers-reduced-motion/,/forced-colors:active/,/appearance:auto/])assert.match(css,pattern);
 assert.doesNotMatch(css,/url\(/);
});
