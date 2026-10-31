package xyz.cerberusgamelabs.iam.messenger;

import android.app.*;
import android.content.*;
import android.os.Build;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;

/** Only opaque subscription IDs, revisions and expiries; no chat content. */
final class ReadNotifications {
    static final String CHANNEL="iam-messages";
    static android.content.SharedPreferences prefs(Context context) {
        return context.getSharedPreferences("iam-notification-state",Context.MODE_PRIVATE);
    }
    static synchronized void register(Context context,String id,long expires) {
        if(!id.matches("[0-9a-f-]{36}") || expires<=System.currentTimeMillis()/1000)throw new SecurityException();
        prefs(context).edit().putLong(id+".expires",expires).commit();
    }
    static synchronized void apply(Context context,String id,long revision,int unread,long expires,boolean alert) {
        long now=System.currentTimeMillis()/1000;
        android.content.SharedPreferences state=prefs(context);
        if(!id.matches("[0-9a-f-]{36}") || !NotificationOrder.accept(revision,state.getLong(id+".revision",-1),unread,
            expires,state.getLong(id+".expires",0),now))return;
        if(!state.edit().putLong(id+".revision",revision).commit())return;
        NotificationManager manager=context.getSystemService(NotificationManager.class);
        if(unread==0){manager.cancel(id,1);return;}
        if(!alert)return; // Foreground reconciliation never creates a new alert.
        if(!NotificationManagerCompat.from(context).areNotificationsEnabled())return;
        if(Build.VERSION.SDK_INT>=33 && context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
            !=android.content.pm.PackageManager.PERMISSION_GRANTED)return;
        manager.createNotificationChannel(new NotificationChannel(CHANNEL,"Messages",NotificationManager.IMPORTANCE_DEFAULT));
        Intent intent=new Intent(context,MainActivity.class).setAction("iam.notification."+id)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP|Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent open=PendingIntent.getActivity(context,0,intent,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        Notification notification=new NotificationCompat.Builder(context,CHANNEL)
            .setSmallIcon(android.R.drawable.stat_notify_chat).setContentTitle("IAM Messenger")
            .setContentText("New messages. Open Messenger to read.")
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE).setOnlyAlertOnce(true)
            .setContentIntent(open).setAutoCancel(true)
            .setTimeoutAfter(Math.max(1,state.getLong(id+".expires",now)-now)*1000).build();
        try{manager.notify(id,1,notification);}catch(SecurityException ignored){}
    }
    static synchronized void clear(Context context) {
        context.getSystemService(NotificationManager.class).cancelAll();prefs(context).edit().clear().commit();
    }
}
