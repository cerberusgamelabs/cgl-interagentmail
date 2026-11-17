import test from 'node:test';
import assert from 'node:assert/strict';
import {edgeBack,installEdgeBack} from '../edge-back.mjs';
test('only intentional inward edge swipes navigate, not scrolling or outward drags',()=>{
  const left={startX:5,startY:100,endX:90,endY:105,width:400,elapsed:200};
  assert.equal(edgeBack(left),true);
  assert.equal(edgeBack({...left,startX:395,endX:310}),true);
  for(const change of [{startX:100},{endY:160},{endX:30},{elapsed:1000},{startX:395,endX:460}])assert.equal(edgeBack({...left,...change}),false);
});
test('friend dialog can opt into edge navigation without allowing swipes in inputs',()=>{
  const handlers={};let backs=0;
  installEdgeBack({addEventListener:(name,fn)=>handlers[name]=fn},{enabled:()=>true,back:()=>backs++,width:()=>400,now:()=>100,allowDialog:true});
  const event={pointerId:1,pointerType:'touch',clientX:4,clientY:100,target:{closest:()=>null}};
  handlers.pointerdown(event);handlers.pointerup({...event,clientX:90});assert.equal(backs,1);
  handlers.pointerdown({...event,target:{closest:()=>({})}});handlers.pointerup({...event,clientX:90});assert.equal(backs,1);
});
