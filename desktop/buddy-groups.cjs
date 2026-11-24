'use strict';
const fs=require('node:fs');
const {randomUUID}=require('node:crypto');
function validate(value) {
  if(!value || !Array.isArray(value.groups) || value.groups.length>100 || !value.members || typeof value.members!=='object' || Array.isArray(value.members))throw Error('Invalid buddy groups');
  const ids=new Set(),names=new Set();
  const groups=value.groups.map(g=>{
    if(!g || typeof g.id!=='string' || !/^[a-zA-Z0-9-]{1,64}$/.test(g.id) || ids.has(g.id) || typeof g.name!=='string' || !g.name.trim() || g.name.trim().length>80 || names.has(g.name.trim().toLowerCase()))throw Error('Group names must be unique and 1–80 characters');
    ids.add(g.id);names.add(g.name.trim().toLowerCase());
    return {id:g.id,name:g.name.trim(),collapsed:g.collapsed===true};
  });
  const entries=Object.entries(value.members);
  if(entries.length>2048)throw Error('Too many group assignments');
  const members=Object.fromEntries(entries.map(([mailbox,id])=>{
    if(!mailbox || mailbox.length>1024 || !ids.has(id))throw Error('Invalid group assignment');
    return [mailbox,id];
  }));
  return {groups,members};
}
function read(file) {
  try{return validate(JSON.parse(fs.readFileSync(file,'utf8')));}
  catch(error){if(error.code==='ENOENT')return {groups:[],members:{}};throw Error('Saved buddy groups could not be read; file preserved');}
}
function write(file,value) {
  const safe=validate(value),temp=file+'.'+randomUUID()+'.tmp';
  try{fs.writeFileSync(temp,JSON.stringify(safe),{mode:0o600,flag:'wx'});fs.renameSync(temp,file);}
  finally{try{fs.unlinkSync(temp);}catch(error){if(error.code!=='ENOENT')throw error;}}
  return safe;
}
module.exports={read,write,validate};
