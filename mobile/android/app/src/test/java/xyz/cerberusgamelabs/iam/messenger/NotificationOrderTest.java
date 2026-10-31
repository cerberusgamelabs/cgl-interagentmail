package xyz.cerberusgamelabs.iam.messenger;
import org.junit.Test;
import static org.junit.Assert.*;
public class NotificationOrderTest {
    @Test public void delayedUnreadCannotResurrectRead(){
        assertTrue(NotificationOrder.accept(2,1,0,200,300,100));
        assertFalse(NotificationOrder.accept(1,2,3,200,300,100));
        assertFalse(NotificationOrder.accept(2,2,0,200,300,100));
    }
    @Test public void expiredUnknownAndInvalidAreRejected(){
        assertFalse(NotificationOrder.accept(1,-1,1,200,0,100));
        assertFalse(NotificationOrder.accept(1,-1,1,100,200,100));
        assertFalse(NotificationOrder.accept(1,-1,-1,200,200,100));
        assertTrue(NotificationOrder.accept(1,-1,1,200,200,100));
    }
}
