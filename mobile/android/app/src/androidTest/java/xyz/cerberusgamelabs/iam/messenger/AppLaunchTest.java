package xyz.cerberusgamelabs.iam.messenger;

import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class AppLaunchTest {
    @Test public void packagedScreenReachesNativeKeyStore() throws Exception {
        try(ActivityScenario<MainActivity> scenario=ActivityScenario.launch(MainActivity.class)) {
            long deadline=System.currentTimeMillis()+90000;String status="";
            while(System.currentTimeMillis()<deadline){
                CountDownLatch done=new CountDownLatch(1);AtomicReference<String> value=new AtomicReference<>("");
                scenario.onActivity(activity->activity.getBridge().getWebView().evaluateJavascript(
                    "document.getElementById('status')?.textContent || ''",result->{value.set(result);done.countDown();}));
                assertTrue("WebView did not answer a DOM query within 20 seconds",done.await(20,TimeUnit.SECONDS));status=value.get();
                if(status.contains("Development preview."))break;
                Thread.sleep(250);
            }
            assertTrue("Packaged native startup failed: "+status,status.contains("Development preview."));
            scenario.recreate();
        }
    }
}
