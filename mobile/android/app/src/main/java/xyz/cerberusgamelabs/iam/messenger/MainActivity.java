package xyz.cerberusgamelabs.iam.messenger;

import com.getcapacitor.BridgeActivity;
import android.os.Bundle;

public class MainActivity extends BridgeActivity {
    private androidx.activity.OnBackPressedCallback conversationBack;
    void setConversationBack(boolean enabled){
        runOnUiThread(()->{if(conversationBack!=null)conversationBack.setEnabled(enabled);});
    }
    private String notificationTarget;
    private synchronized void captureNotification(android.content.Intent intent){
        String action=intent==null?null:intent.getAction();
        if(action!=null && action.startsWith("iam.notification."))notificationTarget=action.substring("iam.notification.".length());
    }
    synchronized String takeNotificationTarget(){String value=notificationTarget;notificationTarget=null;return value;}
    @Override public void onCreate(Bundle savedInstanceState) {
        registerPlugin(OwnerDevicePlugin.class);
        super.onCreate(savedInstanceState);
        conversationBack=new androidx.activity.OnBackPressedCallback(false){
            @Override public void handleOnBackPressed(){getBridge().triggerWindowJSEvent("iamBack");}
        };
        getOnBackPressedDispatcher().addCallback(this,conversationBack);
        captureNotification(getIntent());
    }
    @Override protected void onNewIntent(android.content.Intent intent){super.onNewIntent(intent);captureNotification(intent);}
}
