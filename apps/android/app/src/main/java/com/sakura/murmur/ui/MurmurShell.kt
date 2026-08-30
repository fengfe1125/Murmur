package com.sakura.murmur.ui

import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import android.app.Activity
import com.sakura.murmur.MurmurConnectionState
import com.sakura.murmur.MurmurSessionModel
import com.sakura.murmur.OnThisDayLibraryProvider
import com.sakura.murmur.OnThisDayModel

/**
 * The three places the app has — the Android counterpart of `MurmurTab` in
 * `MurmurShell.swift`. 当年今日 used to be a disc floating over the
 * conversation and settings used to be another one; both were doors on top of
 * the chat.  They are siblings now.
 */
enum class MurmurTab(val title: String) {
    Chat("聊天"),
    OnThisDay("当年今日"),
    Me("我的"),
}

private val MurmurTab.icon: ImageVector
    get() = when (this) {
        MurmurTab.Chat -> Icons.Outlined.ChatBubbleOutline
        MurmurTab.OnThisDay -> Icons.Outlined.CalendarMonth
        MurmurTab.Me -> Icons.Outlined.Person
    }

/** The stop's test tag, matching iOS's `tab-\(rawValue)`. */
private fun MurmurTab.tabTestTag(): String = "tab-" + when (this) {
    MurmurTab.Chat -> "chat"
    MurmurTab.OnThisDay -> "onThisDay"
    MurmurTab.Me -> "me"
}

/**
 * The shell: the gates that must own the whole screen, and otherwise the three
 * tabs with one capsule bar under them.  The gates are iOS's order, stated
 * against the session model's own states: checking → loading; no identity →
 * enrollment; dead device binding → reconnect; otherwise the tabs.
 */
@Composable
fun MurmurShell(session: MurmurSessionModel) {
    val state by session.uiState.collectAsState()
    var tab by remember { mutableStateOf(MurmurTab.Chat) }

    when {
        state.connection == MurmurConnectionState.Checking -> LoadingPane()
        state.identity == null -> EnrollmentPane(state, session)
        state.requiresDeviceReconnect -> ReconnectPane(session)
        else -> MurmurTabs(session = session, tab = tab, onTabChange = { tab = it })
    }
}

/**
 * One tab on screen at a time, and only that one in the tree: rendering one is
 * correct by construction, and what state matters survives in the session
 * anyway (iOS `MurmurShell.tabs` draws the same line — the off-screen tabs'
 * controls must not be reachable).
 */
@Composable
private fun MurmurTabs(
    session: MurmurSessionModel,
    tab: MurmurTab,
    onTabChange: (MurmurTab) -> Unit,
) {
    val colors = MurmurTheme.colors
    val density = LocalDensity.current
    // Measured, not assumed: the bar grows with font scale, and the page is
    // inset by whatever it actually became (iOS measures `barHeight` too).
    var barHeight by remember { mutableStateOf(0.dp) }
    // The flag is read inside the fold animation's target below; the
    // updated-state guard keeps a stale IME frame from pinning the bar open.
    val imeIsVisible by rememberUpdatedState(WindowInsets.ime.getBottom(density) > 0)
    val fold by animateFloatAsState(
        targetValue = if (imeIsVisible) 1f else 0f,
        animationSpec = tween(durationMillis = 200),
        label = "tabBarFold",
    )
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    // iOS MurmurShell owns the OnThisDayModel: switching tabs must not lose
    // the shelf the browser was reading.
    val onThisDayModel = remember {
        OnThisDayModel(
            library = OnThisDayLibraryProvider.make(context, (context as? Activity)?.intent),
            scope = scope,
        )
    }
    // A cover (a calendar day, the browser) owns the whole screen; the bar
    // steps aside for exactly as long — iOS fullScreenCover semantics.
    var onThisDayCover by remember { mutableStateOf(false) }
    val showBar = !(tab == MurmurTab.OnThisDay && onThisDayCover)

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.paper),
    ) {
        // The page owns the whole screen; the bar floats over it and the page
        // is inset by exactly the bar's height — the two halves stated
        // separately, the way iOS states the content padding and the overlay
        // apart.
        Box(modifier = Modifier.padding(bottom = if (showBar) barHeight else 0.dp)) {
            when (tab) {
                MurmurTab.Chat -> MurmurChatScreen(session = session)
                MurmurTab.OnThisDay -> OnThisDayTabView(
                    model = onThisDayModel,
                    archive = session.archive,
                    makeArchiveDay = { session.makeArchiveDay(it) },
                    makeRoom = { session.makePhotoRoom(it) },
                    onCoverChange = { onThisDayCover = it },
                )
                MurmurTab.Me -> MurmurSettingsScreen(session = session)
            }
        }
        if (showBar) {
            MurmurTabBar(
                selected = tab,
                onSelect = { next ->
                    // The screen cuts; the bar flows — no cross-fade on the swap,
                    // the motion belongs to the pill.
                    if (next != tab) onTabChange(next)
                },
                fold = fold,
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .onSizeChanged { barHeight = with(density) { it.height.toDp() } },
            )
        }
    }
}

