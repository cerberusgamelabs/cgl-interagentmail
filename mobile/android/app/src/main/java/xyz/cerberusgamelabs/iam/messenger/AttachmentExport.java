package xyz.cerberusgamelabs.iam.messenger;

import java.io.IOException;
import java.io.OutputStream;

final class AttachmentExport {
    interface Destination { OutputStream open() throws IOException; }
    static void write(byte[] bytes, Destination destination) throws IOException {
        if(bytes==null || bytes.length>4000000)throw new IOException("Invalid export size");
        try(OutputStream output=destination.open()){
            if(output==null)throw new IOException("No destination stream");
            output.write(bytes);
            output.flush();
        }
    }
}
