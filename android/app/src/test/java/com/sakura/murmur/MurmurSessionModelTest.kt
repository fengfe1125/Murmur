package com.sakura.murmur

import java.io.File
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Port of `MurmurSessionModelTests.swift`'s state-machine matrix. The
 * transcript-store tests are deliberately absent: the Android product keeps
 * nothing locally.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class MurmurSessionModelTest {

    private lateinit var dispatcher: TestDispatcher

    @Before
    fun setUp() {
        dispatcher = StandardTestDispatcher()
        Dispatchers.setMain(dispatcher)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun model(
        api: MurmurApiClient,
        loader: PhotoLoader = FakePhotoLoader(),
        requestTimeoutSeconds: Double = 45.0,
        uploadTimeoutSeconds: Double = 300.0,
    ) = MurmurSessionModel(
        api = api,
        configurationFailure = null,
        photoLoader = loader,
        requestTimeoutSeconds = requestTimeoutSeconds,
        uploadTimeoutSeconds = uploadTimeoutSeconds,
        deviceNameProvider = { "Test Phone · Android 15" },
    )

    private suspend fun waitUntil(condition: () -> Boolean) {
        repeat(400) {
            if (condition()) return
            delay(10)
        }
        throw AssertionError("Timed out waiting for state")
    }

    private fun writeTestFile(loader: FakePhotoLoader, size: Int = 64): File {
        val file = File(loader.directory, "murmur-upload-${UUID.randomUUID()}.jpg")
        file.writeBytes(ByteArray(size))
        return file
    }

    @Test
    fun coldBootstrapIsEmptyAndDoesNotFetchProactive() = runTest(dispatcher) {
        val api = FakeMurmurApiClient()
        val session = model(api)
        advanceUntilIdle()

        assertEquals(MurmurPhase.Idle, session.uiState.value.phase)
        assertFalse(session.uiState.value.hasCurrentMoment)
        assertTrue(session.uiState.value.bubbles.isEmpty())
        assertEquals(0, api.proactiveCalls)
    }

    @Test
    fun keyboardAndButtonGateCannotCreateTwoMoments() = runTest(dispatcher) {
        val api = FakeMurmurApiClient()
        val session = model(api)
        advanceUntilIdle()
        session.updateDraftText("同一刻")

        session.submit()
        session.submit()
        advanceUntilIdle()

        assertEquals(1, api.idempotencyKeys.size)
        assertEquals(listOf("reply-1"), session.uiState.value.bubbles.map { it.text })
    }

    @Test
    fun retryReusesIdempotencyKeyAndDoesNotDuplicateBubbles() = runTest(dispatcher) {
        val api = FakeMurmurApiClient(mode = FakeMurmurApiClient.Mode.StreamFailsOnce)
        val session = model(api)
        advanceUntilIdle()
        session.updateDraftText("重试")

        session.submit()
        advanceUntilIdle()
        assertEquals(MurmurPhase.Error, session.uiState.value.phase)
        session.retry()
        advanceUntilIdle()

        assertEquals(2, api.idempotencyKeys.size)
        assertEquals(1, api.idempotencyKeys.toSet().size)
        assertEquals(listOf("reply-after-retry"), session.uiState.value.bubbles.map { it.text })
    }

    @Test
    fun automaticSSEReconnectCarriesLastEventID() = runTest(dispatcher) {
        val api = FakeMurmurApiClient(mode = FakeMurmurApiClient.Mode.DisconnectThenResume)
        val session = model(api)
        advanceUntilIdle()
        session.updateDraftText("继续")

        session.submit()
        advanceUntilIdle()

        assertEquals(listOf<String?>(null, "bubble-1"), api.lastEventIDs)
        assertEquals(listOf("先说一半"), session.uiState.value.bubbles.map { it.text })
    }

    @Test
    fun processingFailureRetryReusesOriginalIdempotencyKey() = runTest(dispatcher) {
        val api = FakeMurmurApiClient(mode = FakeMurmurApiClient.Mode.TerminalFailureThenSuccess)
        val session = model(api)
        advanceUntilIdle()
        session.updateDraftText("重新处理")

        session.submit()
        advanceUntilIdle()
        assertEquals(MurmurPhase.Error, session.uiState.value.phase)
        session.retry()
        advanceUntilIdle()

        assertEquals(2, api.idempotencyKeys.size)
        assertEquals(1, api.idempotencyKeys.toSet().size)
        assertEquals(listOf("processed-again"), session.uiState.value.bubbles.map { it.text })
    }

    @Test
    fun idempotencyConflictIsNotRetryable() = runTest(dispatcher) {
        val api = FakeMurmurApiClient(mode = FakeMurmurApiClient.Mode.IdempotencyConflict)
        val session = model(api)
        advanceUntilIdle()
        session.updateDraftText("不同内容")

        session.submit()
        advanceUntilIdle()
        assertEquals(MurmurPhase.Error, session.uiState.value.phase)
        session.retry()
        advanceUntilIdle()

        assertEquals("idempotency_conflict", session.uiState.value.failure?.code)
        assertEquals(false, session.uiState.value.failure?.retryable)
        assertEquals(1, api.idempotencyKeys.size)
    }

    @Test
    fun newMomentReplacesRatherThanAppendsHistory() = runTest(dispatcher) {
        val api = FakeMurmurApiClient()
        val session = model(api)
        advanceUntilIdle()
        session.updateDraftText("第一刻")
        session.submit()
        advanceUntilIdle()

        session.updateDraftText("第二刻")
        session.submit()
        advanceUntilIdle()

        assertEquals("第二刻", session.uiState.value.currentNote)
        assertEquals(listOf("reply-2"), session.uiState.value.bubbles.map { it.text })
    }

    @Test
    fun streamEndingWithoutDoneIsAnInterruptibleErrorNotCompletion() = runTest(dispatcher) {
        val api = FakeMurmurApiClient(mode = FakeMurmurApiClient.Mode.StreamEndsSilently)
        val session = model(api)
        advanceUntilIdle()
        session.updateDraftText("断开")

        session.submit()
        advanceUntilIdle()

        // T0.1: the moment must surface as a retryable interruption, and the
        // staged inputs must survive for the retry.
        assertEquals(MurmurPhase.Error, session.uiState.value.phase)
        assertEquals("stream_ended", session.uiState.value.failure?.code)
        assertEquals(true, session.uiState.value.failure?.retryable)
        assertEquals("断开", session.uiState.value.currentNote)
    }

    @Test
    fun timeoutBecomesRetryableError() = runTest(dispatcher) {
        val api = FakeMurmurApiClient(mode = FakeMurmurApiClient.Mode.NeverCreates)
        val session = model(api, requestTimeoutSeconds = 0.05, uploadTimeoutSeconds = 0.05)
        advanceUntilIdle()
        session.updateDraftText("超时")

        session.submit()
        advanceUntilIdle()

        assertEquals("timeout", session.uiState.value.failure?.code)
        assertEquals(true, session.uiState.value.failure?.retryable)
    }

    @Test
    fun slowUploadUsesDedicatedLongerTimeout() = runTest(dispatcher) {
        val api = FakeMurmurApiClient(mode = FakeMurmurApiClient.Mode.SlowCreates)
        val session = model(api, requestTimeoutSeconds = 0.02, uploadTimeoutSeconds = 0.25)
        advanceUntilIdle()
        session.updateDraftText("慢速移动网络")

        session.submit()
        advanceUntilIdle()

        assertNull(session.uiState.value.failure)
        assertEquals(listOf("reply-1"), session.uiState.value.bubbles.map { it.text })
    }

    @Test
    fun cancelLeavesCurrentMomentWithoutSubmittingAgain() = runTest(dispatcher) {
        val api = FakeMurmurApiClient(mode = FakeMurmurApiClient.Mode.NeverStreams)
        val session = model(api)
        advanceUntilIdle()
        session.updateDraftText("取消")
        session.submit()
        advanceUntilIdle()
        assertEquals(MurmurPhase.Responding, session.uiState.value.phase)

        session.cancelCurrentOperation()
        advanceUntilIdle()

        assertEquals(MurmurPhase.Ready, session.uiState.value.phase)
        assertEquals(1, api.idempotencyKeys.size)
    }

    @Test
    fun removingCurrentDeviceReturnsToEnrollment() = runTest(dispatcher) {
        val api = FakeMurmurApiClient()
        val session = model(api)
        advanceUntilIdle()
        session.refreshDevices()
        advanceUntilIdle()
        val current = session.uiState.value.devices.first { it.id == "test-device" }

        session.removeDevice(current)
        advanceUntilIdle()

        assertNull(session.uiState.value.identity)
        assertEquals(MurmurConnectionState.NeedsEnrollment, session.uiState.value.connection)
        assertEquals(listOf("test-device"), api.removedDeviceIDs)
    }

    @Test
    fun unknownAttestationKeyOffersExplicitLocalReconnect() = runTest(dispatcher) {
        val api = FakeMurmurApiClient(mode = FakeMurmurApiClient.Mode.AttestationKeyUnknown)
        val session = model(api)

        advanceUntilIdle()

        assertTrue(session.uiState.value.identity != null)
        assertTrue(session.uiState.value.requiresDeviceReconnect)
        assertEquals("连接异常", session.uiState.value.connection.label)
        session.resetLocalDeviceIdentity()
        advanceUntilIdle()
        assertNull(session.uiState.value.identity)
        assertEquals(MurmurConnectionState.NeedsEnrollment, session.uiState.value.connection)
        assertEquals(1, api.resetLocalIdentityCalls)
    }

    @Test
    fun notificationReplyAcknowledgesProactiveAndCreatesNewMoment() = runTest(dispatcher) {
        val api = FakeMurmurApiClient(mode = FakeMurmurApiClient.Mode.ProactiveReply)
        val session = model(api)
        advanceUntilIdle()
        session.handleNotification(momentID = "proactive-1")
        advanceUntilIdle()
        session.updateDraftText("我看见了")

        session.submit()
        advanceUntilIdle()

        assertEquals(2, api.acknowledgements.size)
        assertEquals("proactive-1", api.acknowledgements[0].first)
        assertNull(api.acknowledgements[0].second)
        assertEquals("proactive-1", api.acknowledgements[1].first)
        assertEquals("我看见了", api.acknowledgements[1].second)
        assertEquals(listOf("reply-1"), session.uiState.value.bubbles.map { it.text })
    }

    @Test
    fun successfulMomentDeletesOriginalPhotoButKeepsPreview() = runTest(dispatcher) {
        val loader = FakePhotoLoader()
        val source = writeTestFile(loader)
        val api = FakeMurmurApiClient()
        val session = model(api, loader)
        advanceUntilIdle()

        session.preparePhoto(PhotoInput.FromFile(source))
        advanceUntilIdle()
        assertEquals(MurmurPhase.Ready, session.uiState.value.phase)
        val managed = session.uiState.value.draftPhoto!!.file
        session.submit()
        advanceUntilIdle()

        assertFalse(managed.exists())
        assertTrue(session.uiState.value.currentPhoto != null)
        assertTrue(loader.leftoverFiles().isEmpty())
    }

    @Test
    fun bootstrapLoadsServerPreferences() = runTest(dispatcher) {
        val api = FakeMurmurApiClient()
        val session = model(api)
        advanceUntilIdle()
        assertEquals(2, session.uiState.value.preferences.dailyFrequency)
        assertEquals("21:00", session.uiState.value.preferences.quietStart)
    }

    @Test
    fun stateMachineWalksIdleToComplete() = runTest(dispatcher) {
        val api = FakeMurmurApiClient()
        val session = model(api)
        advanceUntilIdle()
        assertEquals(MurmurPhase.Idle, session.uiState.value.phase)
        session.beginPhotoSelection()
        assertEquals(MurmurPhase.PreparingPhoto, session.uiState.value.phase)
        session.removeDraftPhoto()
        assertEquals(MurmurPhase.Idle, session.uiState.value.phase)
        session.updateDraftText("此刻")
        assertTrue(session.uiState.value.canSubmit)
        session.submit()
        advanceUntilIdle()
        assertEquals(MurmurPhase.Complete, session.uiState.value.phase)
        assertEquals(listOf("reply-1"), session.uiState.value.bubbles.map { it.text })
    }

    @Test
    fun removingPhotoDuringPrepareLeavesIdle() = runTest(dispatcher) {
        val api = FakeMurmurApiClient()
        val session = model(api)
        advanceUntilIdle()
        session.beginPhotoSelection()
        assertEquals(MurmurPhase.PreparingPhoto, session.uiState.value.phase)
        assertTrue(session.uiState.value.phase.isBusy)
        session.removeDraftPhoto()
        assertEquals(MurmurPhase.Idle, session.uiState.value.phase)
        assertFalse(session.uiState.value.phase.isBusy)
    }

    @Test
    fun photoSelectionFailureIsNotRetryable() = runTest(dispatcher) {
        val api = FakeMurmurApiClient()
        val session = model(api)
        advanceUntilIdle()
        session.beginPhotoSelection()
        session.failPhotoSelection()
        assertEquals(MurmurPhase.Error, session.uiState.value.phase)
        assertEquals(false, session.uiState.value.failure?.retryable)
        session.retry()
        assertEquals(MurmurPhase.Error, session.uiState.value.phase)
    }

    @Test
    fun ssePreservesBubbleOrder() = runTest(dispatcher) {
        val api = FakeMurmurApiClient(mode = FakeMurmurApiClient.Mode.OrderedBubbles)
        val session = model(api)
        advanceUntilIdle()
        session.updateDraftText("两句")
        session.submit()
        advanceUntilIdle()
        assertEquals(listOf("先这一句", "再这一句"), session.uiState.value.bubbles.map { it.text })
    }

    @Test
    fun cancelDuringPhotoPrepareDiscardsFileAndIgnoresLateResult() = runTest(dispatcher) {
        val loader = FakePhotoLoader(loadDelayMs = 200)
        val source = writeTestFile(loader)
        val api = FakeMurmurApiClient()
        val session = model(api, loader)
        advanceUntilIdle()
        session.preparePhoto(PhotoInput.FromFile(source))
        assertEquals(MurmurPhase.PreparingPhoto, session.uiState.value.phase)
        session.cancelCurrentOperation()
        advanceUntilIdle()

        assertNull(session.uiState.value.draftPhoto)
        assertTrue(session.uiState.value.phase != MurmurPhase.Ready)
        assertFalse(source.exists())
        assertTrue(loader.leftoverFiles().isEmpty())
    }

    @Test
    fun preparingPhotoPhaseIsSetSynchronously() = runTest(dispatcher) {
        val api = FakeMurmurApiClient()
        val session = model(api)
        advanceUntilIdle()
        session.beginPhotoSelection()
        assertEquals(MurmurPhase.PreparingPhoto, session.uiState.value.phase)
    }

    @Test
    fun preparePhotoDoesNotBlockBeforeBackgroundDecodeFinishes() = runTest(dispatcher) {
        val loader = FakePhotoLoader(loadDelayMs = 500)
        val source = writeTestFile(loader)
        val api = FakeMurmurApiClient()
        val session = model(api, loader)
        advanceUntilIdle()
        session.preparePhoto(PhotoInput.FromFile(source))
        // Phase flips before the decode runs; the load resolves later.
        assertEquals(MurmurPhase.PreparingPhoto, session.uiState.value.phase)
        advanceUntilIdle()
        assertTrue(session.uiState.value.draftPhoto != null)
    }

    @Test
    fun thirtyCompletedMomentsLeaveNoHistoryOrTemporaryFiles() = runTest(dispatcher) {
        val loader = FakePhotoLoader()
        val api = FakeMurmurApiClient()
        val session = model(api, loader)
        advanceUntilIdle()
        for (index in 1..30) {
            val source = writeTestFile(loader)
            session.preparePhoto(PhotoInput.FromFile(source))
            advanceUntilIdle()
            assertEquals(MurmurPhase.Ready, session.uiState.value.phase)
            session.updateDraftText("第${index}刻")
            session.submit()
            advanceUntilIdle()
            assertEquals(MurmurPhase.Complete, session.uiState.value.phase)
        }
        assertEquals(1, session.uiState.value.bubbles.size)
        assertEquals("第30刻", session.uiState.value.currentNote)
        assertFalse(session.uiState.value.currentPhoto!!.file.exists())
        assertTrue(loader.leftoverFiles().isEmpty())
    }

    @Test
    fun pushSyncSkipsAllowedTokenUntilItArrives() {
        assertFalse(MurmurNotificationBridge.shouldSyncToken(null, NotificationAuthorization.Unknown))
        assertFalse(MurmurNotificationBridge.shouldSyncToken(null, NotificationAuthorization.Allowed))
        assertTrue(MurmurNotificationBridge.shouldSyncToken("abc", NotificationAuthorization.Allowed))
        assertTrue(MurmurNotificationBridge.shouldSyncToken(null, NotificationAuthorization.Denied))
        assertTrue(MurmurNotificationBridge.shouldSyncToken(null, NotificationAuthorization.NotDetermined))
    }
}

/** Port of the iOS test double, with a JVM-friendly Flow in place of AsyncThrowingStream. */
private class FakeMurmurApiClient(
    private val mode: Mode = Mode.Normal,
) : MurmurApiClient {

    enum class Mode {
        Normal, StreamFailsOnce, DisconnectThenResume, TerminalFailureThenSuccess,
        IdempotencyConflict, ProactiveReply, AttestationKeyUnknown, SlowCreates,
        NeverCreates, NeverStreams, OrderedBubbles, StreamEndsSilently,
    }

    val idempotencyKeys = mutableListOf<String>()
    val lastEventIDs = mutableListOf<String?>()
    var proactiveCalls = 0
        private set
    val removedDeviceIDs = mutableListOf<String>()
    val acknowledgements = mutableListOf<Pair<String, String?>>()
    var resetLocalIdentityCalls = 0
        private set
    val deviceTokens = mutableListOf<String?>()
    private var storedPreferences = MurmurPreferences(dailyFrequency = 2, quietStart = "21:00", quietEnd = "09:00")
    private var createCount = 0
    private var streamCount = 0

    override suspend fun storedIdentity(): MurmurIdentity? =
        MurmurIdentity("test-user", "test-device", "test-key")

    override suspend fun enroll(inviteCode: String, deviceName: String): MurmurIdentity =
        MurmurIdentity("test-user", "test-device", "test-key")

    override suspend fun createMoment(note: String?, photo: PhotoAttachment?, idempotencyKey: String): MomentReceipt {
        idempotencyKeys += idempotencyKey
        createCount += 1
        if (mode == Mode.IdempotencyConflict) {
            throw MurmurFailure("idempotency_conflict", "幂等键与不同内容冲突。", retryable = false)
        }
        if (mode == Mode.NeverCreates) delay(10_000)
        if (mode == Mode.SlowCreates) delay(90)
        return MomentReceipt("moment-$createCount", "queued")
    }

    override fun events(momentID: String, lastEventID: String?): Flow<MurmurStreamEvent> = flow {
        lastEventIDs += lastEventID
        streamCount += 1
        val call = streamCount
        val currentCreate = createCount
        when (mode) {
            Mode.Normal, Mode.ProactiveReply, Mode.SlowCreates -> {
                emit(MurmurStreamEvent.Accepted("accepted-$currentCreate"))
                emit(MurmurStreamEvent.Bubble("bubble-$currentCreate", "reply-$currentCreate"))
                emit(MurmurStreamEvent.Done("done-$currentCreate", null, null))
            }
            Mode.OrderedBubbles -> {
                emit(MurmurStreamEvent.Accepted("accepted-1"))
                emit(MurmurStreamEvent.Bubble("bubble-a", "先这一句"))
                emit(MurmurStreamEvent.Bubble("bubble-b", "再这一句"))
                emit(MurmurStreamEvent.Done("done-1", "speak", null))
            }
            Mode.StreamFailsOnce -> {
                if (call <= 3) {
                    emit(MurmurStreamEvent.Bubble("partial", "partial"))
                    throw MurmurFailure("stream_lost", "lost", retryable = true)
                } else {
                    emit(MurmurStreamEvent.Bubble("retry", "reply-after-retry"))
                    emit(MurmurStreamEvent.Done("done", null, null))
                }
            }
            Mode.DisconnectThenResume -> {
                if (call == 1) {
                    emit(MurmurStreamEvent.Bubble("bubble-1", "先说一半"))
                    throw MurmurFailure("stream_lost", "lost", retryable = true)
                } else {
                    emit(MurmurStreamEvent.Bubble("bubble-1", "先说一半"))
                    emit(MurmurStreamEvent.Done("done-1", null, null))
                }
            }
            Mode.TerminalFailureThenSuccess -> {
                if (currentCreate == 1) {
                    emit(MurmurStreamEvent.Failure("failed", MurmurFailure("processing_failed", "failed", retryable = true)))
                } else {
                    emit(MurmurStreamEvent.Bubble("retry", "processed-again"))
                    emit(MurmurStreamEvent.Done("done", null, null))
                }
            }
            Mode.NeverStreams -> awaitCancellation()
            Mode.StreamEndsSilently -> Unit // completes without a `done`
            Mode.NeverCreates, Mode.IdempotencyConflict, Mode.AttestationKeyUnknown -> Unit
        }
    }

    override suspend fun currentProactive(): ProactiveMoment? {
        proactiveCalls += 1
        if (mode != Mode.ProactiveReply) return null
        return ProactiveMoment(momentID = "proactive-1", bubbles = listOf("想到你了"))
    }

    override suspend fun acknowledge(momentID: String, reply: String?) {
        acknowledgements += momentID to reply
    }

    override suspend fun updateDevice(pushToken: String?, environment: String, timezone: String, deviceName: String) {
        deviceTokens += pushToken
    }

    override suspend fun devices(): List<MurmurDevice> {
        if (mode == Mode.AttestationKeyUnknown) {
            throw MurmurFailure("attestation_key_unknown", "Device binding is unknown.", retryable = false)
        }
        return listOf(
            MurmurDevice("test-device", "test-key", "development", "Asia/Shanghai", "Test Phone", true, null, null),
            MurmurDevice("other-device", "other-key", "development", "Asia/Shanghai", "Test Pad", false, null, null),
        )
    }

    override suspend fun removeDevice(deviceID: String) {
        removedDeviceIDs += deviceID
    }

    override suspend fun preferences(): MurmurPreferences = storedPreferences

    override suspend fun updatePreferences(preferences: MurmurPreferences) {
        storedPreferences = preferences
    }

    override suspend fun resetLocalIdentity() {
        resetLocalIdentityCalls += 1
    }

    override suspend fun deleteAccount() = Unit
}

/** Mirrors AndroidPhotoLoader.loadFromFile's managed-file semantics on the JVM. */
private class FakePhotoLoader(
    val directory: File = kotlin.io.path.createTempDirectory("murmur-test").toFile(),
    val loadDelayMs: Long = 0,
) : PhotoLoader {

    override suspend fun load(input: PhotoInput): PhotoAttachment {
        if (loadDelayMs > 0) delay(loadDelayMs)
        val source = when (input) {
            is PhotoInput.FromFile -> input.file
            is PhotoInput.FromUri -> error("Uri input is not expected in JVM tests")
        }
        val managed = if (source.parentFile == directory && source.name.startsWith("murmur-upload-")) {
            source
        } else {
            File(directory, "murmur-upload-${UUID.randomUUID()}.jpg").also { source.copyTo(it) }
        }
        return PhotoAttachment(
            file = managed,
            preview = null,
            filename = "test.jpg",
            mimeType = "image/jpeg",
            byteCount = managed.length(),
        )
    }

    override suspend fun discard(attachment: PhotoAttachment?) {
        attachment?.file?.delete()
    }

    override suspend fun discardFile(file: File) {
        if (file.name.startsWith("murmur-upload-")) file.delete()
    }

    override suspend fun cleanupStaleFiles() {
        leftoverFiles().forEach { it.delete() }
    }

    fun leftoverFiles(): List<File> =
        directory.listFiles().orEmpty().filter { it.name.startsWith("murmur-upload-") }
}
