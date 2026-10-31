import test from 'node:test';
import assert from 'node:assert/strict';
import {publicProfile,downloadAvatar} from '../profiles.mjs';
import {digest,encode} from '../images.mjs';
test('public profile admits only bounded chosen status and avatar references',()=>{
  assert.deepEqual(publicProfile({status:'Here',presence:'available',private:'secret',avatar:{url:'https://bad'}}),{status:'Here',presence:'available'});
  assert.deepEqual(publicProfile({status:'x'.repeat(181),presence:'online'}),{});
});
test('avatar transfer binds digest, type and size before displaying bytes',async()=>{
  const raw=new Uint8Array([255,216,255]),meta={total:3,mime:'image/jpeg',sha256:await digest(raw)};
  const image=await downloadAvatar(meta,async q=>{assert.equal(q.action,'read_avatar');return {...meta,offset:0,data:encode(raw)};});
  assert.equal(image.data,encode(raw));
  await assert.rejects(downloadAvatar(meta,async()=>({...meta,offset:0,data:'AAAA'})),/integrity/);
  await assert.rejects(downloadAvatar(meta,async()=>({...meta,total:4,offset:0,data:'AAAA'})),/changed/);
});
