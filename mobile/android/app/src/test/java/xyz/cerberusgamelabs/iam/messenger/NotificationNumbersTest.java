package xyz.cerberusgamelabs.iam.messenger;
import org.junit.Test;
import static org.junit.Assert.*;
public class NotificationNumbersTest {
    @Test public void acceptsJsonIntegerExpiryAndRevision() {
        assertEquals(1792600000L,NotificationNumbers.integer(Integer.valueOf(1792600000)));
        assertEquals(1L,NotificationNumbers.integer(Integer.valueOf(1)));
        assertEquals(2200000000L,NotificationNumbers.integer(Long.valueOf(2200000000L)));
        assertEquals(0L,NotificationNumbers.integer(Integer.valueOf(0)));
    }
    @Test public void rejectsMissingCoercedFractionalAndUnsafeValues() {
        for(Object value:new Object[]{null,"1792600000",1.5,Double.NaN,-1,9007199254740992L}) {
            try {NotificationNumbers.integer(value);fail("Accepted invalid number");}
            catch(IllegalArgumentException expected) {}
        }
    }
}
