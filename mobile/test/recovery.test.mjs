import test from 'node:test';
import assert from 'node:assert/strict';
import {recoveryDue,pendingLabel} from '../conversation.mjs';

test('recovery checks are spaced, capped at ten and survive serialization',()=>{
  let pending={id:'original',body:'Photo',image:{sha256:'same'},status:'outcome uncertain',createdAt:0};
  assert.equal(recoveryDue(pending,7999),false);
  assert.equal(recoveryDue(pending,8000),true);
  for(let i=0;i<10;i++){
    const now=(i+1)*60000;
    assert.equal(recoveryDue(pending,now),true);
    pending=JSON.parse(JSON.stringify({...pending,recoveryAttempts:i+1,lastRecoveryAt:now}));
    assert.equal(recoveryDue(pending,now+1),false);
  }
  assert.equal(recoveryDue(pending,9999999),false);
  assert.match(pendingLabel(pending),/Delivery not confirmed/);
  assert.equal(pending.id,'original');
  assert.equal(pending.image.sha256,'same');
});
test('old pending messages can recover but blocked states cannot',()=>{
  assert.equal(recoveryDue({status:'outcome uncertain',createdAt:0,recoveryAttempts:1},900000),true);
  for(const status of ['blocked','failed','held'])assert.equal(recoveryDue({status,createdAt:0},900000),false);
  assert.equal(recoveryDue(null),false);
  assert.equal(recoveryDue({status:'outcome uncertain',createdAt:10000},0),false);
});
