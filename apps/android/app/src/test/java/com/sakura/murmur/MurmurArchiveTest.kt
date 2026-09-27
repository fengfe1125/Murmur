package com.sakura.murmur

import com.sakura.murmur.ui.MurmurMonthGrid
import java.io.File
import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.YearMonth
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.UUID
import kotlin.io.path.createTempDirectory
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Port of the `MurmurArchiveTests` group in `OnThisDayTests.swift` — 当年今日's
 * archive, away from the screen: the day a room happened on is what the
 * calendar is built from, so the grouping has to be right in the user's own
 * calendar rather than UTC.
 */
class MurmurArchiveTest {

    private lateinit var root: File

    @Before
    fun setUp() {
        root = createTempDirectory("murmur-archive-test").toFile()
    }

    @After
    fun tearDown() {
        root.deleteRecursively()
    }

    /** Pinned, not the device zone: the assertions cross local midnight, so
     *  the archive's calendar must be the same one the fixtures were built in. */
    private val zone: ZoneId = ZoneId.of("Asia/Shanghai")

    private fun makeArchive() = MurmurArchive(MurmurTranscriptStore(File(root, "archive")), zone)

    private fun day(text: String): Instant {
        val (date, time) = text.split(" ")
        return ZonedDateTime.of(LocalDate.parse(date), LocalTime.parse(time), zone).toInstant()
    }

    private fun writePhoto(name: String = UUID.randomUUID().toString()): File =
        File(root, "$name.jpg").apply { writeBytes(ByteArray(64)) }

    @Test
    fun rowsAreFiledUnderTheDayTheyHappenedOn() = runTest {
        val archive = makeArchive()

        // Two rooms on one day and one late the next, including a row at a
        // minute either side of midnight — the case a UTC-based grouping puts
        // on the wrong square.
        for ((text, whenSent) in listOf(
            "第一张" to "2026-08-20 09:15",
            "说了一句" to "2026-08-20 09:16",
            "第二张" to "2026-08-20 23:59",
            "隔天那张" to "2026-08-21 00:01",
        )) {
            archive.record(
                MurmurMessage(author = MurmurMessageAuthor.you, text = text, sentAt = day(whenSent)),
                photoFile = null,
            )
        }

        assertEquals(2, archive.daysWithRooms.size)
        assertEquals(
            listOf("第一张", "说了一句", "第二张"),
            archive.rows(LocalDate.of(2026, 8, 20)).map { it.text },
        )
        assertEquals(
            listOf("隔天那张"),
            archive.rows(LocalDate.of(2026, 8, 21)).map { it.text },
        )
        assertEquals(LocalDate.of(2026, 8, 21), archive.recentDays().first())
    }

    /** The count under a day is photos, not rows: the talk about a picture is
     *  not another picture. */
    @Test
    fun theDayCountIsPhotosRatherThanRows() = runTest {
        val archive = makeArchive()
        val source = writePhoto()

        archive.record(
            MurmurMessage(author = MurmurMessageAuthor.you, text = "", sentAt = day("2026-08-20 09:00")),
            photoFile = source,
        )
        archive.record(
            MurmurMessage(author = MurmurMessageAuthor.murmur, text = "这是哪儿", sentAt = day("2026-08-20 09:01")),
            photoFile = null,
        )
        archive.record(
            MurmurMessage(author = MurmurMessageAuthor.you, text = "老地方", sentAt = day("2026-08-20 09:02")),
            photoFile = null,
        )

        assertEquals(3, archive.rows(LocalDate.of(2026, 8, 20)).size)
        assertEquals(1, archive.photoCount(LocalDate.of(2026, 8, 20)))
    }

    @Test
    fun aContinuedTurnKeepsItsRealSendTimeButStaysOnTheSelectedDay() = runTest {
        val archive = makeArchive()
        val selectedDay = LocalDate.of(2024, 3, 12)
        val actualSendTime = day("2026-08-26 21:17")

        archive.record(
            MurmurMessage(
                author = MurmurMessageAuthor.you,
                text = "后来我又去了",
                sentAt = actualSendTime,
                archiveDay = ZonedDateTime.of(selectedDay, LocalTime.of(9, 0), zone).toInstant(),
                momentID = "continued-moment",
            ),
            photoFile = null,
        )

        assertEquals(listOf("后来我又去了"), archive.rows(selectedDay).map { it.text })
        assertTrue(archive.rows(LocalDate.of(2026, 8, 26)).isEmpty())
        assertEquals(actualSendTime, archive.rows.value.first().sentAt)

        val reloaded = makeArchive()
        reloaded.load()
        assertEquals(listOf("后来我又去了"), reloaded.rows(selectedDay).map { it.text })
        assertEquals(actualSendTime, reloaded.rows.value.first().sentAt)
    }

