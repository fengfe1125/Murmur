package com.sakura.murmur

/**
 * The pure byte-level parts of the Android Key Attestation wire contract
 * (deploy/android-server-plan.md):
 *
 *  - the attestation field is a DER `SEQUENCE OF OCTET STRING`, leaf first,
 *    each OCTET STRING holding one `Certificate.getEncoded()` — exactly what
 *    the server's `AndroidKeyAttestor.verify_attestation` parses;
 *  - `key_id` is `base64url(sha256(SPKI))` without padding (Apple's rule,
 *    imposed on Android so the server keeps one invariant across platforms).
 *
 * Both are pure and JVM-tested.
 */
internal object AttestationChain {

    fun encode(certificates: List<ByteArray>): ByteArray {
        val content = ByteArray(certificates.sumOf { derOctetString(it).size })
        var offset = 0
        for (certificate in certificates) {
            val element = derOctetString(certificate)
            element.copyInto(content, offset)
            offset += element.size
        }
        return ByteArray(1 + derLength(content.size).size + content.size).also { out ->
            out[0] = 0x30 // SEQUENCE, constructed
            val length = derLength(content.size)
            length.copyInto(out, 1)
            content.copyInto(out, 1 + length.size)
        }
    }

    fun keyIdFor(spki: ByteArray): String = Base64Url.encode(sha256(spki))

    private fun derOctetString(bytes: ByteArray): ByteArray {
        val length = derLength(bytes.size)
        return ByteArray(1 + length.size + bytes.size).also { out ->
            out[0] = 0x04 // OCTET STRING
            length.copyInto(out, 1)
            bytes.copyInto(out, 1 + length.size)
        }
    }

    private fun derLength(length: Int): ByteArray = when {
        length < 0x80 -> byteArrayOf(length.toByte())
        length <= 0xFF -> byteArrayOf(0x81.toByte(), length.toByte())
        else -> byteArrayOf(0x82.toByte(), (length shr 8).toByte(), length.toByte())
    }
}
