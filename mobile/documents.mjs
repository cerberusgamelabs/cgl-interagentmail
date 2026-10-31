import {decodeBase64} from './owner-proof.mjs';
import {digest,encode} from './images.mjs';

export function validDocumentName(name){
  return typeof name==='string' && name.length>0 && name.length<=120 &&
    !/[\x00-\x1f<>:"/\\|?*]/.test(name) && !/[. ]$/.test(name) &&
    /\.(txt|md|csv|json|log|pdf|docx|xlsx|pptx)$/i.test(name);
}

export async function downloadDocument(eventId,index,transfer,validName=validDocumentName){
  let offset=0,first,bytes;
  do{
    const result=await transfer({action:'read_file',event_id:eventId,index,offset});
    if(!result || result.event_id!==eventId || result.index!==index || result.offset!==offset ||
       !Number.isSafeInteger(result.total) || result.total<offset || result.total>4000000 ||
       !validName(result.name) || typeof result.sha256!=='string' ||
       !/^[0-9a-f]{64}$/.test(result.sha256))throw new Error('Invalid document response');
    if(first && (first.total!==result.total || first.name!==result.name || first.sha256!==result.sha256))
      throw new Error('Document changed during transfer');
    if(!first){first=result;bytes=new Uint8Array(result.total);}
    const chunk=result.data===''?new Uint8Array():decodeBase64(result.data,87384);
    if(chunk.length!==Math.min(65536,result.total-offset))throw new Error('Incomplete document chunk');
    bytes.set(chunk,offset);offset+=chunk.length;
  }while(offset<first.total);
  if(await digest(bytes)!==first.sha256)throw new Error('Document integrity check failed');
  return {name:first.name,data:encode(bytes)};
}
