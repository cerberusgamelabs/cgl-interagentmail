const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
test('desktop pairing explicitly reviews current and future access before approval',async()=>{
  const elements=new Map(),calls=[];
  const node=tag=>({tag,children:[],append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},setAttribute(){}});
  const get=id=>{if(!elements.has(id))elements.set(id,node('div'));return elements.get(id);};
  const replies={roster:{home:'home',account:'owner',installation:'node',conversations:['one','two']},
    invite:{qr:'data:image/png;base64,AA==',code:'iam-pair:test',challenge:'test',conversations:['one','two'],expires_at:9999999999},
    'prepare-invite':{ticket:'review',identity:{account_id:'owner',device_id:'phone'},desktop_scope:true,conversations:['one','two'],key_fingerprint:'fingerprint',expires_at:9999999999},
    'confirm-invite':{note:'approved'},'cancel-invite':{cancelled:true}};
  vm.runInNewContext(fs.readFileSync(require.resolve('../public/controls.js'),'utf8'),{
    URLSearchParams,location:{search:'?mailbox=buddies&kind=pairing'},Date,JSON,
    document:{getElementById:get,createElement:node,createTextNode:text=>({text}),documentElement:{dataset:{}}},
    window:{close(){},iam:{call:async(action,args)=>{if(action==='theme')return 'dark';calls.push(args);return replies[args.operation];}}}
  });
  const settle=async()=>{for(let n=0;n<20;n++)await Promise.resolve();};await settle();
  const button=label=>get('content').children.find(n=>n.tag==='button' && n.textContent===label);
  assert.equal(button('Select all current conversations'),undefined);
  await button('Create five-minute invitation').onclick();
  assert.equal(calls.find(c=>c.operation==='invite').desktop_scope,true);
  assert.ok(!calls.some(c=>c.operation==='confirm-invite'));
  assert.ok(button('Cancel invitation'));
  await button('Review scanned phone').onclick();
  assert.ok(get('content').children.some(n=>n.textContent?.includes('ALL current and future agents')));
  assert.ok(!calls.some(c=>c.operation==='confirm-invite'));
  await button('Approve this account and phone').onclick();
  assert.equal(calls.at(-1).ticket,'review');assert.equal(calls.at(-1).operation,'confirm-invite');
});
test('pinned QR generator emits a local PNG, not a remote image URL',async()=>{
  const value=await require('qrcode').toDataURL('iam-pair:synthetic',{errorCorrectionLevel:'M',width:320,margin:4});
  assert.match(value,/^data:image\/png;base64,/);
});
