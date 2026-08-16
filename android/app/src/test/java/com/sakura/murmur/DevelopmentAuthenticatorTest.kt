package com.sakura.murmur

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The development bypass must stay byte-compatible with the iOS
 * `DevelopmentAuthenticator` and with `murmur/app_auth.py`'s development path.
 */
class DevelopmentAuthenticatorTest {

    private class InMemoryIdentityStore : IdentityStore {
        var identity: MurmurIdentity? = null
        var pendingKeyID: String? = null

        override suspend fun loadIdentity(): MurmurIdentity? = identity
        override suspend fun saveIdentity(identity: MurmurIdentity) {
            this.identity = identity
            this.pendingKeyID = identity.keyID
        }
        override suspend fun loadPendingKeyID(): String? = pendingKeyID
        override suspend fun savePendingKeyID(keyID: String) {
            pendingKeyID = keyID
        }
        override suspend fun clearPendingKeyID() {
            pendingKeyID = null
        }
        override suspend fun clear() {
            identity = null
            pendingKeyID = null
        }
    }

    @Test
    fun enrollmentKeyIsADevPrefixedUuidAndPersists() = runTest {
        val store = InMemoryIdentityStore()
        val authenticator = DevelopmentAuthenticator("token", store)

        val first = authenticator.enrollmentKeyID()
        assertTrue(first.startsWith("dev-"))
        // A second call reuses the pending key instead of minting a new one.
        assertEquals(first, authenticator.enrollmentKeyID())
        assertEquals(first, store.loadPendingKeyID())
    }

    @Test
    fun attestationIsTheFixedDevelopmentString() = runTest {
        val authenticator = DevelopmentAuthenticator("token", InMemoryIdentityStore())
        val challenge = AppAttestChallenge(challengeID = "c-1", challenge = "Y2hhbGxlbmdl")

        val attestation = authenticator.enrollmentAttestation(challenge, "dev-abc")
        assertEquals("development:c-1:dev-abc", String(Base64Url.decode(attestation)!!, Charsets.UTF_8))
    }

    @Test
    fun assertionHashesChallengeMethodPathDigestAndToken() = runTest {
        val authenticator = DevelopmentAuthenticator("secret-token", InMemoryIdentityStore())
        val challenge = AppAttestChallenge(challengeID = "c-2", challenge = "Y2hhbGxlbmdl")
        val bodyDigest = sha256("body".toByteArray(Charsets.UTF_8))

        val assertion = authenticator.assertion(
            challenge = challenge,
            method = "POST",
            path = "/v1/moments",
            bodyDigest = bodyDigest,
            keyID = "dev-x",
        )

        // The development path hashes the base64url challenge TEXT, exactly as
        // the Phase-0 client shipped and `app_auth.py`'s dev mode accepts.
        val expected = "Y2hhbGxlbmdl".toByteArray(Charsets.UTF_8) +
            "POST".toByteArray(Charsets.UTF_8) +
            "/v1/moments".toByteArray(Charsets.UTF_8) +
            bodyDigest +
            "secret-token".toByteArray(Charsets.UTF_8)
        assertEquals(Base64Url.encode(sha256(expected)), assertion)
    }

    @Test
    fun clientDataHashMatchesTheServerWireContract() {
        val challenge = AppAttestChallenge(challengeID = "c-3", challenge = "Y2hhbGxlbmdl")
        val bodyDigest = sha256(ByteArray(0))

        val hash = clientDataHash(challenge, "get", "/v1/proactive/current", bodyDigest)

        // The release contract hashes the DECODED raw challenge bytes (the
        // server's `request_client_data_hash` receives the same raw bytes).
        val expected = sha256(
            "challenge".toByteArray(Charsets.UTF_8) +
                "GET".toByteArray(Charsets.UTF_8) +
                "/v1/proactive/current".toByteArray(Charsets.UTF_8) +
                bodyDigest,
        )
        assertEquals(Base64Url.encode(expected), Base64Url.encode(hash))
    }

    @Test
    fun completeEnrollmentStoresIdentityAndDiscardClearsPendingKey() = runTest {
        val store = InMemoryIdentityStore()
        val authenticator = DevelopmentAuthenticator("token", store)
        val identity = MurmurIdentity("user-1", "device-1", "dev-k")

        authenticator.completeEnrollment(identity)
        assertEquals(identity, store.loadIdentity())
        assertEquals("dev-k", store.loadPendingKeyID())

        authenticator.discardPendingEnrollmentKey()
        assertNull(store.loadPendingKeyID())

        authenticator.clearIdentity()
        assertNull(store.loadIdentity())
    }

    @Test
    fun publicHeadersCarryTheDevelopmentToken() {
        val authenticator = DevelopmentAuthenticator("token-123", InMemoryIdentityStore())
        assertEquals(mapOf("X-Murmur-Development-Token" to "token-123"), authenticator.publicHeaders())
        assertEquals("development", authenticator.environment)
    }
}
