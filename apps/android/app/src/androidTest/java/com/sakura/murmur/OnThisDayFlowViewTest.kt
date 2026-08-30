package com.sakura.murmur

import android.graphics.Bitmap
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.printToLog
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeDown
import androidx.compose.ui.test.swipeUp
import com.sakura.murmur.ui.MurmurTheme
import com.sakura.murmur.ui.OnThisDayDebugTuning
import com.sakura.murmur.ui.OnThisDayView
import java.time.Instant
import java.time.ZoneId
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import androidx.test.ext.junit.runners.AndroidJUnit4

/**
 * The 当年今日 browser's screen contract, mirroring the iOS UI-test surface
 * (`--murmur-stub-onthisday*` cases + the dissolve/cancel timing case): the
 * four doors render, the ask button flips the stub door, 下滑 advances, 上滑
 * sends only after the dissolve window, and closing mid-dissolve keeps the
 * photo at home.
 */
@RunWith(AndroidJUnit4::class)
class OnThisDayFlowViewTest {

    @get:Rule
    val composeRule = createComposeRule()

    private class UiFakeLibrary(
        private val authorization: OnThisDayAuthorization,
        private val requestAnswer: OnThisDayAuthorization = authorization,
        private val sameDay: Int = 3,
        private val album: Int = 9,
    ) : OnThisDayLibrary {
        override val needsActivityPermissionRequest: Boolean = false

        override suspend fun authorization(): OnThisDayAuthorization = authorization

        override suspend fun requestAuthorization(): OnThisDayAuthorization = requestAnswer

        override suspend fun notePermissionResult(fullAccess: Boolean, partialAccess: Boolean): OnThisDayAuthorization =
            OnThisDayAuthorization.from(fullAccess, partialAccess, askedBefore = true)

        override suspend fun candidates(aroundMillis: Long, yearsBack: Int): List<OnThisDayCandidate> =
            (0 until sameDay).map { offset ->
                OnThisDayCandidate(
                    id = "ui-day-$offset",
                    creationMillis = Instant.ofEpochMilli(aroundMillis)
                        .atZone(ZoneId.systemDefault())
                        .minusYears((offset + 1).toLong())
                        .toInstant()
                        .toEpochMilli(),
                    origin = OnThisDayOrigin.SameDay(offset + 1),
                )
            }

        override suspend fun randomCandidates(count: Int, excluding: Set<String>): List<OnThisDayCandidate> =
            (0 until album)
                .map { "ui-album-$it" }
                .filter { it !in excluding }
                .take(count)
                .map { OnThisDayCandidate(it, 0L, OnThisDayOrigin.Elsewhere) }

        override suspend fun image(candidate: OnThisDayCandidate, targetPixels: Int): Bitmap =
            Bitmap.createBitmap(900, 1200, Bitmap.Config.ARGB_8888).apply { eraseColor(0xFF5AC8FA.toInt()) }
    }

    private lateinit var model: OnThisDayModel
    private val sent = mutableListOf<Bitmap>()

