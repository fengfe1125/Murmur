package com.sakura.murmur

import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.UUID
import kotlin.io.path.createTempDirectory
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Port of `PhotoRoomModelTests.swift` (18 cases) — the room's contract, away
 * from the screen: the photo goes up once with the reading intent, the guess
 * and its three openers come back, an opener is picked up rather than sent,
 * and the full-resolution original is gone on every path out. The three
 * ArchiveDayModel cases from the same file live here too, exactly as on iOS.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class PhotoRoomModelTest {

    private lateinit var root: File
    private lateinit var loaderDir: File

    @Before
    fun setUp() {
        root = createTempDirectory("murmur-room-test").toFile()
        loaderDir = createTempDirectory("murmur-room-loader").toFile()
    }

    @After
    fun tearDown() {
        root.deleteRecursively()
        loaderDir.deleteRecursively()
    }

    private val zone: ZoneId = ZoneId.of("Asia/Shanghai")

    private fun archive(): MurmurArchive =
        MurmurArchive(MurmurTranscriptStore(File(root, "archive")), zone)

    private fun chatStore(): MurmurTranscriptStore = MurmurTranscriptStore(File(root, "transcript"))

    private fun makeModel(
        api: RoomAPI,
        scope: CoroutineScope,
        recorder: RoomRecorder? = null,
    ): PhotoRoomModel {
        val source = File(loaderDir, "picked-${UUID.randomUUID()}.jpg").apply { writeBytes(ByteArray(64)) }
        return PhotoRoomModel(
            input = PhotoInput.FromFile(source),
            api = api,
            photoLoader = FakePhotoLoader(loaderDir),
            scope = scope,
            uploadTimeoutSeconds = 5.0,
            requestTimeoutSeconds = 5.0,
            bubblePacing = MurmurBubblePacing.INSTANT,
            recorder = recorder,
        )
    }

    private fun makeArchiveDayModel(api: RoomAPI, scope: CoroutineScope, archive: MurmurArchive, day: LocalDate) =
        ArchiveDayModel(
            day = day,
            archive = archive,
            api = api,
            scope = scope,
            requestTimeoutSeconds = 5.0,
            bubblePacing = MurmurBubblePacing.INSTANT,
        )

    private fun at(day: LocalDate, hour: Int, minute: Int = 0): Instant =
        ZonedDateTime.of(day, java.time.LocalTime.of(hour, minute), zone).toInstant()

    private fun temporaryUploads(): List<File> =
        loaderDir.listFiles().orEmpty().filter { it.name.startsWith("murmur-upload-") }

    @Test
    fun theOpeningUploadCarriesThePhotoAndTheReadingIntent() = runTest {
        val api = RoomAPI()
        val model = makeModel(api, this)

        model.open()
        advanceUntilIdle()

        assertEquals(1, api.calls.size)
        assertEquals("photo_reading", api.calls[0].intent)
        assertNull(api.calls[0].note)
        assertTrue(api.calls[0].hasPhoto)
    }

    @Test
    fun theGuessAndItsOpenersLandInTheRoom() = runTest {
        val api = RoomAPI()
        val model = makeModel(api, this)

        model.open()
        advanceUntilIdle()

        assertEquals(listOf("这是……刚下过雨？"), model.lines.value.map { it.text })
        assertEquals(listOf(PhotoRoomModel.LineAuthor.Murmur), model.lines.value.map { it.author })
        assertEquals(listOf("那天的天气", "右边那个人", "上次说要再来"), model.openers.value)
    }

    /** An opener is a door, not a message: tapping one fills the field and
     *  leaves the decision with the person. */
    @Test
    fun pickingAnOpenerFillsTheFieldWithoutSending() = runTest {
        val api = RoomAPI()
        val model = makeModel(api, this)
        model.open()
        advanceUntilIdle()

        model.pick("那天的天气")

        assertEquals("那天的天气", model.draft.value)
        assertEquals(1, api.calls.size)
        assertTrue(model.openers.value.isNotEmpty())
    }

    /** Everything after the photo is an ordinary moment — no second upload of
     *  the same pixels, and the openers close behind the first line. */
    @Test
    fun sayingSomethingSendsTextOnlyAndClosesTheOpeners() = runTest {
        val api = RoomAPI()
        val model = makeModel(api, this)
        model.open()
        advanceUntilIdle()

        model.draft.value = "那天是我搬走前最后一次去"
        model.send()
        advanceUntilIdle()

        assertEquals(2, api.calls.size)
        assertNull(api.calls[1].intent)
        assertFalse(api.calls[1].hasPhoto)
        assertEquals("那天是我搬走前最后一次去", api.calls[1].note)
        assertTrue(model.openers.value.isEmpty())
        assertEquals(
            listOf(PhotoRoomModel.LineAuthor.Murmur, PhotoRoomModel.LineAuthor.Mine, PhotoRoomModel.LineAuthor.Murmur),
            model.lines.value.map { it.author },
        )
    }

    /** SSE sequence numbers belong to one moment and restart on the next one.
     *  A room spans several moments, so a bare sequence as an identity makes
     *  later replies reuse and overwrite the first bubble. */
    @Test
    fun repliesKeepDistinctIdentitiesWhenEveryMomentReusesEventSequence() = runTest {
        val api = RoomAPI()
        val model = makeModel(api, this)
        model.open()
        advanceUntilIdle()

        model.draft.value = "第一句"
        model.send()
        advanceUntilIdle()
        model.draft.value = "第二句"
        model.send()
        advanceUntilIdle()

        assertEquals(
            listOf("这是……刚下过雨？", "第一句", "那后来呢", "第二句", "后来真的去了"),
            model.lines.value.map { it.text },
        )
        assertEquals(model.lines.value.map { it.id }.toSet().size, model.lines.value.size)
    }

    /** The original is temporary and the reading is one of its terminal
     *  paths: once the server has answered, the file has nothing left to do. */
    @Test
    fun theOriginalIsDeletedOnceTheReadingIsOver() = runTest {
        val api = RoomAPI()
        val model = makeModel(api, this)
        model.open()
        advanceUntilIdle()

        assertTrue(temporaryUploads().isEmpty())
    }

    /** Leaving is a terminal path too, including while the upload is still on
     *  the wire. A room closed mid-flight must not leave a full-resolution
     *  photo behind for the next cold start to find. */
    @Test
    fun closingMidFlightTakesTheOriginalWithIt() = runTest {
        val api = RoomAPI(mode = RoomAPI.Mode.NeverAnswers)
        val model = makeModel(api, this)
        model.open()
        runCurrent()

        assertTrue(temporaryUploads().isNotEmpty())
        model.close()
        advanceUntilIdle()

        assertTrue(temporaryUploads().isEmpty())
    }

    /** A reading that fails on the wire keeps its file, because 再试一次 is an
     *  offer to send that same photo again under the same idempotency key. */
    @Test
    fun aRetryableFailureKeepsTheFileAndTheKey() = runTest {
        val api = RoomAPI(mode = RoomAPI.Mode.FailsFirstUpload)
        val model = makeModel(api, this)
        model.open()
        advanceUntilIdle()

        assertEquals(PhotoRoomModel.Phase.Unopened, model.phase.value)
        assertTrue(model.canRetryOpening)
        assertEquals(true, model.failure.value?.retryable)
        assertTrue(temporaryUploads().isNotEmpty())

        model.open()
        advanceUntilIdle()

        assertEquals(PhotoRoomModel.Phase.Listening, model.phase.value)
        assertEquals(2, api.calls.size)
        assertEquals(api.calls[0].idempotencyKey, api.calls[1].idempotencyKey)
        model.close()
    }

    /** Nothing was said, so nothing stays on screen claiming it was — and the
     *  words come back to the field rather than being retyped. */
    @Test
    fun aLineThatDidNotLandComesBackToTheField() = runTest {
        val api = RoomAPI(mode = RoomAPI.Mode.FailsSecondSend)
        val model = makeModel(api, this)
        model.open()
        advanceUntilIdle()

        model.draft.value = "那天是我搬走前最后一次去"
        model.send()
        advanceUntilIdle()

        assertEquals("那天是我搬走前最后一次去", model.draft.value)
        assertEquals(listOf(PhotoRoomModel.LineAuthor.Murmur), model.lines.value.map { it.author })
        // The room is still open, and the send button is the retry.
        assertEquals(PhotoRoomModel.Phase.Listening, model.phase.value)
        assertFalse(model.canRetryOpening)
        assertTrue(model.canSend)
    }

    /** A room whose photo the server never saw does not take dictation: an
     *  answer about nothing is worse than a closed field. */
    @Test
    fun anUnopenedRoomDoesNotTakeALine() = runTest {
        val api = RoomAPI(mode = RoomAPI.Mode.FailsFirstUpload)
        val model = makeModel(api, this)
        model.open()
        advanceUntilIdle()

        model.draft.value = "那天是我搬走前最后一次去"
        assertFalse(model.canSend)
        model.send()

        assertEquals(1, api.calls.size)
        assertTrue(model.lines.value.isEmpty())
        model.close()
    }

    /** Kept, but kept apart: the photo, the reading of it and every line
     *  after land in the archive and survive the room closing — and the
     *  conversation never sees a word of it. */
    @Test
    fun theRoomsExchangeGoesToTheArchiveAndNotTheConversation() = runTest {
        val archive = archive()
        archive.load()
        val api = RoomAPI()
        val model = makeModel(api, this, recorder = archive)

        model.open()
        advanceUntilIdle()
        assertEquals(2, archive.rows.value.size)

        assertEquals(
            listOf(MurmurMessageAuthor.you, MurmurMessageAuthor.murmur),
            archive.rows.value.map { it.author },
        )
        assertEquals("这是……刚下过雨？", archive.rows.value[1].text)
        // The picture itself, not just the words about it.
        assertEquals("", archive.rows.value[0].text)
        assertNotNull(archive.rows.value[0].imageFile)

        model.draft.value = "那天是我搬走前最后一次去"
        model.send()
        advanceUntilIdle()
        model.close()

        assertEquals(
            listOf("", "这是……刚下过雨？", "那天是我搬走前最后一次去", "那后来呢"),
            archive.rows.value.map { it.text },
        )
        assertEquals(MurmurDeliveryState.answered, archive.rows.value[2].delivery)
        // The picture got its second tick when the reading landed.
        assertEquals(MurmurDeliveryState.answered, archive.rows.value[0].delivery)
        // The whole point of the archive: none of this is in the chat.
        assertTrue(chatStore().load().isEmpty())
        // And it is filed under the day it happened on.
        val today = LocalDate.now(zone)
        assertEquals(1, archive.daysWithRooms.size)
        assertEquals(1, archive.photoCount(today))
        assertEquals(4, archive.rows(today).size)

        val reloaded = MurmurTranscriptStore(File(root, "archive")).load()
        assertEquals(reloaded.map { it.text }, archive.rows.value.map { it.text })
        val name = reloaded[0].imageFile
        assertNotNull(name)
        assertTrue(archive.imageFile(name!!).isFile)
    }

    /** Nothing was said, so the archive must not claim it was. The room puts
     *  the words back in the field; the archive puts the row back too. */
    @Test
    fun aLineThatDidNotLandLeavesNoTraceInTheArchive() = runTest {
        val archive = archive()
        archive.load()
        val api = RoomAPI(mode = RoomAPI.Mode.FailsSecondSend)
        val model = makeModel(api, this, recorder = archive)

        model.open()
        advanceUntilIdle()
        model.draft.value = "那天是我搬走前最后一次去"
        model.send()
        advanceUntilIdle()

        assertEquals(
            listOf(MurmurMessageAuthor.you, MurmurMessageAuthor.murmur),
            archive.rows.value.map { it.author },
        )
        assertFalse(archive.rows.value.any { it.text.contains("搬走前") })
        model.close()
    }

    @Test
    fun aReceivedLineSurvivesAStreamFailureWithoutReturningToTheDraft() = runTest {
        val archive = archive()
        archive.load()
        val api = RoomAPI(mode = RoomAPI.Mode.FailsSecondStream)
        val model = makeModel(api, this, recorder = archive)

        model.open()
        advanceUntilIdle()
        model.draft.value = "服务端已经收到了"
        model.send()
        advanceUntilIdle()

        assertEquals(
            listOf(PhotoRoomModel.LineAuthor.Murmur, PhotoRoomModel.LineAuthor.Mine),
            model.lines.value.map { it.author },
        )
        assertEquals("", model.draft.value)
        assertEquals("服务端已经收到了", archive.rows.value.last().text)
        assertEquals("moment-2", archive.rows.value.last().momentID)
        assertEquals(MurmurDeliveryState.sent, archive.rows.value.last().delivery)
        model.close()
    }

    /** 再试一次 re-sends the same moment under the same key. One photo was
     *  sent, so the archive shows one photo. */
    @Test
    fun retryingTheOpeningDoesNotWriteThePhotoTwice() = runTest {
        val archive = archive()
        archive.load()
        val api = RoomAPI(mode = RoomAPI.Mode.FailsFirstUpload)
        val model = makeModel(api, this, recorder = archive)

        model.open()
        advanceUntilIdle()
        // The server never saw it, so there is nothing to write down yet.
        assertTrue(archive.rows.value.isEmpty())

        model.open()
        advanceUntilIdle()

        assertEquals(2, archive.rows.value.size)
        assertEquals(1, archive.rows.value.count { it.author == MurmurMessageAuthor.you })
        model.close()
    }

    /** Leaving while a line is still on the wire: this device never saw a
     *  receipt, so the row says failed rather than spinning for ever. */
    @Test
    fun leavingMidSendDoesNotLeaveTheArchiveSpinning() = runTest {
        val archive = archive()
        archive.load()
        val api = RoomAPI(mode = RoomAPI.Mode.HangsOnSecondSend)
        val model = makeModel(api, this, recorder = archive)

        model.open()
        advanceUntilIdle()
        model.draft.value = "那天是我搬走前最后一次去"
        model.send()
        // Drive the turn up to the hanging createMoment, no further.
        runCurrent()

        assertEquals(3, archive.rows.value.size)
        assertEquals(MurmurDeliveryState.sending, archive.rows.value[2].delivery)

        model.close()
        assertEquals(MurmurDeliveryState.failed, archive.rows.value[2].delivery)
    }

    @Test
    fun historicalDayCanContinueWithItsLatestRoomContext() = runTest {
        val archive = archive()
        archive.load()
        val api = RoomAPI()
        val selectedDay = LocalDate.of(2024, 3, 12)
        val source = File(root, "archive-day-model-${UUID.randomUUID()}.jpg").apply { writeBytes(ByteArray(64)) }
        archive.record(
            MurmurMessage(
                author = MurmurMessageAuthor.you,
                text = "",
                sentAt = at(selectedDay, 9),
                momentID = "archive-opening",
            ),
            photoFile = source,
        )
        archive.record(
            MurmurMessage(
                author = MurmurMessageAuthor.murmur,
                text = "那天风很大",
                sentAt = at(selectedDay, 9, 1),
                momentID = "archive-opening",
            ),
            photoFile = null,
        )
        val model = makeArchiveDayModel(api, this, archive, selectedDay)
        val beforeSend = Instant.now()

        model.draft.value = "后来我又去了"
        model.send()
        advanceUntilIdle()

        assertEquals(listOf("archive-opening"), api.calls[0].contextMomentIDs)
        val continued = archive.rows.value.takeLast(2)
        assertEquals(listOf("后来我又去了", "后来真的去了"), continued.map { it.text })
        assertTrue(continued.all { it.archiveDay == model.dayStart })
        assertTrue(continued.all { !it.sentAt.isBefore(beforeSend) })
        assertEquals("moment-1", continued[0].momentID)
        assertEquals(MurmurDeliveryState.answered, continued[0].delivery)

        model.draft.value = "再后来呢"
        model.send()
        advanceUntilIdle()

        assertEquals(listOf("archive-opening", "moment-1"), api.calls[1].contextMomentIDs)
        assertEquals(6, archive.rows.value.size)
        assertEquals(archive.rows.value.map { it.id }.toSet().size, archive.rows.value.size)
    }

    /** The same contract the photo room keeps: leaving while a line is still
     *  on the wire marks it failed. Deleting it would take back words that
     *  were typed on purpose, into no composer that still exists to hold them. */
    @Test
    fun leavingAnArchiveDayMidSendMarksTheRowRatherThanDeletingIt() = runTest {
        val archive = archive()
        archive.load()
        val api = RoomAPI(mode = RoomAPI.Mode.HangsOnSecondSend)
        val selectedDay = LocalDate.of(2024, 3, 12)
        val model = makeArchiveDayModel(api, this, archive, selectedDay)

        model.draft.value = "第一句"
        model.send()
        advanceUntilIdle()

        model.draft.value = "还在路上的那句"
        model.send()
        runCurrent()

        assertEquals(3, archive.rows.value.size)
        assertEquals(MurmurDeliveryState.sending, archive.rows.value[2].delivery)

        model.close()

        assertEquals(3, archive.rows.value.size)
        assertEquals("还在路上的那句", archive.rows.value.last().text)
        assertEquals(MurmurDeliveryState.failed, archive.rows.value.last().delivery)
    }

    @Test
    fun historicalDayFailureWithdrawsTheUnsentRowAndRestoresTheDraft() = runTest {
        val archive = archive()
        archive.load()
        val api = RoomAPI(mode = RoomAPI.Mode.FailsFirstUpload)
        val selectedDay = LocalDate.of(2024, 3, 12)
        val model = makeArchiveDayModel(api, this, archive, selectedDay)

        model.draft.value = "这句先别丢"
        model.send()
        advanceUntilIdle()

        assertEquals(ArchiveDayModel.Phase.Listening, model.phase.value)
        assertEquals("这句先别丢", model.draft.value)
        assertTrue(archive.rows.value.isEmpty())
    }
}

