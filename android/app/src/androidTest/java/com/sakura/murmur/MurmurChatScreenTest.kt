package com.sakura.murmur

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.requiredWidth
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.sakura.murmur.ui.MurmurChatScreen
import com.sakura.murmur.ui.MurmurTheme
import java.io.File
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The UI matrix for the Phase-1 screens: enrollment, reconnect, the compact
 * workbench, the settings pane, the two-pane regular layout, dark mode and
 * the XXXL font smoke check.
 */
@RunWith(AndroidJUnit4::class)
class MurmurChatScreenTest {

    @get:Rule
    val composeRule = createComposeRule()

    private class UiFakeApi(
        private val identity: MurmurIdentity?,
        private val failDevicesWithUnknownKey: Boolean = false,
    ) : MurmurApiClient {
        override suspend fun storedIdentity(): MurmurIdentity? = identity
        override suspend fun enroll(inviteCode: String, deviceName: String): MurmurIdentity =
            identity ?: MurmurIdentity("test-user", "test-device", "test-key")
        override suspend fun createMoment(note: String?, photo: PhotoAttachment?, idempotencyKey: String): MomentReceipt =
            MomentReceipt("moment-1", "queued")
        override fun events(momentID: String, lastEventID: String?): Flow<MurmurStreamEvent> = emptyFlow()
        override suspend fun currentProactive(): ProactiveMoment? = null
        override suspend fun acknowledge(momentID: String, reply: String?) = Unit
        override suspend fun updateDevice(pushToken: String?, environment: String, timezone: String, deviceName: String) = Unit
        override suspend fun devices(): List<MurmurDevice> {
            if (failDevicesWithUnknownKey) {
                throw MurmurFailure("attestation_key_unknown", "Device binding is unknown.", retryable = false)
            }
            return listOf(
                MurmurDevice("test-device", "test-key", "development", "Asia/Shanghai", "Test Phone", true, null, null),
                MurmurDevice("other-device", "other-key", "development", "Asia/Shanghai", "Test Pad", false, null, null),
            )
        }
        override suspend fun removeDevice(deviceID: String) = Unit
        override suspend fun preferences(): MurmurPreferences = MurmurPreferences()
        override suspend fun updatePreferences(preferences: MurmurPreferences) = Unit
        override suspend fun resetLocalIdentity() = Unit
        override suspend fun deleteAccount() = Unit
    }

    private class UiFakePhotoLoader : PhotoLoader {
        private val directory = File(System.getProperty("java.io.tmpdir"), "murmur-ui-test")
        override suspend fun load(input: PhotoInput): PhotoAttachment {
            directory.mkdirs()
            val file = File(directory, "murmur-upload-${java.util.UUID.randomUUID()}.jpg").apply { writeBytes(ByteArray(4)) }
            return PhotoAttachment(file = file, preview = null, filename = "test.jpg", mimeType = "image/jpeg", byteCount = 4)
        }
        override suspend fun discard(attachment: PhotoAttachment?) {
            attachment?.file?.delete()
        }
        override suspend fun discardFile(file: File) = Unit
        override suspend fun cleanupStaleFiles() = Unit
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
                deviceNameProvider = { "Test Phone · Android 15" },
            )
            CompositionLocalProvider(LocalDensity provides Density(LocalDensity.current.density, fontScale)) {
                MurmurTheme(darkTheme = darkTheme) {
                    if (widthDp != null) {
                        Box(Modifier.requiredWidth(widthDp.dp)) {
                            MurmurChatScreen(session = session)
                        }
                    } else {
                        MurmurChatScreen(session = session)
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

    @Test
    fun enrollmentPaneShowsWhenThereIsNoIdentity() {
        setScreen(identity = null)
        waitForText("把 Murmur 带到这里。")
        composeRule.onNodeWithText("把 Murmur 带到这里。").assertIsDisplayed()
        composeRule.onNodeWithText("连接这台设备").assertIsDisplayed()
    }

    @Test
    fun connectedWorkbenchInvitesTheFirstMoment() {
        setScreen(identity = MurmurIdentity("u", "d", "k"))
        waitForText("选一张照片，发送此刻。")
        composeRule.onNodeWithText("Murmur").assertIsDisplayed()
        composeRule.onNodeWithText("发送此刻").assertIsDisplayed()
    }

    @Test
    fun sendButtonStaysDisabledUntilThereIsContent() {
        setScreen(identity = MurmurIdentity("u", "d", "k"))
        waitForText("发送此刻")
        composeRule.onNodeWithText("发送此刻").assertIsNotEnabled()
    }

    @Test
    fun unknownAttestationKeyShowsTheReconnectView() {
        setScreen(identity = MurmurIdentity("u", "d", "k"), failDevicesWithUnknownKey = true)
        waitForText("这台设备需要重新连接。")
        composeRule.onNodeWithText("重新连接此设备").assertIsDisplayed()
    }

    @Test
    fun settingsPaneListsDevicesAndPreferencesAndCloses() {
        setScreen(identity = MurmurIdentity("u", "d", "k"))
        waitForText("选一张照片，发送此刻。")
        composeRule.onNodeWithTag("settings-button").performClick()
        waitForText("完成")
        composeRule.onNodeWithText("每天最多").assertIsDisplayed()
        composeRule.onNodeWithText("Test Phone").assertIsDisplayed()
        composeRule.onNodeWithText("Test Pad").assertIsDisplayed()
        composeRule.onNodeWithText("保存频率与时段").assertIsDisplayed()
        composeRule.onNodeWithText("完成").performClick()
        waitForText("发送此刻")
    }

    @Test
    fun quietTimePickerOpensAndConfirms() {
        setScreen(identity = MurmurIdentity("u", "d", "k"))
        waitForText("选一张照片，发送此刻。")
        composeRule.onNodeWithTag("settings-button").performClick()
        waitForText("每天最多")
        composeRule.onNodeWithText("22:30").performClick()
        waitForText("确定")
        composeRule.onNodeWithText("确定").performClick()
        composeRule.waitUntil(timeoutMillis = 5_000) {
            composeRule.onAllNodesWithText("确定").fetchSemanticsNodes().isEmpty()
        }
    }

    @Test
    fun compactWidthStacksTheWorkbench() {
        setScreen(identity = MurmurIdentity("u", "d", "k"), widthDp = 420)
        waitForText("选一张照片，发送此刻。")
        composeRule.onNodeWithTag("workbench-compact").assertIsDisplayed()
    }

    @Test
    fun regularWidthSplitsIntoTwoPanes() {
        setScreen(identity = MurmurIdentity("u", "d", "k"), widthDp = 800)
        waitForText("选一张照片，发送此刻。")
        composeRule.onNodeWithTag("workbench-regular").assertIsDisplayed()
        composeRule.onNodeWithTag("workbench-response").assertIsDisplayed()
        assertTrue(composeRule.onAllNodesWithText("发送此刻").fetchSemanticsNodes().isNotEmpty())
    }

    @Test
    fun darkThemeRendersTheSameWorkbench() {
        setScreen(identity = MurmurIdentity("u", "d", "k"), darkTheme = true)
        waitForText("选一张照片，发送此刻。")
        composeRule.onNodeWithText("Murmur").assertIsDisplayed()
    }

    @Test
    fun xxxlFontScaleStillRendersWithoutClippingTheWordmark() {
        setScreen(identity = MurmurIdentity("u", "d", "k"), fontScale = 2.4f)
        waitForText("Murmur")
        composeRule.onNodeWithText("Murmur").assertIsDisplayed()
    }
}
