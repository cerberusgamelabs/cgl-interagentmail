import {test} from 'node:test';
import assert from 'node:assert/strict';
import {fitDraft} from '../composer.mjs';
test('composer grows to a cap and shrinks without changing text or selection',()=>{
  const input={style:{},scrollTop:17,scrollHeight:42,value:'draft',selectionStart:2,selectionEnd:4};
  fitDraft(input);assert.equal(input.style.height,'44px');
  input.scrollHeight=90;fitDraft(input);assert.equal(input.style.height,'92px');
  input.scrollHeight=900;fitDraft(input);assert.equal(input.style.height,'120px');
  input.scrollHeight=42;fitDraft(input);assert.equal(input.style.height,'44px');
  assert.equal(input.value,'draft');assert.equal(input.selectionStart,2);assert.equal(input.selectionEnd,4);assert.equal(input.scrollTop,17);
});
