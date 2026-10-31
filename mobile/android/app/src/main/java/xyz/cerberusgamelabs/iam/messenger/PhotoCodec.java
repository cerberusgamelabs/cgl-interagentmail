package xyz.cerberusgamelabs.iam.messenger;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Matrix;
import android.media.ExifInterface;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.util.Arrays;

final class PhotoCodec {
    static BitmapFactory.Options bounds(byte[] raw) {
        if(raw.length>8000000)throw new SecurityException();
        BitmapFactory.Options bounds=new BitmapFactory.Options();bounds.inJustDecodeBounds=true;
        BitmapFactory.decodeByteArray(raw,0,raw.length,bounds);
        if(bounds.outWidth<=0 || bounds.outHeight<=0 || (long)bounds.outWidth*bounds.outHeight>40000000 ||
           !Arrays.asList("image/jpeg","image/png","image/webp").contains(bounds.outMimeType))throw new SecurityException();
        return bounds;
    }
    static byte[] photo(byte[] raw) throws Exception {
        BitmapFactory.Options info=bounds(raw), options=new BitmapFactory.Options();
        options.inSampleSize=1;
        while(Math.max(info.outWidth,info.outHeight)/options.inSampleSize>1600)options.inSampleSize*=2;
        Bitmap bitmap=BitmapFactory.decodeByteArray(raw,0,raw.length,options);
        if(bitmap==null)throw new SecurityException();
        try {
            ExifInterface exif=new ExifInterface(new ByteArrayInputStream(raw));
            Matrix matrix=new Matrix();
            int orientation=exif.getAttributeInt(ExifInterface.TAG_ORIENTATION,ExifInterface.ORIENTATION_NORMAL);
            switch(orientation){
                case 2: matrix.setScale(-1,1);break;
                case 3: matrix.setRotate(180);break;
                case 4: matrix.setScale(1,-1);break;
                case 5: matrix.setRotate(90);matrix.postScale(-1,1);break;
                case 6: matrix.setRotate(90);break;
                case 7: matrix.setRotate(-90);matrix.postScale(-1,1);break;
                case 8: matrix.setRotate(-90);break;
                default: break;
            }
            Bitmap rotated=Bitmap.createBitmap(bitmap,0,0,bitmap.getWidth(),bitmap.getHeight(),matrix,true);
            if(rotated!=bitmap){bitmap.recycle();bitmap=rotated;}
            for(int quality=85;quality>=35;quality-=10) {
                ByteArrayOutputStream output=new ByteArrayOutputStream();
                if(!bitmap.compress(Bitmap.CompressFormat.JPEG,quality,output))throw new SecurityException();
                if(output.size()<=524288)return output.toByteArray();
            }
            throw new SecurityException();
        } finally {bitmap.recycle();}
    }
}
