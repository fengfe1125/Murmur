package com.sakura.murmur

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.PointerEvent
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.DpOffset
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.sakura.murmur.ui.MurmurTheme
import java.io.File
import kotlin.math.max
import kotlin.math.min
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async

/**
 * A photo the person can open full screen: either one waiting in the composer
 * or one already sent and sitting in the transcript — the Android counterpart
 * of `MurmurPhotoPreview` in `MurmurPhotoView.swift`.
 */
data class MurmurPhotoPreview(val id: String, val file: File)

/**
 * Transcript rows are rebuilt constantly, and decoding a 12-megapixel original
 * inside composition every time is what makes a chat list stutter.  Everything
 * goes through here instead: decoded off the main thread, at the size actually
 * being drawn, and kept until memory gets tight.  The in-flight map folds
 * concurrent loads for the same file and size into one decode, the way the
 * iOS `inFlight` task table does.
 */
object MurmurImageCache {
    /** Big enough to stay sharp on a 3x screen at the transcript's width. */
    const val THUMBNAIL_PIXELS: Int = 900
    /** Enough to survive a few steps of pinch-zoom without turning to mush. */
    const val FULL_SCREEN_PIXELS: Int = 2_600

    private const val CACHE_COUNT_LIMIT = 60

    private val cache = android.util.LruCache<String, Bitmap>(CACHE_COUNT_LIMIT)
    private val inFlight = mutableMapOf<String, Deferred<Bitmap?>>()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun cached(name: String, maximumPixels: Int): Bitmap? = cache.get(key(name, maximumPixels))

    suspend fun image(file: File, maximumPixels: Int): Bitmap? {
        val cacheKey = key(file.name, maximumPixels)
        cache.get(cacheKey)?.let { return it }
        val running = synchronized(inFlight) { inFlight[cacheKey] }
        if (running != null) return runCatching { running.await() }.getOrNull()
        val decoding = scope.async { decode(file, maximumPixels) }
        synchronized(inFlight) { inFlight[cacheKey] = decoding }
        val bitmap = try {
            runCatching { decoding.await() }.getOrNull()
        } finally {
            synchronized(inFlight) { inFlight.remove(cacheKey) }
        }
        if (bitmap != null) cache.put(cacheKey, bitmap)
        return bitmap
    }

    private fun key(name: String, maximumPixels: Int): String = "$name@$maximumPixels"

    /** Long-edge-constrained decode; [ImageDecoder] applies EXIF orientation,
     *  same as the composer preview in [AndroidPhotoLoader]. */
    private fun decode(file: File, maximumPixels: Int): Bitmap? = try {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.absolutePath, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        val longEdge = maxOf(bounds.outWidth, bounds.outHeight)
        val (width, height) = if (longEdge <= maximumPixels) {
            bounds.outWidth to bounds.outHeight
        } else {
            val scale = maximumPixels.toFloat() / longEdge.toFloat()
            maxOf(1, (bounds.outWidth * scale).toInt()) to maxOf(1, (bounds.outHeight * scale).toInt())
        }
        ImageDecoder.decodeBitmap(ImageDecoder.createSource(file)) { decoder, info, _ ->
            if (width < info.size.width || height < info.size.height) {
                decoder.setTargetSize(width, height)
            }
        }
    } catch (error: Throwable) {
        // A missing or corrupt file just shows the placeholder; the row's
        // words still carry the message.
        null
    }
}

/**
 * One photo inside a message bubble.  Sized from the image's own proportions
 * so a panorama and a portrait both look deliberate, and tappable to open the
 * [MurmurPhotoLightbox] — the counterpart of `TranscriptPhoto` in
 * `MurmurPhotoView.swift`.
 */
@Composable
fun TranscriptPhoto(
    file: File,
    onOpen: () -> Unit,
    modifier: Modifier = Modifier,
    maximumWidth: Dp = 232.dp,
    maximumHeight: Dp = 300.dp,
) {
    val colors = MurmurTheme.colors
    var bitmap by remember(file) {
        mutableStateOf(MurmurImageCache.cached(file.name, MurmurImageCache.THUMBNAIL_PIXELS))
    }
    LaunchedEffect(file) {
        if (bitmap == null) {
            bitmap = MurmurImageCache.image(file, MurmurImageCache.THUMBNAIL_PIXELS)
        }
    }

    val displaySize = photoDisplaySize(bitmap, maximumWidth, maximumHeight)
    Box(
        modifier = modifier
            .size(displaySize)
            .clip(RoundedCornerShape(14.dp))
            .background(colors.raisedPaper)
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = null,
                onClick = onOpen,
            ),
        contentAlignment = Alignment.Center,
    ) {
        val current = bitmap
        if (current != null) {
            Image(
                bitmap = current.asImageBitmap(),
                contentDescription = null,
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Crop,
            )
        } else {
            CircularProgressIndicator(color = colors.secondaryInk, modifier = Modifier.size(24.dp))
        }
        Canvas(Modifier.fillMaxSize()) {
            drawRoundRect(
                color = colors.rule.copy(alpha = 0.5f),
                cornerRadius = CornerRadius(14.dp.toPx()),
                style = Stroke(width = 0.5.dp.toPx()),
            )
        }
    }
}

/** iOS `TranscriptPhoto.displaySize`: fit the image inside the maximum box,
 *  falling back to a 4:3 placeholder while the decode is running. */
private fun photoDisplaySize(bitmap: Bitmap?, maximumWidth: Dp, maximumHeight: Dp): DpSize {
    if (bitmap == null || bitmap.width <= 0 || bitmap.height <= 0) {
        return DpSize(maximumWidth, maximumWidth * 0.75f)
    }
    val ratio = bitmap.height.toFloat() / bitmap.width.toFloat()
    val height = minOf(maximumWidth * ratio, maximumHeight)
    val width = minOf(height / ratio, maximumWidth)
    return DpSize(width, height)
}

