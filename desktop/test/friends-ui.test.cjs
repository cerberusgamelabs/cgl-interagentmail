const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function fixture(human){
  const rows=[],calls=[],elements={title:{},scope:{},content:{append:x=>rows.push(x),replaceChildren:()=>rows.splice(0)}};
  const context={mailbox:human?'buddies':'Wren',$:id=>elements[id],text:t=>rows.push({text:t}),button:(label,action)=>rows.push({label,action}),load:async()=>{},
    document:{createElement:tag=>({tag,value:'',children:[],append(x){this.children.push(x);}})},
    window:{iam:{call:async(action,args)=>{calls.push({action,...args});if(args.operation==='status')return {signedIn:true,origin:'https://example.test',account:'human'};
      if(action==='agent-friend-roster'||args.operation==='roster')return {self:{enabled:true,name:'Owner'},relationships:[{id:'request',incoming:true,state:'pending',revision:7,peer:{name:'<script>Friend</script>'},reason:'Hello'}]};return {};}}}};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../public/friends.js'),'utf8'),context);return {context,rows,calls};
}
test('agent pending request has status only and no decision actions',async()=>{
  const f=fixture(false);await f.context.desktopFriends();assert.equal(f.calls.length,1);assert.equal(f.calls[0].action,'agent-friend-roster');
  assert.ok(f.rows.some(r=>r.text?.includes('<script>Friend</script>')));assert.equal(f.rows.filter(r=>r.action).length,0);
});
test('human acceptance requires explanation, review then exact revision confirmation',async()=>{
  const f=fixture(true);await f.context.desktopFriends();const review=f.rows.find(r=>r.label==='Review acceptance');
  await assert.rejects(review.action(),/public response/);
  f.rows.filter(r=>r.tag==='label').at(-1).children[0].value='Happy to connect';await review.action();
  assert.ok(!f.calls.some(c=>c.operation==='decide'));
  await f.rows.find(r=>r.label==='Confirm accept').action();const result=f.calls.at(-1);
  assert.equal(result.action,'human-friends');assert.equal(result.mailbox,'buddies');assert.equal(result.value.id,'request');assert.equal(result.value.revision,7);assert.equal(result.value.action,'accept');
});
