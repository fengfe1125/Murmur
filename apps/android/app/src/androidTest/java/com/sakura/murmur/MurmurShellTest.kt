package com.sakura.murmur

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.sakura.murmur.ui.MurmurShell
import com.sakura.murmur.ui.MurmurTheme
import java.nio.file.Files
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The shell's own matrix (T4.3): the three stops on the capsule bar and one
 * page in the tree at a time. 当年今日 is the real tab since T4.6 — its
 * calendar and entry card render without photo permission, so the tab is
 * assertable as-is.
 */
@RunWith(AndroidJUnit4::class)
class MurmurShellTest {

    @get:Rule
    val composeRule = createComposeRule()

    private fun setShell(identity: MurmurIdentity?, darkTheme: Boolean = false) {
        composeRule.setContent {
            val session = MurmurSessionModel(
                api = UiFakeApi(identity),
                configurationFailure = null,
                photoLoader = UiFakePhotoLoader(),
                transcriptStore = MurmurTranscriptStore(Files.createTempDirectory("murmur-shell-test").toFile()),
                deviceNameProvider = { "Test Phone · Android 15" },
            )
            MurmurTheme(darkTheme = darkTheme) {
                MurmurShell(session = session)
            }
        }
    }

    private fun waitForText(text: String, timeoutMs: Long = 5_000) {
        composeRule.waitUntil(timeoutMillis = timeoutMs) {
            composeRule.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty()
        }
    }

    @Test
    fun allThreeStopsAreVisibleOnTheChatTab() {
        setShell(identity = MurmurIdentity("u", "d", "k"))
        waitForText("发来眼前的一刻。")
        composeRule.onNodeWithTag("tab-chat").assertIsDisplayed()
        composeRule.onNodeWithTag("tab-onThisDay").assertIsDisplayed()
        composeRule.onNodeWithTag("tab-me").assertIsDisplayed()
    }

    @Test
    fun switchingTabsShowsOnePageAtATime() {
        setShell(identity = MurmurIdentity("u", "d", "k"))
        waitForText("发来眼前的一刻。")

        composeRule.onNodeWithTag("tab-onThisDay").performClick()
        waitForText("翻翻同一天的旧照片")
        composeRule.onNodeWithText("翻翻同一天的旧照片").assertIsDisplayed()
        // The pages swap; only the current one is in the tree.
        assertTrue(composeRule.onAllNodesWithTag("empty-transcript").fetchSemanticsNodes().isEmpty())
        assertTrue(composeRule.onAllNodesWithText("保存频率与时段").fetchSemanticsNodes().isEmpty())

        composeRule.onNodeWithTag("tab-me").performClick()
        waitForText("保存频率与时段")
        composeRule.onNodeWithText("保存频率与时段").assertIsDisplayed()
        assertTrue(composeRule.onAllNodesWithText("翻翻同一天的旧照片").fetchSemanticsNodes().isEmpty())

        composeRule.onNodeWithTag("tab-chat").performClick()
        waitForText("发来眼前的一刻。")
        composeRule.onNodeWithTag("empty-transcript").assertIsDisplayed()
        assertTrue(composeRule.onAllNodesWithText("保存频率与时段").fetchSemanticsNodes().isEmpty())
    }

    @Test
    fun meStopOpensSettingsAndItHasNoCloseButton() {
        setShell(identity = MurmurIdentity("u", "d", "k"))
        waitForText("发来眼前的一刻。")
        composeRule.onNodeWithTag("tab-me").performClick()
        waitForText("每天最多")
        composeRule.onNodeWithText("设置").assertIsDisplayed()
        assertTrue(composeRule.onAllNodesWithText("完成").fetchSemanticsNodes().isEmpty())
    }

    @Test
    fun enrollmentGateOwnsTheWholeScreen() {
        setShell(identity = null)
        waitForText("把 Murmur 带到这里。")
        // No stops under the gate: the bar only exists with the tabs.
        assertTrue(composeRule.onAllNodesWithTag("tab-chat").fetchSemanticsNodes().isEmpty())
        assertTrue(composeRule.onAllNodesWithTag("tab-me").fetchSemanticsNodes().isEmpty())
    }

    @Test
    fun darkThemeRendersTheBarAndTheOnThisDayTab() {
        setShell(identity = MurmurIdentity("u", "d", "k"), darkTheme = true)
        waitForText("发来眼前的一刻。")
        composeRule.onNodeWithTag("tab-onThisDay").performClick()
        waitForText("翻翻同一天的旧照片")
        composeRule.onNodeWithText("翻翻同一天的旧照片").assertIsDisplayed()
        composeRule.onNodeWithTag("tab-me").assertIsDisplayed()
    }
}
