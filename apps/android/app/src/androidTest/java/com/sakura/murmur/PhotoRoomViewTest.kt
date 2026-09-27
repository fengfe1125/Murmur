package com.sakura.murmur

import android.graphics.Bitmap
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.sakura.murmur.ui.MurmurTheme
import com.sakura.murmur.ui.PhotoRoomView
import java.io.File
import java.nio.file.Files
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 照片房间的屏幕契约，镜像 JVM 侧 `PhotoRoomModelTest` 的读图流：开门后
 * 照片条、猜测气泡和三个话头逐一落地，话头是填进输入框的门而不是消息，
 * 深色下整套渲染不炸。模型与视图之间的接线（collectAsState、open 的
 * LaunchedEffect、dispose 时的 close）只有模拟器上才跑得到 —— 这正是
 * T4.2 之后补齐的组装级覆盖。
 */
@RunWith(AndroidJUnit4::class)
class PhotoRoomViewTest {

    @get:Rule
    val composeRule = createComposeRule()

    private val guess = "这是……刚下过雨？"
    private val openers = listOf("那天的天气", "右边那个人", "上次说要再来")

    private lateinit var model: PhotoRoomModel

    private fun setRoom(darkTheme: Boolean = false) {
        val root = Files.createTempDirectory("murmur-photo-room-ui").toFile()
        val source = File(root, "picked.jpg").apply { writeBytes(ByteArray(64)) }
        model = PhotoRoomModel(
            input = PhotoInput.FromFile(source),
            api = UiFakeApi(
                identity = MurmurIdentity("u", "d", "k"),
                replyText = guess,
                angles = openers,
            ),
            photoLoader = UiFakePhotoLoader(preview = Bitmap.createBitmap(30, 40, Bitmap.Config.ARGB_8888)),
            scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate),
            bubblePacing = MurmurBubblePacing.INSTANT,
            recorder = null,
        )
        composeRule.setContent {
            MurmurTheme(darkTheme = darkTheme) {
                PhotoRoomView(model = model, onClose = {})
            }
        }
    }

    private fun waitForNodeWithTag(tag: String, timeoutMs: Long = 5_000) {
        composeRule.waitUntil(timeoutMillis = timeoutMs) {
            composeRule.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty()
        }
    }

    @Test
    fun openingShowsThePhotoBandTheGuessAndTheThreeOpeners() {
        setRoom()
        waitForNodeWithTag("photo-room-photo")
        composeRule.onNodeWithTag("photo-room-photo").assertIsDisplayed()
        composeRule.onNodeWithText(guess).assertIsDisplayed()
        composeRule.onNodeWithTag("opener-0").assertIsDisplayed()
        composeRule.onNodeWithTag("opener-1").assertIsDisplayed()
        composeRule.onNodeWithTag("opener-2").assertIsDisplayed()
        // The stream has run to its done: the room is live, not still reading.
        composeRule.waitUntil(timeoutMillis = 5_000) { model.phase.value == PhotoRoomModel.Phase.Listening }
        assertTrue(composeRule.onAllNodesWithTag("photo-room-typing").fetchSemanticsNodes().isEmpty())
    }

    /** An opener is a door, not a message: tapping one lifts it into the
     *  field and the guess stays where it was. */
    @Test
    fun pickingAnOpenerFillsTheComposerWithoutSending() {
        setRoom()
        waitForNodeWithTag("opener-1")
        composeRule.onNodeWithTag("opener-1").performClick()
        composeRule.waitForIdle()
        assertEquals(openers[1], model.draft.value)
        composeRule.onNodeWithTag("photo-room-composer").assertTextEquals(openers[1])
        // Nothing left the room: the only line is still Murmur's guess.
        assertEquals(listOf(guess), model.lines.value.map { it.text })
        assertEquals(
            listOf(PhotoRoomModel.LineAuthor.Murmur),
            model.lines.value.map { it.author },
        )
    }

    @Test
    fun darkThemeRendersTheRoom() {
        setRoom(darkTheme = true)
        waitForNodeWithTag("photo-room-photo")
        composeRule.onNodeWithText(guess).assertIsDisplayed()
        composeRule.onNodeWithTag("opener-0").assertIsDisplayed()
        composeRule.onNodeWithTag("photo-room-composer").assertIsDisplayed()
    }
}
