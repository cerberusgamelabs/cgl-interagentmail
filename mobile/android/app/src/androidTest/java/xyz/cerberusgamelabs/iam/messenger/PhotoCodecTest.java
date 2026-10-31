package xyz.cerberusgamelabs.iam.messenger;

import android.graphics.Bitmap;
import android.graphics.Color;
import java.io.ByteArrayOutputStream;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class PhotoCodecTest {
    @Test public void selectedRasterBecomesBoundedJpeg() throws Exception {
        Bitmap bitmap=Bitmap.createBitmap(1800,900,Bitmap.Config.ARGB_8888);
        bitmap.eraseColor(Color.BLUE);
        ByteArrayOutputStream png=new ByteArrayOutputStream();bitmap.compress(Bitmap.CompressFormat.PNG,100,png);bitmap.recycle();
        byte[] result=PhotoCodec.photo(png.toByteArray());
        assertTrue(result.length<=524288);
        assertEquals("image/jpeg",PhotoCodec.bounds(result).outMimeType);
        assertTrue(PhotoCodec.bounds(result).outWidth<=1600);
    }
    @Test public void nonRasterAndOversizedInputAreRejected() {
        for(byte[] bytes:new byte[][]{new byte[]{1,2,3},new byte[8000001]}){
            try{PhotoCodec.bounds(bytes);fail("invalid image accepted");}catch(SecurityException expected){}
        }
    }
}
