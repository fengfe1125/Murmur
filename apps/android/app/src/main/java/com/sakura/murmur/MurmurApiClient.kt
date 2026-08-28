package com.sakura.murmur

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.withContext
import kotlinx.serialization.DeserializationStrategy
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.CookieJar
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.EOFException
import java.io.File
import java.net.URLEncoder
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * OkHttp port of `URLSessionMurmurAPIClient` (MurmurApp/MurmurAPI.swift).
 * Behaviour that must stay identical to the iOS client:
 *
 *  - no cache, no cookies; every request carries `Cache-Control: no-cache`
 *  - 45s connect/read/write timeouts; SSE reads get 300s
 *  - a serial gate (`gate`) around authenticated calls — one at a time
 *  - manual SSE framing (blank line terminates an event; `:` lines are
 *    keep-alive comments; `Last-Event-ID` resume) — do not replace with a
 *    helper that drops blank lines
 *  - multipart bodies are staged in `cacheDir` and deleted right after use
 */
class OkHttpMurmurApiClient(
    baseURL: String,
    private val authenticator: MurmurAuthenticator,
    private val cacheDir: File,
) : MurmurApiClient {

    private val baseURL: String = baseURL.trimEnd('/')
    private val json = Json { ignoreUnknownKeys = true }
    private val gate = Mutex()

    private val client = OkHttpClient.Builder()
        .connectTimeout(45, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS)
        .writeTimeout(45, TimeUnit.SECONDS)
        .callTimeout(300, TimeUnit.SECONDS)
        .cache(null)
        .cookieJar(CookieJar.NO_COOKIES)
        .build()

    private val sseClient = client.newBuilder()
        .readTimeout(300, TimeUnit.SECONDS)
        .build()

    // MARK: identity

    override suspend fun storedIdentity(): MurmurIdentity? = guard {
        authenticator.storedIdentity()
    }

    override suspend fun enroll(inviteCode: String, deviceName: String): MurmurIdentity = guard {
        authenticator.pendingEnrollmentKeyID()?.let { pendingKeyID ->
            try {
                return@guard recoverEnrollment(pendingKeyID)
            } catch (failure: MurmurFailure) {
                if (failure.code == "attestation_key_unknown" || failure.code == "app_attest_invalid_key") {
                    authenticator.discardPendingEnrollmentKey()
                } else {
                    throw failure
                }
            }
        }
        val keyID = authenticator.enrollmentKeyID()
        try {
            val challenge = challenge(purpose = "enrollment", keyID = keyID)
            val attestation = authenticator.enrollmentAttestation(challenge, keyID)
            val payload = EnrollmentRequest(
                challengeID = challenge.challengeID,
                inviteCode = inviteCode,
                keyID = keyID,
                attestation = attestation,
                deviceName = deviceName,
                environment = authenticator.environment,
            )
            val identity: MurmurIdentity = send(
                path = "/v1/enrollments",
                method = "POST",
                body = json.encodeToString(payload).toByteArray(Charsets.UTF_8),
                contentType = "application/json",
                authenticated = false,
                deserializer = MurmurIdentity.serializer(),
            )
            authenticator.completeEnrollment(identity)
            identity
        } catch (failure: MurmurFailure) {
            if (failure.code == "invite_invalid" ||
                failure.code == "invalid_attestation" ||
                failure.code == "app_attest_invalid_key"
            ) {
                authenticator.discardPendingEnrollmentKey()
            }
            throw failure
        }
    }

    // MARK: moments

    override suspend fun createMoment(
        note: String?,
        photo: PhotoAttachment?,
        idempotencyKey: String,
    ): MomentReceipt = guard {
        val boundary = "Murmur-${UUID.randomUUID()}"
        val bodyFile = makeMultipartBody(boundary, note, photo, idempotencyKey)
        try {
            val digest = sha256(bodyFile.readBytes())
            gate.lock()
            try {
                val request = authorizedRequestBuilder("/v1/moments", "POST", digest)
                    .header("Idempotency-Key", idempotencyKey)
                    .post(bodyFile.asRequestBody("multipart/form-data; boundary=$boundary".toMediaType()))
                    .build()
                val data = execute(client, request)
                validate(data, 200..299)
                json.decodeFromString(MomentReceipt.serializer(), data.bodyText())
            } finally {
                gate.unlock()
            }
        } finally {
            bodyFile.delete()
        }
    }

    override fun events(momentID: String, lastEventID: String?): Flow<MurmurStreamEvent> = flow {
        val path = "/v1/moments/${pathComponent(momentID)}/events"
        gate.lock()
        val response = try {
            val builder = authorizedRequestBuilder(path, "GET", EMPTY_DIGEST)
                .header("Accept", "text/event-stream")
                .header("Cache-Control", "no-cache")
            if (lastEventID != null) builder.header("Last-Event-ID", lastEventID)
            val resp = sseClient.newCall(builder.get().build()).execute()
            if (!resp.isSuccessful) {
                resp.close()
                throw MurmurFailure("stream_rejected", "Murmur 没有接通回应流。", retryable = true)
            }
            resp
        } catch (error: Throwable) {
            gate.unlock()
            throw error
        }
        gate.unlock()

        response.use {
            // Split on newlines by hand (see the iOS consumeEvents comment):
            // a blank line terminates an SSE event, so any helper that drops
            // blank lines would silently merge every event into the last one.
            val source = it.body.source()
            var eventID: String? = null
            var eventName = "message"
            val dataLines = mutableListOf<String>()
            while (true) {
                val line = try {
                    source.readUtf8LineStrict()
                } catch (_: EOFException) {
                    null
                } ?: break
                when {
                    line.isEmpty() -> {
                        decodeEvent(eventID, eventName, dataLines.joinToString("\n"))?.let { emit(it) }
                        eventID = null
                        eventName = "message"
                        dataLines.clear()
                    }
                    line.startsWith(":") -> Unit // keep-alive comment
                    line.startsWith("id:") -> eventID = line.drop(3).trim()
                    line.startsWith("event:") -> eventName = line.drop(6).trim()
                    line.startsWith("data:") -> dataLines.add(line.drop(5).trim())
                }
            }
            if (dataLines.isNotEmpty()) {
                decodeEvent(eventID, eventName, dataLines.joinToString("\n"))?.let { emit(it) }
            }
        }
    }.flowOn(Dispatchers.IO)

    override suspend fun currentProactive(): ProactiveMoment? = guard {
        gate.lock()
        try {
            val request = authorizedRequestBuilder("/v1/proactive/current", "GET", EMPTY_DIGEST).get().build()
            val data = execute(client, request)
            if (data.statusCode == 204) return@guard null
            validate(data, 200..299)
            json.decodeFromString(ProactiveMoment.serializer(), data.bodyText())
        } finally {
            gate.unlock()
        }
    }

    override suspend fun acknowledge(momentID: String, reply: String?): Unit = guard {
        send(
            path = "/v1/moments/${pathComponent(momentID)}/ack",
            method = "POST",
            body = json.encodeToString(AcknowledgeRequest(reply)).toByteArray(Charsets.UTF_8),
            contentType = "application/json",
            authenticated = true,
            allowsEmpty = true,
            deserializer = Unit.serializer(),
        )
    }

    // MARK: devices & preferences

    override suspend fun updateDevice(
        pushToken: String?,
        environment: String,
        timezone: String,
        deviceName: String,
    ): Unit = guard {
        send(
            path = "/v1/device",
            method = "PUT",
            body = json.encodeToString(DeviceRequest(pushToken, environment, timezone, deviceName))
                .toByteArray(Charsets.UTF_8),
            contentType = "application/json",
            authenticated = true,
            allowsEmpty = true,
            deserializer = Unit.serializer(),
        )
    }

    override suspend fun devices(): List<MurmurDevice> = guard {
        val response: DevicesResponse = send(
            path = "/v1/devices",
            method = "GET",
            body = ByteArray(0),
            contentType = null,
            authenticated = true,
            deserializer = DevicesResponse.serializer(),
        )
        response.devices
    }

    override suspend fun removeDevice(deviceID: String): Unit = guard {
        send(
            path = "/v1/devices/${pathComponent(deviceID)}",
            method = "DELETE",
            body = ByteArray(0),
            contentType = null,
            authenticated = true,
            allowsEmpty = true,
            deserializer = Unit.serializer(),
        )
        if (authenticator.storedIdentity()?.deviceID == deviceID) {
            authenticator.clearIdentity()
        }
    }

    override suspend fun preferences(): MurmurPreferences = guard {
        send(
            path = "/v1/preferences",
            method = "GET",
            body = ByteArray(0),
            contentType = null,
            authenticated = true,
            deserializer = MurmurPreferences.serializer(),
        )
    }

    override suspend fun updatePreferences(preferences: MurmurPreferences): Unit = guard {
        send(
            path = "/v1/preferences",
            method = "PATCH",
            body = json.encodeToString(preferences).toByteArray(Charsets.UTF_8),
            contentType = "application/json",
            authenticated = true,
            deserializer = MurmurPreferences.serializer(),
        )
    }

    override suspend fun resetLocalIdentity() {
        authenticator.clearIdentity()
    }

    override suspend fun deleteAccount(): Unit = guard {
        send(
            path = "/v1/account",
            method = "DELETE",
            body = ByteArray(0),
            contentType = null,
            authenticated = true,
            allowsEmpty = true,
            deserializer = Unit.serializer(),
        )
        authenticator.clearIdentity()
    }

    // MARK: internals

    private suspend fun challenge(purpose: String, keyID: String?): AppAttestChallenge {
        val body = json.encodeToString(ChallengeRequest(purpose, keyID)).toByteArray(Charsets.UTF_8)
        return send(
            path = "/v1/auth/challenges",
            method = "POST",
            body = body,
            contentType = "application/json",
            authenticated = false,
            deserializer = AppAttestChallenge.serializer(),
        )
    }

    private suspend fun recoverEnrollment(keyID: String): MurmurIdentity {
        val body = "{}".toByteArray(Charsets.UTF_8)
        gate.lock()
        val identity: MurmurIdentity
        try {
            val request = authorizedRequestBuilder(
                "/v1/enrollments/recover", "POST", sha256(body), explicitKeyID = keyID,
            )
                .post(body.toRequestBody("application/json".toMediaType()))
                .header("Accept", "application/json")
                .build()
            val data = execute(client, request)
            validate(data, 200..299)
            identity = json.decodeFromString(MurmurIdentity.serializer(), data.bodyText())
        } finally {
            gate.unlock()
        }
        authenticator.completeEnrollment(identity)
        return identity
    }

    private suspend fun authorizedRequestBuilder(
        path: String,
        method: String,
        bodyDigest: ByteArray,
        explicitKeyID: String? = null,
    ): Request.Builder {
        val keyID = explicitKeyID
            ?: authenticator.storedIdentity()?.keyID
            ?: throw MurmurFailure("not_enrolled", "需要先用邀请码连接 Murmur。", retryable = false)
        val challenge = challenge(purpose = "request", keyID = keyID)
        val assertion = authenticator.assertion(challenge, method, path, bodyDigest, keyID)
        val builder = Request.Builder()
            .url(url(path))
            .header("X-Murmur-Key-ID", keyID)
            .header("X-Murmur-Challenge-ID", challenge.challengeID)
            .header("X-Murmur-Assertion", assertion)
            .header("Cache-Control", "no-cache")
        for ((field, value) in authenticator.publicHeaders()) builder.header(field, value)
        return builder
    }

    private suspend fun <T> send(
        path: String,
        method: String,
        body: ByteArray,
        contentType: String?,
        authenticated: Boolean,
        allowsEmpty: Boolean = false,
        deserializer: DeserializationStrategy<T>,
    ): T {
        if (authenticated) {
            gate.lock()
            try {
                val builder = authorizedRequestBuilder(path, method, sha256(body))
                attachBodyAndHeaders(builder, method, body, contentType)
                val data = execute(client, builder.build())
                validate(data, 200..299)
                return decode(data, allowsEmpty, deserializer)
            } finally {
                gate.unlock()
            }
        }
        val builder = Request.Builder().url(url(path)).header("Cache-Control", "no-cache")
        for ((field, value) in authenticator.publicHeaders()) builder.header(field, value)
        attachBodyAndHeaders(builder, method, body, contentType)
        val data = execute(client, builder.build())
        validate(data, 200..299)
        return decode(data, allowsEmpty, deserializer)
    }

    private fun attachBodyAndHeaders(
        builder: Request.Builder,
        method: String,
        body: ByteArray,
        contentType: String?,
    ) {
        builder.header("Accept", "application/json")
        if (body.isNotEmpty()) {
            builder.method(method, body.toRequestBody(contentType?.toMediaType()))
        } else {
            builder.method(method, if (method == "GET") null else ByteArray(0).toRequestBody(null))
        }
    }

    @Suppress("UNCHECKED_CAST")
    private fun <T> decode(data: ResponseData, allowsEmpty: Boolean, deserializer: DeserializationStrategy<T>): T {
        val text = data.bodyText()
        if (text.isEmpty() && allowsEmpty && deserializer == Unit.serializer()) return Unit as T
        return json.decodeFromString(deserializer, text)
    }

    private fun validate(data: ResponseData, expected: IntRange) {
        if (data.statusCode in expected) return
        val envelope = data.bodyText().let {
            try {
                json.decodeFromString(ErrorEnvelope.serializer(), it)
            } catch (_: Exception) {
                null
            }
        }
        if (envelope != null) {
            throw MurmurFailure(envelope.error.code, envelope.error.message, envelope.error.retryable)
        }
        throw MurmurFailure.fromHTTPStatus(data.statusCode)
    }

    private fun decodeEvent(id: String?, name: String, data: String): MurmurStreamEvent? = when (name) {
        "accepted" -> MurmurStreamEvent.Accepted(id)
        "bubble" -> MurmurStreamEvent.Bubble(id, json.decodeFromString(BubblePayload.serializer(), data).text)
        "quiet" -> MurmurStreamEvent.Quiet(id)
        "done" -> {
            val done = try {
                json.decodeFromString(DonePayload.serializer(), data)
            } catch (_: Exception) {
                DonePayload()
            }
            MurmurStreamEvent.Done(id, done.move, done.scene)
        }
        "error" -> {
            val failure = json.decodeFromString(StreamFailurePayload.serializer(), data)
            MurmurStreamEvent.Failure(id, MurmurFailure(failure.code, failure.message, failure.retryable))
        }
        else -> null
    }

    private fun makeMultipartBody(
        boundary: String,
        note: String?,
        photo: PhotoAttachment?,
        idempotencyKey: String,
    ): File {
        val file = File(cacheDir, "murmur-multipart-${UUID.randomUUID()}")
        file.outputStream().buffered().use { output ->
            fun write(string: String) = output.write(string.toByteArray(Charsets.UTF_8))
            fun field(name: String, value: String) {
                write("--$boundary\r\n")
                write("Content-Disposition: form-data; name=\"$name\"\r\n\r\n")
                write("$value\r\n")
            }
            field("idempotency_key", idempotencyKey)
            if (!note.isNullOrEmpty()) field("note", note)
            if (photo != null) {
                write("--$boundary\r\n")
                write("Content-Disposition: form-data; name=\"image\"; filename=\"${photo.filename}\"\r\n")
                write("Content-Type: ${photo.mimeType}\r\n\r\n")
                photo.file.inputStream().use { it.copyTo(output) }
                write("\r\n")
            }
            write("--$boundary--\r\n")
        }
        return file
    }

    private class ResponseData(val statusCode: Int, private val body: ByteArray) {
        fun bodyText(): String = String(body, Charsets.UTF_8)
    }

    private suspend fun execute(client: OkHttpClient, request: Request): ResponseData =
        withContext(Dispatchers.IO) {
            client.newCall(request).execute().use { response: Response ->
                ResponseData(response.code, response.body.bytes())
            }
        }

    private fun url(path: String): String = "$baseURL$path"

    private fun pathComponent(value: String): String =
        URLEncoder.encode(value, Charsets.UTF_8).replace("+", "%20")

    private suspend fun <T> guard(block: suspend () -> T): T = try {
        block()
    } catch (cancelled: CancellationException) {
        throw cancelled
    } catch (failure: MurmurFailure) {
        throw failure
    } catch (error: Throwable) {
        throw MurmurFailure.from(error)
    }

    private companion object {
        val EMPTY_DIGEST: ByteArray = sha256(ByteArray(0))
    }
}

