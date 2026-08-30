package com.sakura.murmur

import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.ImageDecoder
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import androidx.core.content.ContextCompat
import java.time.Instant
import java.time.ZoneId
import kotlin.math.min
import kotlin.math.max
import kotlin.random.Random
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

/**
 * 当年今日 (OnThisDay), the album side — the Android counterpart of iOS
 * `MurmurOnThisDay.swift` (`OnThisDayAuthorization` / `OnThisDayCandidate` /
 * `OnThisDayLibrary` / `PhotoKitOnThisDayLibrary`).
 *
 * The four doors this feature can be standing in front of. `Limited` is a
 * designed state, not an error: on API 34+ the person can hand Murmur a
 * hand-picked slice of the library, and with that slice the feature cannot
 * tell the truth about what was photographed that day — the gate says so
 * instead of showing an empty day. A device policy that blocks the grant is
 * the same door as a refusal, exactly as iOS maps `.restricted` to `.denied`.
 */
enum class OnThisDayAuthorization {
    NotDetermined,
    Denied,
    Limited,
    Authorized,
    ;

    companion object {
        /**
         * The pure mapping behind the four-state door — the Android
         * counterpart of `OnThisDayAuthorization.init(_ status:)`. Android
         * exposes no single photo-authorization value, so the door is derived:
         * full access wins, a user-selected slice is `Limited`, a request that
         * already happened without a grant is `Denied`, and nothing answered
         * yet is `NotDetermined`.
         */
        fun from(fullAccess: Boolean, partialAccess: Boolean, askedBefore: Boolean): OnThisDayAuthorization =
            when {
                fullAccess -> Authorized
                partialAccess -> Limited
                askedBefore -> Denied
                else -> NotDetermined
            }
    }
}

/**
 * One photo on the shelf. Only the MediaStore id crosses onto the screen —
 * rows are queried, filtered and ranked on [Dispatchers.IO] and only the
 * decoded browsing bitmap ever reaches the view state.
 */
data class OnThisDayCandidate(
    val id: String,
    val creationMillis: Long,
    val origin: OnThisDayOrigin,
)

/**
 * Why this photo is on the shelf. The card says which it is, because
 * 「去年的今天」 over a picture from an ordinary Tuesday is a lie, and a small
 * one told about someone's own memory is not a small one.
 */
sealed interface OnThisDayOrigin {
    /** This day (±1) in an earlier year. */
    data class SameDay(val yearsAgo: Int) : OnThisDayOrigin

    /** Drawn at random from the library: the day is blank in every earlier
     *  year, or its photos ran out. */
    data object Elsewhere : OnThisDayOrigin
}

/**
 * The album read behind 当年今日. Mirrors iOS `OnThisDayLibrary` member for
 * member. Android has no process-wide photo-authorization request — the
 * system dialog must be launched from an activity — so [requestAuthorization]
 * exists for the stub/iOS-parity path (and tests), while the real device path
 * reports the activity result through [notePermissionResult]. Both end at the
 * same four-state door.
 */
interface OnThisDayLibrary {
    /**
     * Whether [requestAuthorization] needs an activity-owned system dialog.
     * The MediaStore implementation does (the UI drives its permission
     * launcher and reports via [notePermissionResult]); the DEBUG stub
     * answers in place, so the gate button can go straight through
     * [requestAuthorization] exactly as on iOS.
     */
    val needsActivityPermissionRequest: Boolean

    suspend fun authorization(): OnThisDayAuthorization

    /**
     * Asks for access. Stub libraries answer directly; the MediaStore
     * implementation returns the current door, because on Android the request
     * dialog belongs to an activity — the UI owns the permission launcher and
     * reports the outcome via [notePermissionResult].
     */
    suspend fun requestAuthorization(): OnThisDayAuthorization

    /** Records the outcome of the activity-owned permission dialog and
     *  returns the door it lands on. */
    suspend fun notePermissionResult(fullAccess: Boolean, partialAccess: Boolean): OnThisDayAuthorization

    /**
     * Ranked best-first for this day: the first card is the one the feature
     *  is judged by, so "first in the query" is not an acceptable answer.
     */
    suspend fun candidates(aroundMillis: Long, yearsBack: Int): List<OnThisDayCandidate>

