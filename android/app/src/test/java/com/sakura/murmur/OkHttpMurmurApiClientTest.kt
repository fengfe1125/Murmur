package com.sakura.murmur

import java.io.File
import java.security.MessageDigest
import java.util.Collections
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/**
 * Wire-level behaviour of [OkHttpMurmurApiClient] against a real socket:
 * headers, error envelopes, SSE framing over the wire, the multipart digest,
 * and the serial gate. The fake authenticator records the digests the client
 * signed, so the server can verify them against the bytes it received.
 */
class OkHttpMurmurApiClientTest {

    private lateinit var server: MockWebServer
    private val order = Collections.synchronizedList(mutableListOf<String>())

    private class RecordingAuthenticator : MurmurAuthenticator {
        override val environment: String = "development"
        val recordedDigests = mutableListOf<ByteArray>()

        override fun publicHeaders(): Map<String, String> =
            mapOf("X-Murmur-Development-Token" to "test-token")

        override suspend fun storedIdentity(): MurmurIdentity? =
            MurmurIdentity("test-user", "test-device", "test-key")

        override suspend fun pendingEnrollmentKeyID(): String? = null
        override suspend fun enrollmentKeyID(): String = "dev-x"

        override suspend fun enrollmentAttestation(challenge: AppAttestChallenge, keyID: String): String =
            "attestation"

        override suspend fun assertion(
            challenge: AppAttestChallenge,
            method: String,
            path: String,
            bodyDigest: ByteArray,
            keyID: String,
        ): String {
            recordedDigests += bodyDigest
            return "assertion-${Base64Url.encode(bodyDigest)}"
        }

        override suspend fun completeEnrollment(identity: MurmurIdentity) = Unit
        override suspend fun discardPendingEnrollmentKey() = Unit
        override suspend fun clearIdentity() = Unit
    }