    private fun setBrowser(
        authorization: OnThisDayAuthorization,
        requestAnswer: OnThisDayAuthorization = authorization,
        sameDay: Int = 3,
        album: Int = 9,
    ) {
        val library = UiFakeLibrary(authorization, requestAnswer, sameDay, album)
        model = OnThisDayModel(
            library = library,
            scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate),
        )
        sent.clear()
        composeRule.setContent {
            MurmurTheme {
                OnThisDayView(
                    model = model,
                    onSend = { sent.add(it) },
                    onClose = {},
                )
            }
        }
    }

    private fun waitForCanSend() {
        composeRule.waitUntil(timeoutMillis = 5_000) { model.canSend }
    }

    @After
    fun tearDown() {
        OnThisDayDebugTuning.slowDissolve = false
    }

    @Test
    fun notDeterminedGateOffersTheAllowButton() {
        setBrowser(OnThisDayAuthorization.NotDetermined)
        composeRule.onNodeWithTag("onthisday-allow").assertIsDisplayed()
    }

    @Test
    fun deniedGateExplainsAndOffersSettings() {
        setBrowser(OnThisDayAuthorization.Denied)
        composeRule.onNodeWithText("相册的入口关着").assertIsDisplayed()
        composeRule.onNodeWithTag("onthisday-open-settings").assertIsDisplayed()
    }

    @Test
    fun limitedGateHasItsOwnExplanation() {
        setBrowser(OnThisDayAuthorization.Limited)
        composeRule.onNodeWithText("只能看到你选的那几张").assertIsDisplayed()
        composeRule.onNodeWithTag("onthisday-open-settings").assertIsDisplayed()
    }

    /** The stub's ask path: the button flips the door and the shelf loads
     *  without any system dialog. */
    @Test
    fun askButtonFlipsTheDoorAndShowsTheCard() {
        setBrowser(
            OnThisDayAuthorization.NotDetermined,
            requestAnswer = OnThisDayAuthorization.Authorized,
        )
        composeRule.onNodeWithTag("onthisday-allow").performClick()
        waitForCanSend()
        composeRule.onNodeWithTag("onthisday-photo").assertIsDisplayed()
        composeRule.onNodeWithText("去年的今天").assertIsDisplayed()
    }

    @Test
    fun authorizedShowsTheFirstCardWithItsHonestCaption() {
        setBrowser(OnThisDayAuthorization.Authorized, sameDay = 3)
        waitForCanSend()
        composeRule.onNodeWithTag("onthisday-photo").assertIsDisplayed()
        composeRule.onNodeWithText("去年的今天").assertIsDisplayed()
    }

    @Test
    fun swipeDownAdvancesToTheNextCard() {
        setBrowser(OnThisDayAuthorization.Authorized, sameDay = 3)
        waitForCanSend()
        composeRule.onNodeWithTag("onthisday-photo").performTouchInput { swipeDown() }
        composeRule.waitUntil(timeoutMillis = 5_000) {
            composeRule.onAllNodesWithText2("2 年前的今天")
        }
        composeRule.onNodeWithText("2 年前的今天").assertIsDisplayed()
    }

    /** 上滑 sends — but only after the dissolve window has elapsed. */
    @Test
    fun swipeUpSendsAfterTheDissolve() {
        OnThisDayDebugTuning.slowDissolve = true
        setBrowser(OnThisDayAuthorization.Authorized, sameDay = 3)
        waitForCanSend()
        composeRule.onNodeWithTag("onthisday-photo").performTouchInput { swipeUp() }
        assertTrue(sent.isEmpty())
        composeRule.mainClock.advanceTimeBy(3_400)
        composeRule.waitForIdle()
        assertEquals(1, sent.size)
    }

    /** The send-off is a particle dissolve, not just a fade: the overlay is
     *  up while the dissolve runs and gone once it has completed. */
    @Test
    fun slowDissolveShowsParticleOverlay() {
        OnThisDayDebugTuning.slowDissolve = true
        setBrowser(OnThisDayAuthorization.Authorized, sameDay = 3)
        waitForCanSend()
        composeRule.onNodeWithTag("onthisday-photo").performTouchInput { swipeUp() }
        composeRule.waitUntil(timeoutMillis = 5_000) {
            composeRule.onAllNodesWithTag("dissolve-particles").fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithTag("dissolve-particles").assertIsDisplayed()
        composeRule.mainClock.advanceTimeBy(3_400)
        composeRule.waitForIdle()
        composeRule.onNodeWithTag("dissolve-particles").assertDoesNotExist()
        assertEquals(1, sent.size)
    }

    /** Closing mid-dissolve cancels the send: the person believed they had
     *  cancelled, and the photo must stay at home. */
    @Test
    fun closingMidDissolveCancelsTheSend() {
        OnThisDayDebugTuning.slowDissolve = true
        setBrowser(OnThisDayAuthorization.Authorized, sameDay = 3)
        waitForCanSend()
        composeRule.onNodeWithTag("onthisday-photo").performTouchInput { swipeUp() }
        composeRule.mainClock.advanceTimeBy(1_000)
        composeRule.onNodeWithTag("close-onthisday").performClick()
        composeRule.mainClock.advanceTimeBy(4_000)
        composeRule.waitForIdle()
        assertTrue(sent.isEmpty())
    }

    /** A barren-but-authorized library is the one honest dead end. */
    @Test
    fun barrenLibraryShowsTheEmptyState() {
        setBrowser(OnThisDayAuthorization.Authorized, sameDay = 0, album = 0)
        composeRule.waitUntil(timeoutMillis = 5_000) {
            composeRule.onAllNodesWithTag2("onthisday-empty")
        }
        composeRule.onNodeWithText("相册里还没有照片。").assertIsDisplayed()
    }

    // Small local helpers so the test reads like the assertions above.
    private fun androidx.compose.ui.test.SemanticsNodeInteractionsProvider.onAllNodesWithText2(
        text: String,
    ): Boolean = this.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty()

    private fun androidx.compose.ui.test.SemanticsNodeInteractionsProvider.onAllNodesWithTag2(
        tag: String,
    ): Boolean = this.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty()
}