/**
 * Port of the `RoomAPI` actor in PhotoRoomModelTests.swift: production SSE
 * IDs are per-moment integer sequences, and they deliberately repeat here so
 * room tests exercise the real wire (later moments reuse `1`...`4`).
 */
private class RoomAPI(
    private val mode: Mode = Mode.Normal,
) : MurmurApiClient {

    enum class Mode { Normal, FailsFirstUpload, FailsSecondSend, FailsSecondStream, NeverAnswers, HangsOnSecondSend }

    data class Call(
        val note: String?,
        val hasPhoto: Boolean,
        val idempotencyKey: String,
        val intent: String?,
        val contextMomentIDs: List<String>?,
    )

    val calls = mutableListOf<Call>()
    private var moments = 0
    private val readingMoments = mutableSetOf<String>()

    override suspend fun storedIdentity(): MurmurIdentity = MurmurIdentity("u", "d", "k")

    override suspend fun enroll(inviteCode: String, deviceName: String): MurmurIdentity = MurmurIdentity("u", "d", "k")

    override suspend fun createMoment(
        note: String?,
        photo: PhotoAttachment?,
        idempotencyKey: String,
        intent: String?,
        contextMomentIDs: List<String>?,
    ): MomentReceipt {
        calls.add(Call(note, photo != null, idempotencyKey, intent, contextMomentIDs))
        if (mode == Mode.FailsFirstUpload && calls.size == 1) {
            throw MurmurFailure("network_error", "没连上。", retryable = true)
        }
        if (mode == Mode.FailsSecondSend && calls.size == 2) {
            throw MurmurFailure("network_error", "没连上。", retryable = true)
        }
        if (mode == Mode.NeverAnswers) delay(30_000)
        if (mode == Mode.HangsOnSecondSend && calls.size == 2) delay(30_000)
        moments += 1
        val momentID = "moment-$moments"
        if (intent == "photo_reading") readingMoments.add(momentID)
        return MomentReceipt(momentID, "queued")
    }

    override fun events(momentID: String, lastEventID: String?): Flow<MurmurStreamEvent> = flow {
        val isOpening = momentID in readingMoments
        emit(MurmurStreamEvent.Accepted("1"))
        if (isOpening) {
            emit(MurmurStreamEvent.Bubble("2", "这是……刚下过雨？"))
            emit(MurmurStreamEvent.Angles("3", listOf("那天的天气", "右边那个人", "上次说要再来")))
        } else {
            if (mode == Mode.FailsSecondStream) {
                emit(
                    MurmurStreamEvent.Failure(
                        "2",
                        MurmurFailure("stream_ended", "回应中断了。", retryable = true),
                    ),
                )
                return@flow
            }
            emit(MurmurStreamEvent.Bubble("2", if (momentID == "moment-2") "那后来呢" else "后来真的去了"))
        }
        emit(MurmurStreamEvent.Done("4", null, null))
    }

    override suspend fun currentProactive(): ProactiveMoment? = null

    override suspend fun acknowledge(momentID: String, reply: String?) = Unit

    override suspend fun updateDevice(pushToken: String?, environment: String, timezone: String, deviceName: String) = Unit

    override suspend fun devices(): List<MurmurDevice> = emptyList()

    override suspend fun removeDevice(deviceID: String) = Unit

    override suspend fun preferences(): MurmurPreferences = MurmurPreferences()

    override suspend fun updatePreferences(preferences: MurmurPreferences) = Unit

    override suspend fun resetLocalIdentity() = Unit

    override suspend fun deleteAccount() = Unit
}

/** Mirrors AndroidPhotoLoader's managed-file semantics on the JVM. */
private class FakePhotoLoader(
    private val directory: File,
) : PhotoLoader {

    override suspend fun load(input: PhotoInput): PhotoAttachment {
        val source = when (input) {
            is PhotoInput.FromFile -> input.file
            is PhotoInput.FromUri -> error("Uri input is not expected in JVM tests")
            is PhotoInput.FromBitmap -> error("Bitmap input is not expected in JVM tests")
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

    override suspend fun cleanupStaleFiles() = Unit
}
