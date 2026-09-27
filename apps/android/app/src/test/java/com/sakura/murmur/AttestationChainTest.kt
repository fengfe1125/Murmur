package com.sakura.murmur

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

/** The DER wire format the server's AndroidKeyAttestor parses. */
class AttestationChainTest {

    @Test
    fun encodesASequenceOfOctetStringsLeafFirst() {
        val c1 = byteArrayOf(0x01, 0x02, 0x03)
        val c2 = byteArrayOf(0xAA.toByte(), 0xBB.toByte())

        val encoded = AttestationChain.encode(listOf(c1, c2))

        // SEQUENCE { OCTET STRING {c1}, OCTET STRING {c2} }, leaf first.
        val expected = byteArrayOf(
            0x30, 0x09, // SEQUENCE, length 9
            0x04, 0x03, 0x01, 0x02, 0x03, // first (leaf)
            0x04, 0x02, 0xAA.toByte(), 0xBB.toByte(), // second
        )
        assertArrayEquals(expected, encoded)
    }

    @Test
    fun usesLongFormLengthsWhenNeeded() {
        val certificate = ByteArray(200) { (it % 251).toByte() }
        val encoded = AttestationChain.encode(listOf(certificate))

        assertEquals(0x30, encoded[0].toInt() and 0xFF)
        // Content = 1 (tag) + 2 (long-form length) + 200 = 203 = 0xCB.
        assertEquals(0x81, encoded[1].toInt() and 0xFF)
        assertEquals(0xCB, encoded[2].toInt() and 0xFF)
        // OCTET STRING with a long-form length (200 = 0xC8).
        assertEquals(0x04, encoded[3].toInt() and 0xFF)
        assertEquals(0x81, encoded[4].toInt() and 0xFF)
        assertEquals(0xC8, encoded[5].toInt() and 0xFF)
        assertArrayEquals(certificate, encoded.copyOfRange(6, 206))
    }

    @Test
    fun keyIdIsUnpaddedBase64UrlOfTheSpkiHash() {
        val spki = ByteArray(91) { (it % 251).toByte() }
        val expected = java.util.Base64.getUrlEncoder().withoutPadding()
            .encodeToString(sha256(spki))
        assertEquals(expected, AttestationChain.keyIdFor(spki))
    }

    @Test
    fun emptyChainEncodesAsAnEmptySequence() {
        assertArrayEquals(byteArrayOf(0x30, 0x00), AttestationChain.encode(emptyList()))
    }
}
