import test from 'node:test';
import assert from 'node:assert/strict';
import {prepareAttachment,addAttachment,uploadAttachments,manifest,manifestHash} from '../uploads.mjs';
import {packState,unpackState} from '../protected-state.mjs';
import {encode} from '../images.mjs';

test('original files resume from authoritative contiguous status and bind metadata',async()=>{
  const bytes=new TextEncoder().encode('♡'.repeat(24000));
  const file=await prepareAttachment({kind:'document',name:'notes.txt',data:encode(bytes)});
  let next=1;const calls=[];
  const transfer=async q=>{calls.push(q);if(q.action==='upload_blob'){assert.equal(q.part,next);next++;}
    return {upload:q.upload,digest:file.fingerprint,next_part:next,...(q.action==='upload_blob'?{part:q.part,stored:true}:{})};};
  await uploadAttachments([file],transfer,()=>{},async()=>{});
  assert.equal(calls[0].action,'upload_status');assert.deepEqual(calls.slice(1).map(q=>q.part),[1,2]);
  assert.deepEqual(manifest([file]),[{upload:file.upload,digest:file.fingerprint}]);
  const renamed=await prepareAttachment({...file,name:'renamed.txt'});assert.notEqual(renamed.fingerprint,file.fingerprint);
  assert.notEqual(await manifestHash([renamed]),await manifestHash([file]));
});
test('invalid receipt or interruption never finishes upload or substitutes an ID',async()=>{
  const file=await prepareAttachment({kind:'document',name:'empty.txt',data:''});
  await assert.rejects(uploadAttachments([file],async q=>({upload:q.upload,digest:'a'.repeat(64),next_part:0}),()=>{},async()=>{}));
  await assert.rejects(uploadAttachments([file],async q=>{if(q.action==='upload_blob')throw Error('offline');return {upload:q.upload,digest:file.fingerprint,next_part:0};},()=>{},async()=>{}));
  assert.equal(file.data,'');
});
test('vault deduplicates original bytes and restores every draft and pending reference',async()=>{
  const file=await prepareAttachment({kind:'document',name:'notes.txt',data:'YWJj'});
  const state={draftFiles:[file],pending:{id:'same',files:[file]},buddies:[{draftFiles:[file]}]};
  const packed=packState(state);assert.equal(packed.split('YWJj').length-1,1);
  assert.deepEqual(unpackState(packed),state);assert.deepEqual(unpackState(packState({draft:'legacy'})),{draft:'legacy'});
  assert.throws(()=>packState({...state,bad:{...file,data:'different'}}));
  const damaged=JSON.parse(packed);delete damaged.blobs[file.upload];assert.throws(()=>unpackState(JSON.stringify(damaged)));
});
test('category counts and aggregate bytes are bounded before persistence',()=>{
  const file={kind:'document',size:4000000};assert.equal(addAttachment([file],file).length,2);
  assert.throws(()=>addAttachment([file,file],{...file,size:1}));
  assert.throws(()=>addAttachment(Array(4).fill({...file,size:0}),{...file,size:0}));
});
