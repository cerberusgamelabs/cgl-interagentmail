import {decodeBase64} from './owner-proof.mjs';
import {encode,digest} from './images.mjs';

export const manifest=items=>items.map(({upload,fingerprint})=>({upload,digest:fingerprint}));
export const manifestHash=items=>digest(new TextEncoder().encode(JSON.stringify(manifest(items))));
export async function prepareAttachment(file){
  if(!file || !['image','document'].includes(file.kind) || typeof file.name!=='string' || !file.name || file.name.length>120 || /[\x00-\x1f\x7f<>:"/\\|?*]/.test(file.name) || /[. ]$/.test(file.name))throw Error('Invalid attachment');
  const raw=file.data===''?new Uint8Array():decodeBase64(file.data,5333336);
  if(raw.length>4000000)throw Error('Each attachment must be no larger than 4 MB');
  const sha256=await digest(raw),size=raw.length;
  const fingerprint=await digest(new TextEncoder().encode(JSON.stringify({kind:file.kind,name:file.name,size,sha256})));
  return {...file,size,sha256,fingerprint,upload:crypto.randomUUID()};
}
export function addAttachment(previous,file){
  const next=[...previous,file];
  for(const kind of ['image','document']){
    const group=next.filter(f=>f.kind===kind);
    if(group.length>4 || group.reduce((n,f)=>n+f.size,0)>8000000)throw Error('Up to four attachments and 8 MB per category');
  }
  return next;
}
export async function uploadAttachments(files,transfer,progress=()=>{},delay=ms=>new Promise(resolve=>setTimeout(resolve,ms))){
  for(const file of files){
    const raw=file.data===''?new Uint8Array():decodeBase64(file.data,5333336);
    if(raw.length!==file.size || await digest(raw)!==file.sha256)throw Error('Saved attachment integrity check failed');
    const meta={upload:file.upload,kind:file.kind,name:file.name,size:file.size,sha256:file.sha256};
    const count=Math.max(1,Math.ceil(raw.length/35000));
    const status=await transfer({action:'upload_status',...meta});
    if(status?.upload!==file.upload || status.digest!==file.fingerprint || !Number.isInteger(status.next_part) || status.next_part<0 || status.next_part>count)throw Error('Invalid upload status');
    for(let part=status.next_part;part<count;part++){
      // Keep within existing per-minute proof/request budgets, including status.
      await delay(650);
      const result=await transfer({action:'upload_blob',...meta,part,data:encode(raw.slice(part*35000,(part+1)*35000))});
      if(result?.upload!==file.upload || result.digest!==file.fingerprint || result.part!==part || result.stored!==true || result.next_part!==part+1)throw Error('Invalid upload receipt');
      progress(file,Math.min((part+1)*35000,raw.length));
    }
  }
}