    /**
     * Photos from anywhere in the library, for when this day is blank in
     * every earlier year or its photos have all been swiped past. The shelf
     * carries on rather than looping, so 下滑 always has somewhere to go.
     * Returns fewer than asked — or none — when the library has no more.
     */
    suspend fun randomCandidates(count: Int, excluding: Set<String>): List<OnThisDayCandidate>

    /**
     * Memory only. Browsing pixels never touch the disk: they live in an
     * LruCache and die with the process, so the photo library gains no second
     * lifecycle inside Murmur. A file appears only when the person swipes a
     * photo up into the conversation, and that file goes through
     * [PhotoLoader]'s swept temporary prefix like every other upload.
     */
    suspend fun image(candidate: OnThisDayCandidate, targetPixels: Int): Bitmap?
}

/**
 * Raw album metadata as MediaStore returns it, before any judgement. The
 * scanner maps rows to this and everything after — window filtering, ranking,
 * random sampling — is a pure function of this list, so the whole decision
 * core runs on the JVM in tests. [screenshot] is the Android stand-in for
 * iOS `mediaSubtypes.photoScreenshot`: MediaStore carries no screenshot flag,
 * so the scanner marks rows whose bucket/path says screenshot (English and
 * 截屏) and the pure core drops them.
 */
data class OnThisDayPhotoMeta(
    val id: String,
    val takenMillis: Long,
    val width: Int,
    val height: Int,
    val favorite: Boolean,
    val screenshot: Boolean,
)

/**
 * The decision core of 当年今日: day windows, ranking and random draws, all
 * pure. Every constant is the iOS one — `minimumEdgePixels = 600`,
 * `perWindowLimit = 60`, `keptPerYear = 6`, `randomDrawCeiling = 200` — and
 * the orderings are the same: 收藏 > 面积 > 时间 for the day windows.
 */
object OnThisDayPicking {
    /** Small enough to throw away stickers and reaction-meme saves; real
     *  camera output is never this small on a modern phone. */
    const val MINIMUM_EDGE_PIXELS: Int = 600
    const val PER_WINDOW_LIMIT: Int = 60
    const val KEPT_PER_YEAR: Int = 6
    const val RANDOM_DRAW_CEILING: Int = 200

    /**
     * The [startMillis, endMillis) window for one earlier year: the whole of
     * the same day ±1, in the device's own calendar — the counterpart of the
     * iOS `startOfDay(-1) ..< startOfDay(+2)` window. Leap day lands on
     * February 28 in non-leap years, the way `Calendar.date(byAdding: .year)`
     * lands it.
     */
    fun windowBounds(aroundMillis: Long, yearsAgo: Int, zone: ZoneId): Pair<Long, Long> {
        val sameDay = Instant.ofEpochMilli(aroundMillis).atZone(zone).toLocalDate()
            .minusYears(yearsAgo.toLong())
        val start = sameDay.minusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
        val end = sameDay.plusDays(2).atStartOfDay(zone).toInstant().toEpochMilli()
        return start to end
    }

    /**
     * Ranks one year's window best-first and keeps the top [keptPerYear]:
     * 收藏 first, then the larger area, then the newer timestamp. The window
     *  limit is spent in time order first (the iOS fetch is `creationDate
     *  DESC` with `fetchLimit`), so a photo outside the newest
     *  [perWindowLimit] never reaches the ranking — "the fetchLimit was spent
     *  on them" is a screenshot problem, but it is also a ranking rule.
     */
    fun pickFromWindow(
        metas: List<OnThisDayPhotoMeta>,
        yearsAgo: Int,
        minimumEdgePixels: Int = MINIMUM_EDGE_PIXELS,
        keptPerYear: Int = KEPT_PER_YEAR,
        perWindowLimit: Int = PER_WINDOW_LIMIT,
    ): List<OnThisDayCandidate> =
        metas.asSequence()
            .filter { !it.screenshot }
            .filter { min(it.width, it.height) >= minimumEdgePixels }
            .sortedByDescending { it.takenMillis }
            .take(perWindowLimit)
            .sortedWith(
                compareByDescending<OnThisDayPhotoMeta> { it.favorite }
                    .thenByDescending { it.width.toLong() * it.height.toLong() }
                    .thenByDescending { it.takenMillis },
            )
            .take(keptPerYear)
            .map { OnThisDayCandidate(it.id, it.takenMillis, OnThisDayOrigin.SameDay(yearsAgo)) }
            .toList()

