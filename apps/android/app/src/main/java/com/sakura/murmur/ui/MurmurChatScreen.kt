package com.sakura.murmur.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sakura.murmur.MurmurConnectionState
import com.sakura.murmur.MurmurPhase
import com.sakura.murmur.MurmurSessionModel

/**
 * Phase 0 workbench: enroll → pick a photo → send → stream the reply.
 * Single-column only; the ≥600dp two-pane layout and the full state matrix
 * arrive in Phase 1 (docs/android-adaptation-plan.md §3/§9).
 */
@Composable
fun MurmurChatScreen(session: MurmurSessionModel) {
    val state by session.uiState.collectAsState()
    val colors = MurmurTheme.colors
    val context = LocalContext.current

    val photoPicker = rememberLauncherForActivityResult(
        ActivityResultContracts.PickVisualMedia(),
    ) { uri -> uri?.let { session.attachPhoto(context, it) } }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.paper)
            .safeDrawingPadding()
            .padding(horizontal = MurmurSpacing.xl),
    ) {
        // Top bar: wordmark | connection state — no tabs, ever.
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = MurmurSpacing.lg),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Murmur", style = MaterialTheme.typography.headlineMedium, color = colors.ink)
            Spacer(Modifier.weight(1f))
            Text(state.connection.label, color = colors.secondaryInk, fontSize = 14.sp)
        }
        HorizontalDivider(color = colors.rule)

        Box(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth(),
            contentAlignment = Alignment.Center,
        ) {
            when (state.connection) {
                MurmurConnectionState.NeedsEnrollment -> EnrollmentPane(state, session)
                else -> MomentPane(state)
            }
        }

        if (state.connection == MurmurConnectionState.Connected) {
            ComposerPane(
                state = state,
                session = session,
                onPickPhoto = {
                    photoPicker.launch(
                        PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly),
                    )
                },
            )
        }
    }
}

@Composable
private fun EnrollmentPane(state: MurmurSessionModel.UiState, session: MurmurSessionModel) {
    val colors = MurmurTheme.colors
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(MurmurSpacing.lg),
    ) {
        Text("输入邀请码，连接你的 Murmur。", color = colors.secondaryInk, fontSize = 15.sp)
        OutlinedTextField(
            value = state.inviteCode,
            onValueChange = session::updateInviteCode,
            singleLine = true,
            label = { Text("邀请码") },
        )
        state.failure?.let { Text(it.message, color = colors.coral, fontSize = 14.sp) }
        Button(
            onClick = session::enroll,
            modifier = Modifier.height(48.dp),
            colors = ButtonDefaults.buttonColors(containerColor = colors.olive),
        ) {
            Text("连接", fontSize = 16.sp)
        }
    }
}

@Composable
private fun MomentPane(state: MurmurSessionModel.UiState) {
    val colors = MurmurTheme.colors
    Column(
        modifier = Modifier.verticalScroll(rememberScrollState()),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(MurmurSpacing.md),
    ) {
        state.photo?.preview?.let { bitmap ->
            Image(
                bitmap = bitmap.asImageBitmap(),
                contentDescription = "已选照片",
                modifier = Modifier
                    .size(180.dp)
                    .clip(RoundedCornerShape(16.dp)),
                contentScale = ContentScale.Crop,
            )
        }
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
                Text("选一张照片，发送此刻。", color = colors.secondaryInk, fontSize = 15.sp)
            MurmurPhase.PreparingPhoto -> Text("正在准备照片…", color = colors.secondaryInk, fontSize = 15.sp)
            MurmurPhase.Uploading -> Text("正在发送…", color = colors.secondaryInk, fontSize = 15.sp)
            MurmurPhase.Responding ->
                if (state.bubbles.isEmpty()) {
                    Text("Murmur 正在回应…", color = colors.secondaryInk, fontSize = 15.sp)
                }
            MurmurPhase.Quiet -> Text("这一刻很安静。", color = colors.secondaryInk, fontSize = 15.sp)
            MurmurPhase.Complete -> Unit
            MurmurPhase.Error ->
                Text(state.failure?.message ?: "出了点问题。", color = colors.coral, fontSize = 15.sp)
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
        if (state.phase == MurmurPhase.Error && state.failure?.retryable == true) {
            TextButton(onClick = session::retrySend) { Text("再试一次", color = colors.olive) }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = state.note,
                onValueChange = session::updateNote,
                modifier = Modifier.weight(1f),
                placeholder = { Text("一句此刻（可留空）") },
                singleLine = true,
            )
        }
        Spacer(Modifier.height(MurmurSpacing.sm))
        Row(horizontalArrangement = Arrangement.spacedBy(MurmurSpacing.md)) {
            TextButton(
                onClick = onPickPhoto,
                modifier = Modifier.height(48.dp),
                enabled = !state.phase.isBusy,
            ) {
                Text(if (state.photo == null) "选一张照片" else "换一张照片", color = colors.olive, fontSize = 15.sp)
            }
            Spacer(Modifier.weight(1f))
            if (state.phase.isBusy) {
                TextButton(onClick = session::cancel, modifier = Modifier.height(48.dp)) {
                    Text("取消", color = colors.secondaryInk, fontSize = 15.sp)
                }
            }
            Button(
                onClick = session::send,
                modifier = Modifier.height(48.dp),
                enabled = !state.phase.isBusy && (state.photo != null || state.note.isNotBlank()),
                colors = ButtonDefaults.buttonColors(containerColor = colors.olive),
            ) {
                Text("发送此刻", fontSize = 16.sp)
            }
        }
    }
}
