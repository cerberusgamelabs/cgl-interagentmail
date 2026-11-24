'use strict';
const fs=require('node:fs'),path=require('node:path');
function runtimeRoot(env) {
  const root=env.IAM_DESKTOP_DATA_ROOT;
  if(!root || !path.isAbsolute(root))throw Error('Start with the updated iam desktop start launcher.');
  return path.resolve(root);
}
function preserveLegacyState(packageRoot,dataRoot) {
  if(path.resolve(packageRoot)===path.resolve(dataRoot))return;
  fs.mkdirSync(path.join(dataRoot,'run'),{recursive:true});
  for(const name of ['desktop-windows.json','desktop-theme.json','remote-desktop.secure']) {
    const source=path.join(packageRoot,'run',name),target=path.join(dataRoot,'run',name);
    if(!fs.existsSync(source)||fs.existsSync(target))continue;
    try {fs.copyFileSync(source,target,fs.constants.COPYFILE_EXCL);}
    catch(error){if(error.code!=='EEXIST')throw error;}
  }
}
module.exports={runtimeRoot,preserveLegacyState};
