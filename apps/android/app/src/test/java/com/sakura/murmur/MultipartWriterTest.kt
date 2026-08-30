package com.sakura.murmur

import java.io.File
import java.security.MessageDigest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

private fun ByteArray.indexOfSub(sub: ByteArray): Int {
    outer@ for (i in 0..size - sub.size) {
        for (j in sub.indices) if (this[i + j] != sub[j]) continue@outer
        return i
    }
    return -1
}

class MultipartWriterTest {

    @Test
    fun layoutMatchesTheWireFormatByteForByte() {
        val boundary = "Murmur-test"
        val photo = File.createTempFile("murmur-photo", ".jpg").apply { writeBytes(byteArrayOf(1, 2, 3, 4, 5)) }
        val body = File.createTempFile("murmur-multipart", ".bin")
        try {
            val writer = MultipartWriter(boundary)
            body.outputStream().buffered().use { output ->
                writer.field(output, "idempotency_key", "key-1")
                writer.field(output, "note", "一句此刻")
                writer.image(output, "test.jpg", "image/jpeg", photo)
                writer.finish(output)
            }

            val text = body.readText(Charsets.ISO_8859_1)
            assertTrue(text.startsWith("--$boundary\r\nContent-Disposition: form-data; name=\"idempotency_key\"\r\n\r\nkey-1\r\n"))
            assertTrue(text.contains("Content-Disposition: form-data; name=\"image\"; filename=\"test.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n"))
            assertTrue(text.endsWith("--$boundary--\r\n"))
            // Image bytes appear verbatim right after the image header.
            val bytes = body.readBytes()
            val header = "Content-Type: image/jpeg\r\n\r\n".toByteArray(Charsets.ISO_8859_1)
            val start = bytes.indexOfSub(header) + header.size
            assertArrayEquals(byteArrayOf(1, 2, 3, 4, 5), bytes.copyOfRange(start, start + 5))
        } finally {
            photo.delete()
            body.delete()
        }
    }

    @Test
    fun digestEqualsSha256OfTheWholeFile() {
        val boundary = "Murmur-digest"
        val photo = File.createTempFile("murmur-photo", ".jpg").apply { writeBytes(ByteArray(2048) { (it % 251).toByte() }) }
        val body = File.createTempFile("murmur-multipart", ".bin")
        try {
            val writer = MultipartWriter(boundary)
            body.outputStream().buffered().use { output ->
                writer.field(output, "idempotency_key", "key-2")
                writer.image(output, "photo.jpg", "image/jpeg", photo)
                writer.finish(output)
            }
            val expected = MessageDigest.getInstance("SHA-256").digest(body.readBytes())
            assertArrayEquals(expected, writer.sha256())
        } finally {
            photo.delete()
            body.delete()
        }
    }

    @Test
    fun emptyMultipartIsJustTheBoundary() {
        val writer = MultipartWriter("b")
        val body = File.createTempFile("murmur-multipart", ".bin")
        try {
            body.outputStream().buffered().use { writer.finish(it) }
            assertEquals("--b--\r\n", body.readText(Charsets.ISO_8859_1))
        } finally {
            body.delete()
        }
    }
}
