package com.sakura.murmur

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/**
 * The parts of the Key Attestation authenticator that live in the Android
 * Keystore. Split out so the authenticator itself stays JVM-testable against a
 * fake; [KeystoreAttestationKeyProvider] is the production implementation.
 */
interface AttestationKeyProvider {
    /** Generates (or regenerates) the attestation key and returns the
     *  certificate chain, leaf first, each element a DER certificate. */
    fun generateAttested(alias: String, attestationChallenge: ByteArray): List<ByteArray>

    /** SPKI DER of the key, or null when no key exists yet. */
    fun spki(alias: String): ByteArray?

    /** Raw ECDSA/SHA-256 signature. Keystore emits DER (r,s) — exactly the
     *  format the server feeds to `cryptography`'s `key.verify`. */
    fun sign(alias: String, data: ByteArray): ByteArray

    fun delete(alias: String)
}

class KeystoreAttestationKeyProvider : AttestationKeyProvider {

    private val keyStore: KeyStore by lazy {
        KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
    }

    override fun generateAttested(alias: String, attestationChallenge: ByteArray): List<ByteArray> {
        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, ANDROID_KEYSTORE)
        generator.initialize(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                // The server verifies this exact value as the enrollment
                // client_data_hash = sha256(raw challenge).
                .setAttestationChallenge(attestationChallenge)
                .setUserAuthenticationRequired(false)
                .build(),
        )
        generator.generateKeyPair()
        val entry = keyStore.getEntry(alias, null) as? KeyStore.PrivateKeyEntry
            ?: throw MurmurFailure("attestation_failed", "无法生成设备安全密钥。", retryable = true)
        return entry.certificateChain.map { it.encoded }
    }

    override fun spki(alias: String): ByteArray? {
        val entry = keyStore.getEntry(alias, null) as? KeyStore.PrivateKeyEntry ?: return null
        // SPKI DER = the X.509 encoding of the public key — what the server
        // stores and hashes into the key_id.
        return entry.certificate.publicKey.encoded
    }

    override fun sign(alias: String, data: ByteArray): ByteArray {
        val privateKey = keyStore.getKey(alias, null) as? PrivateKey
            ?: throw MurmurFailure("attestation_key_unknown", "本机安全密钥不存在。", retryable = false)
        val signature = Signature.getInstance("SHA256withECDSA")
        signature.initSign(privateKey)
        signature.update(data)
        return signature.sign()
    }

    override fun delete(alias: String) {
        try {
            keyStore.deleteEntry(alias)
        } catch (_: Exception) {
            // A missing entry is already the desired state.
        }
    }

    private companion object {
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
    }
}

/**
 * Release-mode device authentication — the Android counterpart of the iOS
 * App Attest authenticator. Enrollment submits the Key Attestation chain
 * (attestation challenge baked into the key at generation); every request
 * signs `client_data_hash` with the same Keystore-held P-256 key. The wire
 * contract is fixed in deploy/android-server-plan.md and verified server-side
 * by `AndroidKeyAttestor`.
 */
class KeyAttestationAuthenticator(
    private val store: IdentityStore,
    private val keys: AttestationKeyProvider,
    private val alias: String = "murmur-attestation-v1",
) : MurmurAuthenticator {

    override val environment: String = "production"

    override fun publicHeaders(): Map<String, String> = emptyMap()

    override suspend fun storedIdentity(): MurmurIdentity? = store.loadIdentity()

    override suspend fun pendingEnrollmentKeyID(): String? = store.loadPendingKeyID()

    override suspend fun enrollmentKeyID(): String? {
        store.loadPendingKeyID()?.let { return it }
        val spki = keys.spki(alias) ?: return null
        val keyID = AttestationChain.keyIdFor(spki)
        store.savePendingKeyID(keyID)
        return keyID
    }

    override suspend fun enrollmentAttestation(challenge: AppAttestChallenge, keyID: String?): String {
        val challengeBytes = challenge.bytes
            ?: throw MurmurFailure("invalid_challenge", "服务端发来的挑战无法解析。", retryable = false)
        // The attestation challenge embedded at key generation is the
        // enrollment client_data_hash: sha256(raw challenge bytes).
        val chain = keys.generateAttested(alias, sha256(challengeBytes))
        val spki = keys.spki(alias)
            ?: throw MurmurFailure("attestation_failed", "无法读取设备安全密钥。", retryable = true)
        val computedKeyID = AttestationChain.keyIdFor(spki)
        store.savePendingKeyID(computedKeyID)
        return Base64Url.encode(AttestationChain.encode(chain))
    }

    override suspend fun assertion(
        challenge: AppAttestChallenge,
        method: String,
        path: String,
        bodyDigest: ByteArray,
        keyID: String,
    ): String {
        val data = clientDataHash(challenge, method, path, bodyDigest)
        // Keystore's ECDSA output is DER-encoded (r,s); the server's
        // `key.verify(assertion, client_data_hash, ec.ECDSA(SHA256()))`
        // expects exactly that. No raw r||s conversion.
        return Base64Url.encode(keys.sign(alias, data))
    }

    override suspend fun completeEnrollment(identity: MurmurIdentity) = store.saveIdentity(identity)

    override suspend fun discardPendingEnrollmentKey() {
        store.clearPendingKeyID()
        keys.delete(alias)
    }

    override suspend fun clearIdentity() {
        store.clear()
        keys.delete(alias)
    }
}