    @Before
    fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                order += request.path ?: ""
                return when {
                    request.path == "/v1/auth/challenges" -> MockResponse()
                        .setResponseCode(200)
                        .setHeader("Content-Type", "application/json")
                        .setBody("""{"challenge_id":"c-1","challenge":"Y2hhbGxlbmdl"}""")
                    request.path == "/v1/moments" -> MockResponse()
                        .setResponseCode(200)
                        .setHeader("Content-Type", "application/json")
                        .setBody("""{"moment_id":"moment-1","status":"queued"}""")
                    request.path == "/v1/moments/moment-1/events" -> MockResponse()
                        .setResponseCode(200)
                        .setHeader("Content-Type", "text/event-stream")
                        .setBody(sseBody)
                    request.path == "/v1/devices" -> MockResponse()
                        .setResponseCode(200)
                        .setHeader("Content-Type", "application/json")
                        .setBody("""{"devices":[]}""")
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private val sseBody: String = listOf(
        ": keep-alive comment",
        "id: evt-1",
        "event: accepted",
        "data: {}",
        "",
        "id: evt-2",
        "event: bubble",
        """data: {"text":"你好"}""",
        "",
        "event: quiet",
        "data: {}",
        "",
        "event: done",
        """data: {"move":"speak","scene":"office"}""",
        "",
        "id: evt-3",
        "event: bubble",
        """data: {"text":"尾巴"}""",
    ).joinToString("\n") + "\n"

    private fun client(authenticator: RecordingAuthenticator) = OkHttpMurmurApiClient(
        baseURL = server.url("/").toString(),
        authenticator = authenticator,
        cacheDir = File(System.getProperty("java.io.tmpdir")),
    )

    @Test
    fun authenticatedRequestsCarryTheDevelopmentTokenAndNoCacheHeaders() = runTest {
        val client = client(RecordingAuthenticator())
        client.devices()
        server.takeRequest() // the challenge
        val request = server.takeRequest()
        assertEquals("/v1/devices", request.path)
        assertEquals("test-token", request.getHeader("X-Murmur-Development-Token"))
        assertEquals("no-cache", request.getHeader("Cache-Control"))
    }

    @Test
    fun errorEnvelopeIsThrownAsAMurmurFailure() = runTest {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = MockResponse()
                .setResponseCode(400)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"error":{"code":"invite_invalid","message":"邀请码无效。","retryable":false}}""")
        }
        val client = client(RecordingAuthenticator())
        try {
            client.devices()
            fail("expected MurmurFailure")
        } catch (failure: MurmurFailure) {
            assertEquals("invite_invalid", failure.code)
            assertEquals("邀请码无效。", failure.message)
            assertEquals(false, failure.retryable)
        }
    }

    @Test
    fun nonEnvelopeErrorsFallBackToHttpStatusMapping() = runTest {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = MockResponse()
                .setResponseCode(503)
                .setBody("upstream exploded")
        }
        val client = client(RecordingAuthenticator())
        try {
            client.devices()
            fail("expected MurmurFailure")
        } catch (failure: MurmurFailure) {
            assertEquals("http_503", failure.code)
            assertEquals(true, failure.retryable)
        }
    }

    @Test
    fun sseStreamDecodesEventsAcrossBlankLinesAndFlushesAtEof() = runTest {
        val client = client(RecordingAuthenticator())
        val received = mutableListOf<MurmurStreamEvent>()
        client.events("moment-1", lastEventID = null).collect { received += it }

        assertEquals(
            listOf(
                "accepted(evt-1)",
                "bubble(evt-2, 你好)",
                "quiet(null)",
                "done(null, speak, office)",
                "bubble(evt-3, 尾巴)",
            ),
            received.map {
                when (it) {
                    is MurmurStreamEvent.Accepted -> "accepted(${it.id})"
                    is MurmurStreamEvent.Bubble -> "bubble(${it.id}, ${it.text})"
                    is MurmurStreamEvent.Quiet -> "quiet(${it.id})"
                    is MurmurStreamEvent.Done -> "done(${it.id}, ${it.move}, ${it.scene})"
                    is MurmurStreamEvent.Failure -> "failure(${it.id})"
                }
            },
        )
    }

    @Test
    fun sseResumeSendsTheLastEventIDHeader() = runTest {
        val client = client(RecordingAuthenticator())
        client.events("moment-1", lastEventID = "evt-9").collect { }
        server.takeRequest() // the challenge
        val request = server.takeRequest()
        assertEquals("/v1/moments/moment-1/events", request.path)
        assertEquals("evt-9", request.getHeader("Last-Event-ID"))
    }

    @Test
    fun multipartUploadSignsTheDigestOfTheExactBytesTheServerReceives() = runTest {
        val authenticator = RecordingAuthenticator()
        val client = client(authenticator)
        val photo = File.createTempFile("murmur-photo", ".jpg").apply {
            writeBytes(ByteArray(1024) { (it % 251).toByte() })
        }
        try {
            client.createMoment(
                note = "一句此刻",
                photo = PhotoAttachment(file = photo, preview = null, filename = "photo.jpg", mimeType = "image/jpeg", byteCount = photo.length()),
                idempotencyKey = "key-1",
            )
            // The challenge request comes first, the moment POST second.
            server.takeRequest()
            val momentRequest = server.takeRequest()
            assertEquals("/v1/moments", momentRequest.path)
            assertEquals("key-1", momentRequest.getHeader("Idempotency-Key"))
            assertTrue(momentRequest.getHeader("X-Murmur-Assertion")!!.startsWith("assertion-"))

            // The digest the assertion signed equals the sha256 of the exact
            // body bytes the server received.
            val expected = MessageDigest.getInstance("SHA-256").digest(momentRequest.body.readByteArray())
            val signed = Base64Url.decode(momentRequest.getHeader("X-Murmur-Assertion")!!.removePrefix("assertion-"))!!
            assertEquals(Base64Url.encode(expected), Base64Url.encode(signed))
            // And the authenticator saw the same digest the client computed.
            assertEquals(Base64Url.encode(expected), Base64Url.encode(authenticator.recordedDigests.last()))
        } finally {
            photo.delete()
        }
    }

    @Test
    fun serialGateKeepsAuthenticatedCallsSequential() = runTest {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                order += request.path ?: ""
                if (request.path == "/v1/moments") {
                    Thread.sleep(150) // hold the first moment open long enough to race
                }
                return if (request.path == "/v1/moments") {
                    MockResponse()
                        .setResponseCode(200)
                        .setHeader("Content-Type", "application/json")
                        .setBody("""{"moment_id":"moment-1","status":"queued"}""")
                } else {
                    MockResponse()
                        .setResponseCode(200)
                        .setHeader("Content-Type", "application/json")
                        .setBody("""{"challenge_id":"c-1","challenge":"Y2hhbGxlbmdl"}""")
                }
            }
        }
        val authenticator = RecordingAuthenticator()
        val client = client(authenticator)
        val photo = File.createTempFile("murmur-photo", ".jpg").apply { writeBytes(ByteArray(8)) }
        try {
            val first = async {
                client.createMoment(null, PhotoAttachment(file = photo, preview = null, filename = "a.jpg", mimeType = "image/jpeg", byteCount = 8), "key-a")
            }
            val second = async {
                client.createMoment(null, PhotoAttachment(file = photo, preview = null, filename = "b.jpg", mimeType = "image/jpeg", byteCount = 8), "key-b")
            }
            first.await()
            second.await()
        } finally {
            photo.delete()
        }
        // The gate wraps challenge+POST as one unit: no interleaving.
        assertEquals(
            listOf("/v1/auth/challenges", "/v1/moments", "/v1/auth/challenges", "/v1/moments"),
            order.toList(),
        )
    }

    @Test
    fun sseHandshakeReleasesTheGateWhileTheStreamIsOpen() = runTest {
        order.clear()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                order += request.path ?: ""
                return when (request.path) {
                    "/v1/auth/challenges" -> MockResponse()
                        .setResponseCode(200)
                        .setHeader("Content-Type", "application/json")
                        .setBody("""{"challenge_id":"c-1","challenge":"Y2hhbGxlbmdl"}""")
                    "/v1/moments/moment-1/events" -> MockResponse()
                        .setResponseCode(200)
                        .setHeader("Content-Type", "text/event-stream")
                        .setBody("id: 1\nevent: accepted\ndata: {}\n\n")
                        .setSocketPolicy(SocketPolicy.KEEP_OPEN)
                    "/v1/devices" -> MockResponse()
                        .setResponseCode(200)
                        .setHeader("Content-Type", "application/json")
                        .setBody("""{"devices":[]}""")
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        val client = client(RecordingAuthenticator())
        val stream = launch {
            client.events("moment-1", lastEventID = null).collect { }
        }
        // Run the launched collector to its first suspension so the handshake
        // actually starts on the IO dispatcher before we block this thread in
        // takeRequest below.
        advanceUntilIdle()
        // The stream's handshake is challenge → GET events. Once the GET has
        // arrived the socket stays open; a concurrent authenticated call must
        // then complete — the gate was released right after the handshake,
        // exactly like the iOS client.
        server.takeRequest() // challenge for the stream
        server.takeRequest() // the events GET, still open
        client.devices()
        assertTrue(order.indexOf("/v1/moments/moment-1/events") < order.indexOf("/v1/devices"))

        // Close the socket first: the blocked read then finishes and the
        // collector job can complete.
        server.shutdown()
        try {
            stream.join()
        } catch (_: Throwable) {
            // Tearing the socket down may surface as an IOException from the
            // read loop; the handshake assertion above is what this test owns.
        }
    }
}
