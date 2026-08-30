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
import kotlin.math.min
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
 * iOS sends the card off with a particle shader; Android has no Metal, so the
 * send-off is the same motion in the vocabulary this platform allows:
 * fade + scale, same 0.85s window, same cancellability.
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
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = MurmurSpacing.lg)
                .aspectRatio(3f / 4f)
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
