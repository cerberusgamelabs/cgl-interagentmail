// UTF-8 chunks are decoded only after complete, consistent, bounded retrieval.
export async function downloadText(eventId,transfer){
  let offset=0,total=null,hash=null,output;
  do{
    const part=await transfer({event_id:eventId,offset});
    if(!part || part.event_id!==eventId || part.offset!==offset ||
       !Number.isSafeInteger(part.total) || part.total<0 || part.total>1000000 ||
       typeof part.sha256!=='string' || !/^[0-9a-f]{64}$/.test(part.sha256) ||
       typeof part.data!=='string' || part.data.length>87384 ||
       !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(part.data))throw new Error('Invalid full-message response');
    if(total===null){total=part.total;hash=part.sha256;output=new Uint8Array(total);}
    if(total!==part.total || hash!==part.sha256)throw new Error('Message changed during retrieval; open it again');
    const bytes=Uint8Array.from(atob(part.data),c=>c.charCodeAt(0));
    if(bytes.length!==Math.min(65536,total-offset))throw new Error('Incomplete full-message response');
    output.set(bytes,offset);offset+=bytes.length;
  }while(offset<total);
  const actual=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',output)),b=>b.toString(16).padStart(2,'0')).join('');
  if(actual!==hash)throw new Error('Full-message integrity check failed');
  return new TextDecoder('utf-8',{fatal:true}).decode(output);
}
