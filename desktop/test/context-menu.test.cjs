const test = require('node:test');
const assert = require('node:assert/strict');
const {contextMenuItems} = require('../context-menu.cjs');

test('editable right-click offers standard native editing roles with capability gates', () => {
  const items = contextMenuItems({isEditable:true,editFlags:{canUndo:true,canCopy:true,canPaste:true,canSelectAll:true}});
  assert.deepEqual(items.filter(i=>i.role).map(i=>i.role),['undo','redo','cut','copy','paste','selectAll']);
  assert.equal(items.find(i=>i.role==='cut').enabled,false);
  assert.equal(items.find(i=>i.role==='paste').enabled,true);
});

test('read-only messages never expose editing actions', () => {
  const items = contextMenuItems({isEditable:false,editFlags:{canCopy:true,canSelectAll:true}});
  assert.deepEqual(items.map(i=>i.role),['copy','selectAll']);
  assert.ok(items.every(i=>i.enabled));
  assert.equal(contextMenuItems({})[0].enabled,false);
});

test('only supplied trusted download callback is offered; arbitrary URLs are ignored', () => {
  const params = {srcURL:'file:///secret',linkURL:'https://untrusted.invalid'};
  assert.ok(!contextMenuItems(params).some(i=>i.click));
  let clicked = false;
  const item = contextMenuItems(params,()=>{clicked=true;}).find(i=>i.click);
  item.click(); assert.equal(clicked,true);
});