    @Test
    fun messagesWrittenBeforeArchiveDayStillDecodeAndGroupBySentAt() = runTest {
        // Written by a build before archiveDay existed; identical to the
        // fixture in OnThisDayTests.testMessagesWrittenBeforeArchiveDayStillDecodeAndGroupBySentAt.
        File(root, "archive").mkdirs()
        File(root, "archive/transcript.json").writeText(
            """[{"id":"legacy","author":"murmur","text":"旧回答","sentAt":"2026-08-20T09:00:00Z","delivery":"sent"}]""",
        )

        val archive = makeArchive()
        archive.load()

        assertEquals(1, archive.rows.value.size)
        assertNull(archive.rows.value[0].archiveDay)
        assertEquals("旧回答", archive.rows.value[0].text)
        // Grouping for such rows falls back to the real send time.
        assertEquals(listOf("旧回答"), archive.rows(LocalDate.of(2026, 8, 20)).map { it.text })
    }

    @Test
    fun contextComesOnlyFromTheLatestPhotoRoomAndKeepsTheNewestEightMoments() = runTest {
        val archive = makeArchive()
        val source = writePhoto()
        val selectedDay = LocalDate.of(2026, 8, 20)

        archive.record(
            MurmurMessage(author = MurmurMessageAuthor.you, text = "", sentAt = day("2026-08-20 08:00"), momentID = "older-room"),
            photoFile = source,
        )
        archive.record(
            MurmurMessage(author = MurmurMessageAuthor.murmur, text = "旧房间", sentAt = day("2026-08-20 08:01"), momentID = "older-room"),
            photoFile = null,
        )
        archive.record(
            MurmurMessage(author = MurmurMessageAuthor.you, text = "", sentAt = day("2026-08-20 20:00"), momentID = "moment-0"),
            photoFile = source,
        )
        for (index in 1..10) {
            archive.record(
                MurmurMessage(
                    author = if (index % 2 == 0) MurmurMessageAuthor.murmur else MurmurMessageAuthor.you,
                    text = "第 $index 轮",
                    sentAt = day("2026-08-20 20:%02d".format(index)),
                    momentID = "moment-$index",
                ),
                photoFile = null,
            )
        }
        // Duplicates do not consume the bounded context budget.
        archive.record(
            MurmurMessage(author = MurmurMessageAuthor.murmur, text = "补一句", sentAt = day("2026-08-20 20:20"), momentID = "moment-10"),
            photoFile = null,
        )

        assertEquals(
            (3..10).map { "moment-$it" },
            archive.contextMomentIDs(selectedDay),
        )
    }

    /** A row with neither words nor a picture would be an empty bubble on the
     *  day screen; the copy failing is not a reason to put one there. */
    @Test
    fun anEmptyRowIsNotFiled() = runTest {
        val archive = makeArchive()

        archive.record(MurmurMessage(author = MurmurMessageAuthor.you, text = ""), photoFile = null)

        assertTrue(archive.rows.value.isEmpty())
    }

    /** Murmur is not localised, so the grid is pinned rather than following
     *  the device: Monday first, whatever phone this is. */
    @Test
    fun theCalendarStartsOnMonday() {
        assertEquals(listOf("一", "二", "三", "四", "五", "六", "日"), MurmurMonthGrid.WEEKDAY_LABELS)

        for (month in listOf(YearMonth.of(2024, 3), YearMonth.of(2026, 8), YearMonth.of(2026, 1))) {
            val weeks = MurmurMonthGrid.weeks(month)
            // After the leading padding every week begins on a Monday, and the
            // month fills the grid in order with nothing dropped or doubled.
            assertTrue(
                weeks.drop(1).all { week -> week.first()?.dayOfWeek == DayOfWeek.MONDAY },
            )
            assertEquals(
                (1..month.lengthOfMonth()).map { month.atDay(it) },
                weeks.flatten().filterNotNull(),
            )
        }

        // Concrete: 2026-08-01 is a Saturday, so five blank cells lead the grid.
        val august = MurmurMonthGrid.weeks(YearMonth.of(2026, 8))
        assertEquals(
            listOf(null, null, null, null, null, LocalDate.of(2026, 8, 1), LocalDate.of(2026, 8, 2)),
            august.first(),
        )
    }
}
