import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../android/app/src/main/java/xyz/cerberusgamelabs/iam/messenger/OwnerDevicePlugin.java',import.meta.url),'utf8');
test('save picker options exclude large payload before activity launch',()=>{
  const save=source.split('void saveAttachment(PluginCall call)')[1].split('@ActivityCallback')[0];
  assert.ok(save.indexOf('call.getString("data")')<save.indexOf('call.getData().remove("data")'));
  assert.ok(save.indexOf('call.getData().remove("data")')<save.indexOf('startActivityForResult'));
  assert.ok(save.includes('pendingExport=bytes'));
  const callback=source.split('void attachmentSaved(')[1].split('@PluginMethod')[0];
  assert.ok(callback.indexOf('if(bytes==null)')<callback.indexOf('AttachmentExport.write'));
  assert.ok(callback.indexOf('AttachmentExport.write')<callback.indexOf('put("saved",true)'));
});
