package com.sakura.murmur.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sakura.murmur.MurmurConnectionState
import com.sakura.murmur.MurmurPhase
import com.sakura.murmur.MurmurPhotoLightbox
import com.sakura.murmur.MurmurPhotoPreview
import com.sakura.murmur.MurmurSessionModel
import com.sakura.murmur.PhotoInput

/**
 * The 聊天 tab — the Android counterpart of `MurmurChatView.swift`: the
 * conversation and the composer under top chrome that is only the wordmark
 * and the connection state.  The gates around it (checking / enrollment /
 * reconnect) live in `MurmurShell`; 设置 moved sideways into the 我的 tab,
 * so there is no settings entry here (iOS parity).
 */
@Composable
fun MurmurChatScreen(session: MurmurSessionModel) {
    val state by session.uiState.collectAsState()
    val colors = MurmurTheme.colors

    NotificationPromptEffect(session)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.paper)
            .safeDrawingPadding()
            .imePadding()
            .padding(horizontal = MurmurSpacing.xl),
    ) {
        // Top chrome: wordmark | connection state when the wire is broken.
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
        }
        HorizontalDivider(color = colors.rule)

        TranscriptPane(state = state, session = session)
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

/** The conversation and the composer beneath it — the workbench is gone:
 *  the scrollback is the screen, and a failed send says so on its own row
 *  instead of in a strip above the field (iOS `MomentWorkbench`). */
@Composable
private fun TranscriptPane(state: MurmurSessionModel.UiState, session: MurmurSessionModel) {
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

    var openPhoto by remember { mutableStateOf<MurmurPhotoPreview?>(null) }

    Column(modifier = Modifier.fillMaxSize()) {
        MurmurTranscriptView(
            state = state,
            // The typing indicator reads the phase, the way iOS's reads
            // `isAwaitingReply`: a send on the wire, not a photo decoding.
            showsTyping = state.phase == MurmurPhase.Uploading || state.phase == MurmurPhase.Responding,
            imageFile = session.transcriptStore::imageFile,
            onOpenImage = { openPhoto = it },
            onResend = session::resend,
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth(),
        )
        // A failed *send* is not shown here at all: it belongs to its own
        // bubble up in the conversation.  Only the draft itself speaks here.
        DraftFailureLine(state.draftFailure)
        ComposerPane(state, session, pickPhoto)
    }

    openPhoto?.let { preview ->
        MurmurPhotoLightbox(preview = preview, onClose = { openPhoto = null })
    }
}

/** One line above the field, only when the draft itself is the problem. */
@Composable
private fun DraftFailureLine(message: String?) {
    if (message == null) return
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
        Text(message, color = colors.ink, fontSize = 15.sp, modifier = Modifier.weight(1f))
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
