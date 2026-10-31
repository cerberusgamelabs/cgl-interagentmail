package xyz.cerberusgamelabs.iam.messenger;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.junit.*;
import org.junit.runner.RunWith;
import org.json.JSONObject;
import java.security.*;
import java.security.spec.X509EncodedKeySpec;
import java.nio.charset.StandardCharsets;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class DeviceVaultTest {
    DeviceVault vault;
    final String prefix="instrumentation-only";
    @Before public void setup(){vault=new DeviceVault(InstrumentationRegistry.getInstrumentation().getTargetContext(),prefix);}
    @After public void cleanup() throws Exception {
        vault.clear();KeyStore ks=KeyStore.getInstance("AndroidKeyStore");ks.load(null);ks.deleteEntry(prefix+".owner-p256");
    }
    @Test public void keyIsStableAndPrivateMaterialCannotBeExported() throws Exception {
        JSONObject first=vault.identity();
        DeviceVault reopened=new DeviceVault(InstrumentationRegistry.getInstrumentation().getTargetContext(),prefix);
        assertEquals(first.getString("publicKey"),reopened.identity().getString("publicKey"));
        assertNull(vault.signingKey().getEncoded());
        assertEquals(64,first.getString("fingerprint").length());
    }
    @Test public void encryptedStateSurvivesReopenAndClear() throws Exception {
        vault.save("synthetic-token-only");
        DeviceVault reopened=new DeviceVault(InstrumentationRegistry.getInstrumentation().getTargetContext(),prefix);
        assertEquals("synthetic-token-only",reopened.load());
        reopened.clear();assertNull(vault.load());
    }
    private JSONObject intent() throws Exception {
        return new JSONObject().put("operation","history").put("grant_id","grant").put("home_server","home")
            .put("account_id","account").put("device_id","device").put("installation_id","node").put("conversation_id","pilot")
            .put("content",DeviceVault.b64("{}".getBytes(StandardCharsets.UTF_8)));
    }
    @Test public void multiConversationStateExceedsOldLimitButRemainsBounded() throws Exception {
        String state="x".repeat(100000);
        vault.save(state);
        DeviceVault reopened=new DeviceVault(InstrumentationRegistry.getInstrumentation().getTargetContext(),prefix);
        assertEquals(state,reopened.load());
        try{vault.save("x".repeat(8388609));fail("oversized state accepted");}catch(SecurityException expected){}
        assertEquals(state,reopened.load());
    }
    private String proof(JSONObject intent,long expiry) throws Exception {
        JSONObject proof=new JSONObject(intent.toString());proof.remove("content");
        proof.put("protocol","iam-owner-proof").put("version",1).put("nonce","n".repeat(43)).put("expires_at",expiry)
            .put("content_sha256",DeviceVault.hex(MessageDigest.getInstance("SHA-256").digest(DeviceVault.decode(intent.getString("content"),4096))));
        return DeviceVault.b64(proof.toString().getBytes(StandardCharsets.UTF_8));
    }
    @Test public void nativeSignatureVerifiesAndRejectsSubstitution() throws Exception {
        long now=System.currentTimeMillis()/1000;JSONObject intent=intent();String payload=proof(intent,now+50);
        String signature=vault.signHistory(payload,intent,now);
        PublicKey key=KeyFactory.getInstance("EC").generatePublic(new X509EncodedKeySpec(DeviceVault.decode(vault.identity().getString("publicKey"),256)));
        Signature verify=Signature.getInstance("SHA256withECDSA");verify.initVerify(key);verify.update(DeviceVault.decode(payload,8192));
        assertTrue(verify.verify(DeviceVault.decode(signature,128)));
        // Public synthetic vector for verification by the real Python verifier.
        JSONObject vector=new JSONObject().put("publicKey",vault.identity().getString("publicKey"))
            .put("signature",signature).put("payload",payload);
        try(java.io.FileOutputStream out=InstrumentationRegistry.getInstrumentation().getTargetContext()
            .openFileOutput("native-proof-vector.json",android.content.Context.MODE_PRIVATE)) {
            out.write(vector.toString().getBytes(StandardCharsets.UTF_8));
        }
        intent.put("conversation_id","other");
        try{vault.signHistory(payload,intent,now);fail("scope accepted");}catch(SecurityException expected){}
    }
    @Test public void expiryAndUnsupportedOperationsAreDenied() throws Exception {
        long now=System.currentTimeMillis()/1000;JSONObject intent=intent();
        for(long expiry:new long[]{now,now+61}){
            try{vault.signHistory(proof(intent,expiry),intent,now);fail("expiry accepted");}catch(SecurityException expected){}
        }
        intent.put("operation","send");
        try{vault.signHistory(proof(intent,now+50),intent,now);fail("send accepted");}catch(SecurityException expected){}
    }
    @Test public void contentSubstitutionIsDenied() throws Exception {
        long now=System.currentTimeMillis()/1000;JSONObject intent=intent();String payload=proof(intent,now+50);
        intent.put("content",DeviceVault.b64("changed".getBytes(StandardCharsets.UTF_8)));
        try{vault.signHistory(payload,intent,now);fail("content accepted");}catch(SecurityException expected){}
    }
    @Test public void networkEndpointRejectsBroadCalls() throws Exception {
        assertEquals("/v1/messenger/logout",OwnerDevicePlugin.endpoint("https://example.test","/v1/messenger/logout").getPath());
        for(String origin:new String[]{"http://example.test","https://u:p@example.test","https://example.test/path","https://example.test?x=1"}){
            try{OwnerDevicePlugin.endpoint(origin,"/v1/messenger/logout");fail("origin accepted");}catch(SecurityException expected){}
        }
        try{OwnerDevicePlugin.endpoint("https://example.test","/admin");fail("admin accepted");}catch(SecurityException expected){}
    }
    @Test public void textSendIsBoundAndCannotUseHistorySigner() throws Exception {
        long now=System.currentTimeMillis()/1000;
        JSONObject intent=intent().put("operation","send").put("content",DeviceVault.b64(
            new JSONObject().put("request_id",java.util.UUID.randomUUID().toString()).put("body","hello").toString().getBytes(StandardCharsets.UTF_8)));
        String payload=proof(intent,now+50);
        String signed=vault.signSend(payload,intent,now);
        PublicKey key=KeyFactory.getInstance("EC").generatePublic(new X509EncodedKeySpec(DeviceVault.decode(vault.identity().getString("publicKey"),256)));
        Signature verify=Signature.getInstance("SHA256withECDSA");verify.initVerify(key);verify.update(DeviceVault.decode(payload,8192));
        assertTrue(verify.verify(DeviceVault.decode(signed,128)));
        try{vault.signHistory(payload,intent,now);fail("wrong signer");}catch(SecurityException expected){}
        intent.put("content",DeviceVault.b64("{}".getBytes(StandardCharsets.UTF_8)));
        try{vault.signSend(payload,intent,now);fail("malformed send");}catch(org.json.JSONException | SecurityException expected){}
    }
}
