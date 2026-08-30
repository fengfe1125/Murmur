package com.sakura.murmur.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.ArrowUpward
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
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
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
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
            messages = state.messages,
            sendFailures = state.sendFailures,
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
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.Bottom,
        ) {
            if (state.phase.isBusy) {
                TextButton(onClick = session::cancelCurrentOperation, modifier = Modifier.height(48.dp)) {
                    Text("取消", color = colors.secondaryInk, fontSize = 15.sp)
                }
                Spacer(Modifier.width(MurmurSpacing.sm))
            }
            // One pill instead of a square button beside a taller field:
            // both controls are discs inside the container, the counterpart
            // of `MomentComposer` in `MurmurChatView.swift`.
            Surface(
                modifier = Modifier.weight(1f),
                shape = RoundedCornerShape(26.dp),
                color = colors.raisedPaper,
                border = BorderStroke(1.dp, colors.rule),
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 5.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.Bottom,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    // The plus disc: a 36dp ink disc inside a 44dp tap target,
                    // the transparent ring keeping the hit area honest.
                    Box(
                        modifier = Modifier
                            .size(44.dp)
                            .clickable(enabled = !state.phase.isBusy, onClick = onPickPhoto),
                        contentAlignment = Alignment.Center,
                    ) {
                        Box(
                            modifier = Modifier
                                .size(36.dp)
                                .background(colors.ink, CircleShape),
                            contentAlignment = Alignment.Center,
                        ) {
                            Icon(
                                Icons.Outlined.Add,
                                contentDescription = "添加照片",
                                tint = colors.paper,
                                modifier = Modifier.size(18.dp),
                            )
                        }
                    }
                    // No box, no background: the field is just the line of
                    // text; the hint shows only while the draft is empty.
                    BasicTextField(
                        value = state.draftText,
                        onValueChange = session::updateDraftText,
                        modifier = Modifier
                            .weight(1f)
                            .testTag("moment-composer"),
                        textStyle = TextStyle(fontSize = 16.sp, color = colors.ink),
                        singleLine = true,
                        enabled = !state.phase.isBusy,
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                        keyboardActions = KeyboardActions(onSend = { session.submit() }),
                        cursorBrush = SolidColor(colors.ink),
                        decorationBox = { innerTextField ->
                            Box(contentAlignment = Alignment.CenterStart) {
                                if (state.draftText.isEmpty()) {
                                    Text("发一张图，或说点什么", color = colors.secondaryInk, fontSize = 16.sp)
                                }
                                innerTextField()
                            }
                        },
                    )
                    // The send disc: ink on paper when there is something to
                    // send, rule on secondary ink when there is not.
                    Box(
                        modifier = Modifier
                            .size(44.dp)
                            .clickable(enabled = state.canSubmit, onClick = session::submit)
                            .testTag("send-moment"),
                        contentAlignment = Alignment.Center,
                    ) {
                        Box(
                            modifier = Modifier
                                .size(36.dp)
                                .background(if (state.canSubmit) colors.ink else colors.rule, CircleShape),
                            contentAlignment = Alignment.Center,
                        ) {
                            Icon(
                                Icons.Outlined.ArrowUpward,
                                contentDescription = "发送这一刻",
                                tint = if (state.canSubmit) colors.paper else colors.secondaryInk,
                                modifier = Modifier.size(16.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}
