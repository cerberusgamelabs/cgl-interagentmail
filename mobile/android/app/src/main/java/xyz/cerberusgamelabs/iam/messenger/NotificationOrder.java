package xyz.cerberusgamelabs.iam.messenger;

final class NotificationOrder {
    static boolean accept(long revision,long previous,int unread,long payloadExpiry,long scopeExpiry,long now){
        return revision>=0 && revision>previous && unread>=0 && payloadExpiry>now && scopeExpiry>now;
    }
}
