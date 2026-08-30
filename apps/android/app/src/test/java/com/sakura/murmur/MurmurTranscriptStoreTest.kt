package com.sakura.murmur

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.time.Instant
import kotlin.io.path.createTempDirectory

/**
 * MurmurTranscriptStore contract tests, aligned with the iOS suite
 * (MurmurSessionModelTests / OnThisDayTests MurmurArchiveTests) — same caps,
 * same relaunch semantics, same pruning, same legacy-data tolerance.
 */
class MurmurTranscriptStoreTest {
    private lateinit var root: File

    @Before
    fun setUp() {
        root = createTempDirectory("murmur-transcript-test").toFile()
    }

    @After
    fun tearDown() {
        root.deleteRecursively()
    }

    /** The chat store's home, the way MurmurApp will wire it: `<filesDir>/transcript`. */
    private fun chatStore() = MurmurTranscriptStore(File(root, "transcript"))

    @Test
    fun transcriptPersistsAcrossReload() {
        val photoRow = MurmurMessage(
            id = "m-photo",
            author = MurmurMessageAuthor.you,
            text = "看这个",
            imageFile = "m-photo.jpg",
            sentAt = Instant.parse("2026-08-20T09:00:00Z"),
            archiveDay = Instant.parse("2026-08-20T00:00:00Z"),
            delivery = MurmurDeliveryState.answered,
            momentID = "moment-1",
            idempotencyKey = "key-1",
        )
        chatStore().save(
            listOf(
                MurmurMessage(author = MurmurMessageAuthor.you, text = "在吗", delivery = MurmurDeliveryState.answered),
                MurmurMessage(author = MurmurMessageAuthor.murmur, text = "在呢"),
                MurmurMessage(author = MurmurMessageAuthor.you, text = "这条没发出去", delivery = MurmurDeliveryState.sending),
                photoRow,
            ),
        )

        // A fresh instance, the way a relaunch constructs one.
        val reloaded = chatStore().load()
        assertEquals(listOf("在吗", "在呢", "这条没发出去", "看这个"), reloaded.map { it.text })
        assertEquals(
            listOf(MurmurMessageAuthor.you, MurmurMessageAuthor.murmur, MurmurMessageAuthor.you, MurmurMessageAuthor.you),
            reloaded.map { it.author },
        )
        // A send interrupted by a crash never reached the server, so it must
        // not come back still spinning.
        assertEquals(MurmurDeliveryState.failed, reloaded[2].delivery)
        assertEquals(MurmurDeliveryState.answered, reloaded[0].delivery)
        assertEquals(MurmurDeliveryState.sent, reloaded[1].delivery)
        // Every field of a fully populated row survives the round trip.
        assertEquals(photoRow, reloaded[3])
    }

    @Test
    fun coldBootstrapLoadsEmpty() {
        assertTrue(chatStore().load().isEmpty())
    }

    @Test
    fun clearEmptiesHistoryAndImages() {
        val store = chatStore()
        val source = File(root, "picked.jpg").apply { writeBytes(byteArrayOf(1, 2, 3)) }
        val name = store.adoptImage(source, "m1")!!
        store.save(
            listOf(MurmurMessage(id = "m1", author = MurmurMessageAuthor.you, text = "看这个", imageFile = name)),
        )
        assertTrue(store.imageFile(name).isFile)

        store.clear()

        assertTrue(chatStore().load().isEmpty())
        assertFalse(File(root, "transcript/images").exists())
    }

    @Test
    fun keepsOnlyTheMostRecentHistory() {
        val overflow = MurmurTranscriptStore.HISTORY_LIMIT + 40
        chatStore().save(
            (0 until overflow).map { MurmurMessage(author = MurmurMessageAuthor.you, text = "m$it") },
        )

        val reloaded = chatStore().load()
        assertEquals(MurmurTranscriptStore.HISTORY_LIMIT, reloaded.size)
        assertEquals("m40", reloaded.first().text)
        assertEquals("m${overflow - 1}", reloaded.last().text)
    }

    @Test
    fun archiveKeepsSixThousandRowsInADirectoryBesideTheChat() {
        val overflow = MurmurTranscriptStore.ARCHIVE_LIMIT + 40
        MurmurTranscriptStore.archive(root).save(
            (0 until overflow).map { MurmurMessage(author = MurmurMessageAuthor.you, text = "a$it") },
        )

        val reloaded = MurmurTranscriptStore.archive(root).load()
        assertEquals(MurmurTranscriptStore.ARCHIVE_LIMIT, reloaded.size)
        assertEquals("a40", reloaded.first().text)
        assertEquals("a${overflow - 1}", reloaded.last().text)
        // Its own transcript.json, beside — never inside — the chat's.
        assertTrue(File(root, "archive/transcript.json").isFile)
        assertFalse(File(root, "transcript/transcript.json").exists())
        assertTrue(chatStore().load().isEmpty())
    }

