const {test}=require('node:test');
const assert=require('node:assert/strict');
const {pager}=require('../public/history-view.js');
const page=(start,count=50)=>Array.from({length:count},(_,i)=>({id:String(start+i).padStart(4,'0'),created:start+i,state:'queued'}));
test('recent view rolls at fifty without duplicates',()=>{
 const p=pager(); assert.equal(p.merge(page(50)).length,50);
 const rows=p.merge(page(51)); assert.equal(rows.length,50);assert.equal(rows[0].created,51);
 assert.deepEqual(p.before,[51,'0051']);assert.equal(p.more,true);
});
test('older pages expand view and updates retain loaded history',()=>{
 const p=pager();p.merge(page(50));assert.equal(p.merge(page(0),true).length,100);
 const latest=page(50);latest[49].state='replied';
 const rows=p.merge(latest);assert.equal(rows.length,100);assert.equal(rows[99].state,'replied');
 p.merge([],true);assert.equal(p.more,false);
});
