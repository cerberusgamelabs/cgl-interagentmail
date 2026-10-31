const test = require('node:test');
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const {Writable} = require('node:stream');
const {createBackendRpc} = require('../backend-rpc.cjs');

function setup(write = (_chunk, _encoding, done) => done(), options) {
  const backend = new EventEmitter();
  backend.exitCode = null;
  backend.stdin = new Writable({write});
  return {backend, rpc: createBackendRpc(backend, options)};
}

test('normal replies resolve and late duplicate replies are ignored', async () => {
  const {rpc} = setup();
  const response = rpc.request('snapshot');
  rpc.receive({id:1, result:{ok:true}});
  assert.deepEqual(await response, {ok:true});
  rpc.receive({id:1, error:'late'});
  rpc.stop();
});

test('shutdown settles pending calls and prohibits new writes', async () => {
  let writes=0;
  const {rpc,backend} = setup((_c,_e,done)=>{writes++;done();});
  const pending = assert.rejects(rpc.request('send'), /original request ID/);
  rpc.stop(); rpc.stop();
  await pending;
  await assert.rejects(rpc.request('snapshot'), /stopped/);
  assert.equal(writes,1);
  assert.equal(backend.stdin.writableEnded,true);
  rpc.receive({id:1,result:'late'});
});

test('broken pipe callback and error event are both handled', async () => {
  const {rpc} = setup((_c,_e,done)=>done(Object.assign(new Error('private'),{code:'EPIPE'})));
  await assert.rejects(rpc.request('send'), /uncertain/);
  await new Promise(resolve=>setImmediate(resolve));
  await assert.rejects(rpc.request('snapshot'), /stopped/);
  rpc.stop();
});

test('ended stream is rejected before write', async () => {
  let writes=0;
  const {rpc,backend} = setup((_c,_e,done)=>{writes++;done();});
  backend.stdin.end();
  await assert.rejects(rpc.request('snapshot'), /stopped/);
  assert.equal(writes,0);
  rpc.stop();
});

test('synchronous write failure settles requests safely', async () => {
  const {rpc,backend} = setup();
  backend.stdin.write = () => {throw new Error('private');};
  await assert.rejects(rpc.request('send'), /uncertain/);
  rpc.stop();
});

test('child exit and stream close settle all pending calls', async () => {
  for (const event of ['exit','error','close']) {
    const {rpc,backend} = setup();
    const first=assert.rejects(rpc.request('one'),/stopped/);
    const second=assert.rejects(rpc.request('two'),/stopped/);
    backend.emit(event, new Error('private'));
    await Promise.all([first,second]);
    rpc.stop();
  }
});

test('timeout does not resend and ignores late response', async () => {
  const {rpc} = setup(undefined,{timeoutMs:5});
  await assert.rejects(rpc.request('send'),/Do not resend/);
  rpc.receive({id:1,result:'late'});
  rpc.stop();
});

test('late write error after shutdown is handled without reopening transport', async () => {
  let complete;
  const {rpc} = setup((_c,_e,done)=>{complete=done;});
  const rejected=assert.rejects(rpc.request('send'),/uncertain/);
  rpc.stop();
  complete(Object.assign(new Error('late pipe failure'),{code:'ERR_STREAM_WRITE_AFTER_END'}));
  await rejected;
  await new Promise(resolve=>setImmediate(resolve));
  await assert.rejects(rpc.request('send'),/stopped/);
});
