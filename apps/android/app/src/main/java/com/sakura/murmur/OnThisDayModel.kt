package com.sakura.murmur

import android.graphics.Bitmap
import java.time.Instant
import java.time.ZoneId
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * The 当年今日 shelf — the Android counterpart of iOS `OnThisDayModel`
 * (`@MainActor final class OnThisDayModel: ObservableObject`). One card at a
 * time: the day's own photos first, then the album behind them, never
 * looping. The view owns no fetching logic; it only reads these flows.
 *
 * State lives in [MutableStateFlow]s rather than Compose state so the whole
 * contract — blank-day fallback, carry-on, exhaustion — is testable on the
 * JVM with an injected library, the way `PhotoRoomModel`'s is.
 */
class OnThisDayModel(
    private val library: OnThisDayLibrary,
    private val scope: CoroutineScope,
    /** The device's own calendar: filing days and day windows are computed
     *  in this zone, never UTC — same injectable-zone pattern as
     *  [MurmurArchive]. The view reads it for the date line. */
    val zone: ZoneId = ZoneId.systemDefault(),
    private val now: () -> Instant = { Instant.now() },
    private val yearsBack: Int = 5,
) {
    private val _authorization = MutableStateFlow(OnThisDayAuthorization.NotDetermined)
    val authorization: StateFlow<OnThisDayAuthorization> = _authorization

    private val _candidates = MutableStateFlow<List<OnThisDayCandidate>>(emptyList())
    val candidates: StateFlow<List<OnThisDayCandidate>> = _candidates

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading

    /** Index into [candidates]; the viewer is one card, not a grid. */
    private val _index = MutableStateFlow(0)
    val index: StateFlow<Int> = _index

    private val _currentImage = MutableStateFlow<Bitmap?>(null)
    val currentImage: StateFlow<Bitmap?> = _currentImage

    /** Swipe-up is not an offer until there is actually a photo to send. */
    val canSend: Boolean
        get() = _currentImage.value != null && _candidates.value.isNotEmpty()

    /** Whether the library answers [OnThisDayLibrary.requestAuthorization]
     *  in place (the stub) or needs the activity-owned dialog (MediaStore).
     *  The gate button branches on it. */
    val libraryNeedsActivityPermissionRequest: Boolean
        get() = library.needsActivityPermissionRequest

    /** How many photos stay queued behind the one on screen. The shelf tops
     *  itself up from that far out so 下滑 never waits on a fetch. */
    private val reserve = 3
    /** One top-up. Small enough that a person who swipes twice and leaves has
     *  not made the library do work for nothing. */
    private val randomBatch = 6

    private var imageGeneration = 0
    /** The one top-up in flight, so two swipes cannot start two fetches and a
     *  swipe that arrives mid-fetch waits for it instead of being dropped. */
    private var topUpJob: Job? = null
    private val topUpLock = Any()
    /** The library has no more to give. Asking again would only spend two
     *  hundred draws to be told the same thing. */
    @Volatile
    private var libraryExhausted = false

    fun refreshAuthorization() {
        scope.launch { _authorization.value = library.authorization() }
    }

    /** The one place the stub's system prompt path is triggered from: the
     *  person pressed the button under the explanation. On a real device the
     *  activity-owned dialog reports through [onPermissionResult]; this path
     *  exists for parity with iOS `requestAuthorization()` and the stub. */
    fun requestAuthorization() {
        scope.launch {
            _authorization.value = library.requestAuthorization()
            if (_authorization.value == OnThisDayAuthorization.Authorized) load()
        }
    }

    /** The real-device door: the UI's permission launcher finished, full or
     *  partial. Flips the shelf open when the grant lands. */
    fun onPermissionResult(fullAccess: Boolean, partialAccess: Boolean) {
        scope.launch {
            _authorization.value = library.notePermissionResult(fullAccess, partialAccess)
            if (_authorization.value == OnThisDayAuthorization.Authorized) load()
        }
    }

    fun load() {
        scope.launch {
            if (_authorization.value != OnThisDayAuthorization.Authorized) return@launch
            _isLoading.value = true
            imageGeneration += 1
            libraryExhausted = false
            _candidates.value = library.candidates(now().toEpochMilli(), yearsBack)
            _index.value = 0
            // A blank day is not a dead end. With nothing from this day in
            // any earlier year the shelf starts on album photos instead, and
            // each card says which it is rather than dressing a Tuesday up as
            // an anniversary.
            topUpIfNeeded()
            _isLoading.value = false
            showCurrent()
        }
    }

    /** 下滑换一张. The shelf runs forward and never wraps: past the last photo
     *  from this day it carries on with the album, so 下滑 always has somewhere
     *  to go until the library itself runs out. */
    fun advance() {
        scope.launch {
            if (_candidates.value.isEmpty()) return@launch
            if (_index.value + 1 >= _candidates.value.size) topUp()
            if (_index.value + 1 >= _candidates.value.size) return@launch
            _index.value += 1
            showCurrent()
            topUpIfNeeded()
        }
    }

    private suspend fun topUpIfNeeded() {
        if (_candidates.value.size - _index.value - 1 >= reserve) return
        topUp()
    }

    /** Appends the next handful of album photos. A second caller arriving
     *  mid-fetch waits for the same job rather than starting another — and,
     *  because the append happens inside that job, waking up after it means
     *  the photos are already on the shelf. */
    private suspend fun topUp() {
        val existing = synchronized(topUpLock) { topUpJob }
        if (existing != null) {
            existing.join()
            return
        }
        if (libraryExhausted) return
        val known = _candidates.value.map { it.id }.toSet()
        val job = scope.launch {
            val more = library.randomCandidates(randomBatch, known)
            if (more.isEmpty()) {
                libraryExhausted = true
            } else {
                _candidates.value = _candidates.value + more
            }
        }
        synchronized(topUpLock) { topUpJob = job }
        job.join()
        synchronized(topUpLock) { topUpJob = null }
    }

    private suspend fun showCurrent() {
        val list = _candidates.value
        if (_index.value !in list.indices) {
            _currentImage.value = null
            return
        }
        imageGeneration += 1
        val generation = imageGeneration
        val candidate = list[_index.value]
        val image = library.image(candidate, MurmurImageCache.FULL_SCREEN_PIXELS)
        // A stale answer from a slower fetch must not overwrite the card the
        // person is already looking at.
        if (generation == imageGeneration) _currentImage.value = image
    }
}
