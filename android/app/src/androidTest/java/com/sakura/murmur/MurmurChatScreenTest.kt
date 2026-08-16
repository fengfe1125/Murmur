package com.sakura.murmur

import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.unit.Density
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.sakura.murmur.ui.MurmurChatScreen
import com.sakura.murmur.ui.MurmurTheme
import java.io.File
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The cold-start subset of the iOS `MurmurUITests.swift` matrix: empty state,
 * enrollment, disabled submit, dark theme, and the XXXL font smoke check.
 * The two-pane / orientation assertions land with the Phase-1 layout pass.
 */
@RunWith(AndroidJUnit4::class)
class MurmurChatScreenTest {

    @get:Rule
    val composeRule = createComposeRule()

    private class UiFakeApi(private val identity: MurmurIdentity?) : MurmurApiClient {
        override suspend fun storedIdentity(): MurmurIdentity? = identity
        override suspend fun enroll(inviteCode: String, deviceName: String): MurmurIdentity =
            identity ?: MurmurIdentity("test-user", "test-device", "test-key")
        override suspend fun createMoment(note: String?, photo: PhotoAttachment?, idempotencyKey: String): MomentReceipt =
            MomentReceipt("moment-1", "queued")
        override fun events(momentID: String, lastEventID: String?): Flow<MurmurStreamEvent> = emptyFlow()
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

    private fun setScreen(identity: MurmurIdentity?, darkTheme: Boolean = false, fontScale: Float = 1f) {
        composeRule.setContent {
            val session = MurmurSessionModel(
                api = UiFakeApi(identity),
                configurationFailure = null,
                photoLoader = UiFakePhotoLoader(),
                deviceNameProvider = { "Test Phone · Android 15" },
            )
            CompositionLocalProvider(LocalDensity provides Density(LocalDensity.current.density, fontScale)) {
                MurmurTheme(darkTheme = darkTheme) {
                    MurmurChatScreen(session = session)
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
        waitForText("输入邀请码，连接你的 Murmur。")
        composeRule.onNodeWithText("输入邀请码，连接你的 Murmur。").assertIsDisplayed()
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
