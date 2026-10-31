const {test}=require('node:test'),assert=require('node:assert/strict');
const {createHumanFriends}=require('../human-friends.cjs');
const config={origin:'https://example.test'},token='a'.repeat(43);
const auth={token,expiresAt:Date.now()+600000,account_id:'human'};
function fixture(){const calls=[];let handler=async url=>url.endsWith('/login')?auth:{relationships:[]};
  const client=createHumanFriends({fetchImpl:async(url,args)=>{calls.push({url,args});return {ok:true,text:async()=>JSON.stringify(await handler(url,args))};}});
  return {client,calls,set handler(fn){handler=fn;}};
}
test('human token stays in broker and requests use human endpoints only',async()=>{
  const f=fixture();const signed=await f.client.call(config,'login',{email:'test@example.test',password:'secret'});
  assert.equal(signed.account,'human');assert.equal(signed.token,undefined);
  await f.client.call(config,'decide',{id:'relation',revision:3,action:'accept'});
  assert.equal(f.calls.at(-1).url,'https://example.test/v1/messenger/friends/decide');
  assert.equal(f.calls.at(-1).args.headers.Authorization,'Bearer '+token);
  assert.equal(f.calls.at(-1).args.redirect,'error');
  assert.equal(JSON.stringify(await f.client.call(config,'status')).includes(token),false);
  for(const op of ['send','history','attachment','pairing','receipt'])await assert.rejects(f.client.call(config,op),/Unsupported/);
  await f.client.close();assert.equal((await f.client.call(config,'status')).signedIn,false);
  await assert.rejects(f.client.call(config,'roster'),/Sign-in required/);
});
test('closing during login discards and revokes the late session',async()=>{
  const f=fixture();let finish;f.handler=url=>url.endsWith('/login')?new Promise(r=>finish=r):{};
  const pending=f.client.call(config,'login',{email:'a',password:'b'});
  await new Promise(r=>setImmediate(r));await f.client.close();finish(auth);
  await assert.rejects(pending,/superseded/);assert.ok(f.calls.at(-1).url.endsWith('/logout'));
  assert.equal((await f.client.call(config,'status')).signedIn,false);
});
test('logout discards in-flight roster and changed relay cannot reuse token',async()=>{
  const f=fixture();await f.client.call(config,'login',{email:'a',password:'b'});
  await assert.rejects(f.client.call({origin:'https://other.test'},'roster'),/Sign-in required/);
  let finish;f.handler=url=>url.endsWith('/roster')?new Promise(r=>finish=r):{};
  const pending=f.client.call(config,'roster');await new Promise(r=>setImmediate(r));
  await f.client.close();finish({relationships:[]});await assert.rejects(pending,/Account changed/);
});
test('network failure does not retry or retain a failed login',async()=>{
  let count=0;const client=createHumanFriends({fetchImpl:async()=>{count++;throw Error('offline');}});
  await assert.rejects(client.call(config,'login',{email:'a',password:'b'}));assert.equal(count,1);
  assert.equal((await client.call(config,'status')).signedIn,false);
  await assert.rejects(client.call({origin:'http://example.test'},'login',{email:'a',password:'b'}),/Invalid/);assert.equal(count,1);
});
test('IPC friend operations require dedicated window; agent backend only reads roster',()=>{
  const fs=require('node:fs'),path=require('node:path');
  const main=fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8');
  assert.match(main,/sender.searchParams.get\('kind'\)!=='friends'/);
  assert.match(main,/args.mailbox!==mailbox/);assert.match(main,/if\(mailbox!=='buddies'\)throw/);
  const backend=fs.readFileSync(path.join(__dirname,'../../iam_desktop.py'),'utf8');
  const block=backend.split("if action == 'agent-friend-roster':")[1].split("if action == 'chat-window':")[0];
  assert.match(block,/'roster', \{\}/);assert.ok(!block.includes("'decide'"));
});
