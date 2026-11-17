import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cachedAvatar,rememberAvatar} from '../avatar-cache.mjs';
test('encrypted-state avatar cache binds grant and digest and bounds its size',()=>{
  const state={},image={sha256:'a'.repeat(64),uri:'data:image/png;base64,AAAA'};
  rememberAvatar(state,'buddy','grant',image);
  assert.equal(cachedAvatar(state,'buddy','grant',image.sha256).uri,image.uri);
  assert.equal(cachedAvatar(state,'buddy','other',image.sha256),null);
  assert.equal(cachedAvatar(state,'buddy','grant','b'.repeat(64)),null);
  for(let i=0;i<60;i++)rememberAvatar(state,String(i),'grant',image);
  assert.equal(state.avatarCache.length,48);
  rememberAvatar(state,'invalid','grant',{...image,uri:'https://outside.test/picture'});
  assert.equal(cachedAvatar(state,'invalid','grant',image.sha256),null);
});
