import {test} from 'node:test';
import assert from 'node:assert/strict';
import {uploadImage,downloadImage,digest,encode} from '../images.mjs';
test('chunk upload is immutable and never itself submits a message',async()=>{
  const bytes=new Uint8Array(40000).fill(10),calls=[];
  const image={upload:'stable',data:encode(bytes),sha256:await digest(bytes)};
  await uploadImage(image,async query=>{calls.push(query);return {upload:query.upload,part:query.part,stored:true};});
  assert.equal(calls.length,2);assert.equal(calls[1].total,40000);assert.equal(calls[1].part,1);
  await assert.rejects(uploadImage({...image,sha256:'0'.repeat(64)},async()=>assert.fail()),/integrity/);
  await assert.rejects(uploadImage(image,async()=>({stored:true,upload:'wrong',part:0})),/receipt/);
});
test('download validates scope, chunks and final digest before exposing image',async()=>{
  const bytes=new Uint8Array(70000).fill(3),sha256=await digest(bytes),calls=[];
  const transfer=async query=>{calls.push(query);return {...query,total:bytes.length,mime:'image/png',sha256,data:encode(bytes.slice(query.offset,query.offset+65536))};};
  const image=await downloadImage('message:reply',0,transfer);
  assert.equal(image.data,encode(bytes));assert.equal(calls.length,2);
  await assert.rejects(downloadImage('message:reply',0,async q=>({...await transfer(q),event_id:'other:reply'})),/Invalid/);
  await assert.rejects(downloadImage('message:reply',0,async q=>({...await transfer(q),sha256:'0'.repeat(64)})),/integrity/);
  await assert.rejects(downloadImage('message:reply',0,async q=>({...await transfer(q),data:'AA=='})),/chunk/);
});