    /**
     * How many index draws one top-up may spend before giving up. A draw that
     * lands on a screenshot, a too-small photo or a photo already on the
     * shelf costs one attempt and nothing else. The ceiling shrinks with the
     * library so a hand-sized album is not asked two hundred questions.
     */
    fun randomDraw(
        metas: List<OnThisDayPhotoMeta>,
        count: Int,
        excluding: Set<String>,
        random: Random = Random,
        minimumEdgePixels: Int = MINIMUM_EDGE_PIXELS,
        drawCeiling: Int = RANDOM_DRAW_CEILING,
    ): List<OnThisDayCandidate> {
        if (count <= 0 || metas.isEmpty()) return emptyList()
        val seen = excluding.toMutableSet()
        val picked = mutableListOf<OnThisDayCandidate>()
        val ceiling = min(drawCeiling, max(metas.size * 2, count * 8))
        var draws = 0
        while (picked.size < count && draws < ceiling) {
            draws += 1
            val meta = metas[random.nextInt(metas.size)]
            if (meta.id in seen) continue
            if (meta.screenshot) continue
            if (min(meta.width, meta.height) < minimumEdgePixels) continue
            seen.add(meta.id)
            picked.add(OnThisDayCandidate(meta.id, meta.takenMillis, OnThisDayOrigin.Elsewhere))
        }
        return picked
    }
}

/**
 * MediaStore, on [Dispatchers.IO] — the Android counterpart of
 * `PhotoKitOnThisDayLibrary`. There is no Moments API to lean on either: the
 * query is N explicit `date_taken` windows — this day ±1 in each earlier year —
 * each bounded by `QUERY_ARG_LIMIT` so a dense year costs one capped query,
 * never a table walk. Random sampling reads only the cheap metadata columns
 * (ids, timestamps, dimensions, favorite, bucket) and draws indices in
 * memory: a library of fifty thousand photos is sampled, never enumerated
 * into full assets.
 *
 * Deliberate platform differences from the PhotoKit original, kept honest:
 *  - iCloud-synced and shared-album exclusion has no MediaStore equivalent —
 *    the provider sees this device's own library only. No filter needed, and
 *    no false exclusion of an OEM gallery-synced file.
 *  - Burst representative frames are an iOS concept; Android bursts are plain
 *    rows.
 *  - Screenshots are detected by bucket/path name (localized), not a flag.
 */
