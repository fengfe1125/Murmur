package com.sakura.murmur.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.VerticalDivider
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sakura.murmur.MurmurConnectionState
import com.sakura.murmur.MurmurPhase
import com.sakura.murmur.MurmurSessionModel
import com.sakura.murmur.PhotoInput

/**
 * Top-level switch — the Android counterpart of `MurmurChatView.swift`:
 * checking → loading; no identity → enrollment; dead device binding →
 * reconnect; otherwise the moment workbench. Settings open as a full pane
 * from the gear at the top trailing edge.
 */
@Composable
fun MurmurChatScreen(session: MurmurSessionModel) {
    val state by session.uiState.collectAsState()
    val colors = MurmurTheme.colors
    var showSettings by remember { mutableStateOf(false) }

    if (showSettings) {
        MurmurSettingsScreen(session = session, onClose = { showSettings = false })
        return
    }

    NotificationPromptEffect(session)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.paper)
            .safeDrawingPadding()
            .padding(horizontal = MurmurSpacing.xl),
    ) {
        // Top chrome: wordmark | (connection when broken) + settings.
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = MurmurSpacing.sm),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Murmur", style = MaterialTheme.typography.headlineMedium, color = colors.ink)
            Spacer(Modifier.weight(1f))
            if (state.identity != null && state.connection != MurmurConnectionState.Connected) {
                Text(state.connection.label, color = colors.coral, fontSize = 14.sp)
                Spacer(Modifier.width(MurmurSpacing.md))
            }
            if (state.identity != null) {
                IconButton(onClick = { showSettings = true }, modifier = Modifier.testTag("settings-button")) {
                    Icon(Icons.Outlined.Settings, contentDescription = "设置", tint = colors.ink)
                }
            }
        }
        HorizontalDivider(color = colors.rule)

        when {
            state.connection == MurmurConnectionState.Checking -> LoadingPane()
            state.identity == null -> EnrollmentPane(state, session)
            state.requiresDeviceReconnect -> ReconnectPane(session)
            else -> MomentWorkbench(state = state, session = session)
        }
    }
}

/**
 * T1.5: the session model flips `notificationPromptRequested` after the first
 * complete reply; the UI asks POST_NOTIFICATIONS exactly once, here.
 */
@Composable
private fun NotificationPromptEffect(session: MurmurSessionModel) {
    val context = LocalContext.current
    val state by session.uiState.collectAsState()
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { /* outcome only matters for the next token sync (T2.2) */ }

    LaunchedEffect(state.notificationPromptRequested) {
        if (state.notificationPromptRequested && Build.VERSION.SDK_INT >= 33) {
            permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            session.consumeNotificationPromptRequest()
        } else if (state.notificationPromptRequested) {
            // API < 33: notifications need no runtime permission.
            session.consumeNotificationPromptRequest()
        }
    }
}

