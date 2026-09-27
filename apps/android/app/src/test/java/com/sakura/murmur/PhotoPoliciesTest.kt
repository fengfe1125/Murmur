package com.sakura.murmur

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Test

/** The pure photo policies, without touching the Android decoder. */
class PhotoPoliciesTest {

    @Test
    fun emptyFilesAreRefused() {
        val failure = assertThrows(MurmurFailure::class.java) { PhotoPolicies.checkSize(0) }
        assertEquals("empty_image", failure.code)
        assertThrows(MurmurFailure::class.java) { PhotoPolicies.checkSize(-1) }
    }

    @Test
    fun filesAtOrUnderTwentyFiveMegabytesAreAccepted() {
        PhotoPolicies.checkSize(1)
        PhotoPolicies.checkSize(PhotoPolicies.MAX_UPLOAD_BYTES)
        val failure = assertThrows(MurmurFailure::class.java) {
            PhotoPolicies.checkSize(PhotoPolicies.MAX_UPLOAD_BYTES + 1)
        }
        assertEquals("image_too_large", failure.code)
        assertFalse(failure.retryable)
    }

    @Test
    fun filenamesAreSanitizedToASafeBase() {
        assertEquals("moment.jpg", PhotoPolicies.safeFilename("../../etc/passwd", "jpg"))
        assertEquals("IMG-4821.jpg", PhotoPolicies.safeFilename("IMG-4821.HEIC", "jpg"))
        assertEquals("ab1.jpg", PhotoPolicies.safeFilename("a b!1.jpg", "jpg"))
        assertEquals("x".repeat(80) + ".jpg", PhotoPolicies.safeFilename("x".repeat(300) + ".png", "jpg"))
        assertEquals("moment.jpg", PhotoPolicies.safeFilename("???", "jpg"))
        assertEquals("raw.jpg", PhotoPolicies.safeFilename("raw", "jpg"))
    }

    @Test
    fun extensionsAreCleaned() {
        assertEquals("jpg", PhotoPolicies.safeFilename("photo", "JPG<").substringAfterLast('.'))
    }

    @Test
    fun decodeTargetKeepsTheLongEdgeWithinTheCeiling() {
        assertEquals(1200 to 900, PhotoPolicies.decodeTargetSize(1200, 900))
        assertEquals(1800 to 1350, PhotoPolicies.decodeTargetSize(2400, 1800))
        assertEquals(1800 to 1800, PhotoPolicies.decodeTargetSize(4000, 4000))
        // Very thin panoramas still collapse to at least 1px on the short edge.
        assertEquals(1800 to 1, PhotoPolicies.decodeTargetSize(18000, 10))
    }
}
