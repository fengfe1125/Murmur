package com.sakura.murmur

import android.content.Intent
import android.graphics.Bitmap
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.sakura.murmur.ui.MurmurShell
import com.sakura.murmur.ui.MurmurTheme
import java.io.File
import java.nio.file.Files
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 组装级的当年今日（B3）：`--murmur-stub-onthisday` 旗标经启动 intent 进
 * `OnThisDayLibraryProvider`（与 MainActivity 同一条接线），整个 MurmurShell
 * 在模拟器上跑真 tab 切换。入口卡和日历上的日子都是 cover —— cover 起来
 * 时底栏让出整屏，关掉就回来（iOS `fullScreenCover` 语义），这正是
 * `OnThisDayFlowViewTest` / `ArchiveViewsTest` 单视图测不到的 shell 行为。
 */
@RunWith(AndroidJUnit4::class)
class OnThisDayTabTest {

    @get:Rule
    val composeRule = createEmptyComposeRule()

    /** The shell reads the stub flags off its hosting activity's intent
     *  (debug `OnThisDayLibraryProvider`), so the test hosts the shell in a
     *  ComponentActivity it launched itself with the extras on the intent. */
    private fun launchShellWithFlags(
        vararg extras: String,
        block: (ActivityScenario<ComponentActivity>) -> Unit,
    ) {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val intent = Intent(context, ComponentActivity::class.java).apply {
            for (extra in extras) putExtra(extra, true)
        }
        ActivityScenario.launch<ComponentActivity>(intent).use(block)
    }

    private fun ActivityScenario<ComponentActivity>.setShellContent(archive: MurmurArchive? = null) {
        onActivity { activity ->
            activity.setContent {
                val session = MurmurSessionModel(
                    api = UiFakeApi(MurmurIdentity("u", "d", "k")),
                    configurationFailure = null,
                    photoLoader = UiFakePhotoLoader(),
                    transcriptStore = MurmurTranscriptStore(
                        Files.createTempDirectory("murmur-tab-test").toFile(),
                    ),
                    deviceNameProvider = { "Test Phone · Android 15" },
                    archive = archive ?: MurmurArchive(
                        MurmurTranscriptStore(
                            Files.createTempDirectory("murmur-tab-archive").toFile(),
                        ),
                    ),
                )
                MurmurTheme {
                    MurmurShell(session = session)
                }
            }
        }
    }

    private fun waitForNodeWithTag(tag: String, timeoutMs: Long = 5_000) {
        composeRule.waitUntil(timeoutMillis = timeoutMs) {
            composeRule.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty()
        }
    }

    @Test
    fun entryCardOpensTheBrowserCoverAndTheBarStepsAside() {
        launchShellWithFlags("--murmur-stub-onthisday") { scenario ->
            scenario.setShellContent()

            // Chat tab is home; the bar is up.
            waitForNodeWithTag("tab-chat")
            composeRule.onNodeWithTag("tab-onThisDay").performClick()
            waitForNodeWithTag("onthisday-entry")
            composeRule.onNodeWithTag("onthisday-entry").assertIsDisplayed()
            composeRule.onNodeWithTag("tab-chat").assertIsDisplayed()

            // The entry card opens the browser: a cover owns the whole screen
            // and the stub shelf's first card is on it.
            composeRule.onNodeWithTag("onthisday-entry").performClick()
            waitForNodeWithTag("onthisday-photo", timeoutMs = 10_000)
            composeRule.onNodeWithText("去年的今天").assertIsDisplayed()
            assertTrue(composeRule.onAllNodesWithTag("tab-chat").fetchSemanticsNodes().isEmpty())

            // Closing the cover hands the screen back, bar included.
            composeRule.onNodeWithTag("close-onthisday").performClick()
            waitForNodeWithTag("onthisday-entry")
            composeRule.onNodeWithTag("tab-chat").assertIsDisplayed()
        }
    }

    @Test
    fun aMarkedDayOpensItsThreadAsACoverToo() {
        val zone = ZoneId.of("Asia/Shanghai")
        val today = LocalDate.now(zone)
        val storeDir = Files.createTempDirectory("murmur-tab-archive").toFile()
        val archive = MurmurArchive(MurmurTranscriptStore(File(storeDir, "store")), zone)
        runBlocking {
            val source = File(storeDir, "source.jpg")
            source.outputStream().use { output ->
                Bitmap.createBitmap(8, 8, Bitmap.Config.ARGB_8888)
                    .compress(Bitmap.CompressFormat.JPEG, 90, output)
            }
            archive.record(
                MurmurMessage(
                    author = MurmurMessageAuthor.you,
                    text = "",
                    sentAt = ZonedDateTime.of(today, LocalTime.of(9, 0), zone).toInstant(),
                    momentID = "room-1",
                ),
                photoFile = source,
            )
            archive.record(
                MurmurMessage(
                    author = MurmurMessageAuthor.murmur,
                    text = "今天这张",
                    sentAt = ZonedDateTime.of(today, LocalTime.of(9, 1), zone).toInstant(),
                    momentID = "room-1",
                ),
                photoFile = null,
            )
        }

        launchShellWithFlags("--murmur-stub-onthisday") { scenario ->
            scenario.setShellContent(archive = archive)

            waitForNodeWithTag("tab-chat")
            composeRule.onNodeWithTag("tab-onThisDay").performClick()
            waitForNodeWithTag("onthisday-entry")

            // Today's cell carries the record dot; pressing it pushes the
            // day's thread over the tab and the bar steps aside for it.
            composeRule.onNodeWithTag("archive-day-$today").performClick()
            waitForNodeWithTag("archive-day-composer")
            composeRule.onNodeWithText("今天这张").assertIsDisplayed()
            assertTrue(composeRule.onAllNodesWithTag("tab-chat").fetchSemanticsNodes().isEmpty())

            // Back to the calendar, bar back with it.
            composeRule.onNodeWithTag("archive-day-back").performClick()
            waitForNodeWithTag("onthisday-entry")
            composeRule.onNodeWithTag("tab-chat").assertIsDisplayed()
        }
    }
}
