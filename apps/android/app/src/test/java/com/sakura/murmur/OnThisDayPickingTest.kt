package com.sakura.murmur

import java.time.Instant
import java.time.ZoneId
import kotlin.random.Random
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The pure decision core of 当年今日 — the contracts from iOS
 * `OnThisDayTests.swift` that are platform-independent: the four-state door
 * mapping, the day-window math, the window ranking (收藏 > 面积 > 时间,
 * screenshots and thumbnails out) and the bounded random draw. The shader
 * existence case has no Android counterpart (no Metal); the animation it
 * guarded is covered in the instrumented test by waiting on the card itself.
 */
class OnThisDayPickingTest {

    private val zone: ZoneId = ZoneId.of("Asia/Shanghai")

    // ---- The four doors -------------------------------------------------------

    /** `.restricted` is not a fifth state in the UI on iOS; on Android the
     *  managed-device case simply never grants, and the asked-and-refused
     *  mapping lands it on the same Denied door. */
    @Test
    fun everyPermissionAnswerLandsOnADesignedDoor() {
        assertEquals(
            OnThisDayAuthorization.Authorized,
            OnThisDayAuthorization.from(fullAccess = true, partialAccess = false, askedBefore = false),
        )
        assertEquals(
            OnThisDayAuthorization.Authorized,
            OnThisDayAuthorization.from(fullAccess = true, partialAccess = true, askedBefore = true),
        )
        assertEquals(
            OnThisDayAuthorization.Limited,
            OnThisDayAuthorization.from(fullAccess = false, partialAccess = true, askedBefore = true),
        )
        assertEquals(
            OnThisDayAuthorization.Denied,
            OnThisDayAuthorization.from(fullAccess = false, partialAccess = false, askedBefore = true),
        )
        assertEquals(
            OnThisDayAuthorization.NotDetermined,
            OnThisDayAuthorization.from(fullAccess = false, partialAccess = false, askedBefore = false),
        )
    }

    // ---- Day windows ------------------------------------------------------------

    /** The window is the whole of the same day ±1 in the device's own
     *  calendar — a UTC-based window would put the edge hours on the wrong
     *  day for half the planet. The end is exclusive, so it sits two midnights
     *  after the day's start. */
    @Test
    fun windowBoundsCoverTheSameDayPlusMinusOneInDeviceZone() {
        val around = Instant.parse("2026-08-30T15:00:00+08:00").toEpochMilli()
        val (start, end) = OnThisDayPicking.windowBounds(around, yearsAgo = 1, zone)
        assertEquals(Instant.parse("2025-08-29T00:00:00+08:00").toEpochMilli(), start)
        assertEquals(Instant.parse("2025-09-01T00:00:00+08:00").toEpochMilli(), end)
    }

    /** February 29 steps back to February 28 in a non-leap year, the way
     *  `Calendar.date(byAdding: .year)` lands it. */
    @Test
    fun windowBoundsStepLeapDayBackToFeb28() {
        val around = Instant.parse("2024-02-29T12:00:00+08:00").toEpochMilli()
        val (start, end) = OnThisDayPicking.windowBounds(around, yearsAgo = 1, zone)
        assertEquals(Instant.parse("2023-02-27T00:00:00+08:00").toEpochMilli(), start)
        assertEquals(Instant.parse("2023-03-02T00:00:00+08:00").toEpochMilli(), end)
    }

    // ---- Window ranking -----------------------------------------------------------

    private fun meta(
        id: String,
        taken: Long,
        width: Int = 4000,
        height: Int = 3000,
        favorite: Boolean = false,
        screenshot: Boolean = false,
    ) = OnThisDayPhotoMeta(id, taken, width, height, favorite, screenshot)

    @Test
    fun favoritesWinOverAreaAndTime() {
        val plainBigNew = meta("plain-big-new", taken = 3000, width = 5000, height = 4000)
        val favSmallOld = meta("fav-small-old", taken = 1000, width = 800, height = 600, favorite = true)
        val picked = OnThisDayPicking.pickFromWindow(listOf(plainBigNew, favSmallOld), yearsAgo = 2)
        assertEquals(listOf("fav-small-old", "plain-big-new"), picked.map { it.id })
        assertTrue(picked.all { it.origin == OnThisDayOrigin.SameDay(2) })
    }

    /** Area ranks above time, and within equal areas the newer photo wins:
     *  bigOld ties the two on area, so its older timestamp loses. */
    @Test
    fun areaWinsOverTimeAndNewerBreaksTheTie() {
        val smallNew = meta("small-new", taken = 3000, width = 3000, height = 2000)
        val bigOld = meta("big-old", taken = 1000, width = 4000, height = 3000)
        val tieA = meta("tie-a", taken = 2000, width = 4000, height = 3000)
        val tieB = meta("tie-b", taken = 1500, width = 4000, height = 3000)
        val picked = OnThisDayPicking.pickFromWindow(listOf(smallNew, bigOld, tieA, tieB), yearsAgo = 1)
        assertEquals(listOf("tie-a", "tie-b", "big-old", "small-new"), picked.map { it.id })
    }