class MediaStoreOnThisDayLibrary(
    context: Context,
    private val zone: ZoneId = ZoneId.systemDefault(),
) : OnThisDayLibrary {

    private val appContext = context.applicationContext
    private val resolver = appContext.contentResolver
    private val prefs = appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    override val needsActivityPermissionRequest: Boolean = true

    /** Browsing copies, keyed by "id@pixels" and capped at 12 — the NSCache
     *  `countLimit = 12`, keeping the whole shelf well under a memory
     *  warning. Memory only; nothing here touches disk. */
    private val images = android.util.LruCache<String, Bitmap>(IMAGE_CACHE_LIMIT)

    override suspend fun authorization(): OnThisDayAuthorization = OnThisDayAuthorization.from(
        fullAccess = hasPermission(fullAccessPermission()),
        partialAccess = hasPermission(PERMISSION_USER_SELECTED),
        askedBefore = prefs.getBoolean(KEY_ASKED, false),
    )

    /**
     * The activity-owned dialog is the real path on Android (see
     * [OnThisDayLibrary.requestAuthorization]); without a launcher in hand
     * this answers with the current door instead of pretending to ask.
     */
    override suspend fun requestAuthorization(): OnThisDayAuthorization = authorization()

    override suspend fun notePermissionResult(fullAccess: Boolean, partialAccess: Boolean): OnThisDayAuthorization {
        prefs.edit().putBoolean(KEY_ASKED, true).apply()
        return OnThisDayAuthorization.from(fullAccess, partialAccess, askedBefore = true)
    }

    override suspend fun candidates(aroundMillis: Long, yearsBack: Int): List<OnThisDayCandidate> =
        withContext(Dispatchers.IO) {
            val picked = mutableListOf<OnThisDayCandidate>()
            for (yearsAgo in 1..yearsBack) {
                val (start, end) = OnThisDayPicking.windowBounds(aroundMillis, yearsAgo, zone)
                val metas = queryWindow(start, end)
                picked += OnThisDayPicking.pickFromWindow(metas, yearsAgo)
            }
            picked
        }

    override suspend fun randomCandidates(count: Int, excluding: Set<String>): List<OnThisDayCandidate> =
        withContext(Dispatchers.IO) {
            OnThisDayPicking.randomDraw(queryAllMetadata(), count, excluding)
        }

    /**
     * One photo, decoded at the size the card actually draws. Long enough for
     * a cloud-original over a thin connection, short enough that a stalled
     * read does not read as a hung app — the 20-second timeout is the iOS one.
     */
    override suspend fun image(candidate: OnThisDayCandidate, targetPixels: Int): Bitmap? =
        withContext(Dispatchers.IO) {
            val key = "${candidate.id}@$targetPixels"
            images.get(key)?.let { return@withContext it }
            val id = candidate.id.toLongOrNull() ?: return@withContext null
            val uri = android.content.ContentUris.withAppendedId(
                MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                id,
            )
            val decoded = try {
                withTimeoutOrNull(IMAGE_TIMEOUT_MILLIS) {
                    ImageDecoder.decodeBitmap(ImageDecoder.createSource(resolver, uri)) { decoder, info, _ ->
                        val longEdge = max(info.size.width, info.size.height)
                        if (longEdge > targetPixels) {
                            val scale = targetPixels.toFloat() / longEdge.toFloat()
                            decoder.setTargetSize(
                                max(1, (info.size.width * scale).toInt()),
                                max(1, (info.size.height * scale).toInt()),
                            )
                        }
                    }
                }
            } catch (error: Throwable) {
                // A row that vanished between the query and the decode, or a
                // codec the device refuses: the card shows the spinner until
                // the person swipes on.
                null
            }
            decoded?.let { images.put(key, it) }
            decoded
        }

    // ---- Queries --------------------------------------------------------------

    /** The metadata columns every query shares. `WIDTH`/`HEIGHT` come back 0
     *  for rows the provider never measured; the pure core filters those out
     *  with the short-edge rule, same as a genuinely tiny photo. */
    private fun projection(): Array<String> = listOfNotNull(
        MediaStore.Images.Media._ID,
        MediaStore.Images.Media.DATE_TAKEN,
        MediaStore.Images.Media.WIDTH,
        MediaStore.Images.Media.HEIGHT,
        MediaStore.Images.Media.IS_FAVORITE,
        MediaStore.Images.Media.BUCKET_DISPLAY_NAME,
        MediaStore.Images.Media.DATA,
        if (Build.VERSION.SDK_INT >= 29) MediaStore.Images.Media.IS_TRASHED else null,
        if (Build.VERSION.SDK_INT >= 29) MediaStore.Images.Media.IS_PENDING else null,
    ).toTypedArray()

    private fun baseSelection(): String = buildString {
        append("${MediaStore.Images.Media.DATE_TAKEN} IS NOT NULL")
        if (Build.VERSION.SDK_INT >= 29) {
            append(" AND ${MediaStore.Images.Media.IS_TRASHED} = 0")
            append(" AND ${MediaStore.Images.Media.IS_PENDING} = 0")
        }
    }

    private fun queryWindow(startMillis: Long, endMillis: Long): List<OnThisDayPhotoMeta> {
        val selection =
            "${baseSelection()} AND ${MediaStore.Images.Media.DATE_TAKEN} >= ? " +
                "AND ${MediaStore.Images.Media.DATE_TAKEN} < ?"
        return query(
            selection = selection,
            selectionArgs = arrayOf(startMillis.toString(), endMillis.toString()),
            limit = OnThisDayPicking.PER_WINDOW_LIMIT,
        )
    }

    private fun queryAllMetadata(): List<OnThisDayPhotoMeta> = query(
        selection = baseSelection(),
        selectionArgs = emptyArray(),
        limit = null,
    )

    /**
     * One bounded query, mapped straight to metadata. The LIMIT travels in
     * the query-args bundle (API 26+), not the sort order, so a dense window
     * cannot accidentally pull a whole year.
     */
    private fun query(
        selection: String,
        selectionArgs: Array<String>,
        limit: Int?,
    ): List<OnThisDayPhotoMeta> {
        val bundle = Bundle().apply {
            putString(ContentResolver_QUERY_ARG_SQL_SELECTION, selection)
            putStringArray(ContentResolver_QUERY_ARG_SQL_SELECTION_ARGS, selectionArgs)
            putString(ContentResolver_QUERY_ARG_SQL_SORT_ORDER, "${MediaStore.Images.Media.DATE_TAKEN} DESC")
            if (limit != null) putInt(ContentResolver_QUERY_ARG_LIMIT, limit)
        }
        val cursor = try {
            resolver.query(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, projection(), bundle, null)
        } catch (error: Throwable) {
            // A revoked permission mid-read, an OEM provider that rejects the
            // bundle form: the shelf just has less on it.
            return emptyList()
        }
        cursor.use { rows ->
            if (rows == null) return emptyList()
            val idCol = rows.getColumnIndexOrThrow(MediaStore.Images.Media._ID)
            val takenCol = rows.getColumnIndexOrThrow(MediaStore.Images.Media.DATE_TAKEN)
            val widthCol = rows.getColumnIndexOrThrow(MediaStore.Images.Media.WIDTH)
            val heightCol = rows.getColumnIndexOrThrow(MediaStore.Images.Media.HEIGHT)
            val favoriteCol = rows.getColumnIndexOrThrow(MediaStore.Images.Media.IS_FAVORITE)
            val bucketCol = rows.getColumnIndexOrThrow(MediaStore.Images.Media.BUCKET_DISPLAY_NAME)
            val dataCol = rows.getColumnIndexOrThrow(MediaStore.Images.Media.DATA)
            val result = mutableListOf<OnThisDayPhotoMeta>()
            while (rows.moveToNext()) {
                val bucket = rows.getString(bucketCol).orEmpty()
                val path = rows.getString(dataCol).orEmpty()
                result += OnThisDayPhotoMeta(
                    id = rows.getLong(idCol).toString(),
                    takenMillis = rows.getLong(takenCol),
                    width = rows.getInt(widthCol),
                    height = rows.getInt(heightCol),
                    favorite = rows.getInt(favoriteCol) != 0,
                    screenshot = isScreenshotPath(bucket) || isScreenshotPath(path),
                )
            }
            return result
        }
    }

    private fun isScreenshotPath(value: String): Boolean {
        val lower = value.lowercase()
        return "screenshot" in lower || "截屏" in value
    }

    // ---- Permissions ------------------------------------------------------------

    private fun fullAccessPermission(): String =
        if (Build.VERSION.SDK_INT >= 33) PERMISSION_READ_MEDIA_IMAGES else PERMISSION_READ_EXTERNAL_STORAGE

    private fun hasPermission(permission: String): Boolean =
        if (permission == PERMISSION_USER_SELECTED && Build.VERSION.SDK_INT < 34) {
            false
        } else {
            ContextCompat.checkSelfPermission(appContext, permission) == PackageManager.PERMISSION_GRANTED
        }

    companion object {
        private const val PREFS_NAME = "onthisday"
        private const val KEY_ASKED = "permission_asked"

        /** API 33+: runtime media permission. API 28–32: the storage read,
         *  declared with `maxSdkVersion = 32` in the manifest. */
        private const val PERMISSION_READ_MEDIA_IMAGES = android.Manifest.permission.READ_MEDIA_IMAGES
        private const val PERMISSION_READ_EXTERNAL_STORAGE = android.Manifest.permission.READ_EXTERNAL_STORAGE

        /** API 34+ partial photo access — the iOS `.limited` door. */
        private const val PERMISSION_USER_SELECTED = android.Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED

        private const val IMAGE_CACHE_LIMIT = 12
        private const val IMAGE_TIMEOUT_MILLIS = 20_000L

        // android.content.ContentResolver query-arg keys, spelled out to keep
        // the query in one place; these are the platform constants.
        private const val ContentResolver_QUERY_ARG_SQL_SELECTION = "android:query-arg-sql-selection"
        private const val ContentResolver_QUERY_ARG_SQL_SELECTION_ARGS = "android:query-arg-sql-selection-args"
        private const val ContentResolver_QUERY_ARG_SQL_SORT_ORDER = "android:query-arg-sql-sort-order"
        private const val ContentResolver_QUERY_ARG_LIMIT = "android:query-arg-limit"
    }
}

/**
 * Where the default library comes from — the Android counterpart of iOS
 * `OnThisDayLibraryResolver`. Debug builds can layer the intent-flag stub on
 * top (see `OnThisDayDebug.kt` in the debug source set); release always gets
 * MediaStore.
 */
object OnThisDayLibraryResolver {
    fun makeDefault(context: Context): OnThisDayLibrary = MediaStoreOnThisDayLibrary(context)
}
