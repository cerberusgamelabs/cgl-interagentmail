package xyz.cerberusgamelabs.iam.messenger;

import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;
import java.util.Map;

public class MessagePushService extends FirebaseMessagingService {
    @Override public void onMessageReceived(RemoteMessage message) {
        Map<String,String> data=message.getData();
        if(!"iam-unread-v1".equals(data.get("kind")))return;
        try{ReadNotifications.apply(this,data.get("subscription"),Long.parseLong(data.get("revision")),
            Integer.parseInt(data.get("unread")),Long.parseLong(data.get("expires")),true);}
        catch(RuntimeException ignored){} // Reject malformed data without logging it.
    }
    // Queue loss is not proof that a message was read. Periodic current-state
    // retransmission and foreground signed sync repair it without resetting revisions.
    @Override public void onDeletedMessages(){}
}
