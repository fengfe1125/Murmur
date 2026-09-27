package com.sakura.murmur.ui

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sakura.murmur.BuildConfig
import com.sakura.murmur.OnThisDayAuthorization
import com.sakura.murmur.OnThisDayCandidate
import com.sakura.murmur.OnThisDayModel
import com.sakura.murmur.OnThisDayOrigin
import com.sakura.murmur.PhotoRoomModel
import java.time.Instant
import java.time.ZoneId
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.PI
import kotlin.math.sin
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** Debug-only knob for the dissolve window — the Android counterpart of the
 *  iOS `--murmur-slow-dissolve` launch argument. The shell (or a UI test)
 *  sets this before the cover opens; release builds never read it. */
object OnThisDayDebugTuning {
    @Volatile
    var slowDissolve: Boolean = false
}

/** 当年今日, both halves, inside one cover — ported from iOS
 *  `OnThisDayFlowView`. The browser and the room are not two screens that
 *  happen to follow each other: presenting the room as a second cover would
 *  put a system transition through the middle of the send-off, so the two
 *  live in one cover and cross-fade under the photo.
 *
 * `makeRoom` is the Android counterpart of iOS `makePhotoRoom(image:)`: it
 *  turns the swiped-up bitmap into a [PhotoRoomModel] (the shell borrows the
 *  session's api/photoLoader for it, and stages the bitmap under the swept
 *  temporary prefix on the way in).
 */
@Composable
fun OnThisDayFlowView(
    model: OnThisDayModel,
    makeRoom: (Bitmap) -> PhotoRoomModel,
    onClose: () -> Unit,
) {
    var room by remember { mutableStateOf<PhotoRoomModel?>(null) }
    Crossfade(targetState = room, animationSpec = tween(durationMillis = 250), label = "onthisday-flow") { current ->
        if (current != null) {
            PhotoRoomView(model = current, onClose = { room = null })
        } else {
            OnThisDayView(model = model, onSend = { image -> room = makeRoom(image) }, onClose = onClose)
        }
    }
}

/**
 * 当年今日: one old photo at a time. Down for the next one, up to send it
 * into the conversation. Deliberately not the lightbox: the lightbox owns
 * pull-down-to-dismiss, and stacking "up to send" on top of it would put
 * three vertical gestures on one photo. This screen has exactly one vertical
 * gesture with an explicit direction-and-distance gate.
 *
 * iOS sends the card off with a Metal particle shader; Android has no Metal,
 * so the send-off is the same dissolve in the vocabulary this platform
 * allows: the card keeps its fade + scale while a Canvas layer of
 * photo-coloured particles — sampled from the bitmap the moment the send
 * starts — drifts up and apart over the same 0.85s window, same
 * cancellability. Reduce Motion keeps the plain fade, particles skipped.
 */