    @Test
    fun adoptedPhotoSurvivesASaveThatDoesNotNameItYet() {
        val store = chatStore()
        val source = File(root, "picked.jpg").apply { writeBytes(byteArrayOf(1, 2, 3)) }

        val name = store.adoptImage(source, "m1")
        assertEquals("m1.jpg", name)

        // The reply lands first: the transcript is saved while the message
        // still knows nothing about the photo copied in beside it.
        store.save(listOf(MurmurMessage(id = "m1", author = MurmurMessageAuthor.you, text = "看这个")))
        assertTrue(store.imageFile(name!!).isFile)

        store.save(listOf(MurmurMessage(id = "m1", author = MurmurMessageAuthor.you, text = "看这个", imageFile = name)))
        assertTrue(store.imageFile(name).isFile)

        // Once nothing refers to it, it goes.
        store.save(listOf(MurmurMessage(id = "m2", author = MurmurMessageAuthor.you, text = "别的")))
        assertFalse(store.imageFile(name).exists())
    }

    @Test
    fun adoptImageFallsBackToJpgWhenTheSourceHasNoExtension() {
        val store = chatStore()
        val source = File(root, "picked").apply { writeBytes(byteArrayOf(1, 2, 3)) }

        assertEquals("m1.jpg", store.adoptImage(source, "m1"))
        assertTrue(store.imageFile("m1.jpg").isFile)
    }

    @Test
    fun legacyRowsWithoutArchiveDayStillDecode() {
        // Written by an iOS build before archiveDay (and imageFile, momentID,
        // idempotencyKey) existed; identical to the fixture in
        // OnThisDayTests.testMessagesWrittenBeforeArchiveDayStillDecodeAndGroupBySentAt.
        File(root, "transcript").mkdirs()
        File(root, "transcript/transcript.json").writeText(
            """[{"id":"legacy","author":"murmur","text":"旧回答","sentAt":"2026-08-20T09:00:00Z","delivery":"sent"}]""",
        )

        val rows = chatStore().load()

        assertEquals(1, rows.size)
        assertEquals("legacy", rows[0].id)
        assertEquals("旧回答", rows[0].text)
        assertNull(rows[0].archiveDay)
        assertNull(rows[0].imageFile)
        assertNull(rows[0].momentID)
        assertNull(rows[0].idempotencyKey)
        // Grouping for such rows falls back to the real send time.
        assertEquals(Instant.parse("2026-08-20T09:00:00Z"), rows[0].sentAt)
    }

    @Test
    fun savedJsonMatchesTheIosWireFormat() {
        chatStore().save(
            listOf(
                MurmurMessage(
                    id = "m1",
                    author = MurmurMessageAuthor.you,
                    text = "你好",
                    imageFile = "m1.jpg",
                    sentAt = Instant.parse("2026-08-20T09:00:00Z"),
                    archiveDay = Instant.parse("2026-08-20T00:00:00Z"),
                    delivery = MurmurDeliveryState.answered,
                    momentID = "moment-9",
                    idempotencyKey = "key-1",
                ),
            ),
        )
        val raw = File(root, "transcript/transcript.json").readText()
        assertEquals(
            """[{"id":"m1","author":"you","text":"你好","imageFile":"m1.jpg","sentAt":"2026-08-20T09:00:00Z","archiveDay":"2026-08-20T00:00:00Z","delivery":"answered","momentID":"moment-9","idempotencyKey":"key-1"}]""",
            raw,
        )

        // Nil optionals are omitted, not written as null — same as iOS.
        chatStore().save(listOf(MurmurMessage(author = MurmurMessageAuthor.murmur, text = "在呢")))
        val sparse = File(root, "transcript/transcript.json").readText()
        assertFalse(sparse.contains("null"))
        // id is a fresh UUID; assert the shape around it instead of its value.
        assertTrue(
            sparse.matches(
                Regex("""\[\{"id":"[0-9a-f-]{36}","author":"murmur","text":"在呢","sentAt":"[^"]+","delivery":"sent"\}\]"""),
            ),
        )
    }

    @Test
    fun concurrentSavesNeverTearTheFile() {
        val store = chatStore()
        val threads = (0 until 8).map { worker ->
            Thread {
                repeat(50) {
                    store.save(listOf(MurmurMessage(author = MurmurMessageAuthor.you, text = "w$worker-$it")))
                }
            }
        }
        threads.forEach { it.start() }
        threads.forEach { it.join() }

        // Every save wrote one row, well under the cap: the last write wins,
        // and the file is never left half-written.
        val reloaded = chatStore().load()
        assertEquals(1, reloaded.size)
    }
}
