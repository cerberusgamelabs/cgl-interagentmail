// Deduplicate selected bytes within the encrypted native vault, never on disk
// outside it. Pending sends and buddy snapshots may reference the same file.
export function packState(state){
  const blobs={};
  const value=JSON.stringify(state,(key,value)=>{
    if(value && typeof value==='object' && typeof value.data==='string' && typeof value.fingerprint==='string' && /^[0-9a-f-]{36}$/.test(value.upload || '')){
      if(Object.hasOwn(blobs,value.upload) && blobs[value.upload]!==value.data)throw Error('Conflicting saved attachment');
      blobs[value.upload]=value.data;return {...value,data:undefined,blobRef:value.upload};
    }
    return value;
  });
  return Object.keys(blobs).length?JSON.stringify({storageVersion:2,state:JSON.parse(value),blobs}):value;
}
export function unpackState(text){
  const root=JSON.parse(text);if(root.storageVersion!==2)return root;
  if(!root.blobs || typeof root.blobs!=='object' || Object.keys(root.blobs).length>256)throw Error('Invalid saved attachments');
  return JSON.parse(JSON.stringify(root.state),(key,value)=>{
    if(value && typeof value==='object' && Object.hasOwn(value,'blobRef')){
      const ref=value.blobRef;
      if(ref!==value.upload || !/^[0-9a-f-]{36}$/.test(ref) || !Object.hasOwn(root.blobs,ref) || typeof root.blobs[ref]!=='string' || root.blobs[ref].length>5333336)throw Error('Saved attachment unavailable');
      const restored={...value,data:root.blobs[ref]};delete restored.blobRef;return restored;
    }return value;
  });
}
