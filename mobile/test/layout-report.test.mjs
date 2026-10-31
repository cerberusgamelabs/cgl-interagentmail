import {test} from 'node:test';
import assert from 'node:assert/strict';
import {reporter} from './layout-report.cjs';

test('layout report writes bounded test records to stdout without console streams',()=>{
  const writes=[];
  reporter({write:(...args)=>writes.push(args),exit:()=>assert.fail('unexpected exit')}).record({width:320});
  assert.deepEqual(writes,[[1,'{"width":320}\n']]);
});

test('closed stdout and stderr fail the layout run without throwing from error reporting',async()=>{
  const codes=[];const error=Object.assign(new Error('broken pipe'),{code:'EPIPE'});
  const report=reporter({write:()=>{throw error;},exit:code=>codes.push(code)});
  await Promise.resolve().then(()=>report.record({width:320})).catch(e=>report.fail(e));
  assert.deepEqual(codes,[1]);
});

test('layout assertion failures remain failures with working output',()=>{
  const codes=[],writes=[];
  reporter({write:(...args)=>writes.push(args),exit:code=>codes.push(code)}).fail(new Error('layout assertion'));
  assert.deepEqual(codes,[1]);assert.equal(writes[0][0],2);assert.match(writes[0][1],/layout assertion/);
});
