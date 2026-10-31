const {test}=require('node:test'),assert=require('node:assert/strict');
const {format}=require('../public/message-time.js');
test('local calendar labels use requested 12-hour format',()=>{
  const now=new Date(2026,9,21,12);
  assert.equal(format(new Date(2026,9,21,11,43).getTime()/1000,now),'Today - 11:43 AM');
  assert.equal(format(new Date(2026,9,20,23,43).getTime()/1000,now),'Yesterday - 11:43 PM');
  assert.equal(format(new Date(2026,9,18,11,43).getTime()/1000,now),'10/18/2026 - 11:43 AM');
  assert.equal(format(NaN,now),'Time unavailable');
});
test('yesterday is previous calendar day, including month/year boundaries',()=>{
  assert.equal(format(new Date(2025,11,31,23,43).getTime()/1000,new Date(2026,0,1,0,1)),'Yesterday - 11:43 PM');
});
test('timezone and daylight-saving labels follow the device, not UTC',()=>{
  const {execFileSync}=require('node:child_process');
  const modulePath=require.resolve('../public/message-time.js');
  const script=`const {format}=require(${JSON.stringify(modulePath)});console.log(format(Date.parse('2026-03-08T05:30:00Z')/1000,new Date('2026-03-09T04:01:00Z')));`;
  assert.equal(execFileSync(process.execPath,['-e',script],{env:{...process.env,TZ:'America/New_York'}}).toString().trim(),'Yesterday - 12:30 AM');
  assert.equal(execFileSync(process.execPath,['-e',script],{env:{...process.env,TZ:'America/Los_Angeles'}}).toString().trim(),'Yesterday - 9:30 PM');
});
