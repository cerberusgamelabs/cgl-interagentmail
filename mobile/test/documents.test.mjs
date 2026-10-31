import test from 'node:test';
import assert from 'node:assert/strict';
import {downloadDocument,validDocumentName} from '../documents.mjs';
import {digest,encode} from '../images.mjs';

test('original Unicode document survives chunk boundaries',async()=>{
  const bytes=new TextEncoder().encode('♡ original\n'.repeat(14000));
  const sha256=await digest(bytes);let count=0;
  const value=await downloadDocument('one:reply',0,async query=>{
    assert.equal(query.action,'read_file');count++;
    return {...query,name:'notes.md',total:bytes.length,sha256,data:encode(bytes.slice(query.offset,query.offset+65536))};
  });
  assert.ok(count>1);assert.equal(value.data,encode(bytes));assert.equal(value.name,'notes.md');
});
test('empty documents and unsafe metadata',async()=>{
  const response={event_id:'one:reply',index:0,offset:0,name:'empty.txt',total:0,sha256:await digest(new Uint8Array()),data:''};
  assert.equal((await downloadDocument('one:reply',0,async()=>response)).data,'');
  for(const change of [{name:'../secret.txt'},{name:'launch.exe'},{total:4000001},{index:1},{sha256:'a'.repeat(64)}])
    await assert.rejects(downloadDocument('one:reply',0,async()=>({...response,...change})));
  assert.equal(validDocumentName('report.pdf'),true);
});
test('changed or revoked transfer never returns partial content',async()=>{
  const bytes=new Uint8Array(70000),sha256=await digest(bytes);
  for(const revoked of [true,false])await assert.rejects(downloadDocument('one:reply',0,async q=>{
    if(q.offset && revoked)throw new Error('revoked');
    return {...q,name:q.offset?'changed.txt':'first.txt',total:bytes.length,sha256,data:encode(bytes.slice(q.offset,q.offset+65536))};
  }));
});