/** Two-finger zoom factor and centroid shift for one pointer event.  The
 *  math is the same `calculateZoom`/`calculatePan` use, written out because
 *  this Compose version no longer ships those helpers. */
private fun PointerEvent.zoomAndPan(): Pair<Float, Offset> {
    val pressed = changes.filter { it.pressed }
    val previous = changes.filter { it.previousPressed }
    var zoom = 1f
    if (pressed.size >= 2 && previous.size >= 2) {
        val distance = (pressed[1].position - pressed[0].position).getDistance()
        val previousDistance = (previous[1].previousPosition - previous[0].previousPosition).getDistance()
        if (previousDistance > 0f) zoom = distance / previousDistance
    }
    if (pressed.isEmpty() || previous.isEmpty()) return zoom to Offset.Zero
    val centroid = pressed.fold(Offset.Zero) { acc, change -> acc + change.position } / pressed.size.toFloat()
    val previousCentroid = previous.fold(Offset.Zero) { acc, change -> acc + change.previousPosition } / previous.size.toFloat()
    return zoom to (centroid - previousCentroid)
}

/**
 * The photo on its own: pinch to zoom (0.7x–6x), double-tap for 2.4x, drag
 * down to put it away.  The counterpart of `MurmurPhotoLightbox` in
 * `MurmurPhotoView.swift`; the dismissal thresholds are the same ones —
 * 120dp of downward travel, with the backdrop thinning on the way down.
 */
@Composable
fun MurmurPhotoLightbox(preview: MurmurPhotoPreview, onClose: () -> Unit) {
    var image by remember(preview) {
        mutableStateOf(MurmurImageCache.cached(preview.file.name, MurmurImageCache.FULL_SCREEN_PIXELS))
    }
    LaunchedEffect(preview) {
        if (image == null) {
            image = MurmurImageCache.image(preview.file, MurmurImageCache.FULL_SCREEN_PIXELS)
        }
    }

    var scale by remember { mutableStateOf(1f) }
    var committedScale by remember { mutableStateOf(1f) }
    var offset by remember { mutableStateOf(DpOffset.Zero) }
    var committedOffset by remember { mutableStateOf(DpOffset.Zero) }
    val density = LocalDensity.current

    fun reset() {
        scale = 1f
        committedScale = 1f
        offset = DpOffset.Zero
        committedOffset = DpOffset.Zero
    }

    // Only a downward drag on an unzoomed photo dismisses, and the backdrop
    // thins out as it goes so the gesture explains itself halfway through.
    val isZoomed = scale > 1.01f
    val backdropAlpha = if (isZoomed) 1f else 1f - min(max(offset.y.value, 0f) / 600f, 0.55f)

    Dialog(
        onDismissRequest = onClose,
        properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false),
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black.copy(alpha = backdropAlpha)),
            contentAlignment = Alignment.Center,
        ) {
            val current = image
            if (current != null) {
                Image(
                    bitmap = current.asImageBitmap(),
                    contentDescription = "照片",
                    contentScale = ContentScale.Fit,
                    modifier = Modifier
                        .fillMaxSize()
                        .graphicsLayer {
                            scaleX = scale
                            scaleY = scale
                            translationX = with(density) { offset.x.toPx() }
                            translationY = with(density) { offset.y.toPx() }
                        }
                        .pointerInput(preview) {
                            awaitEachGesture {
                                awaitFirstDown()
                                while (true) {
                                    val event = awaitPointerEvent()
                                    if (event.changes.any { it.pressed }) {
                                        val (zoomChange, pan) = event.zoomAndPan()
                                        if (zoomChange != 1f) {
                                            scale = (scale * zoomChange).coerceIn(0.7f, 6f)
                                        }
                                        val panX = with(density) { pan.x.toDp() }
                                        val panY = with(density) { pan.y.toDp() }
                                        if (scale > 1.01f) {
                                            offset = DpOffset(committedOffset.x + panX, committedOffset.y + panY)
                                        } else {
                                            offset = DpOffset(0.dp, maxOf(0.dp, offset.y + panY))
                                        }
                                    }
                                    if (event.changes.none { it.pressed }) {
                                        // Fingers lifted: commit the zoom or
                                        // put the photo back where it was.
                                        if (scale > 1.01f) {
                                            committedScale = scale
                                            committedOffset = offset
                                        } else if (offset.y > 120.dp) {
                                            onClose()
                                        } else {
                                            scale = committedScale
                                            offset = committedOffset
                                        }
                                        break
                                    }
                                }
                            }
                        }
                        .pointerInput(preview) {
                            // Double-tap toggles the same 2.4x the iOS lightbox uses.
                            detectTapGestures(
                                onDoubleTap = {
                                    if (scale > 1.01f) {
                                        reset()
                                    } else {
                                        scale = 2.4f
                                        committedScale = 2.4f
                                        offset = DpOffset.Zero
                                        committedOffset = DpOffset.Zero
                                    }
                                },
                            )
                        },
                )
            } else {
                CircularProgressIndicator(color = Color.White, modifier = Modifier.size(36.dp))
            }
            IconButton(
                onClick = onClose,
                modifier = Modifier
                    .align(Alignment.TopStart)
                    .padding(start = 8.dp, top = 8.dp)
                    .size(44.dp)
                    .testTag("close-photo"),
            ) {
                Box(
                    modifier = Modifier
                        .size(34.dp)
                        .background(Color.White.copy(alpha = 0.18f), CircleShape),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        Icons.Outlined.Close,
                        contentDescription = null,
                        tint = Color.White,
                        modifier = Modifier.size(18.dp),
                    )
                }
            }
        }
    }
}
