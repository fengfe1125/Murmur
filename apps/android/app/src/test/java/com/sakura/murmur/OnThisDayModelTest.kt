package com.sakura.murmur

import android.graphics.Bitmap
import java.time.Instant
import java.time.ZoneId
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The shelf's contract, away from the screen — the four shelf cases from iOS
 * `OnThisDayTests.swift` (`OnThisDayShelfTests`) plus the ask-flip case from
 * the stub section, semantically ported. The archive grouping and
 * Monday-first calendar cases have Android counterparts already living in
 * `MurmurArchiveTest` / `MurmurMonthGrid`; they are not repeated here.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class OnThisDayModelTest {

    private val zone: ZoneId = ZoneId.of("Asia/Shanghai")

    /** Counts what it is asked for, so a top-up that fires twice — or never
     *  stops firing — is visible rather than merely slow. */
    private class CountingLibrary(
        val sameDay: Int,
        val album: Int,
        private val authorization: OnThisDayAuthorization = OnThisDayAuthorization.Authorized,
        private val requestAnswer: OnThisDayAuthorization = authorization,
    ) : OnThisDayLibrary {
        override val needsActivityPermissionRequest: Boolean = false
        var randomCalls = 0

        override suspend fun authorization(): OnThisDayAuthorization = authorization

        override suspend fun requestAuthorization(): OnThisDayAuthorization = requestAnswer

        override suspend fun notePermissionResult(fullAccess: Boolean, partialAccess: Boolean): OnThisDayAuthorization =
            OnThisDayAuthorization.from(fullAccess, partialAccess, askedBefore = true)

        override suspend fun candidates(aroundMillis: Long, yearsBack: Int): List<OnThisDayCandidate> =
            (0 until sameDay).map { offset ->
                OnThisDayCandidate(
                    id = "day-$offset",
                    creationMillis = aroundMillis,
                    origin = OnThisDayOrigin.SameDay(offset + 1),
                )
            }

        override suspend fun randomCandidates(count: Int, excluding: Set<String>): List<OnThisDayCandidate> {
            randomCalls += 1
            return (0 until album)
                .map { "album-$it" }
                .filter { it !in excluding }
                .take(count)
                .map { OnThisDayCandidate(it, 0L, OnThisDayOrigin.Elsewhere) }
        }

        override suspend fun image(candidate: OnThisDayCandidate, targetPixels: Int): Bitmap? = null
    }

    private fun CoroutineScope.makeModel(library: OnThisDayLibrary): OnThisDayModel =
        OnThisDayModel(library, this, zone = zone, now = { Instant.parse("2026-08-30T12:00:00+08:00") })

    /** A day that is blank in every earlier year is not an empty screen: the
     *  shelf falls back to the album, and the card says which it is. */
    @Test
    fun aBlankDayStartsOnTheAlbum() = runTest {
        val library = CountingLibrary(sameDay = 0, album = 20)
        val model = makeModel(library)
        model.refreshAuthorization()
        model.load()
        advanceUntilIdle()

        assertFalse(model.candidates.value.isEmpty())
        assertEquals(OnThisDayOrigin.Elsewhere, model.candidates.value.first().origin)
    }

    /** Past the day's own photos the shelf carries on rather than wrapping:
     *  swiping down from the last one used to land back on the first. */
    @Test
    fun theShelfCarriesOnPastTheDaysOwnPhotos() = runTest {
        val library = CountingLibrary(sameDay = 2, album = 20)
        val model = makeModel(library)
        model.refreshAuthorization()
        model.load()
        advanceUntilIdle()

        repeat(4) {
            model.advance()
            advanceUntilIdle()
        }
        assertEquals(4, model.index.value)
        assertEquals(OnThisDayOrigin.Elsewhere, model.candidates.value[model.index.value].origin)
    }

    /** A library with nothing left to give is asked once and then left alone;
     *  each top-up spends up to two hundred draws inside the scanner. */
    @Test
    fun anExhaustedLibraryIsNotAskedAgain() = runTest {
        val library = CountingLibrary(sameDay = 1, album = 0)
        val model = makeModel(library)
        model.refreshAuthorization()
        model.load()
        advanceUntilIdle()

        repeat(5) {
            model.advance()
            advanceUntilIdle()
        }
        assertEquals(1, library.randomCalls)
        // And the card stays where it is rather than looping.
        assertEquals(0, model.index.value)
    }

    /** The album never hands back a photo that is already on the shelf. */
    @Test
    fun topUpsExcludeWhatIsAlreadyOnTheShelf() = runTest {
        val library = CountingLibrary(sameDay = 2, album = 20)
        val model = makeModel(library)
        model.refreshAuthorization()
        model.load()
        advanceUntilIdle()
        repeat(6) {
            model.advance()
            advanceUntilIdle()
        }

        val identifiers = model.candidates.value.map { it.id }
        assertEquals(identifiers.size, identifiers.toSet().size)
    }

    /** The ask door: a notDetermined library that flips to authorized on the
     *  request — the stub's `--murmur-stub-onthisday-ask` path — opens the
     *  shelf and loads it in the same gesture. */
    @Test
    fun askingFlipsTheDoorAndLoadsTheShelf() = runTest {
        val library = CountingLibrary(
            sameDay = 2,
            album = 9,
            authorization = OnThisDayAuthorization.NotDetermined,
            requestAnswer = OnThisDayAuthorization.Authorized,
        )
        val model = makeModel(library)
        model.refreshAuthorization()
        advanceUntilIdle()
        assertEquals(OnThisDayAuthorization.NotDetermined, model.authorization.value)

        model.requestAuthorization()
        advanceUntilIdle()

        assertEquals(OnThisDayAuthorization.Authorized, model.authorization.value)
        assertTrue(model.candidates.value.isNotEmpty())
    }

    /** A load resets the shelf from the top: index back at zero and the
     *  exhaustion latch released, so a later swipe asks the library again. */
    @Test
    fun loadResetsIndexAndExhaustion() = runTest {
        val library = CountingLibrary(sameDay = 1, album = 0)
        val model = makeModel(library)
        model.refreshAuthorization()
        model.load()
        advanceUntilIdle()
        assertEquals(1, library.randomCalls)

        repeat(3) {
            model.advance()
            advanceUntilIdle()
        }
        assertEquals(0, model.index.value)

        model.load()
        advanceUntilIdle()
        assertEquals(0, model.index.value)
        assertEquals(2, library.randomCalls)
    }
}
