const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('iam', Object.freeze({
  call: (action, args) => ipcRenderer.invoke('iam:call', action, args),
  onGroups: callback => {
    const listener=(_event,value)=>callback(value);ipcRenderer.on('iam:groups',listener);
    return ()=>ipcRenderer.removeListener('iam:groups',listener);
  },
  onUpdate: callback => {
    const listener=(_event,snapshot)=>callback(snapshot);
    ipcRenderer.on('iam:update',listener);
    return ()=>ipcRenderer.removeListener('iam:update',listener);
  },
}));