    @Test
    fun screenshotsAndThumbnailsNeverReachTheRanking() {
        val screenshot = meta("screenshot", taken = 4000, screenshot = true)
        val sticker = meta("sticker", taken = 3000, width = 400, height = 300)
        val onePxShort = meta("one-short", taken = 2000, width = 599, height = 4000)
        val real = meta("real", taken = 1000)
        val picked = OnThisDayPicking.pickFromWindow(listOf(screenshot, sticker, onePxShort, real), yearsAgo = 1)
        assertEquals(listOf("real"), picked.map { it.id })
    }

    /** The window limit is spent in time order before the ranking runs — a
     *  favorite older than the newest `perWindowLimit` photos never reaches
     *  the comparison. */
    @Test
    fun theWindowLimitIsSpentInTimeOrder() {
        val recent = (0 until 60).map { meta("recent-$it", taken = 1000L + it) }
        val oldFavorite = meta("old-favorite", taken = 10, favorite = true)
        val picked = OnThisDayPicking.pickFromWindow(recent + oldFavorite, yearsAgo = 1)
        assertFalse(picked.any { it.id == "old-favorite" })
        // The 6 kept are the newest 60's top by area-desc/time-desc: all same
        // area here, so the newest six, newest first.
        assertEquals(6, picked.size)
        assertEquals((59 downTo 54).map { "recent-$it" }, picked.map { it.id })
    }

    @Test
    fun atMostKeptPerYearSurviveOneWindow() {
        val metas = (0 until 10).map { meta("m$it", taken = 1000L + it) }
        val picked = OnThisDayPicking.pickFromWindow(metas, yearsAgo = 1)
        assertEquals(6, picked.size)
    }

    // ---- Random draws -------------------------------------------------------------

    @Test
    fun randomDrawsExcludeWhatIsAlreadySpent() {
        val pool = (0 until 5).map { meta("p$it", taken = 1000L + it) }
        val excluding = setOf("p0", "p1", "p2")
        val picked = OnThisDayPicking.randomDraw(pool, count = 3, excluding = excluding, random = Random(7))
        assertEquals(2, picked.size)
        assertTrue(picked.none { it.id in excluding })
        assertTrue(picked.all { it.origin == OnThisDayOrigin.Elsewhere })
    }

    @Test
    fun randomDrawsNeverHandBackADuplicate() {
        val pool = (0 until 20).map { meta("p$it", taken = 1000L + it) }
        val first = OnThisDayPicking.randomDraw(pool, count = 10, excluding = emptySet(), random = Random(11))
        val second = OnThisDayPicking.randomDraw(pool, count = 10, excluding = first.map { it.id }.toSet(), random = Random(13))
        val ids = first.map { it.id }
        assertEquals(ids.size, ids.toSet().size)
        assertTrue(second.none { it.id in ids })
    }

    /** A pool smaller than the ask gives what it has and stops — the draw
     *  ceiling is a budget, not a promise of `count`. */
    @Test
    fun anExhaustedPoolStopsDrawing() {
        val pool = listOf(meta("only", taken = 1000))
        val picked = OnThisDayPicking.randomDraw(pool, count = 6, excluding = emptySet(), random = Random(17))
        assertEquals(listOf("only"), picked.map { it.id })
    }

    /** Draws that land on noise cost an attempt and nothing else: excluding
     *  the only clean photo burns the whole ceiling on screenshots and
     *  thumbnails and returns nothing; excluding the noise hands the clean
     *  photo straight back. Both halves are deterministic. */
    @Test
    fun drawsThatLandOnNoiseCostAnAttemptAndNothingElse() {
        val noisy = (0..7).map {
            meta(
                id = "n$it",
                taken = 1000L + it,
                screenshot = it % 2 == 0,
                width = if (it % 2 == 1) 200 else 4000,
            )
        }
        val clean = meta("clean", taken = 2000)
        val pool = noisy + clean

        val noiseOnly = OnThisDayPicking.randomDraw(pool, count = 1, excluding = setOf("clean"), random = Random(3))
        assertTrue(noiseOnly.isEmpty())

        val cleanOnly = OnThisDayPicking.randomDraw(
            pool,
            count = 1,
            excluding = noisy.map { it.id }.toSet(),
            random = Random(3),
        )
        assertEquals(listOf("clean"), cleanOnly.map { it.id })
    }
}
