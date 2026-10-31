const test=require('node:test'), assert=require('node:assert/strict');
const {create}=require('../public/history-view.js');
class Container {
  constructor(){this.children=[];this.clientHeight=100;this.top=0;this.moves=0;}
  get firstChild(){return this.children[0]||null;}
  get scrollHeight(){return this.children.reduce((n,c)=>n+c.height,0);}
  get scrollTop(){return this.top;}
  set scrollTop(v){this.top=Math.max(0,Math.min(v,this.scrollHeight-this.clientHeight));}
  getBoundingClientRect(){return {top:0};}
  insertBefore(node,before){this.moves++;if(this.children.includes(node))this.children.splice(this.children.indexOf(node),1);const at=before?this.children.indexOf(before):this.children.length;this.children.splice(at,0,node);node.parent=this;}
  removeChild(node){this.children.splice(this.children.indexOf(node),1);node.parent=null;}
}
function fixture(){
  const container=new Container();let builds=0;
  const update=create(container,m=>{
    if(m.fail)throw new Error('render failure'); builds++;
    return {id:m.id,height:m.height||100,
      get nextSibling(){const p=this.parent;return p?.children[p.children.indexOf(this)+1]||null;},
      getBoundingClientRect(){const p=this.parent;const top=p.children.slice(0,p.children.indexOf(this)).reduce((n,c)=>n+c.height,0)-p.scrollTop;return {top,bottom:top+this.height};}
    };
  });
  return {container,update,get builds(){return builds;}};
}
const history=()=>[1,2,3,4,5].map(id=>({id}));
test('unchanged image-bearing messages keep identity without rebuild or movement',()=>{
  const f=fixture(), rows=history();rows[0].reply_images=[{index:0}];
  f.update(rows);const nodes=[...f.container.children], moves=f.container.moves;
  f.update(rows);
  assert.equal(f.builds,5);assert.deepEqual(f.container.children,nodes);assert.equal(f.container.moves,moves);
  f.update([...rows,{id:6}]);assert.equal(f.container.children[0],nodes[0]);assert.equal(f.builds,6);
});
test('initial load and updates at bottom follow latest messages',()=>{
  const f=fixture();f.update(history());assert.equal(f.container.scrollTop,400);
  f.update([...history(),{id:6}]);assert.equal(f.container.scrollTop,500);
});
test('new messages do not move a reader looking at older history',()=>{
  const f=fixture();f.update(history());f.container.scrollTop=125;
  f.update([...history(),{id:6}]);assert.equal(f.container.scrollTop,125);
});
test('height changes and prepended messages preserve visible anchor offset',()=>{
  const f=fixture();f.update(history());f.container.scrollTop=125;
  f.update([{id:0},...history().map(m=>m.id===1?{...m,height:150}:m)]);
  assert.equal(f.container.scrollTop,275);
});
test('deleted offscreen rows preserve anchor and obsolete DOM is removed',()=>{
  const f=fixture();f.update(history());f.container.scrollTop=125;
  f.update(history().slice(1));assert.equal(f.container.scrollTop,25);assert.equal(f.container.children.length,4);
});
test('failed rendering leaves old history and scroll untouched and can recover',()=>{
  const f=fixture();f.update(history());f.container.scrollTop=125;const nodes=[...f.container.children];
  assert.throws(()=>f.update([...history(),{id:6,fail:true}]));
  assert.deepEqual(f.container.children,nodes);assert.equal(f.container.scrollTop,125);
  f.update([...history(),{id:6}]);assert.equal(f.container.children.length,6);
});
test('notice and request IDs cannot collide',()=>{
  const f=fixture();f.update([{id:'same'},{id:'same',notice_kind:'agent'}]);assert.equal(f.container.children.length,2);
});
