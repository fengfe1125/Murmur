package com.sakura.murmur

import java.io.File
import java.io.OutputStream
import java.security.MessageDigest

/**
 * Streaming multipart/form-data writer that hashes the body as it goes, so the
 * SHA-256 request digest never needs the whole body in memory (a 25 MB photo
 * is the realistic worst case). Layout is byte-identical to the previous
 * hand-rolled body and to the iOS client.
 */
internal class MultipartWriter(private val boundary: String) {

    private val digest = MessageDigest.getInstance("SHA-256")

    fun field(output: OutputStream, name: String, value: String) {
        write(output, "--$boundary\r\n")
        write(output, "Content-Disposition: form-data; name=\"$name\"\r\n\r\n")
        write(output, "$value\r\n")
    }

    fun image(output: OutputStream, filename: String, mimeType: String, file: File) {
        write(output, "--$boundary\r\n")
        write(output, "Content-Disposition: form-data; name=\"image\"; filename=\"$filename\"\r\n")
        write(output, "Content-Type: $mimeType\r\n\r\n")
        file.inputStream().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
                output.write(buffer, 0, count)
            }
        }
        write(output, "\r\n")
    }

    fun finish(output: OutputStream) {
        write(output, "--$boundary--\r\n")
        output.flush()
    }

    /** SHA-256 of every byte written so far. */
    fun sha256(): ByteArray = digest.digest()

    private fun write(output: OutputStream, string: String) {
        val bytes = string.toByteArray(Charsets.UTF_8)
        digest.update(bytes)
        output.write(bytes)
    }
}
