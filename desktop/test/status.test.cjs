const test=require('node:test'),assert=require('node:assert/strict');
const {render,permissionLabel,percentage}=require('../public/status.js');
class Node {
  constructor(tag){this.tag=tag;this.children=[];this.text='';this.attrs={};this.classList={add:()=>{}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(){this.children=[];this.text='';}
  set textContent(v){this.text=String(v);this.children=[];}
  get textContent(){return this.text+this.children.map(n=>n.textContent).join('');}
  setAttribute(k,v){this.attrs[k]=v;}
}
const doc={createElement:tag=>new Node(tag)};
function all(n){return [n,...n.children.flatMap(all)];}
test('status presents labeled rows and accessible usage bars without raw JSON',()=>{
  const root=new Node('main');render(root,{mailbox:'One',name:'Rowan',state:'idle',model:'test-model',effort:'medium',thread:'thread-id',directory:'E:/Example',observedSettings:{sandboxPolicy:{type:'dangerFullAccess'},approvalPolicy:'never',mode:'default'},context:{remainingPercent:19,usedTokens:212000,windowTokens:258000},weekly:{remainingPercent:42},credits:{balance:'62498'},account:{email:'test@example.test',planType:'pro'}},doc);
  const nodes=all(root);assert.equal(nodes.filter(n=>n.tag==='progress').length,2);
  assert.ok(nodes.some(n=>n.tag==='dt'&&n.textContent==='Session'));
  assert.match(root.textContent,/Full Access/);assert.match(root.textContent,/19% left/);assert.match(root.textContent,/62498/);
  assert.equal(nodes.some(n=>n.tag==='pre'),false);
});
test('missing data is honest and compaction replaces the context bar',()=>{
  const root=new Node('main');render(root,{mailbox:'One',context:{state:'compacting'}},doc);
  assert.match(root.textContent,/Compacting/);assert.match(root.textContent,/Not reported/);
  assert.equal(all(root).filter(n=>n.tag==='progress').length,0);
  assert.equal(percentage(undefined),null);assert.equal(percentage(NaN),null);assert.equal(percentage(120),100);
  assert.match(permissionLabel(null,{sandbox:'workspace-write'}),/live policy unavailable/);
});
test('status renders untrusted labels as inert text',()=>{
  const root=new Node('main');render(root,{mailbox:'<script>bad</script>'},doc);
  assert.match(root.textContent,/<script>/);assert.equal(all(root).some(n=>n.tag==='script'),false);
});
