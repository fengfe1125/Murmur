package com.sakura.murmur

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import java.time.Instant
import java.time.ZoneId

/**
 * The DEBUG side of 当年今日 — the Android counterpart of the iOS
 * `#if DEBUG` stub in `MurmurOnThisDay.swift`. Debug builds resolve the
 * library through [makeDefault] with the intent-flag stub layered on top;
 * release builds only ever see [OnThisDayLibraryResolver.makeDefault].
 *
 * The shell reads the flags once from its launching intent and hands them
 * here:
 * ```
 * val flags = OnThisDayDebug.stubFlags(activity.intent)
 * val library = if (BuildConfig.DEBUG) {
 *     OnThisDayLibraryResolver.makeDefault(context, flags)
 * } else {
 *     OnThisDayLibraryResolver.makeDefault(context)
 * }
 * ```
 * (Debug-only call sites compile against the debug source set; release has
 * only the one-argument resolver.)
 */
object OnThisDayDebug {
    /** The `--murmur-stub-onthisday*` extras a launch intent carries. */
    fun stubFlags(intent: Intent?): Set<String> =
        intent?.extras?.keySet()
            ?.filter { it.startsWith("--murmur-stub-onthisday") }
            ?.toSet()
            .orEmpty()
}

/**
 * Debug resolver: the stub answers first, MediaStore answers when no stub
 * flag is present — the same layering as iOS `OnThisDayLibraryResolver`.
 */
fun OnThisDayLibraryResolver.makeDefault(context: Context, stubFlags: Set<String>): OnThisDayLibrary =
    StubOnThisDayLibrary.fromFlags(stubFlags) ?: MediaStoreOnThisDayLibrary(context)

/**
 * `--murmur-stub-onthisday`          → authorized, three photos from this day
 * `--murmur-stub-onthisday-ask`      → notDetermined; the button flips it
 * `--murmur-stub-onthisday-denied`   → denied
 * `--murmur-stub-onthisday-limited`  → limited (the entry hides behind the gate)
 * `--murmur-stub-onthisday-empty`    → blank day, album photos behind it
 * `--murmur-stub-onthisday-barren`   → authorized, and nothing anywhere
 */
internal class StubOnThisDayLibrary private constructor(
    private val state: OnThisDayAuthorization,
    private val shelfSize: Int,
    private val albumSize: Int,
) : OnThisDayLibrary {

    override val needsActivityPermissionRequest: Boolean = false

    override suspend fun authorization(): OnThisDayAuthorization = state

    override suspend fun requestAuthorization(): OnThisDayAuthorization =
        if (state == OnThisDayAuthorization.NotDetermined) {
            OnThisDayAuthorization.Authorized
        } else {
            state
        }

    override suspend fun notePermissionResult(fullAccess: Boolean, partialAccess: Boolean): OnThisDayAuthorization =
        OnThisDayAuthorization.from(fullAccess, partialAccess, askedBefore = true)

    override suspend fun candidates(aroundMillis: Long, yearsBack: Int): List<OnThisDayCandidate> {
        val zone = ZoneId.systemDefault()
        return (0 until shelfSize).map { offset ->
            val yearsAgo = offset + 1
            val day = Instant.ofEpochMilli(aroundMillis).atZone(zone).minusYears(yearsAgo.toLong())
            OnThisDayCandidate(
                id = "stub-$offset",
                creationMillis = day.toInstant().toEpochMilli(),
                origin = OnThisDayOrigin.SameDay(yearsAgo),
            )
        }
    }

    override suspend fun randomCandidates(count: Int, excluding: Set<String>): List<OnThisDayCandidate> {
        val zone = ZoneId.systemDefault()
        val now = Instant.now()
        return (0 until albumSize)
            .map { "stub-album-$it" }
            .filter { it !in excluding }
            .take(count)
            .mapIndexed { index, identifier ->
                val days = ((index + 1) * 2654435761L % 900 + 30).toLong()
                OnThisDayCandidate(
                    id = identifier,
                    creationMillis = now.atZone(zone).minusDays(days).toInstant().toEpochMilli(),
                    origin = OnThisDayOrigin.Elsewhere,
                )
            }
    }

    override suspend fun image(candidate: OnThisDayCandidate, targetPixels: Int): Bitmap {
        val colors = intArrayOf(0xFF5AC8FA.toInt(), 0xFF5856D6.toInt(), 0xFFFF9500.toInt())
        val index = (abs(candidate.id.hashCode()) % colors.size)
        return Bitmap.createBitmap(900, 1200, Bitmap.Config.ARGB_8888).apply {
            eraseColor(colors[index])
        }
    }

    private fun abs(value: Int): Int = if (value == Int.MIN_VALUE) 0 else kotlin.math.abs(value)

    companion object {
        fun fromFlags(flags: Set<String>): StubOnThisDayLibrary? = when {
            "--murmur-stub-onthisday" in flags ->
                StubOnThisDayLibrary(OnThisDayAuthorization.Authorized, shelfSize = 3, albumSize = 9)
            "--murmur-stub-onthisday-ask" in flags ->
                StubOnThisDayLibrary(OnThisDayAuthorization.NotDetermined, shelfSize = 3, albumSize = 9)
            "--murmur-stub-onthisday-denied" in flags ->
                StubOnThisDayLibrary(OnThisDayAuthorization.Denied, shelfSize = 0, albumSize = 0)
            "--murmur-stub-onthisday-limited" in flags ->
                StubOnThisDayLibrary(OnThisDayAuthorization.Limited, shelfSize = 0, albumSize = 0)
            "--murmur-stub-onthisday-empty" in flags ->
                StubOnThisDayLibrary(OnThisDayAuthorization.Authorized, shelfSize = 0, albumSize = 9)
            "--murmur-stub-onthisday-barren" in flags ->
                StubOnThisDayLibrary(OnThisDayAuthorization.Authorized, shelfSize = 0, albumSize = 0)
            else -> null
        }
    }
}