@Composable
private fun LoadingPane() {
    val colors = MurmurTheme.colors
    Column(
        modifier = Modifier.fillMaxSize(),
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

/** One workbench: compact stacks response above the composer; regular width
 *  splits into the asymmetric two columns from design.md. */
@Composable
private fun MomentWorkbench(state: MurmurSessionModel.UiState, session: MurmurSessionModel) {
    val colors = MurmurTheme.colors
    val context = LocalContext.current
    val photoPicker = rememberLauncherForActivityResult(
        ActivityResultContracts.PickVisualMedia(),
    ) { uri ->
        if (uri != null) {
            session.preparePhoto(PhotoInput.FromUri(uri))
        } else {
            session.failPhotoSelection()
        }
    }
    val pickPhoto: () -> Unit = {
        session.beginPhotoSelection()
        photoPicker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
    }

    BoxWithConstraints(modifier = Modifier.fillMaxSize()) {
        val regular = maxWidth >= 600.dp
        if (regular) {
            Row(modifier = Modifier.fillMaxSize().testTag("workbench-regular")) {
                Column(
                    modifier = Modifier
                        .weight(0.42f)
                        .fillMaxHeight()
                        .padding(end = MurmurSpacing.xl),
                    verticalArrangement = Arrangement.SpaceBetween,
                ) {
                    Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
                        VisualPanel(state)
                    }
                    ErrorNotice(state, session)
                    ComposerPane(state, session, pickPhoto)
                }
                VerticalDivider(color = colors.rule)
                ResponsePane(
                    state = state,
                    modifier = Modifier
                        .weight(0.58f)
                        .fillMaxHeight()
                        .padding(start = MurmurSpacing.xl)
                        .testTag("workbench-response"),
                )
            }
        } else {
            Column(modifier = Modifier.fillMaxSize().testTag("workbench-compact")) {
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxWidth(),
                    contentAlignment = Alignment.Center,
                ) {
                    Column(
                        modifier = Modifier
                            .fillMaxSize()
                            .verticalScroll(rememberScrollState()),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(MurmurSpacing.md),
                    ) {
                        Spacer(Modifier.height(MurmurSpacing.lg))
                        VisualPanel(state)
                        ResponseTexts(state)
                    }
                }
                ErrorNotice(state, session)
                Text(
                    text = state.phase.statusText,
                    color = colors.secondaryInk,
                    fontSize = 13.sp,
                    modifier = Modifier.padding(top = MurmurSpacing.sm, bottom = MurmurSpacing.sm),
                )
                ComposerPane(state, session, pickPhoto)
            }
        }
    }
}

@Composable
private fun VisualPanel(state: MurmurSessionModel.UiState) {
    val colors = MurmurTheme.colors
    val photo = state.currentPhoto ?: state.draftPhoto
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(260.dp)
            .clip(RoundedCornerShape(18.dp))
            .background(colors.raisedPaper),
        contentAlignment = Alignment.Center,
    ) {
        val bitmap = photo?.preview
        when {
            bitmap != null -> Image(
                bitmap = bitmap.asImageBitmap(),
                contentDescription = if (state.currentPhoto == null) "待发送的照片" else "当前照片",
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Fit,
            )
            state.phase == MurmurPhase.PreparingPhoto -> Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(MurmurSpacing.md),
            ) {
                CircularProgressIndicator(color = colors.olive, modifier = Modifier.size(32.dp))
                Text("正在准备照片", color = colors.secondaryInk, fontSize = 13.sp)
            }
            else -> Text("照片会在这里出现", color = colors.secondaryInk, fontSize = 13.sp)
        }
    }
}

@Composable
private fun ResponsePane(state: MurmurSessionModel.UiState, modifier: Modifier = Modifier) {
    Box(modifier, contentAlignment = Alignment.Center) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState()),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(MurmurSpacing.md),
        ) {
            ResponseTexts(state)
        }
    }
}

@Composable
private fun ResponseTexts(state: MurmurSessionModel.UiState) {
    val colors = MurmurTheme.colors
    state.bubbles.forEach { bubble ->
        Text(
            bubble.text,
            color = colors.ink,
            fontSize = 17.sp,
            modifier = Modifier
                .clip(RoundedCornerShape(16.dp))
                .background(colors.raisedPaper)
                .padding(horizontal = MurmurSpacing.lg, vertical = MurmurSpacing.md),
        )
    }
    when (state.phase) {
        MurmurPhase.Idle, MurmurPhase.Ready ->
            if (!state.hasCurrentMoment) {
                Text("选一张照片，发送此刻。", color = colors.secondaryInk, fontSize = 15.sp)
            }
        MurmurPhase.PreparingPhoto -> Text("正在准备照片…", color = colors.secondaryInk, fontSize = 15.sp)
        MurmurPhase.Uploading -> Text("正在发送…", color = colors.secondaryInk, fontSize = 15.sp)
        MurmurPhase.Responding ->
            if (state.bubbles.isEmpty()) {
                TypingIndicator()
            }
        MurmurPhase.Quiet -> Text("这一刻很安静。", color = colors.secondaryInk, fontSize = 15.sp)
        MurmurPhase.Complete -> Unit
        MurmurPhase.Error ->
            Text(state.failure?.message ?: "出了点问题。", color = colors.coral, fontSize = 15.sp)
    }
}

