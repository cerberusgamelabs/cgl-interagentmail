import {decodeBase64} from './owner-proof.mjs';
export const encode=bytes=>btoa(Array.from(bytes,b=>String.fromCharCode(b)).join(''));
export async function digest(bytes){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');}
export async function uploadImage(image,transfer){
  const raw=decodeBase64(image.data,699052);
  if(raw.length>524288 || await digest(raw)!==image.sha256)throw new Error('Saved photo integrity check failed');
  for(let part=0;part*32768<raw.length;part++){
    const result=await transfer({action:'upload',upload:image.upload,part,total:raw.length,sha256:image.sha256,data:encode(raw.slice(part*32768,(part+1)*32768))});
    if(result?.upload!==image.upload || result?.part!==part || result?.stored!==true)throw new Error('Upload receipt invalid; no message submitted');
  }
}
export async function downloadImage(eventId,index,transfer){
  let offset=0,first=null;const chunks=[];
  do {
    const result=await transfer({action:'read',event_id:eventId,index,offset});
    if(!result || result.event_id!==eventId || result.index!==index || result.offset!==offset ||
       !Number.isInteger(result.total) || result.total<=offset || result.total>4000000 ||
       !['image/png','image/jpeg','image/webp'].includes(result.mime) || !/^[0-9a-f]{64}$/.test(result.sha256))throw new Error('Invalid image response');
    if(first && (first.sha256!==result.sha256 || first.total!==result.total || first.mime!==result.mime))throw new Error('Image changed during transfer');
    first ??=result;
    const chunk=decodeBase64(result.data,87384);
    if(chunk.length!==Math.min(65536,result.total-offset))throw new Error('Incomplete image chunk');
    chunks.push(chunk);offset+=chunk.length;
  }while(offset<first.total);
  const bytes=new Uint8Array(offset);let at=0;for(const chunk of chunks){bytes.set(chunk,at);at+=chunk.length;}
  if(await digest(bytes)!==first.sha256)throw new Error('Image integrity check failed');
  return {mime:first.mime,data:encode(bytes)};
}
