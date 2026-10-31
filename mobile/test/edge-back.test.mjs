import test from 'node:test';
import assert from 'node:assert/strict';
import {edgeBack} from '../edge-back.mjs';
test('only intentional inward edge swipes navigate, not scrolling or outward drags',()=>{
  const left={startX:5,startY:100,endX:90,endY:105,width:400,elapsed:200};
  assert.equal(edgeBack(left),true);
  assert.equal(edgeBack({...left,startX:395,endX:310}),true);
  for(const change of [{startX:100},{endY:160},{endX:30},{elapsed:1000},{startX:395,endX:460}])assert.equal(edgeBack({...left,...change}),false);
});
