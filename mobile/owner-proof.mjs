// Validate the local intent before a native key signs any relay-supplied bytes.
// Signature production is injected by the Android Keystore adapter, never JS keys.
export function decodeBase64(value,max=8192) {
  if(typeof value!=='string' || !value || value.length>max)throw new Error('Invalid proof encoding');
  let text;try{text=atob(value);}catch{throw new Error('Invalid proof encoding');}
  if(btoa(text)!==value)throw new Error('Invalid proof encoding');
  return Uint8Array.from(text,c=>c.charCodeAt(0));
}
export async function validateOwnerProof(proof,intent,{now=Date.now(),cryptoImpl=globalThis.crypto}={}) {
  const bytes=decodeBase64(proof.payload);
  const text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);
  const value=JSON.parse(text);
  const expectedKeys=['protocol','version','nonce','grant_id','home_server','account_id','device_id',
    'installation_id','conversation_id','operation','content_sha256','expires_at'].sort();
  if(!value || Array.isArray(value) || JSON.stringify(Object.keys(value).sort())!==JSON.stringify(expectedKeys) ||
     value.protocol!=='iam-owner-proof' || value.version!==1 || typeof proof.nonce!=='string' ||
     !/^[A-Za-z0-9_-]{32,128}$/.test(proof.nonce) || value.nonce!==proof.nonce ||
     !Number.isSafeInteger(value.expires_at) || value.expires_at!==proof.expires_at ||
     value.expires_at*1000<=now || value.expires_at*1000>now+60000)
    throw new Error('Device proof does not match intent');
  for(const key of ['grant_id','home_server','account_id','device_id','installation_id','conversation_id','operation'])
    if(typeof intent[key]!=='string' || !intent[key] || value[key]!==intent[key])throw new Error('Device proof does not match intent');
  if(!['history','send','attachment','events'].includes(intent.operation))throw new Error('Unsupported signed operation');
  const digest=await cryptoImpl.subtle.digest('SHA-256',decodeBase64(intent.content,['attachment','events'].includes(intent.operation)?65536:4096));
  const hex=Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');
  if(value.content_sha256!==hex)throw new Error('Device proof does not match content');
  return bytes;
}
