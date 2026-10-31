package xyz.cerberusgamelabs.iam.messenger;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyInfo;
import android.security.keystore.KeyProperties;
import android.util.AtomicFile;
import android.util.Base64;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.spec.ECGenParameterSpec;
import java.util.Arrays;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;

/** No private-key export API; encrypted state is excluded from Android backup. */
final class DeviceVault {
    private final String signingAlias, storageAlias;
    private final AtomicFile file;
    DeviceVault(Context context, String prefix) {
        signingAlias=prefix+".owner-p256";storageAlias=prefix+".state-aes";
        file=new AtomicFile(new File(context.getNoBackupFilesDir(),prefix+".state"));
    }
    private KeyStore store() throws Exception {KeyStore s=KeyStore.getInstance("AndroidKeyStore");s.load(null);return s;}
    synchronized PrivateKey signingKey() throws Exception {
        KeyStore s=store();
        if(!s.containsAlias(signingAlias)) {
            KeyPairGenerator generator=KeyPairGenerator.getInstance("EC","AndroidKeyStore");
            generator.initialize(new KeyGenParameterSpec.Builder(signingAlias,KeyProperties.PURPOSE_SIGN|KeyProperties.PURPOSE_VERIFY)
                .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256).setUnlockedDeviceRequired(true).build());
            generator.generateKeyPair();
        }
        return (PrivateKey)store().getKey(signingAlias,null);
    }
    synchronized JSONObject identity() throws Exception {
        PrivateKey key=signingKey();
        byte[] publicKey=store().getCertificate(signingAlias).getPublicKey().getEncoded();
        KeyInfo info=KeyFactory.getInstance(key.getAlgorithm(),"AndroidKeyStore").getKeySpec(key,KeyInfo.class);
        return new JSONObject().put("publicKey",b64(publicKey)).put("fingerprint",hex(MessageDigest.getInstance("SHA-256").digest(publicKey)))
            .put("hardwareBacked",info.isInsideSecureHardware());
    }
    synchronized String signHistory(String payload,JSONObject intent,long now) throws Exception {
        return signOperation(payload,intent,now,"history");
    }
    synchronized String signEvents(String payload,JSONObject intent,long now) throws Exception {
        JSONObject query=new JSONObject(new String(decode(intent.getString("content"),65536),StandardCharsets.UTF_8));
        org.json.JSONArray ids=query.getJSONArray("read");
        if(query.has("roster")){
            if(ids.length()!=0 || query.has("push") || query.length()!=(query.has("listen")?3:2))throw new SecurityException();
            org.json.JSONArray roster=query.getJSONArray("roster");if(roster.length()<1 || roster.length()>64)throw new SecurityException();
            java.util.Set<String> seen=new java.util.HashSet<>();
            for(int i=0;i<roster.length();i++){JSONObject scope=roster.getJSONObject(i);
                if(scope.length()!=2 || scope.getString("conversation").isEmpty() || scope.getString("conversation").length()>512 ||
                   !scope.getString("grant").matches("[A-Za-z0-9_-]{32,128}") || !seen.add(scope.getString("conversation")))throw new SecurityException();}
        }else if((query.length()!=1 && !(query.length()==2 && (query.has("push") || query.has("listen")))) || ids.length()>20)throw new SecurityException();
        if(query.has("listen") && (ids.length()!=0 || !query.getString("listen").matches("[0-9a-f-]{36}")))throw new SecurityException();
        if(query.has("push")) {
            JSONObject push=query.getJSONObject("push");
            if(push.length()!=2 || !push.getString("id").matches("[0-9a-f-]{36}") ||
                !push.getString("token").matches("[!-~]{20,4096}"))throw new SecurityException();
        }
        for(int i=0;i<ids.length();i++)if(!(ids.get(i) instanceof String) || ids.getString(i).isEmpty() || ids.getString(i).length()>200)throw new SecurityException();
        return signOperation(payload,intent,now,"events");
    }
    synchronized String signSend(String payload,JSONObject intent,long now) throws Exception {
        byte[] content=decode(intent.getString("content"),4096);
        JSONObject text=new JSONObject(new String(content,StandardCharsets.UTF_8));
        if(content.length>3000 || (text.length()!=2 && text.length()!=3 && text.length()!=4) || !(text.get("body") instanceof String) || text.getString("body").trim().isEmpty() ||
           !text.getString("request_id").matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"))throw new SecurityException();
        if(text.length()==4 && (!text.getString("image_upload").matches("[0-9a-f-]{36}") || !text.getString("image_sha256").matches("[0-9a-f]{64}")))throw new SecurityException();
        if(text.length()==3){
            org.json.JSONArray attachments=text.getJSONArray("attachments");
            if(attachments.length()<1 || attachments.length()>8)throw new SecurityException();
            java.util.Set<String> ids=new java.util.HashSet<>();
            for(int i=0;i<attachments.length();i++){
                JSONObject item=attachments.getJSONObject(i);
                if(item.length()!=2 || !item.getString("upload").matches("[0-9a-f-]{36}") || !item.getString("digest").matches("[0-9a-f]{64}") || !ids.add(item.getString("upload")))throw new SecurityException();
            }
        }
        return signOperation(payload,intent,now,"send");
    }
    synchronized String signAttachment(String payload,JSONObject intent,long now) throws Exception {
        JSONObject query=new JSONObject(new String(decode(intent.getString("content"),65536),StandardCharsets.UTF_8));
        String action=query.getString("action");
        if("read_avatar".equals(action)) {
            if(query.length()!=3 || !query.getString("sha256").matches("[0-9a-f]{64}") || query.getInt("offset")<0 || query.getInt("offset")>=1000000 || query.getInt("offset")%65536!=0)throw new SecurityException();
        } else if("read".equals(action) || "read_file".equals(action)) {
            if(query.length()!=4 || query.getString("event_id").length()>200 || query.getInt("index")<0 || query.getInt("index")>3 ||
               query.getInt("offset")<0 || query.getInt("offset")>=4000000 || query.getInt("offset")%65536!=0)throw new SecurityException();
        } else if("upload_blob".equals(action) || "upload_status".equals(action)) {
            int size=query.getInt("size");boolean status="upload_status".equals(action);
            if(query.length()!=(status?6:8) || size<0 || size>4000000 ||
               !query.getString("upload").matches("[0-9a-f-]{36}") || !query.getString("sha256").matches("[0-9a-f]{64}") ||
               !Arrays.asList("image","document").contains(query.getString("kind")) || query.getString("name").isEmpty() || query.getString("name").length()>120)throw new SecurityException();
            if(!status){int part=query.getInt("part");String data=query.getString("data");
                if(part<0 || part>=Math.max(1,(size+34999)/35000) || (data.isEmpty()?0:decode(data,46668).length)!=Math.min(35000,size-part*35000))throw new SecurityException();
            }
        } else if("upload".equals(action)) {
            int total=query.getInt("total"),part=query.getInt("part");
            if(query.length()!=6 || !query.getString("upload").matches("[0-9a-f-]{36}") || !query.getString("sha256").matches("[0-9a-f]{64}") ||
               total<=0 || total>524288 || part<0 || part>=(total+32767)/32768 ||
               decode(query.getString("data"),44000).length!=Math.min(32768,total-part*32768))throw new SecurityException();
        } else throw new SecurityException();
        return signOperation(payload,intent,now,"attachment");
    }
    private String signOperation(String payload,JSONObject intent,long now,String operation) throws Exception {
        byte[] bytes=decode(payload,8192);
        JSONObject proof=new JSONObject(new String(bytes,StandardCharsets.UTF_8));
        String[] fields={"grant_id","home_server","account_id","device_id","installation_id","conversation_id","operation"};
        if(proof.length()!=12 || !"iam-owner-proof".equals(proof.getString("protocol")) ||
           !(proof.get("version") instanceof Integer) || proof.getInt("version")!=1 ||
           !operation.equals(intent.getString("operation")))throw new SecurityException();
        for(String field:fields) {
            Object expected=intent.get(field);
            if(!(expected instanceof String) || ((String)expected).isEmpty() || ((String)expected).length()>512 || !expected.equals(proof.get(field)))throw new SecurityException();
        }
        Object expiry=proof.get("expires_at");
        if(!(expiry instanceof Integer || expiry instanceof Long))throw new SecurityException();
        long expires=proof.getLong("expires_at");
        if(expires<=now || expires>now+60 || !proof.getString("nonce").matches("[A-Za-z0-9_-]{32,128}"))throw new SecurityException();
        String digest=hex(MessageDigest.getInstance("SHA-256").digest(decode(intent.getString("content"),(operation.equals("attachment") || operation.equals("events"))?65536:4096)));
        if(!digest.equals(proof.getString("content_sha256")))throw new SecurityException();
        Signature signature=Signature.getInstance("SHA256withECDSA");signature.initSign(signingKey());signature.update(bytes);
        return b64(signature.sign());
    }
    private SecretKey storageKey() throws Exception {
        KeyStore s=store();
        if(!s.containsAlias(storageAlias)) {
            KeyGenerator generator=KeyGenerator.getInstance("AES","AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(storageAlias,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT)
                .setKeySize(256).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setUnlockedDeviceRequired(true).build());generator.generateKey();
        }
        return (SecretKey)store().getKey(storageAlias,null);
    }
    synchronized void save(String value) throws Exception {
        if(value==null || value.getBytes(StandardCharsets.UTF_8).length>33554432)throw new SecurityException();
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,storageKey());
        cipher.updateAAD(storageAlias.getBytes(StandardCharsets.UTF_8));
        byte[] ciphertext=cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
        byte[] data=new JSONObject().put("iv",b64(cipher.getIV())).put("data",b64(ciphertext)).toString().getBytes(StandardCharsets.UTF_8);
        FileOutputStream out=null;
        try {out=file.startWrite();out.write(data);file.finishWrite(out);}catch(Exception e){if(out!=null)file.failWrite(out);throw e;}
    }
    synchronized String load() throws Exception {
        if(!file.getBaseFile().exists())return null;
        if(file.getBaseFile().length()>45000000)throw new SecurityException();
        JSONObject data=new JSONObject(new String(file.readFully(),StandardCharsets.UTF_8));
        byte[] iv=decode(data.getString("iv"),32);if(iv.length!=12)throw new SecurityException();
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,storageKey(),new GCMParameterSpec(128,iv));
        cipher.updateAAD(storageAlias.getBytes(StandardCharsets.UTF_8));
        return new String(cipher.doFinal(decode(data.getString("data"),45000000)),StandardCharsets.UTF_8);
    }
    synchronized void clear() throws Exception {
        file.delete();
        // Deleting the wrapping key also makes any surviving ciphertext unusable.
        store().deleteEntry(storageAlias);
        if(file.getBaseFile().exists())throw new SecurityException();
    }
    static String b64(byte[] bytes){return Base64.encodeToString(bytes,Base64.NO_WRAP);}
    static byte[] decode(String value,int max){
        if(value==null || value.isEmpty() || value.length()>max)throw new SecurityException();
        byte[] result=Base64.decode(value,Base64.DEFAULT);if(!b64(result).equals(value))throw new SecurityException();return result;
    }
    static String hex(byte[] bytes){StringBuilder out=new StringBuilder();for(byte b:bytes)out.append(String.format(java.util.Locale.ROOT,"%02x",b&255));return out.toString();}
}