/** Three quiet dots with an opacity pulse — the only motion design.md allows. */
@Composable
private fun TypingIndicator() {
    val colors = MurmurTheme.colors
    val transition = rememberInfiniteTransition(label = "typing")
    val alpha by transition.animateFloat(
        initialValue = 0.35f,
        targetValue = 0.9f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 600, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "typing-alpha",
    )
    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(16.dp))
            .background(colors.raisedPaper)
            .padding(horizontal = MurmurSpacing.lg, vertical = MurmurSpacing.md),
        horizontalArrangement = Arrangement.spacedBy(5.dp),
    ) {
        repeat(3) {
            Box(
                Modifier
                    .size(7.dp)
                    .alpha(alpha)
                    .background(colors.secondaryInk, CircleShape),
            )
        }
    }
}

@Composable
private fun ErrorNotice(state: MurmurSessionModel.UiState, session: MurmurSessionModel) {
    if (state.phase != MurmurPhase.Error || state.failure == null) return
    val colors = MurmurTheme.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = MurmurSpacing.sm),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier
                .width(2.dp)
                .height(36.dp)
                .background(colors.coral),
        )
        Spacer(Modifier.width(MurmurSpacing.md))
        Text(
            state.failure!!.message,
            color = colors.ink,
            fontSize = 15.sp,
            modifier = Modifier.weight(1f),
        )
        if (state.failure!!.retryable) {
            TextButton(onClick = session::retry, modifier = Modifier.height(48.dp)) {
                Text("再试一次", color = colors.olive)
            }
        }
    }
}

@Composable
private fun ComposerPane(
    state: MurmurSessionModel.UiState,
    session: MurmurSessionModel,
    onPickPhoto: () -> Unit,
) {
    val colors = MurmurTheme.colors
    Column(modifier = Modifier.padding(bottom = MurmurSpacing.lg)) {
        state.draftPhoto?.preview?.let { bitmap ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Image(
                    bitmap = bitmap.asImageBitmap(),
                    contentDescription = "待发送照片",
                    modifier = Modifier
                        .size(56.dp)
                        .clip(RoundedCornerShape(12.dp)),
                    contentScale = ContentScale.Crop,
                )
                Spacer(Modifier.width(MurmurSpacing.md))
                TextButton(
                    onClick = session::removeDraftPhoto,
                    modifier = Modifier.height(48.dp),
                ) {
                    Text("移除照片", color = colors.secondaryInk, fontSize = 14.sp)
                }
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(
                onClick = onPickPhoto,
                modifier = Modifier.size(48.dp),
                enabled = !state.phase.isBusy,
            ) {
                Icon(Icons.Outlined.Add, contentDescription = "添加照片", tint = colors.olive)
            }
            OutlinedTextField(
                value = state.draftText,
                onValueChange = session::updateDraftText,
                modifier = Modifier.weight(1f),
                placeholder = { Text("发一张图，或说点什么") },
                singleLine = true,
                enabled = !state.phase.isBusy,
            )
            if (state.phase.isBusy) {
                TextButton(onClick = session::cancelCurrentOperation, modifier = Modifier.height(48.dp)) {
                    Text("取消", color = colors.secondaryInk, fontSize = 15.sp)
                }
            }
            Button(
                onClick = session::submit,
                modifier = Modifier
                    .height(48.dp)
                    .testTag("send-moment"),
                enabled = state.canSubmit,
                colors = ButtonDefaults.buttonColors(containerColor = colors.olive),
            ) {
                Text("发送此刻", fontSize = 16.sp)
            }
        }
    }
}