/**
 * One capsule of glass with three stops on it, and one pill that travels
 * between them rather than blinking out on one and in on the next — the
 * Android counterpart of `MurmurTabBar.swift`.  The pill's place is
 * arithmetic: three stops divide the row equally, so the selected one begins
 * at `stopWidth * index`; animating that offset is the travel.  One pill that
 * is never inserted or removed has no fade available to it and has to move.
 */
@Composable
private fun MurmurTabBar(
    selected: MurmurTab,
    onSelect: (MurmurTab) -> Unit,
    fold: Float,
    modifier: Modifier = Modifier,
) {
    val colors = MurmurTheme.colors
    val density = LocalDensity.current
    // The row is measured rather than assumed: it grows with font scale, and
    // the measured width is read out of any pill animation on purpose (iOS
    // measures `stopWidth` with animations disabled for the same reason).
    var stopWidth by remember { mutableStateOf(0.dp) }
    var stopHeight by remember { mutableStateOf(0.dp) }
    val pillOffset by animateDpAsState(
        targetValue = stopWidth * selected.ordinal,
        // The underdamped spring is what reads as liquid rather than as a
        // slide (iOS: response 0.42, damping 0.72).
        animationSpec = spring(dampingRatio = 0.72f),
        label = "tabPill",
    )

    Row(
        modifier = modifier
            .fillMaxWidth()
            // Folded means the keyboard is up: the whole capsule slides clear
            // of the screen — translation parks it below the fold, so it can
            // neither show over the keyboard nor take touches on its way out.
            .graphicsLayer {
                alpha = 1f - fold
                translationY = fold * (size.height + with(density) { 40.dp.toPx() })
            }
            .navigationBarsPadding()
            .padding(horizontal = 16.dp)
            .padding(bottom = 6.dp)
            .background(colors.raisedPaper, RoundedCornerShape(28.dp))
            .border(1.dp, colors.rule, RoundedCornerShape(28.dp))
            .padding(8.dp),
    ) {
        Box {
            // The one pill: never inserted, never removed, only moved.
            // Nothing to draw before the row has been measured — a full-width
            // capsule for a single frame at launch is the very artefact the
            // arithmetic is here to remove.
            if (stopWidth > 0.dp) {
                Box(
                    modifier = Modifier
                        .offset(x = pillOffset)
                        .width(stopWidth)
                        .height(stopHeight)
                        .background(colors.olive, RoundedCornerShape(50)),
                )
            }
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .onSizeChanged {
                        stopWidth = with(density) { (it.width / MurmurTab.entries.size).toDp() }
                        stopHeight = with(density) { it.height.toDp() }
                    },
            ) {
                MurmurTab.entries.forEach { tab ->
                    MurmurTabStop(
                        tab = tab,
                        isSelected = tab == selected,
                        onClick = { onSelect(tab) },
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
    }
}

@Composable
private fun MurmurTabStop(
    tab: MurmurTab,
    isSelected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = MurmurTheme.colors
    Column(
        modifier = modifier
            // heightIn, not height: the bar grows with font scale.
            .heightIn(min = 52.dp)
            .clip(RoundedCornerShape(14.dp))
            .clickable(onClick = onClick)
            .testTag(tab.tabTestTag())
            .padding(vertical = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(
            imageVector = tab.icon,
            contentDescription = null,
            tint = if (isSelected) colors.paper else colors.secondaryInk,
            modifier = Modifier.size(20.dp),
        )
        Spacer(Modifier.height(3.dp))
        // The glyph and the label stay put and only change colour: the motion
        // belongs to the pill, and two things moving at once reads as jitter.
        // The weight change is the second signal the colour cannot carry.
        Text(
            tab.title,
            color = if (isSelected) colors.paper else colors.secondaryInk,
            fontSize = 11.sp,
            fontWeight = if (isSelected) FontWeight.SemiBold else FontWeight.Normal,
        )
    }
}

@Composable
private fun LoadingPane() {
    val colors = MurmurTheme.colors
    Column(
        modifier = Modifier
            .fillMaxSize()
            // A gate owns the whole screen, so the system bars are its to clear.
            .safeDrawingPadding(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        CircularProgressIndicator(color = colors.olive, modifier = Modifier.size(36.dp))
        Spacer(Modifier.height(MurmurSpacing.lg))
        Text("正在确认这台设备", color = colors.secondaryInk, fontSize = 15.sp)
    }
}

@Composable
private fun EnrollmentPane(state: MurmurSessionModel.UiState, session: MurmurSessionModel) {
    val colors = MurmurTheme.colors
    var inviteCode by remember { mutableStateOf("") }
    Column(
        modifier = Modifier
            .fillMaxSize()
            // A gate owns the whole screen, so the system bars are its to
            // clear; the scroll must answer the keyboard too, or the field
            // and the button slide under it.
            .safeDrawingPadding()
            .imePadding()
            .verticalScroll(rememberScrollState()),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Spacer(Modifier.height(MurmurSpacing.xxxl))
        Text("把 Murmur 带到这里。", style = MaterialTheme.typography.titleLarge, color = colors.ink)
        Spacer(Modifier.height(MurmurSpacing.lg))
        Text(
            "首次连接使用邀请码；已有账号的新设备使用管理员签发的设备码。设备会通过安全绑定认证。",
            color = colors.secondaryInk,
            fontSize = 15.sp,
            modifier = Modifier
                .fillMaxWidth()
                .widthIn(max = 520.dp),
        )
        Spacer(Modifier.height(MurmurSpacing.xl))
        OutlinedTextField(
            value = inviteCode,
            onValueChange = { inviteCode = it },
            singleLine = true,
            label = { Text("邀请码或设备码") },
            enabled = !state.phase.isBusy,
            modifier = Modifier
                .fillMaxWidth()
                .widthIn(max = 520.dp),
        )
        state.failure?.let {
            Spacer(Modifier.height(MurmurSpacing.lg))
            Text(it.message, color = colors.coral, fontSize = 14.sp)
        }
        Spacer(Modifier.height(MurmurSpacing.lg))
        Button(
            onClick = { session.enroll(inviteCode) },
            modifier = Modifier
                .fillMaxWidth()
                .widthIn(max = 520.dp)
                .height(52.dp),
            enabled = inviteCode.isNotBlank() && !state.phase.isBusy,
            colors = ButtonDefaults.buttonColors(containerColor = colors.ink, contentColor = colors.paper),
        ) {
            Text(if (state.phase.isBusy) "正在连接…" else "连接这台设备", fontSize = 16.sp)
        }
        Spacer(Modifier.height(MurmurSpacing.md))
        Text(
            "聊天记录只留在这台设备上，删除 App 就一并消失。服务端保存的是私有记忆，不是对话本身。",
            color = colors.secondaryInk,
            fontSize = 13.sp,
            modifier = Modifier
                .fillMaxWidth()
                .widthIn(max = 520.dp),
        )
        Spacer(Modifier.height(MurmurSpacing.xxxl))
    }
}

@Composable
private fun ReconnectPane(session: MurmurSessionModel) {
    val colors = MurmurTheme.colors
    var confirmReset by remember { mutableStateOf(false) }
    Column(
        modifier = Modifier
            .fillMaxSize()
            // A gate owns the whole screen, so the system bars are its to
            // clear. No input field here, so no imePadding.
            .safeDrawingPadding()
            .padding(MurmurSpacing.xl),
        verticalArrangement = Arrangement.spacedBy(MurmurSpacing.lg),
    ) {
        Text("这台设备需要重新连接。", style = MaterialTheme.typography.titleLarge, color = colors.ink)
        Text(
            "本机的安全身份已经失效。重置只移除本机绑定，不会删除 Murmur 的账号或记忆。",
            color = colors.secondaryInk,
            fontSize = 15.sp,
        )
        Button(
            onClick = { confirmReset = true },
            modifier = Modifier.height(48.dp),
            colors = ButtonDefaults.buttonColors(containerColor = colors.coral),
        ) {
            Text("重新连接此设备", fontSize = 16.sp)
        }
        Text("重置后，请向管理员索取新的设备码。", color = colors.secondaryInk, fontSize = 13.sp)
    }
    if (confirmReset) {
        MurmurConfirmDialog(
            title = "重置本机安全身份？",
            message = "账号与服务端记忆不会被删除；再次连接需要管理员签发的新设备码。",
            confirmTitle = "确认重置",
            destructive = true,
            onConfirm = {
                confirmReset = false
                session.resetLocalDeviceIdentity()
            },
            onDismiss = { confirmReset = false },
        )
    }
}