@Serializable
private data class ChallengeRequest(
    val purpose: String,
    @SerialName("key_id") val keyID: String? = null,
)

@Serializable
private data class EnrollmentRequest(
    @SerialName("challenge_id") val challengeID: String,
    @SerialName("invite_code") val inviteCode: String,
    @SerialName("key_id") val keyID: String,
    val attestation: String,
    @SerialName("device_name") val deviceName: String? = null,
    val environment: String,
    // The server defaults a missing platform to "ios" (the shipped iOS client
    // predates the field).  An Android enrolment that stays silent would be
    // recorded as iOS: push-token validation and the per-request attestor are
    // both chosen from the enrolled key's platform, so declare it here.
    val platform: String = "android",
)

@Serializable
private data class AcknowledgeRequest(val reply: String? = null)

@Serializable
private data class DeviceRequest(
    // The server grades the token against the platform recorded at enrolment;
    // "push_token" is the platform-neutral spelling (`apns_token` remains a
    // legacy alias for the shipped iOS client, which we are not).
    @SerialName("push_token") val pushToken: String? = null,
    val environment: String,
    val timezone: String,
    @SerialName("device_name") val deviceName: String? = null,
)

@Serializable
private data class DevicesResponse(val devices: List<MurmurDevice>)

@Serializable
private data class BubblePayload(val text: String)

@Serializable
private data class DonePayload(val move: String? = null, val scene: String? = null)

@Serializable
private data class StreamFailurePayload(val code: String, val message: String, val retryable: Boolean)

@Serializable
private data class ErrorEnvelope(val error: StreamFailurePayload)