@Composable
fun OnThisDayView(
    model: OnThisDayModel,
    onSend: (Bitmap) -> Unit,
    onClose: () -> Unit,
) {
    val colors = MurmurTheme.colors
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val authorization by model.authorization.collectAsState()
    val candidates by model.candidates.collectAsState()
    val index by model.index.collectAsState()
    val isLoading by model.isLoading.collectAsState()
    val currentImage by model.currentImage.collectAsState()

    var dragOffset by remember { mutableStateOf(Animatable(0f)) }
    /** Set from the swipe until the photo has actually left. While it holds,
     *  the card takes no further gestures: a second swipe during the dissolve
     *  would change the picture on screen while the first one was still on
     *  its way out. */
    var isSending by remember { mutableStateOf(false) }
    /** The send already under way, held so that leaving can call it off. The
     *  dissolve runs for most of a second and the close button stays live for
     *  all of it — cancelling happens in the same event as the tap, not on
     *  dispose, which arrives only after the cover has been torn down. */
    var sendJob by remember { mutableStateOf<Job?>(null) }
    val dissolve = remember { Animatable(0f) }
    val reduceMotion = remember { reduceMotionEnabled(context) }
    val dissolveDurationMillis = when {
        reduceMotion -> 0L
        BuildConfig.DEBUG && OnThisDayDebugTuning.slowDissolve -> SLOW_DISSOLVE_DURATION_MILLIS
        else -> DISSOLVE_DURATION_MILLIS
    }
    /** Particles only exist while a dissolve actually runs: Reduce Motion's
     *  zero-duration window keeps the plain fade and skips the sampling and
     *  per-frame draw entirely. */
    val particleDissolve = dissolveDurationMillis > 0

    fun springHome() {
        scope.launch { dragOffset.animateTo(0f, tween(240)) }
    }

    fun cancelSend() {
        sendJob?.cancel()
        sendJob = null
        isSending = false
        scope.launch { dissolve.snapTo(0f) }
    }

    fun send() {
        val image = currentImage ?: return
        if (!model.canSend) return
        sendJob?.cancel()
        isSending = true
        if (dissolveDurationMillis > 0) {
            scope.launch {
                try {
                    dissolve.animateTo(1f, tween(dissolveDurationMillis.toInt()))
                } catch (cancelled: CancellationException) {
                    throw cancelled
                }
            }
        }
        sendJob = scope.launch {
            // Not a swallowing cancel: leaving must be able to stop the photo.
            try {
                delay(dissolveDurationMillis + 120)
            } catch (cancelled: CancellationException) {
                throw cancelled
            }
            onSend(image)
            // The send has left: standalone viewers (tests, other hosts) keep
            // composing, so the sending state must end on its own — the
            // production flow swaps this screen for the room either way.
            isSending = false
        }
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { grants ->
        val full = when {
            Build.VERSION.SDK_INT >= 33 -> grants[android.Manifest.permission.READ_MEDIA_IMAGES] == true
            else -> grants[android.Manifest.permission.READ_EXTERNAL_STORAGE] == true
        }
        val partial = Build.VERSION.SDK_INT >= 34 &&
            grants[android.Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED] == true
        model.onPermissionResult(fullAccess = full, partialAccess = partial)
    }

    LaunchedEffect(model) { model.refreshAuthorization() }
    LaunchedEffect(authorization) {
        if (authorization == OnThisDayAuthorization.Authorized && candidates.isEmpty()) model.load()
    }
    DisposableEffect(model) { onDispose { cancelSend() } }

    // The browser owns the whole screen while it is up, so its root clears the
    // system bars — the close button and the bottom gesture hint both sit at
    // the edges. When nested under OnThisDayTabView the insets are already
    // consumed and this adds nothing.
    Box(modifier = Modifier.fillMaxSize().background(colors.paper).safeDrawingPadding()) {
        when (authorization) {
            OnThisDayAuthorization.NotDetermined -> OnThisDayGate(
                title = "翻翻同一天的旧照片",
                message = "Murmur 想在本机翻找你相册里同一天的旧照片；发送哪一张，由你决定。照片只在这台设备上翻找。",
                buttonTitle = "允许翻找",
                testTag = "onthisday-allow",
            ) {
                if (model.libraryNeedsActivityPermissionRequest) {
                    permissionLauncher.launch(onThisDayPermissions())
                } else {
                    model.requestAuthorization()
                }
            }
            OnThisDayAuthorization.Denied -> OnThisDayGate(
                title = "相册的入口关着",
                message = "没有相册权限，Murmur 翻不到那一天。到系统设置里打开后，回来就能翻。",
                buttonTitle = "前往系统设置",
                testTag = "onthisday-open-settings",
            ) { openAppSettings(context) }
            // 当年今日 is a tab, and a tab cannot quietly absent itself the way
            // the old disc did — so limited access says what it is instead of
            // the day looking empty.
            OnThisDayAuthorization.Limited -> OnThisDayGate(
                title = "只能看到你选的那几张",
                message = "当年今日需要翻整个相册才找得到那一天。在系统设置里把权限改成「所有照片」后再来。",
                buttonTitle = "前往系统设置",
                testTag = "onthisday-open-settings",
            ) { openAppSettings(context) }
            OnThisDayAuthorization.Authorized -> when {
                isLoading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(color = colors.secondaryInk, modifier = Modifier.testTag("onthisday-loading"))
                }
                candidates.isEmpty() -> Column(
                    modifier = Modifier
                        .align(Alignment.Center)
                        .padding(MurmurSpacing.xl)
                        .testTag("onthisday-empty"),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text("相册里还没有照片。", fontSize = 20.sp, color = colors.ink)
                    Text("等你拍下第一张，这里就有东西可翻了。", fontSize = 12.sp, color = colors.secondaryInk)
                }
                else -> OnThisDayViewer(
                    candidate = candidates[index.coerceAtMost(candidates.lastIndex)],
                    image = currentImage,
                    canSend = model.canSend,
                    isSending = isSending,
                    dragOffset = dragOffset,
                    dissolve = dissolve.value,
                    zone = model.zone,
                    particleDissolve = particleDissolve,
                    onDragOffset = { next -> scope.launch { dragOffset.snapTo(next) } },
                    onDragEnd = { dy, dx ->
                        val vertical = abs(dy) > 2 * abs(dx)
                        when {
                            !isSending && vertical && dy <= -60 && model.canSend -> send()
                            !isSending && vertical && dy >= 60 -> {
                                model.advance()
                                springHome()
                            }
                            else -> springHome()
                        }
                    },
                    onResetDrag = { springHome() },
                    onSendAction = { send() },
                    onNextAction = { model.advance() },
                )
            }
        }

        IconButton(
            onClick = {
                // The cancel happens here, in the same event as the tap.
                cancelSend()
                onClose()
            },
            modifier = Modifier
                .align(Alignment.TopStart)
                .padding(start = MurmurSpacing.sm, top = 10.dp)
                .size(44.dp)
                .testTag("close-onthisday"),
        ) {
            Surface(
                shape = CircleShape,
                color = colors.raisedPaper,
                border = androidx.compose.foundation.BorderStroke(1.dp, colors.rule),
                modifier = Modifier.size(34.dp),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Icon(Icons.Outlined.Close, contentDescription = "关闭当年今日", tint = colors.ink)
                }
            }
        }
    }
}

