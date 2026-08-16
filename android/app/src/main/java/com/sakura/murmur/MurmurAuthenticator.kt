package com.sakura.murmur

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.security.MessageDigest
import java.util.UUID

@Serializable
data class AppAttestChallenge(
    @SerialName("challenge_id") val challengeID: String,
    val challenge: String,
    @SerialName("expires_at") val expiresAt: String? = null,
) {
    val bytes: ByteArray? get() = Base64Url.decode(challenge)
}

interface MurmurAuthenticator {
    val environment: String
    fun publicHeaders(): Map<String, String>
    suspend fun storedIdentity(): MurmurIdentity?
    suspend fun pendingEnrollmentKeyID(): String?
    suspend fun enrollmentKeyID(): String
    suspend fun enrollmentAttestation(challenge: AppAttestChallenge, keyID: String): String
    suspend fun assertion(
        challenge: AppAttestChallenge,
        method: String,
        path: String,
        bodyDigest: ByteArray,
        keyID: String,
    ): String
    suspend fun completeEnrollment(identity: MurmurIdentity)
    suspend fun discardPendingEnrollmentKey()
    suspend fun clearIdentity()
}

internal object Base64Url {
    fun encode(data: ByteArray): String =
        java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(data)

    fun decode(value: String): ByteArray? = try {
        java.util.Base64.getUrlDecoder().decode(value)
    } catch (_: IllegalArgumentException) {
        null
    }
}

internal fun sha256(data: ByteArray): ByteArray =
    MessageDigest.getInstance("SHA-256").digest(data)

/**
 * The exact bytes a release-mode per-request assertion signs:
 * `sha256(challenge ‖ METHOD ‖ path ‖ sha256(body))` — the wire contract in
 * `deploy/android-server-plan.md` / `AppAuthenticator.request_client_data_hash`
 * (the challenge is the DECODED raw bytes, not the base64url text). The
 * development authenticator does NOT use this (its byte format is fixed
 * separately); Key Attestation will in Phase 2.
 */
internal fun clientDataHash(
    challenge: AppAttestChallenge,
    method: String,
    path: String,
    bodyDigest: ByteArray,
): ByteArray {
    val challengeBytes = challenge.bytes
        ?: throw MurmurFailure("invalid_challenge", "服务端发来的挑战无法解析。", retryable = false)
    var value = challengeBytes
    value += method.uppercase().toByteArray(Charsets.UTF_8)
    value += path.toByteArray(Charsets.UTF_8)
    value += bodyDigest
    return sha256(value)
}

/**
 * Device-bound identity storage — the Android counterpart of the iOS
 * `KeychainIdentityStore` (AfterFirstUnlockThisDeviceOnly). An interface so
 * the session model and authenticators stay testable on the JVM; production
 * uses [AndroidIdentityStore].
 */
interface IdentityStore {
    suspend fun loadIdentity(): MurmurIdentity?
    suspend fun saveIdentity(identity: MurmurIdentity)
    suspend fun loadPendingKeyID(): String?
    suspend fun savePendingKeyID(keyID: String)
    suspend fun clearPendingKeyID()
    suspend fun clear()
}

@Suppress("DEPRECATION")
class AndroidIdentityStore(context: Context) : IdentityStore {
    private val json = Json { ignoreUnknownKeys = true }
    private val mutex = Mutex()
    private val prefs = EncryptedSharedPreferences.create(
        context,
        "murmur_identity",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    override suspend fun loadIdentity(): MurmurIdentity? = mutex.withLock {
        prefs.getString(KEY_IDENTITY, null)?.let { json.decodeFromString<MurmurIdentity>(it) }
    }

    override suspend fun saveIdentity(identity: MurmurIdentity) = mutex.withLock {
        prefs.edit()
            .putString(KEY_IDENTITY, json.encodeToString(MurmurIdentity.serializer(), identity))
            .putString(KEY_PENDING, identity.keyID)
            .apply()
    }

    override suspend fun loadPendingKeyID(): String? = mutex.withLock {
        prefs.getString(KEY_PENDING, null)
    }

    override suspend fun savePendingKeyID(keyID: String) = mutex.withLock {
        prefs.edit().putString(KEY_PENDING, keyID).apply()
    }

    override suspend fun clearPendingKeyID() = mutex.withLock {
        prefs.edit().remove(KEY_PENDING).apply()
    }

    override suspend fun clear() = mutex.withLock {
        prefs.edit().remove(KEY_IDENTITY).remove(KEY_PENDING).apply()
    }

    private companion object {
        const val KEY_IDENTITY = "identity"
        const val KEY_PENDING = "pending-app-attest-key"
    }
}

/**
 * Shared-token development authenticator — the exact counterpart of the iOS
 * `DEBUG` DevelopmentAuthenticator, and only ever selected for debug builds
 * (see MurmurEnvironment). The wire format must stay byte-compatible with
 * `murmur/app_auth.py`'s development path:
 *
 *  - attestation: base64url("development:{challenge_id}:{key_id}")
 *  - assertion:   base64url(sha256(challenge.challenge | METHOD | path | bodyDigest | token))
 */
class DevelopmentAuthenticator(
    private val token: String,
    private val store: IdentityStore,
) : MurmurAuthenticator {

    override val environment: String = "development"

    override fun publicHeaders(): Map<String, String> =
        mapOf("X-Murmur-Development-Token" to token)

    override suspend fun storedIdentity(): MurmurIdentity? = store.loadIdentity()

    override suspend fun pendingEnrollmentKeyID(): String? = store.loadPendingKeyID()

    override suspend fun enrollmentKeyID(): String {
        store.loadPendingKeyID()?.let { if (it.startsWith("dev-")) return it }
        val keyID = "dev-${UUID.randomUUID().toString().lowercase()}"
        store.savePendingKeyID(keyID)
        return keyID
    }

    override suspend fun enrollmentAttestation(challenge: AppAttestChallenge, keyID: String): String =
        Base64Url.encode("development:${challenge.challengeID}:$keyID".toByteArray(Charsets.UTF_8))

    override suspend fun assertion(
        challenge: AppAttestChallenge,
        method: String,
        path: String,
        bodyDigest: ByteArray,
        keyID: String,
    ): String {
        var value = challenge.challenge.toByteArray(Charsets.UTF_8)
        value += method.uppercase().toByteArray(Charsets.UTF_8)
        value += path.toByteArray(Charsets.UTF_8)
        value += bodyDigest
        value += token.toByteArray(Charsets.UTF_8)
        return Base64Url.encode(sha256(value))
    }

    override suspend fun completeEnrollment(identity: MurmurIdentity) = store.saveIdentity(identity)

    override suspend fun discardPendingEnrollmentKey() = store.clearPendingKeyID()

    override suspend fun clearIdentity() = store.clear()
}
