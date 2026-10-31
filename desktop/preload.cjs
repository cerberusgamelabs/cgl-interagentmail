const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('iam', Object.freeze({
  call: (action, args) => ipcRenderer.invoke('iam:call', action, args),
}));
