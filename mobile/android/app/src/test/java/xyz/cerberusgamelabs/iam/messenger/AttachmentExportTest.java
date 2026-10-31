package xyz.cerberusgamelabs.iam.messenger;

import org.junit.Test;
import java.io.*;
import java.util.Arrays;
import static org.junit.Assert.*;

public class AttachmentExportTest {
    @Test public void largeImageIsWrittenUnchangedAndClosed() throws Exception {
        byte[] bytes=new byte[4000000];Arrays.fill(bytes,(byte)113);
        final boolean[] closed={false};
        ByteArrayOutputStream output=new ByteArrayOutputStream(){@Override public void close(){closed[0]=true;}};
        AttachmentExport.write(bytes,()->output);
        assertArrayEquals(bytes,output.toByteArray());assertTrue(closed[0]);
    }
    @Test public void closeFailureCannotReportSuccess() throws Exception {
        try {AttachmentExport.write(new byte[]{1},()->new ByteArrayOutputStream(){@Override public void close() throws IOException{throw new IOException("close");}});fail();}
        catch(IOException expected){assertEquals("close",expected.getMessage());}
    }
    @Test public void missingBytesNeverOpenDestination() throws Exception {
        for(byte[] value:new byte[][]{null,new byte[4000001]}){
            try{AttachmentExport.write(value,()->{fail("must not create file");return null;});fail();}catch(IOException expected){}
        }
    }
    @Test public void nullStreamRejectedAndWriteFailureCloses() throws Exception {
        try{AttachmentExport.write(new byte[]{1},()->null);fail();}catch(IOException expected){}
        final boolean[] closed={false};
        try{AttachmentExport.write(new byte[]{1},()->new OutputStream(){public void write(int b)throws IOException{throw new IOException("write");}public void close(){closed[0]=true;}});fail();}
        catch(IOException expected){assertEquals("write",expected.getMessage());}assertTrue(closed[0]);
    }
}