@Composable
private fun OnThisDayViewer(
    candidate: OnThisDayCandidate,
    image: Bitmap?,
    canSend: Boolean,
    isSending: Boolean,
    dragOffset: Animatable<Float, *>,
    dissolve: Float,
    zone: ZoneId,
    particleDissolve: Boolean,
    onDragOffset: (Float) -> Unit,
    onDragEnd: (Float, Float) -> Unit,
    onResetDrag: () -> Unit,
    onSendAction: () -> Unit,
    onNextAction: () -> Unit,
) {
    val colors = MurmurTheme.colors
    val caption = onThisDayCaption(candidate.origin)
    val dragAlpha = 1f - min(abs(dragOffset.value) / 700f, 0.45f)
    var totalDx by remember { mutableStateOf(0f) }
    var totalDy by remember { mutableStateOf(0f) }
    /** The dust of the send-off, sampled from the bitmap at the moment the
     *  send starts and dropped the moment it is cancelled — the dissolve
     *  state machine itself owns timing; this only mirrors isSending. */
    var particles by remember { mutableStateOf<List<DissolveParticle>?>(null) }
    LaunchedEffect(isSending) {
        particles = when {
            !isSending || !particleDissolve || image == null -> null
            else -> sampleDissolveParticles(image, candidate.id.hashCode().toLong())
        }
    }

    Column(modifier = Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            text = caption,
            fontSize = 20.sp,
            color = colors.ink,
            modifier = Modifier.padding(top = 64.dp),
        )
        Text(
            text = onThisDayDateLine(candidate.creationMillis, zone),
            fontSize = 12.sp,
            color = colors.secondaryInk,
            modifier = Modifier.padding(top = 4.dp),
        )
        Spacer(Modifier.weight(1f))
        // The wrapper owns the card's slot; the particle overlay is its
        // second child, exactly the card's bounds, drawn above it.
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = MurmurSpacing.lg)
                .aspectRatio(3f / 4f),
        ) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .shadow(
                        elevation = 18.dp,
                        shape = RoundedCornerShape(16.dp),
                        clip = false,
                        ambientColor = colors.ink.copy(alpha = 0.12f),
                        spotColor = colors.ink.copy(alpha = 0.12f),
                    )
                .graphicsLayer {
                    alpha = (1f - dissolve) * dragAlpha
                    val scale = 1f - 0.08f * dissolve
                    scaleX = scale
                    scaleY = scale
                }
                .pointerInput(isSending) {
                    detectDragGestures(
                        onDragStart = {
                            totalDx = 0f
                            totalDy = 0f
                        },
                        onDrag = { change, amount ->
                            change.consume()
                            if (!isSending) {
                                totalDx += amount.x
                                totalDy += amount.y
                                // Vertical wins only while it is clearly
                                // vertical; anything weaker reports zero
                                // instead of half-firing.
                                val next = if (abs(totalDy) > 2 * abs(totalDx)) totalDy * 0.55f else 0f
                                onDragOffset(next)
                            }
                        },
                        onDragEnd = {
                            val (dy, dx) = totalDy to totalDx
                            totalDx = 0f
                            totalDy = 0f
                            onDragEnd(dy, dx)
                        },
                        onDragCancel = {
                            totalDx = 0f
                            totalDy = 0f
                            onResetDrag()
                        },
                    )
                }
                // The card is on screen before its pixels are, and until they
                //  arrive there is no photo to describe and nothing to send.
                //  Saying so is both the honest label and the only signal
                //  anything outside can wait on.
                .semantics {
                    contentDescription = if (canSend) {
                        "$caption 的照片"
                    } else {
                        "$caption 的照片，正在载入"
                    }
                    customActions = listOf(
                        CustomAccessibilityAction(label = "下一张") { onNextAction(); true },
                        CustomAccessibilityAction(label = "发给 Murmur") {
                            if (canSend) onSendAction()
                            canSend
                        },
                    )
                }
                .testTag("onthisday-photo"),
            ) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .clip(RoundedCornerShape(16.dp))
                        .background(colors.raisedPaper)
                        .border(1.dp, colors.rule, RoundedCornerShape(16.dp)),
                    contentAlignment = Alignment.Center,
                ) {
                    val bitmap = image
                    if (bitmap != null) {
                        Image(
                            bitmap = bitmap.asImageBitmap(),
                            contentDescription = null,
                            modifier = Modifier.fillMaxSize(),
                            contentScale = ContentScale.Fit,
                        )
                    } else {
                        CircularProgressIndicator(color = colors.secondaryInk)
                    }
                }
            }
            // The overlay lives for the whole send, mirroring isSending —
            // never on the dissolve float: the animation clock can run
            // ahead of composition in tests, and a completed dissolve would
            // tear the dust down early. At progress 1 the particles draw
            // fully transparent anyway.
            val activeParticles = if (particleDissolve && isSending) particles else null
            if (activeParticles != null && image != null) {
                DissolveParticleOverlay(
                    particles = activeParticles,
                    bitmap = image,
                    progress = dissolve,
                    modifier = Modifier
                        .fillMaxSize()
                        .testTag("dissolve-particles"),
                )
            }
        }
        Spacer(Modifier.weight(1f))
        Text(
            text = "上滑跟 Murmur 说说这张 · 下滑换一张",
            fontSize = 12.sp,
            color = colors.secondaryInk,
            modifier = Modifier.padding(bottom = 34.dp),
        )
    }
}

