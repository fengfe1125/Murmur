package com.sakura.murmur

import java.io.IOException
import java.io.InterruptedIOException
import java.net.SocketTimeoutException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Port of the iOS `testFailureMappingCoversTimeoutAndAttestation`. */
class MurmurFailureTest {

    @Test
    fun failureInstancesPassThroughUnchanged() {
        val original = MurmurFailure("x", "y", retryable = false)
        assertEquals(original, MurmurFailure.from(original))
    }

    @Test
    fun timeoutAndCancellationMap() = runTest {
        // TimeoutCancellationException's constructors are internal; obtain one
        // the way production code does — from withTimeout.
        val tce = try {
            withTimeout(1) { delay(1_000) }
            error("unreachable")
        } catch (e: TimeoutCancellationException) {
            e
        }
        val timeout = MurmurFailure.from(tce)
        assertEquals("timeout", timeout.code)
        assertTrue(timeout.retryable)

        val cancelled = MurmurFailure.from(CancellationException("c"))
        assertEquals("cancelled", cancelled.code)
        assertTrue(cancelled.retryable)

        val socketTimeout = MurmurFailure.from(SocketTimeoutException("read"))
        assertEquals("timeout", socketTimeout.code)
        assertTrue(socketTimeout.retryable)

        val interrupted = MurmurFailure.from(InterruptedIOException("io"))
        assertEquals("timeout", interrupted.code)
        assertTrue(interrupted.retryable)
    }

    @Test
    fun networkErrorsAreRetryable() {
        val mapped = MurmurFailure.from(IOException("down"))
        assertEquals("network_error", mapped.code)
        assertTrue(mapped.retryable)
    }

    @Test
    fun httpStatusMapping() {
        assertEquals("http_401", MurmurFailure.fromHTTPStatus(401).code)
        assertFalse(MurmurFailure.fromHTTPStatus(401).retryable)
        assertTrue(MurmurFailure.fromHTTPStatus(408).retryable)
        assertTrue(MurmurFailure.fromHTTPStatus(429).retryable)
        assertTrue(MurmurFailure.fromHTTPStatus(503).retryable)
        assertFalse(MurmurFailure.fromHTTPStatus(400).retryable)
    }

    @Test
    fun requiresDeviceReconnectIsAPropertyOfSpecificCodes() {
        assertTrue(MurmurFailure("app_attest_invalid_key", "m", false).requiresDeviceReconnect)
        assertTrue(MurmurFailure("attestation_key_unknown", "m", false).requiresDeviceReconnect)
        assertFalse(MurmurFailure("network_error", "m", true).requiresDeviceReconnect)
        assertFalse(MurmurFailure("http_401", "m", false).requiresDeviceReconnect)
    }

    @Test
    fun productFailureCodesKeepTheirRetryability() {
        assertTrue(MurmurFailure("stream_ended", "回应中断了。", true).retryable)
        assertFalse(MurmurFailure("image_too_large", "图片不能超过 25 MB。", false).retryable)
        assertFalse(MurmurFailure("empty_image", "这张图片是空的。", false).retryable)
        assertFalse(MurmurFailure("invalid_image", "无法读取这张图片。", false).retryable)
        assertFalse(MurmurFailure("photo_unavailable", "没有读取到这张图片。", false).retryable)
        assertFalse(MurmurFailure("idempotency_conflict", "幂等键与不同内容冲突。", false).retryable)
    }
}
