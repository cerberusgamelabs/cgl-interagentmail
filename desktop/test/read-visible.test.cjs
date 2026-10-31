const {test}=require('node:test'),assert=require('node:assert/strict');
const {visibleIds,tracker}=require('../public/read-visible.js');
function fixture(){
  const nodes=[{dataset:{readEvent:'a'},getBoundingClientRect:()=>({top:10,bottom:40,width:100,height:30})},
    {dataset:{readEvent:'b'},getBoundingClientRect:()=>({top:200,bottom:240,width:100,height:40})}];
  return {container:{getBoundingClientRect:()=>({top:0,bottom:100}),querySelectorAll:()=>nodes},
    doc:{hidden:false,hasFocus:()=>true,querySelector:()=>null}};
}
test('read requires focus, foreground, no modal, and actual visible message',()=>{
  const {container,doc}=fixture();assert.deepEqual(visibleIds(container,doc),['a']);
  doc.hasFocus=()=>false;assert.deepEqual(visibleIds(container,doc),[]);
  doc.hasFocus=()=>true;doc.hidden=true;assert.deepEqual(visibleIds(container,doc),[]);
  doc.hidden=false;doc.querySelector=()=>({});assert.deepEqual(visibleIds(container,doc),[]);
});
test('only successful acknowledgments are cached; no duplicate concurrent writes',async()=>{
  const {container,doc}=fixture();let attempts=0;
  const check=tracker(container,doc,async()=>{if(++attempts===1)throw Error('offline');});
  await check();await check();await check();assert.equal(attempts,2);
});