@Composable
private fun OnThisDayGate(
    title: String,
    message: String,
    buttonTitle: String,
    testTag: String,
    onButton: () -> Unit,
) {
    val colors = MurmurTheme.colors
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .widthIn(max = 520.dp)
            .padding(horizontal = 28.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        Spacer(Modifier.weight(1f))
        Text(title, fontSize = 22.sp, color = colors.ink)
        Text(message, fontSize = 14.sp, color = colors.secondaryInk)
        Button(
            onClick = onButton,
            shape = RoundedCornerShape(12.dp),
            colors = ButtonDefaults.buttonColors(containerColor = colors.ink, contentColor = colors.paper),
            modifier = Modifier
                .fillMaxWidth()
                .testTag(testTag),
        ) {
            Text(buttonTitle, fontSize = 16.sp, modifier = Modifier.padding(vertical = 6.dp))
        }
        Spacer(Modifier.weight(2f))
    }
}

/** The card's honest label: which day this photo actually belongs to. */
internal fun onThisDayCaption(origin: OnThisDayOrigin): String = when (origin) {
    is OnThisDayOrigin.SameDay ->
        if (origin.yearsAgo == 1) "去年的今天" else "${origin.yearsAgo} 年前的今天"
    OnThisDayOrigin.Elsewhere -> "相册里翻到的"
}

