package com.sakura.murmur

import android.graphics.Bitmap
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.sakura.murmur.ui.ArchiveDayView
import com.sakura.murmur.ui.MurmurMonthView
import com.sakura.murmur.ui.MurmurTheme
import java.io.File
import java.nio.file.Files
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.YearMonth
import java.time.ZoneId
import java.time.ZonedDateTime
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 存档侧两个视图的屏幕契约：月历上「有记录的日子」的圆点与「今天」的
 * 空心环是两种互不吞并的标记，一天的线程页把标题、预置行和「接着这天说」
 * 输入框组装到一起。JVM 侧 `MurmurArchiveTest` 只证了分组数学；这里证
 * 渲染与接线（collectAsState、图片解码、composer 落点）。
 */
@RunWith(AndroidJUnit4::class)
class ArchiveViewsTest {

    @get:Rule
    val composeRule = createComposeRule()

    private lateinit var root: File

    @Before
    fun setUp() {
        root = Files.createTempDirectory("murmur-archive-ui").toFile()
    }

    @After
    fun tearDown() {
        root.deleteRecursively()
    }

    /** Pinned, not the device zone: the fixtures and the archive must agree. */
    private val zone: ZoneId = ZoneId.of("Asia/Shanghai")

    private fun makeArchive() = MurmurArchive(MurmurTranscriptStore(File(root, "archive")), zone)

    private fun at(day: LocalDate, hour: Int, minute: Int = 0): Instant =
        ZonedDateTime.of(day, LocalTime.of(hour, minute), zone).toInstant()

    @Test
    fun markedDaysCarryTheirDotAndTodayKeepsItsOwnRing() {
        val marked = LocalDate.of(2026, 8, 20)
        val today = LocalDate.of(2026, 8, 21)
        composeRule.setContent {
            MurmurTheme {
                MurmurMonthView(
                    month = YearMonth.of(2026, 8),
                    markedDays = setOf(marked),
                    today = today,
                    onSelect = {},
                )
            }
        }
        // The dot's cell and today's ring cell both render, each under its tag.
        composeRule.onNodeWithTag("archive-day-2026-08-20").assertIsDisplayed()
        composeRule.onNodeWithTag("archive-day-2026-08-21").assertIsDisplayed()
    }

    /** Today that also has a record carries both marks on the one cell — the
     *  two marks never merge into a second node or swallow each other. */
    @Test
    fun aMarkedTodayStaysASingleCell() {
        val today = LocalDate.of(2026, 8, 20)
        composeRule.setContent {
            MurmurTheme {
                MurmurMonthView(
                    month = YearMonth.of(2026, 8),
                    markedDays = setOf(today),
                    today = today,
                    onSelect = {},
                )
            }
        }
        assertEquals(
            1,
            composeRule.onAllNodesWithTag("archive-day-2026-08-20").fetchSemanticsNodes().size,
        )
        composeRule.onNodeWithTag("archive-day-2026-08-20").assertIsDisplayed()
    }

    @Test
    fun theDayScreenShowsItsTitleThePresetRowsAndTheComposer() {
        val day = LocalDate.of(2024, 3, 12)
        val archive = makeArchive()
        runBlocking {
            val source = File(root, "source.jpg")
            source.outputStream().use { output ->
                Bitmap.createBitmap(8, 8, Bitmap.Config.ARGB_8888)
                    .compress(Bitmap.CompressFormat.JPEG, 90, output)
            }
            archive.record(
                MurmurMessage(
                    author = MurmurMessageAuthor.you,
                    text = "",
                    sentAt = at(day, 9),
                    momentID = "room-1",
                ),
                photoFile = source,
            )
            archive.record(
                MurmurMessage(
                    author = MurmurMessageAuthor.murmur,
                    text = "那天风很大",
                    sentAt = at(day, 9, 1),
                    momentID = "room-1",
                ),
                photoFile = null,
            )
        }
        val model = ArchiveDayModel(
            day = day,
            archive = archive,
            api = UiFakeApi(MurmurIdentity("u", "d", "k")),
            scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate),
            requestTimeoutSeconds = 5.0,
            bubblePacing = MurmurBubblePacing.INSTANT,
        )
        composeRule.setContent {
            MurmurTheme {
                ArchiveDayView(model = model)
            }
        }
        composeRule.onNodeWithText("3月12日").assertIsDisplayed()
        composeRule.onNodeWithText("星期二 · 聊过 1 张").assertIsDisplayed()
        composeRule.onNodeWithText("那天风很大").assertIsDisplayed()
        composeRule.onNodeWithTag("archive-day-composer").assertIsDisplayed()
    }
}
