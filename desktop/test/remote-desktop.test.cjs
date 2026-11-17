const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const os=require('node:os');const path=require('node:path');const crypto=require('node:crypto');
const {RemoteDesktop,remoteId}=require('../remote-desktop.cjs');
function fixture(t,{owner='owner'}={}) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'iam-remote-test-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const config={enabled:true,origin:'https://relay.invalid',owner:'user:owner',node:'local',epoch:'one',peers:['mini']};
  let sends=0,lookups=0;
  const transport={session:null,restoreSession(s){this.session=s;},async login(){this.session={accountId:owner,deviceId:'device',homeServer:'home',token:'synthetic',expiresAt:Date.now()+60000};return this.session;},
    async logout(){this.session=null;},async requestPairing(){return {challenge:'challenge',state:'awaiting_local_confirmation'};},
    async pairingStatus(){return {state:'approved',desktopScope:true,grantId:'grant',expiresAt:Math.floor(Date.now()/1000)+60};},
    async syncEvents(){return {desktopRoster:[{conversation:'Agent',displayName:'Agent'}]};},
    async sendText(){sends++;return {state:'queued'};},async lookupSend(){lookups++;return {found:true,state:'queued'};},
    async historyPage(){return {events:[{id:'request:1',requestId:'request',sender:'owner',body:'hello',created:1,state:'accepted'},
      {id:'reply:1',requestId:'request',sender:'resident',body:'hi',created:2,state:'replied'}],after:[2,'reply:1'],more:false};}};
  const secret=crypto.randomBytes(32);
  const safeStorage={isEncryptionAvailable:()=>true,encryptString(text){const iv=crypto.randomBytes(12),cipher=crypto.createCipheriv('aes-256-gcm',secret,iv);return Buffer.concat([iv,cipher.update(text),cipher.final(),cipher.getAuthTag()]);},
    decryptString(bytes){const decipher=crypto.createDecipheriv('aes-256-gcm',secret,bytes.subarray(0,12));decipher.setAuthTag(bytes.subarray(-16));return Buffer.concat([decipher.update(bytes.subarray(12,-16)),decipher.final()]).toString();}};
  const client=new RemoteDesktop({safeStorage,file:path.join(dir,'store'),config:async()=>config,transportFactory:async()=>transport});
  return {client,transport,config,safeStorage,counts:()=>({sends,lookups})};
}
async function paired(t){const f=fixture(t);await f.client.login('owner','password');await f.client.pair('mini');await f.client.check('mini');await f.client.update();return f;}
test('owner account is pinned and secrets never leave protected storage',async t=>{
  const {client}=fixture(t,{owner:'other'});await assert.rejects(client.login('other','password'),/account that owns/);
  const f=fixture(t);await f.client.login('owner','password');const bytes=fs.readFileSync(f.client.file).toString();
  assert.equal(bytes.includes('PRIVATE KEY'),false);assert.equal(bytes.includes('synthetic'),false);
  const status=JSON.stringify(await f.client.status());assert.equal(status.includes('privateKey'),false);assert.equal(status.includes('synthetic'),false);
});
test('plaintext credential storage is rejected',async t=>{
  const f=fixture(t);f.safeStorage.isEncryptionAvailable=()=>false;await assert.rejects(f.client.login('owner','password'),/Protected/);
});
test('only configured peers can pair and approved remote roster has collision-safe ids',async t=>{
  const f=await paired(t);await assert.rejects(f.client.pair('foreign'));
  assert.equal(f.client.buddies[0].mailbox,remoteId('mini','Agent'));
  assert.notEqual(remoteId('mini','Agent'),remoteId('other','Agent'));
  const history=await f.client.history(remoteId('mini','Agent'));assert.equal(history[0].body,'hello');assert.equal(history[0].reply,'hi');
});
test('signer binds node, conversation, grant, account and device',async t=>{
  const f=await paired(t),link=f.client.state.links[0];
  const intent={installation_id:'mini',conversation_id:'Agent',grant_id:'grant',account_id:'owner',device_id:'device',home_server:'home',operation:'send'};
  const sign=f.client.signer(link,'Agent'),bytes=Buffer.from('proof');
  const signature=await sign(bytes,intent);
  assert.equal(crypto.verify('sha256',bytes,crypto.createPublicKey(f.client.state.privateKey),Buffer.from(signature,'base64')),true);
  for(const [key,value] of Object.entries({installation_id:'other',conversation_id:'Other',grant_id:'wrong',account_id:'other',device_id:'other',operation:'runtime'}))
    await assert.rejects(sign(bytes,{...intent,[key]:value}));
});
test('uncertain sends are looked up, not blindly retried',async t=>{
  const f=await paired(t),id=crypto.randomUUID();f.client.refresh=()=>{};
  await f.client.send(remoteId('mini','Agent'),{request_id:id,body:'hello'});
  f.transport.sendText=async()=>{throw Error('lost reply');};await f.client.flush();assert.equal(f.client.state.pending[0].state,'uncertain');
  await f.client.flush();assert.equal(f.counts().lookups,1);assert.equal(f.client.state.pending[0].state,'submitted');
  await assert.rejects(f.client.send(remoteId('mini','Agent'),{request_id:id,body:'different'}));
});
test('queued drafts expire, attachment paths are never exported',async t=>{
  const f=await paired(t);f.client.refresh=()=>{};
  await assert.rejects(f.client.send(remoteId('mini','Agent'),{request_id:crypto.randomUUID(),body:'file',files:[{path:'private'}]}));
  await f.client.send(remoteId('mini','Agent'),{request_id:crypto.randomUUID(),body:'hello'});
  f.client.state.pending[0].expires=0;await f.client.flush();assert.equal(f.client.state.pending[0].state,'expired');assert.equal(f.counts().sends,0);
});
test('vendored owner transport remains identical to its reviewed source',
  {skip:!fs.existsSync(path.join(__dirname,'..','..','mobile','transport.mjs'))},()=>{
  for(const name of ['transport','owner-proof','uploads','profiles','images','pairing'])
    assert.equal(fs.readFileSync(path.join(__dirname,'..','remote',name+'.mjs'),'utf8').trim().replace(/\r\n/g,'\n'),
      fs.readFileSync(path.join(__dirname,'..','..','mobile',name+'.mjs'),'utf8').trim().replace(/\r\n/g,'\n'));
});
