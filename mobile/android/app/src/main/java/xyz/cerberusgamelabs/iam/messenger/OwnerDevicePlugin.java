package xyz.cerberusgamelabs.iam.messenger;

import com.getcapacitor.*;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.ActivityCallback;
import androidx.activity.result.ActivityResult;
import android.content.Intent;
import android.app.Activity;
import java.net.URI;
import javax.net.ssl.HttpsURLConnection;
import java.nio.charset.StandardCharsets;
import java.io.*;
import java.util.List;
import java.util.Arrays;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;

@CapacitorPlugin(name="OwnerDevice",permissions={@com.getcapacitor.annotation.Permission(alias="notifications",strings={"android.permission.POST_NOTIFICATIONS"})})
public class OwnerDevicePlugin extends Plugin {
    private DeviceVault vault;
    private final ExecutorService network=Executors.newSingleThreadExecutor();
    private final ExecutorService streaming=Executors.newSingleThreadExecutor();
    private volatile HttpsURLConnection streamConnection;
    private volatile long streamGeneration=0;
    private volatile boolean foreground=true;
    private volatile String streamId;
    private byte[] pendingExport;
    private static final List<String> PATHS=Arrays.asList("/v1/auth/register","/v1/auth/resend","/v1/auth/forgot","/v1/auth/verify","/v1/auth/reset",
        "/v1/messenger/login","/v1/messenger/logout",
        "/v1/messenger/friends/self","/v1/messenger/friends/roster","/v1/messenger/friends/invite",
        "/v1/messenger/friends/discover","/v1/messenger/friends/request","/v1/messenger/friends/decide",
        "/v1/messenger/friends/send","/v1/messenger/friends/history","/v1/messenger/friends/read",
        "/v1/messenger/friends/listen","/v1/messenger/friends/attachment","/v1/messenger/friends/typing",
        "/v1/messenger/events/challenge","/v1/messenger/events/sync",
        "/v1/messenger/pairing/request","/v1/messenger/pairing/status","/v1/messenger/pairing/claim","/v1/messenger/history/challenge","/v1/messenger/history/read",
        "/v1/messenger/send/challenge","/v1/messenger/send/submit",
        "/v1/messenger/attachment/challenge","/v1/messenger/attachment/transfer");
    @Override public void load(){vault=new DeviceVault(getContext(),"iam-v1");}
    private synchronized void stopStream(){streamGeneration++;if(streamConnection!=null)streamConnection.disconnect();streamConnection=null;}
    @PluginMethod public void closeStream(PluginCall call){stopStream();call.resolve();}
    @PluginMethod public void openStream(PluginCall call){
        stopStream();final long generation=streamGeneration;
        final String origin=call.getString("origin"),token=call.getString("token"),id=call.getString("id");
        final String kind=call.getString("kind","owner");
        if(!"owner".equals(kind) && !"friends".equals(kind)){call.reject("Invalid stream kind");return;}
        if(!foreground){call.reject("Foreground connection paused");return;}
        if(token==null || !token.matches("[A-Za-z0-9_-]{43}") || id==null || !id.matches("[0-9a-f-]{36}")){call.reject("Invalid stream request");return;}
        streamId=id;
        streaming.execute(()->{
            HttpsURLConnection connection=null;
            try{
                // Reuse strict origin validation, then select this one fixed SSE route.
                URI url=endpoint(origin,"/v1/messenger/events/sync").resolve("friends".equals(kind)?"/v1/messenger/friends/events":"/v1/messenger/events/listen");
                connection=(HttpsURLConnection)url.toURL().openConnection();
                synchronized(this){if(generation!=streamGeneration)return;streamConnection=connection;}
                connection.setInstanceFollowRedirects(false);connection.setConnectTimeout(15000);connection.setReadTimeout(25000);
                connection.setUseCaches(false);connection.setDoOutput(true);connection.setRequestMethod("POST");
                connection.setRequestProperty("Authorization","Bearer "+token);connection.setRequestProperty("Content-Type","application/json");
                byte[] body=new org.json.JSONObject().put("stream_id",id).toString().getBytes(StandardCharsets.UTF_8);
                try(OutputStream out=connection.getOutputStream()){out.write(body);}
                if(connection.getResponseCode()!=200 || !connection.getContentType().startsWith("text/event-stream"))throw new IOException();
                if(generation!=streamGeneration)return;
                notifyListeners("messengerStream",new JSObject().put("id",id).put("state","connected"));
                long deadline=android.os.SystemClock.elapsedRealtime()+300000;
                try(InputStream input=connection.getInputStream()){
                    ByteArrayOutputStream line=new ByteArrayOutputStream();int b;
                    while(generation==streamGeneration && android.os.SystemClock.elapsedRealtime()<deadline && (b=input.read())!=-1){
                        if(b=='\n'){
                            String value=line.toString(StandardCharsets.UTF_8.name());line.reset();
                            if(value.startsWith("data: ")){
                                org.json.JSONObject event=new org.json.JSONObject(value.substring(6));
                                long revision=NotificationNumbers.integer(event.opt("revision"));
                                if(event.length()!=2 || !"changed".equals(event.getString("state")))throw new IOException();
                                if(generation==streamGeneration)notifyListeners("messengerStream",new JSObject().put("id",id).put("state","changed").put("revision",revision));
                            }
                        }else{if(line.size()>=1024)throw new IOException();line.write(b);}
                    }
                }
            }catch(Exception ignored){}finally{
                if(connection!=null)connection.disconnect();
                if(generation==streamGeneration)notifyListeners("messengerStream",new JSObject().put("id",id).put("state","disconnected"));
            }
        });call.resolve();
    }
    @Override protected void handleOnPause(){foreground=false;stopStream();if(streamId!=null)notifyListeners("messengerStream",new JSObject().put("id",streamId).put("state","disconnected"));super.handleOnPause();}
    @Override protected void handleOnResume(){foreground=true;super.handleOnResume();}
    @PluginMethod public void scanPairing(PluginCall call){
        getActivity().runOnUiThread(()->{
            com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions options=new com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions.Builder()
                .setBarcodeFormats(com.google.mlkit.vision.barcode.common.Barcode.FORMAT_QR_CODE).build();
            com.google.mlkit.vision.codescanner.GmsBarcodeScanning.getClient(getActivity(),options).startScan()
                .addOnSuccessListener(code->{String raw=code.getRawValue();
                    if(raw==null || raw.length()>4096 || !raw.startsWith("iam-pair:"))call.reject("Not an IAM pairing code.");
                    else call.resolve(new JSObject().put("code",raw));})
                .addOnCanceledListener(()->call.resolve(new JSObject().put("cancelled",true)))
                .addOnFailureListener(error->call.reject("QR scanner unavailable. Paste the Desktop manual code instead."));
        });
    }
    @PluginMethod public void setConversationBack(PluginCall call){
        ((MainActivity)getActivity()).setConversationBack(Boolean.TRUE.equals(call.getBoolean("enabled")));
        call.resolve();
    }
    @PluginMethod public void notificationTarget(PluginCall call){
        call.resolve(new JSObject().put("id",((MainActivity)getActivity()).takeNotificationTarget()));
    }
    @PluginMethod public void enableNotifications(PluginCall call){
        if(android.os.Build.VERSION.SDK_INT>=33 && getPermissionState("notifications")!=PermissionState.GRANTED){
            requestPermissionForAlias("notifications",call,"notificationPermission");return;
        }
        notificationToken(call);
    }
    @PluginMethod public void refreshNotificationToken(PluginCall call){
        if(!androidx.core.app.NotificationManagerCompat.from(getContext()).areNotificationsEnabled()){
            call.reject("Notifications disabled in Android settings.");return;
        }
        notificationToken(call);
    }
    @com.getcapacitor.annotation.PermissionCallback private void notificationPermission(PluginCall call){
        if(getPermissionState("notifications")!=PermissionState.GRANTED){call.reject("Notifications were not allowed. Chat still works.");return;}
        notificationToken(call);
    }
    private void notificationToken(PluginCall call){
        try{
            com.google.firebase.messaging.FirebaseMessaging messaging=com.google.firebase.messaging.FirebaseMessaging.getInstance();
            messaging.setAutoInitEnabled(true);
            messaging.getToken().addOnCompleteListener(task->{
                if(task.isSuccessful())call.resolve(new JSObject().put("token",task.getResult()));
                else call.reject("Push registration unavailable. Chat still works.");
            });
        }catch(Exception e){call.reject("Push is not configured in this build.");}
    }
    @PluginMethod public void registerNotificationScope(PluginCall call){
        try{ReadNotifications.register(getContext(),call.getString("id"),NotificationNumbers.integer(call.getData().opt("expires")));call.resolve();}
        catch(Exception e){call.reject("Invalid notification scope.");}
    }
    @PluginMethod public void reconcileNotification(PluginCall call){
        try{ReadNotifications.apply(getContext(),call.getString("id"),NotificationNumbers.integer(call.getData().opt("revision")),call.getInt("unread",-1),
            System.currentTimeMillis()/1000+60,false);call.resolve();}
        catch(Exception e){call.reject("Notification state unavailable.");}
    }
    @PluginMethod public void signEvents(PluginCall call){
        try{
            JSObject intent=call.getObject("intent");
            org.json.JSONObject query=new org.json.JSONObject(new String(DeviceVault.decode(intent.getString("content"),4096),StandardCharsets.UTF_8));
            if(query.getJSONArray("read").length()>0 && (!getActivity().hasWindowFocus() ||
                getContext().getSystemService(android.app.KeyguardManager.class).isDeviceLocked()))throw new SecurityException();
            call.resolve(new JSObject().put("signature",vault.signEvents(call.getString("payload"),intent,System.currentTimeMillis()/1000)));
        }
        catch(Exception e){call.reject("Read-state signing denied.");}
    }
    @PluginMethod public void getKey(PluginCall call){try{call.resolve(JSObject.fromJSONObject(vault.identity()));}catch(Exception e){call.reject("Protected device key unavailable. Unlock the phone and try again.");}}
    @PluginMethod public void signHistory(PluginCall call){
        try{call.resolve(new JSObject().put("signature",vault.signHistory(call.getString("payload"),call.getObject("intent"),System.currentTimeMillis()/1000)));}
        catch(Exception e){call.reject("Device signing denied: invalid scope, expired proof or unavailable key.");}
    }
    @PluginMethod public void signSend(PluginCall call){
        try{call.resolve(new JSObject().put("signature",vault.signSend(call.getString("payload"),call.getObject("intent"),System.currentTimeMillis()/1000)));}
        catch(Exception e){call.reject("Device signing denied: invalid text, scope, expired proof or unavailable key.");}
    }
    @PluginMethod public void signAttachment(PluginCall call){
        try{call.resolve(new JSObject().put("signature",vault.signAttachment(call.getString("payload"),call.getObject("intent"),System.currentTimeMillis()/1000)));}
        catch(Exception e){call.reject("Image transfer signing denied.");}
    }
    @PluginMethod public void pickPhoto(PluginCall call){
        Intent intent=new Intent(Intent.ACTION_OPEN_DOCUMENT);intent.setType("image/*");intent.addCategory(Intent.CATEGORY_OPENABLE);
        startActivityForResult(call,intent,"photoPicked");
    }
    @PluginMethod public void pickAttachment(PluginCall call){
        Intent intent=new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("*/*").addCategory(Intent.CATEGORY_OPENABLE);
        startActivityForResult(call,intent,"attachmentPicked");
    }
    @ActivityCallback private void attachmentPicked(PluginCall call,ActivityResult result){
        if(call==null)return;
        if(result.getResultCode()!=Activity.RESULT_OK || result.getData()==null || result.getData().getData()==null){call.resolve(new JSObject().put("cancelled",true));return;}
        final android.net.Uri uri=result.getData().getData();
        network.execute(()->{
            try{
                String name=null;
                try(android.database.Cursor cursor=getContext().getContentResolver().query(uri,new String[]{android.provider.OpenableColumns.DISPLAY_NAME},null,null,null)){
                    if(cursor!=null && cursor.moveToFirst())name=cursor.getString(0);
                }
                if(name==null || name.isEmpty() || name.length()>120 || java.util.regex.Pattern.compile("[\\p{Cntrl}<>:\"/\\\\|?*]").matcher(name).find() || name.lastIndexOf('.')<0 || name.endsWith(".") || name.endsWith(" "))throw new SecurityException();
                String extension=name.substring(name.lastIndexOf('.')+1).toLowerCase(java.util.Locale.ROOT);
                boolean image=Arrays.asList("png","jpg","jpeg","webp").contains(extension);
                if(!image && !Arrays.asList("txt","md","csv","json","log","pdf","docx","xlsx","pptx").contains(extension))throw new SecurityException();
                byte[] bytes;
                try(InputStream input=getContext().getContentResolver().openInputStream(uri);ByteArrayOutputStream output=new ByteArrayOutputStream()){
                    byte[] chunk=new byte[8192];int n;
                    while((n=input.read(chunk))!=-1){if(output.size()+n>4000000)throw new SecurityException();output.write(chunk,0,n);}bytes=output.toByteArray();
                }
                if(image){String mime="image/"+(extension.equals("jpg")?"jpeg":extension);if(!mime.equals(PhotoCodec.bounds(bytes).outMimeType))throw new SecurityException();}
                else if(Arrays.asList("txt","md","csv","json","log").contains(extension)){
                    String text=StandardCharsets.UTF_8.newDecoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT).decode(java.nio.ByteBuffer.wrap(bytes)).toString();if(text.indexOf(0)>=0)throw new SecurityException();
                }else if(extension.equals("pdf")){
                    if(bytes.length<5 || !new String(bytes,0,5,StandardCharsets.US_ASCII).equals("%PDF-"))throw new SecurityException();
                }else if(bytes.length<4 || bytes[0]!=80 || bytes[1]!=75 || bytes[2]!=3 || bytes[3]!=4)throw new SecurityException();
                call.resolve(new JSObject().put("name",name).put("kind",image?"image":"document").put("data",DeviceVault.b64(bytes)));
            }catch(Exception e){call.reject("Choose a supported image or document up to 4 MB. The original file could not be read.");}
        });
    }
    @ActivityCallback private void photoPicked(PluginCall call,ActivityResult result){
        if(call==null)return;
        if(result.getResultCode()!=Activity.RESULT_OK || result.getData()==null || result.getData().getData()==null){call.resolve(new JSObject().put("cancelled",true));return;}
        network.execute(()->{try(InputStream input=getContext().getContentResolver().openInputStream(result.getData().getData());ByteArrayOutputStream output=new ByteArrayOutputStream()){
            byte[] chunk=new byte[8192];int n;
            while((n=input.read(chunk))!=-1){if(output.size()+n>8000000)throw new SecurityException();output.write(chunk,0,n);}
            byte[] jpeg=PhotoCodec.photo(output.toByteArray());
            call.resolve(new JSObject().put("data",DeviceVault.b64(jpeg)).put("sha256",DeviceVault.hex(java.security.MessageDigest.getInstance("SHA-256").digest(jpeg))));
        }catch(Exception e){call.reject("Choose a PNG, JPEG or WebP under 8 MB and 40 megapixels. Photo could not be prepared.");}});
    }
    @PluginMethod public void validateImage(PluginCall call){
        try{PhotoCodec.bounds(DeviceVault.decode(call.getString("data"),5333336));call.resolve();}
        catch(Exception e){call.reject("Image dimensions or format rejected.");}
    }
    @PluginMethod public synchronized void saveAttachment(PluginCall call){
        if(pendingExport!=null){call.reject("Finish the current save first.");return;}
        try{
            String name=call.getString("name"),data=call.getString("data");
            // Capacitor serializes activity call options into Android saved state.
            // Keep large bytes only in pendingExport, never in a Binder Bundle.
            call.getData().remove("data");
            if(!foreground || name==null || name.isEmpty() || name.length()>120 || java.util.regex.Pattern.compile("[\\p{Cntrl}<>:\"/\\\\|?*]").matcher(name).find() || name.lastIndexOf('.')<0 || name.endsWith(".") || name.endsWith(" "))throw new SecurityException();
            String extension=name.substring(name.lastIndexOf('.')+1).toLowerCase(java.util.Locale.ROOT);
            if(!Arrays.asList("txt","md","csv","json","log","pdf","docx","xlsx","pptx","png","jpg","jpeg","webp").contains(extension))throw new SecurityException();
            byte[] bytes=data!=null && data.isEmpty()?new byte[0]:DeviceVault.decode(data,5333336);
            if(bytes.length>4000000)throw new SecurityException();
            String mime="application/octet-stream";
            if(Arrays.asList("png","jpg","jpeg","webp").contains(extension)){
                mime="image/"+(extension.equals("jpg")?"jpeg":extension);
                if(!mime.equals(PhotoCodec.bounds(bytes).outMimeType))throw new SecurityException();
            }else if(Arrays.asList("txt","md","csv","json","log").contains(extension)){
                String text=StandardCharsets.UTF_8.newDecoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT).decode(java.nio.ByteBuffer.wrap(bytes)).toString();
                if(text.indexOf(0)>=0)throw new SecurityException();mime="text/plain";
            }else if(extension.equals("pdf")){
                if(bytes.length<5 || !new String(bytes,0,5,StandardCharsets.US_ASCII).equals("%PDF-"))throw new SecurityException();mime="application/pdf";
            }else if(bytes.length<4 || bytes[0]!=80 || bytes[1]!=75 || bytes[2]!=3 || bytes[3]!=4)throw new SecurityException();
            pendingExport=bytes;
            Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType(mime).putExtra(Intent.EXTRA_TITLE,name);
            startActivityForResult(call,intent,"attachmentSaved");
        }catch(Exception e){pendingExport=null;call.reject("Attachment cannot be saved: invalid format or size.");}
    }
    @ActivityCallback private synchronized void attachmentSaved(PluginCall call,ActivityResult result){
        final byte[] bytes=pendingExport;pendingExport=null;
        if(call==null){if(bytes!=null)Arrays.fill(bytes,(byte)0);return;}
        if(result.getResultCode()!=Activity.RESULT_OK || result.getData()==null || result.getData().getData()==null){if(bytes!=null)Arrays.fill(bytes,(byte)0);call.resolve(new JSObject().put("cancelled",true));return;}
        if(bytes==null){call.reject("Save expired; download the attachment again.");return;}
        final android.net.Uri destination=result.getData().getData();
        network.execute(()->{
            try{
                AttachmentExport.write(bytes,()->getContext().getContentResolver().openOutputStream(destination,"w"));
                // Resolve only after close succeeds: providers can fail on close.
                call.resolve(new JSObject().put("saved",true));
            }catch(Exception e){call.reject("Could not save the attachment. The selected file may be incomplete.");}
            finally{Arrays.fill(bytes,(byte)0);}
        });
    }
    @PluginMethod public void saveState(PluginCall call){try{vault.save(call.getString("value"));call.resolve();}catch(Exception e){call.reject("Could not save protected login state.");}}
    @PluginMethod public void loadState(PluginCall call){try{call.resolve(new JSObject().put("value",vault.load()));}catch(Exception e){call.reject("Protected login state unavailable. Unlock the phone; do not re-pair until recovery is checked.");}}
    @PluginMethod public void clearState(PluginCall call){try{vault.clear();ReadNotifications.clear(getContext());call.resolve();}catch(Exception e){call.reject("Could not remove saved login state.");}}
    static URI endpoint(String origin,String path) throws Exception {
        URI uri=new URI(origin);
        if(!"https".equals(uri.getScheme()) || uri.getHost()==null || uri.getRawUserInfo()!=null || uri.getRawQuery()!=null || uri.getRawFragment()!=null ||
           !(uri.getRawPath().isEmpty() || "/".equals(uri.getRawPath())) || !PATHS.contains(path))throw new SecurityException();
        return uri.resolve(path);
    }
    @PluginMethod public void request(PluginCall call){network.execute(()->{
        HttpsURLConnection connection=null;
        try{
            URI url=endpoint(call.getString("origin"),call.getString("path"));
            String body=call.getString("body"),token=call.getString("token","");
            int maximum=(call.getString("path","").startsWith("/v1/messenger/attachment/") || call.getString("path","").startsWith("/v1/messenger/events/") || call.getString("path","").equals("/v1/messenger/friends/attachment") || call.getString("path","").equals("/v1/messenger/friends/send"))?70000:8192;
            if(body==null || body.getBytes(StandardCharsets.UTF_8).length>maximum || (!token.isEmpty() && !token.matches("[A-Za-z0-9_-]{43}")))throw new SecurityException();
            connection=(HttpsURLConnection)url.toURL().openConnection();connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(15000);connection.setReadTimeout(15000);connection.setRequestMethod("POST");
            connection.setUseCaches(false);connection.setDoOutput(true);connection.setRequestProperty("Content-Type","application/json");
            if(!token.isEmpty())connection.setRequestProperty("Authorization","Bearer "+token);
            try(OutputStream stream=connection.getOutputStream()){stream.write(body.getBytes(StandardCharsets.UTF_8));}
            int status=connection.getResponseCode();String response="{}";
            if(status>=200 && status<300){
                if(connection.getContentLengthLong()>160000)throw new SecurityException();
                try(InputStream stream=connection.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){
                    byte[] chunk=new byte[4096];int n;
                    long deadline=android.os.SystemClock.elapsedRealtime()+30000;
                    while((n=stream.read(chunk))!=-1){if(out.size()+n>160000 || android.os.SystemClock.elapsedRealtime()>deadline)throw new SecurityException();out.write(chunk,0,n);}
                    response=out.toString(StandardCharsets.UTF_8.name());
                }
            }
            call.resolve(new JSObject().put("status",status).put("body",response));
        }catch(Exception e){call.reject("Connection failed or response rejected; outcome may be uncertain.");}
        finally{if(connection!=null)connection.disconnect();}
    });}
    @Override protected synchronized void handleOnDestroy(){if(pendingExport!=null)Arrays.fill(pendingExport,(byte)0);pendingExport=null;stopStream();streaming.shutdownNow();network.shutdownNow();super.handleOnDestroy();}
}