/** Pinned Chinese date formatting — the copy is pinned, so the dates are
 *  too: `2025年8月30日`, never a locale fallback. */
internal fun onThisDayDateLine(millis: Long, zone: ZoneId): String {
    val day = Instant.ofEpochMilli(millis).atZone(zone).toLocalDate()
    return "${day.year}年${day.monthValue}月${day.dayOfMonth}日"
}

/** The runtime permission(s) this device needs for the album door. */
private fun onThisDayPermissions(): Array<String> = when {
    Build.VERSION.SDK_INT >= 34 -> arrayOf(
        android.Manifest.permission.READ_MEDIA_IMAGES,
        android.Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED,
    )
    Build.VERSION.SDK_INT >= 33 -> arrayOf(android.Manifest.permission.READ_MEDIA_IMAGES)
    else -> arrayOf(android.Manifest.permission.READ_EXTERNAL_STORAGE)
}

private fun openAppSettings(context: Context) {
    val intent = Intent(
        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
        Uri.fromParts("package", context.packageName, null),
    ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    context.startActivity(intent)
}

/** Accessibility → 「移除动画」 sets the animator scale to 0; that is the
 *  Android answer to iOS `accessibilityReduceMotion`. */
private fun reduceMotionEnabled(context: Context): Boolean = try {
    Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE) == 0f
} catch (error: Throwable) {
    false
}

private const val DISSOLVE_DURATION_MILLIS = 850L
private const val SLOW_DISSOLVE_DURATION_MILLIS = 3000L

/** One mote of the send-off. [homeX]/[homeY] are normalised over the drawn
 *  photo; [color] is the pixel sampled from the bitmap; direction, speed,
 *  stagger and lift all come from the seeded LCG. Immutable, so the
 *  per-frame draw allocates nothing but the [Offset]s. */
private class DissolveParticle(
    val homeX: Float,
    val homeY: Float,
    val color: Color,
    val sizeDp: Float,
    val dirX: Float,
    val dirY: Float,
    val speed: Float,
    val delay: Float,
    val buoyancy: Float,
)

/** Samples the card's bitmap into the dissolve's particle set, at the moment
 *  the send starts. Deterministic per candidate id — same photo, same dust.
 *  Homes sit on a 12×17 lattice (≈200 motes) with a little jitter; a
 *  recycled bitmap or an out-of-range [Bitmap.getPixel] skips that mote. */
