const {test}=require('node:test'),assert=require('node:assert/strict');
const {dayLabel}=require('../public/history-view.js');
test('date dividers use local calendar dates and correct ordinal suffixes',()=>{
 for(const [day,suffix] of [[1,'st'],[2,'nd'],[3,'rd'],[11,'th'],[12,'th'],[13,'th'],[21,'st'],[22,'nd'],[23,'rd'],[30,'th']]){
  const stamp=new Date(2026,10,day,0,1).getTime()/1000;
  assert.equal(dayLabel(stamp).label,`Nov ${day}${suffix}, 2026`);
  assert.equal(dayLabel(stamp).key,`2026-11-${day}`);
 }
 assert.equal(dayLabel(null),null);assert.equal(dayLabel(NaN),null);
});
