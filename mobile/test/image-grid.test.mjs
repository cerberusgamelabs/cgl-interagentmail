import {test} from 'node:test';
import assert from 'node:assert/strict';
import {imagePreviews} from '../image-grid.mjs';
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  setAttribute(){}
}
test('one to four ordered tiles open original bytes, and repeated renders reuse verified downloads',async()=>{
  const document={createElement:tag=>new Element(tag)};let downloads=0,opened=[];
  const ui=imagePreviews({document,Observer:null});
  for(let count=1;count<=4;count++){
    const parent=new Element('article');
    ui.render(parent,Array.from({length:count},(_,i)=>({key:String(i),label:`Image ${i}`,active:()=>true,
      load:async()=>{downloads++;return {mime:'image/png',data:'YQ=='};},open:async image=>opened.push(image.data)})));
    const grid=parent.children[0];assert.equal(grid.dataset.count,String(count));
    assert.equal(grid.children.length,count);
    for(const tile of grid.children)await tile.onclick();
    assert.deepEqual(grid.children.map(t=>t.children[0].alt),Array.from({length:count},(_,i)=>`Image ${i}`));
  }
  assert.equal(downloads,4);assert.equal(opened.length,10);assert.ok(opened.every(x=>x==='YQ=='));
});
test('late or hidden previews cannot render or open and invalid MIME cannot display',async()=>{
  const document={createElement:tag=>new Element(tag)};const ui=imagePreviews({document,Observer:null});
  let finish,active=true,opens=0;const parent=new Element('article');
  ui.render(parent,[{key:'late',label:'late',active:()=>active,load:()=>new Promise(r=>finish=r),open:()=>opens++}]);
  const tile=parent.children[0].children[0],pending=tile.onclick();active=false;
  finish({mime:'image/png',data:'YQ=='});await pending;assert.equal(tile.children.length,0);assert.equal(opens,0);
  active=true;document.hidden=true;await tile.onclick();assert.equal(opens,0);
  document.hidden=false;ui.clear();
  ui.render(parent,[{key:'bad',label:'bad',active:()=>true,load:async()=>({mime:'image/svg+xml',data:'YQ=='}),open:()=>opens++}]);
  const bad=parent.children[1].children[0];await bad.onclick();assert.equal(bad.textContent,'Retry image');assert.equal(opens,0);
});
