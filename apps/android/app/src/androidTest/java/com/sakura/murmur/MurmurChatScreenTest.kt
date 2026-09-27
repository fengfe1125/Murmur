package com.sakura.murmur

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.requiredWidth
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import android.Manifest
import android.os.Build
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.sakura.murmur.ui.MurmurShell
import com.sakura.murmur.ui.MurmurTheme
import java.nio.file.Files
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The UI matrix for the shell: the gates (checking / enrollment / reconnect)
 * that own the whole screen, the three tabs, the transcript (empty, sending,
 * dark, narrow and wide, XXXL fonts), and the settings pane behind the 我的
 * stop.  The workbench cases this file used to carry moved to the bubble
 * paradigm with T4.2 — the transcript is the screen now — and T4.3 moved the
 * gates and the settings entry out of the chat into the shell.
 */
@RunWith(AndroidJUnit4::class)
class MurmurChatScreenTest {

    @get:Rule
    val composeRule = createComposeRule()

    // Two tests here send a message, and a completed reply trips the
    // notification-permission prompt (T1.5) on API 33+. Pre-granting keeps
    // GrantPermissionsActivity from stealing the stage mid-test — the launcher
    // resolves immediately when the permission is already granted. (Test
    // target is the API 35 emulator; older APIs never see the prompt.)
    @Before
    fun grantNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33) {
            val instrumentation = InstrumentationRegistry.getInstrumentation()
            instrumentation.uiAutomation.grantRuntimePermission(
                instrumentation.targetContext.packageName,
                Manifest.permission.POST_NOTIFICATIONS,
            )
        }
    }

    private fun setScreen(
        identity: MurmurIdentity?,
        failDevicesWithUnknownKey: Boolean = false,
        darkTheme: Boolean = false,
        fontScale: Float = 1f,
        widthDp: Int? = null,
    ) {
        composeRule.setContent {
            val session = MurmurSessionModel(
                api = UiFakeApi(identity, failDevicesWithUnknownKey),
                configurationFailure = null,
                photoLoader = UiFakePhotoLoader(),
                transcriptStore = MurmurTranscriptStore(Files.createTempDirectory("murmur-ui-test").toFile()),
                deviceNameProvider = { "Test Phone · Android 15" },
            )
            CompositionLocalProvider(LocalDensity provides Density(LocalDensity.current.density, fontScale)) {
                MurmurTheme(darkTheme = darkTheme) {
                    if (widthDp != null) {
                        Box(Modifier.requiredWidth(widthDp.dp)) {
                            MurmurShell(session = session)
                        }
                    } else {
                        MurmurShell(session = session)
                    }
                }
            }
        }
    }

    private fun waitForText(text: String, timeoutMs: Long = 5_000) {
        composeRule.waitUntil(timeoutMillis = timeoutMs) {
            composeRule.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty()
        }
    }

    private fun waitForTag(tag: String, timeoutMs: Long = 5_000) {
        composeRule.waitUntil(timeoutMillis = timeoutMs) {
            composeRule.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty()
        }
    }

    @Test
    fun enrollmentPaneShowsWhenThereIsNoIdentity() {
        setScreen(identity = null)
        waitForText("把 Murmur 带到这里。")
        composeRule.onNodeWithText("把 Murmur 带到这里。").assertIsDisplayed()
        composeRule.onNodeWithText("连接这台设备").assertIsDisplayed()
        // The gates own the whole screen: no tab bar underneath them.
        assertTrue(composeRule.onAllNodesWithTag("tab-chat").fetchSemanticsNodes().isEmpty())
    }

    @Test
    fun emptyTranscriptInvitesTheFirstMoment() {
        setScreen(identity = MurmurIdentity("u", "d", "k"))
        waitForText("发来眼前的一刻。")
        composeRule.onNodeWithTag("empty-transcript").assertIsDisplayed()
        composeRule.onNodeWithText("Murmur").assertIsDisplayed()
        composeRule.onNodeWithTag("send-moment").assertIsDisplayed()
    }

    @Test
    fun chatTopBarHasNoSettingsButton() {
        setScreen(identity = MurmurIdentity("u", "d", "k"))
        waitForText("发来眼前的一刻。")
        assertTrue(composeRule.onAllNodesWithTag("settings-button").fetchSemanticsNodes().isEmpty())
    }

    @Test
    fun sendButtonStaysDisabledUntilThereIsContent() {
        setScreen(identity = MurmurIdentity("u", "d", "k"))
        waitForTag("send-moment")
        composeRule.onNodeWithTag("send-moment").assertIsNotEnabled()
    }

    @Test
    fun sentMessageAppearsAsABubble() {
        setScreen(identity = MurmurIdentity("u", "d", "k"))
        waitForText("发来眼前的一刻。")

        composeRule.onNodeWithTag("moment-composer").performTextInput("看看这个")
        composeRule.onNodeWithTag("send-moment").performClick()

        // The outgoing row is on screen the instant the button is pressed,
        // and the streamed reply lands as a murmur bubble beneath it.
        composeRule.onNodeWithTag("murmur-message-0").assertIsDisplayed()
        waitForText("reply-1")
        composeRule.onNodeWithText("reply-1").assertIsDisplayed()
    }

    @Test
    fun outgoingAndIncomingBubblesLandOnTheirOwnSides() {
        setScreen(identity = MurmurIdentity("u", "d", "k"))
        waitForText("发来眼前的一刻。")

        composeRule.onNodeWithTag("moment-composer").performTextInput("看看这个")
        composeRule.onNodeWithTag("send-moment").performClick()

        // Tags follow the transcript's append order, not visual position:
        // the outgoing row is index 0 (the only message until the reply
        // lands); the reply appends as index 1.
        composeRule.onNodeWithTag("murmur-message-0").assertIsDisplayed()
        waitForText("reply-1")

        val rootWidth = composeRule.onRoot().fetchSemanticsNode().size.width.toFloat()
        val outgoingCenter = composeRule.onNodeWithTag("murmur-message-0").fetchSemanticsNode().boundsInWindow.center.x
        val replyCenter = composeRule.onNodeWithText("reply-1").fetchSemanticsNode().boundsInWindow.center.x
        assertTrue("outgoing row should sit right of centre (x=$outgoingCenter)", outgoingCenter > rootWidth / 2f)
        assertTrue("reply row should sit left of centre (x=$replyCenter)", replyCenter < rootWidth / 2f)
    }

    @Test
    fun unknownAttestationKeyShowsTheReconnectView() {
        setScreen(identity = MurmurIdentity("u", "d", "k"), failDevicesWithUnknownKey = true)
        waitForText("这台设备需要重新连接。")
        composeRule.onNodeWithText("重新连接此设备").assertIsDisplayed()
    }

    @Test
    fun settingsPaneListsDevicesAndPreferences() {
        setScreen(identity = MurmurIdentity("u", "d", "k"))
        waitForText("发来眼前的一刻。")
        composeRule.onNodeWithTag("tab-me").performClick()
        waitForText("每天最多")
        composeRule.onNodeWithText("每天最多").assertIsDisplayed()
        composeRule.onNodeWithText("Test Phone").assertIsDisplayed()
        composeRule.onNodeWithText("Test Pad").assertIsDisplayed()
        // The grouped layout is taller than the viewport; the save row sits
        // below the fold until scrolled.
        composeRule.onNodeWithText("保存频率与时段").performScrollTo().assertIsDisplayed()
        // The tab owns its own exit: there is no 完成 in the shell.
        assertTrue(composeRule.onAllNodesWithText("完成").fetchSemanticsNodes().isEmpty())
        composeRule.onNodeWithTag("tab-chat").performClick()
        waitForTag("send-moment")
    }

    @Test
    fun settingsClearTranscriptEmptiesTheConversation() {
        var session: MurmurSessionModel? = null
        composeRule.setContent {
            session = MurmurSessionModel(
                api = UiFakeApi(MurmurIdentity("u", "d", "k")),
                configurationFailure = null,
                photoLoader = UiFakePhotoLoader(),
                transcriptStore = MurmurTranscriptStore(Files.createTempDirectory("murmur-ui-test").toFile()),
                deviceNameProvider = { "Test Phone · Android 15" },
            )
            MurmurTheme {
                MurmurShell(session = session!!)
            }
        }
        waitForText("发来眼前的一刻。")
        // Send through the session like the composer does — keyboard typing is
        // covered by sentMessageAppearsAsABubble; this test is about clearing.
        session!!.updateDraftText("等下被清掉")
        session!!.submit()
        composeRule.onNodeWithTag("murmur-message-0").assertIsDisplayed()

        composeRule.onNodeWithTag("tab-me").performClick()
        waitForText("每天最多")
        val clear = composeRule.onNodeWithTag("clear-transcript")
        clear.assertIsEnabled()
        // The button lives near the bottom of the scrollable settings list;
        // without scrolling, its centre is off-screen and the tap drops.
        clear.performScrollTo()
        clear.performClick()
        waitForText("确认清空")
        composeRule.onNodeWithText("确认清空").performClick()

        composeRule.onNodeWithTag("tab-chat").performClick()
        waitForText("发来眼前的一刻。")
        composeRule.onNodeWithTag("empty-transcript").assertIsDisplayed()
    }

    @Test
    fun quietTimePickerOpensAndConfirms() {
        setScreen(identity = MurmurIdentity("u", "d", "k"))
        waitForText("发来眼前的一刻。")
        composeRule.onNodeWithTag("tab-me").performClick()
        waitForText("每天最多")
        composeRule.onNodeWithText("22:30").performClick()
        waitForText("确定")
        composeRule.onNodeWithText("确定").performClick()
        composeRule.waitUntil(timeoutMillis = 5_000) {
            composeRule.onAllNodesWithText("确定").fetchSemanticsNodes().isEmpty()
        }
    }

    @Test
    fun compactWidthRendersTheTranscript() {
        setScreen(identity = MurmurIdentity("u", "d", "k"), widthDp = 420)
        waitForText("发来眼前的一刻。")
        composeRule.onNodeWithTag("empty-transcript").assertIsDisplayed()
    }

    @Test
    fun regularWidthRendersTheTranscript() {
        setScreen(identity = MurmurIdentity("u", "d", "k"), widthDp = 800)
        waitForText("发来眼前的一刻。")
        composeRule.onNodeWithTag("empty-transcript").assertIsDisplayed()
        assertTrue(composeRule.onAllNodesWithTag("send-moment").fetchSemanticsNodes().isNotEmpty())
    }

    @Test
    fun darkThemeRendersTheSameTranscript() {
        setScreen(identity = MurmurIdentity("u", "d", "k"), darkTheme = true)
        waitForText("发来眼前的一刻。")
        composeRule.onNodeWithText("Murmur").assertIsDisplayed()
        composeRule.onNodeWithTag("empty-transcript").assertIsDisplayed()
    }

    @Test
    fun xxxlFontScaleStillRendersWithoutClippingTheWordmark() {
        setScreen(identity = MurmurIdentity("u", "d", "k"), fontScale = 2.4f)
        waitForText("Murmur")
        composeRule.onNodeWithText("Murmur").assertIsDisplayed()
        composeRule.onNodeWithTag("empty-transcript").assertIsDisplayed()
    }
}