private fun sampleDissolveParticles(bitmap: Bitmap, seed: Long): List<DissolveParticle> {
    if (bitmap.isRecycled) return emptyList()
    var state = (seed xor 0x2545F4914F6CDD1DL) * 6_364_136_223_846_793_005L + 1_442_695_040_888_963_407L
    fun next(): Double {
        state = state * 6_364_136_223_846_793_005L + 1_442_695_040_888_963_407L
        return (state ushr 33).toDouble() / (1L shl 31).toDouble()
    }
    val particles = ArrayList<DissolveParticle>(DISSOLVE_GRID_COLS * DISSOLVE_GRID_ROWS)
    for (row in 0 until DISSOLVE_GRID_ROWS) {
        for (col in 0 until DISSOLVE_GRID_COLS) {
            val homeX = ((col + 0.2 + next() * 0.6) / DISSOLVE_GRID_COLS).toFloat()
            val homeY = ((row + 0.2 + next() * 0.6) / DISSOLVE_GRID_ROWS).toFloat()
            val px = (homeX * (bitmap.width - 1)).toInt().coerceIn(0, bitmap.width - 1)
            val py = (homeY * (bitmap.height - 1)).toInt().coerceIn(0, bitmap.height - 1)
            val argb = try {
                bitmap.getPixel(px, py)
            } catch (_: Throwable) {
                continue
            }
            val angle = next() * PI * 2.0
            particles += DissolveParticle(
                homeX = homeX,
                homeY = homeY,
                color = Color(argb),
                sizeDp = (3.0 + next() * 5.0).toFloat(),
                // Upward-outward scatter, the same cone the Metal shader's
                // direction formula draws: sideways damped, vy always rising.
                dirX = (cos(angle) * 0.45).toFloat(),
                dirY = (-(0.55 + 0.45 * sin(angle))).toFloat(),
                speed = (0.6 + next() * 0.5).toFloat(),
                delay = (next() * 0.4).toFloat(),
                buoyancy = next().toFloat(),
            )
        }
    }
    return particles
}

/** The particle layer of the send-off, the Android counterpart of the iOS
 *  `onThisDayDissolve` shader: every mote sits at its home on the photo at
 *  progress 0; as progress advances each one waits out its staggered delay,
 *  then drifts up and apart with `t²` easing — let go of, not thrown — while
 *  fading non-linearly (`1 - t`, squared, as the shader). The card under it
 *  keeps its own fade + scale, which is what holds the photo legible. */
@Composable
private fun DissolveParticleOverlay(
    particles: List<DissolveParticle>,
    bitmap: Bitmap,
    progress: Float,
    modifier: Modifier = Modifier,
) {
    Canvas(modifier = modifier) {
        if (bitmap.isRecycled) return@Canvas
        // ContentScale.Fit: the photo is centred in the card with the same
        // letterbox the Image applies, so homes land on their pixels.
        val fit = min(size.width / bitmap.width.toFloat(), size.height / bitmap.height.toFloat())
        val imageWidth = bitmap.width * fit
        val imageHeight = bitmap.height * fit
        val left = (size.width - imageWidth) / 2f
        val top = (size.height - imageHeight) / 2f
        val drift = DISSOLVE_DRIFT_DP.dp.toPx()
        val lift = DISSOLVE_LIFT_DP.dp.toPx()
        particles.forEach { particle ->
            val span = 1f - particle.delay
            val t = ((progress - particle.delay) / span).coerceIn(0f, 1f)
            if (t >= 1f) return@forEach
            // t² easing: the scatter accelerates out of the still photo, and
            // the buoyancy term lifts instead of gravity pulling down.
            val ease = t * t
            val x = left + particle.homeX * imageWidth + particle.dirX * drift * particle.speed * ease
            val y = top + particle.homeY * imageHeight +
                (particle.dirY * drift * particle.speed - particle.buoyancy * lift) * ease
            drawCircle(
                color = particle.color,
                radius = particle.sizeDp.dp.toPx() * (1f - 0.4f * t) * 0.5f,
                center = Offset(x, y),
                alpha = (1f - t) * (1f - t),
            )
        }
    }
}

private const val DISSOLVE_GRID_COLS = 12
private const val DISSOLVE_GRID_ROWS = 17
private const val DISSOLVE_DRIFT_DP = 90f
private const val DISSOLVE_LIFT_DP = 24f
