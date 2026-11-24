const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const groups=require('../buddy-groups.cjs');
test('groups and stable mailbox assignments persist independently of display names',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'iam-groups-')),file=path.join(dir,'groups.json');
 try {
  assert.deepEqual(groups.read(file),{groups:[],members:{}});
  const value={groups:[{id:'one',name:'Team',collapsed:true}],members:{Local:'one','remote:node:agent':'one'}};
  assert.deepEqual(groups.write(file,value),value);assert.deepEqual(groups.read(file),value);
  value.groups[0].name='Work';groups.write(file,value);assert.equal(groups.read(file).members.Local,'one');
  assert.throws(()=>groups.write(file,{groups:[],members:value.members}));
  assert.equal(groups.read(file).groups[0].name,'Work');
 } finally{fs.rmSync(dir,{recursive:true,force:true});}
});
test('validation rejects duplicate names, invalid members, and excessive names',()=>{
 assert.throws(()=>groups.validate({groups:[{id:'a',name:'Work'},{id:'b',name:' work '}],members:{}}));
 assert.throws(()=>groups.validate({groups:[{id:'a',name:'x'.repeat(81)}],members:{}}));
 assert.throws(()=>groups.validate({groups:[],members:{mailbox:'missing'}}));
});
test('unread and approval indicators survive collapsed groups; search expands matches',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../public/app.js'),'utf8');
 assert.match(source,/section\.open=Boolean\(filter\) \|\| !group\.collapsed/);
 assert.match(source,/entries\.reduce\(\(n,e\)=>n\+\(Number\(e\.buddy\.unread\)/);
 assert.match(source,/pending\?' · Approval needed'/);
 const main=fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8');
 assert.match(main,/Groups require the buddy list window/);
 assert.match(main,/desktop-buddy-groups\.json/);
});
test('unreadable saved groups are preserved rather than silently overwritten',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'iam-groups-')),file=path.join(dir,'groups.json');
 try{fs.writeFileSync(file,'broken');assert.throws(()=>groups.read(file));assert.equal(fs.readFileSync(file,'utf8'),'broken');}
 finally{fs.rmSync(dir,{recursive:true,force:true});}
});
test('compact tools launch dedicated settings and group edits broadcast to roster',()=>{
 const ui=fs.readFileSync(path.join(__dirname,'../public/app.js'),'utf8');
 const main=fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8');
 const controls=fs.readFileSync(path.join(__dirname,'../public/controls.js'),'utf8');
 assert.match(ui,/toolbar\.append\(\$\('search'\),tools\)/);
 assert.match(ui,/\['groups','Manage groups'\]/);
 assert.doesNotMatch(ui,/editor\.showModal\(/);
 assert.match(main,/sender\.pathname==='\/controls.html' && sender\.searchParams\.get\('kind'\)==='groups'/);
 assert.match(main,/webContents\.send\('iam:groups',saved\)/);
 assert.match(controls,/if\(kind==='groups'\)/);
});
