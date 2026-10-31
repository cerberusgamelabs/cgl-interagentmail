const test = require('node:test'), assert = require('node:assert/strict');
const {controller} = require('../public/reconnect.js');

test('natural connection clears busy banner without another open request', async () => {
  const shown = [], calls = [];
  const reconnect = controller(async action => {
    calls.push(action); return {state: action === 'open' ? 'busy' : 'bound'};
  }, 'One', (...args) => shown.push(args));
  await reconnect();
  reconnect.observe({availability:'busy', sessionConnected:false});
  assert.match(shown.at(-1)[0], /busy/);
  reconnect.observe({availability:'busy', sessionConnected:true});
  assert.deepEqual(shown.at(-1), ['',false]);
  assert.deepEqual(calls, ['session-readiness','open']);
});

test('late reconnect failure cannot restore banner after natural connection', async () => {
  const shown = []; let reject;
  const reconnect = controller(() => new Promise((_, r) => {reject=r;}),
    'One', (...args) => shown.push(args));
  const attempt = reconnect();
  reconnect.observe({sessionConnected:true});
  reject(new Error('stale failure'));
  await attempt;
  assert.deepEqual(shown.at(-1), ['',false]);
});

test('restored bound chat attaches without creation, sending or policy overrides', async () => {
  const calls = [], shown = [];
  const reconnect = controller(async (action,args) => {
    calls.push([action,args]); return {state: action === 'open' ? 'attached' : 'bound'};
  }, 'One', (...args)=>shown.push(args));
  await reconnect();
  assert.deepEqual(calls, [['session-readiness',{mailbox:'One'}],['open',{mailbox:'One'}]]);
  assert.deepEqual(shown.at(-1), ['',false]);
});
test('unbound chats never open or create automatically', async () => {
  const calls = [];
  await controller(async action => {calls.push(action);return {state:'unbound'};},'New',()=>{})();
  assert.deepEqual(calls,['session-readiness']);
});
test('busy sessions wait for explicit retry and errors remain visible', async () => {
  const shown = []; let mode='busy';
  const reconnect=controller(async action=>{
    if (action==='session-readiness') return {state:'bound'};
    if (mode==='error') throw new Error('offline');
    return {state:mode};
  },'One',(...args)=>shown.push(args));
  await reconnect(); assert.match(shown.at(-1)[0],/busy/); assert.equal(shown.at(-1)[1],true);
  mode='error'; await reconnect(); assert.match(shown.at(-1)[0],/offline/);
  mode='attached'; await reconnect(); assert.deepEqual(shown.at(-1),['',false]);
});
test('overlapping retries share one attachment attempt', async()=>{
  let release, count=0;
  const reconnect=controller(async()=>{count++;return await new Promise(r=>{release=r;});},'One',()=>{});
  const first=reconnect(); await reconnect(); assert.equal(count,1);
  release({state:'unbound'}); await first;
});
