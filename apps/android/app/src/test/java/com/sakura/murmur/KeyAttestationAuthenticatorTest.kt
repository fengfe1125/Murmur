package com.sakura.murmur

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The release authenticator's state machine against a fake key provider —
 * the Keystore itself is exercised on real hardware (T2.1 acceptance), but
 * every byte the client emits is decided here.
 */
class KeyAttestationAuthenticatorTest {

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

    private class FakeAttestationKeys(
        var spki: ByteArray? = null,
        var chain: List<ByteArray> = listOf(ByteArray(120), ByteArray(90)),
    ) : AttestationKeyProvider {
        val generatedChallenges = mutableListOf<ByteArray>()
        val signedData = mutableListOf<ByteArray>()
        var deleted = 0

        override fun generateAttested(alias: String, attestationChallenge: ByteArray): List<ByteArray> {
            generatedChallenges += attestationChallenge
            if (spki == null) {
                spki = ByteArray(91) { (it % 251).toByte() }
            }
            return chain
        }

        override fun spki(alias: String): ByteArray? = spki

        override fun sign(alias: String, data: ByteArray): ByteArray {
            signedData += data
            return data // the fake signature echoes the signed bytes
        }

        override fun delete(alias: String) {
            deleted += 1
            spki = null
        }
    }

    private val challenge = AppAttestChallenge(challengeID = "c-1", challenge = "Y2hhbGxlbmdl")

    @Test
    fun enrollmentIsTwoPhaseAndEmbedsTheClientDataHashAsChallenge() = runTest {
        val store = InMemoryIdentityStore()
        val keys = FakeAttestationKeys(spki = null)
        val authenticator = KeyAttestationAuthenticator(store, keys)

        // Phase 1: no key exists yet, so no key ID.
        assertNull(authenticator.enrollmentKeyID())
        assertNull(store.loadPendingKeyID())

        // Phase 2: generate with the challenge, then the key ID exists.
        val attestation = authenticator.enrollmentAttestation(challenge, keyID = null)
        assertEquals(1, keys.generatedChallenges.size)
        assertArrayEquals(sha256("challenge".toByteArray(Charsets.UTF_8)), keys.generatedChallenges[0])
        assertEquals(AttestationChain.keyIdFor(keys.spki!!), authenticator.enrollmentKeyID())
        assertEquals(authenticator.enrollmentKeyID(), store.loadPendingKeyID())

        // The attestation field is base64url(SEQUENCE OF OCTET STRING).
        val decoded = Base64Url.decode(attestation)!!
        assertArrayEquals(AttestationChain.encode(keys.chain), decoded)
    }

    @Test
    fun assertionSignsTheExactClientDataHash() = runTest {
        val store = InMemoryIdentityStore()
        val keys = FakeAttestationKeys(spki = ByteArray(91))
        val authenticator = KeyAttestationAuthenticator(store, keys)
        authenticator.enrollmentAttestation(challenge, keyID = null)

        val bodyDigest = sha256("body".toByteArray(Charsets.UTF_8))
        val assertion = authenticator.assertion(challenge, "POST", "/v1/moments", bodyDigest, "key")

        val expected = clientDataHash(challenge, "POST", "/v1/moments", bodyDigest)
        assertEquals(Base64Url.encode(expected), assertion)
        assertEquals(1, keys.signedData.size)
        assertArrayEquals(expected, keys.signedData[0])
    }

    @Test
    fun failedEnrollmentDiscardsThePendingKeyAndDeletesTheKeystoreEntry() = runTest {
        val store = InMemoryIdentityStore()
        val keys = FakeAttestationKeys(spki = ByteArray(91))
        val authenticator = KeyAttestationAuthenticator(store, keys)
        authenticator.enrollmentAttestation(challenge, keyID = null)
        assertTrue(store.loadPendingKeyID() != null)

        authenticator.discardPendingEnrollmentKey()

        assertNull(store.loadPendingKeyID())
        assertEquals(1, keys.deleted)
        assertNull(authenticator.enrollmentKeyID()) // the key is gone
    }

    @Test
    fun clearingIdentityDeletesTheKeystoreEntry() = runTest {
        val store = InMemoryIdentityStore()
        val keys = FakeAttestationKeys(spki = ByteArray(91))
        val authenticator = KeyAttestationAuthenticator(store, keys)
        authenticator.enrollmentAttestation(challenge, keyID = null)
        authenticator.completeEnrollment(MurmurIdentity("u", "d", "k"))

        authenticator.clearIdentity()

        assertNull(store.loadIdentity())
        assertEquals(1, keys.deleted)
    }

    @Test
    fun existingKeyIsReusedWithoutRegenerating() = runTest {
        val store = InMemoryIdentityStore()
        val keys = FakeAttestationKeys(spki = ByteArray(91) { (it % 251).toByte() })
        val authenticator = KeyAttestationAuthenticator(store, keys)

        // The key is already in the store: the first call already knows the ID.
        val keyID = authenticator.enrollmentKeyID()
        assertEquals(AttestationChain.keyIdFor(keys.spki!!), keyID)
        assertEquals(0, keys.generatedChallenges.size)
    }

    @Test
    fun publicHeadersAreEmptyAndEnvironmentIsProduction() {
        val authenticator = KeyAttestationAuthenticator(
            InMemoryIdentityStore(),
            FakeAttestationKeys(),
        )
        assertTrue(authenticator.publicHeaders().isEmpty())
        assertEquals("production", authenticator.environment)
    }
}
