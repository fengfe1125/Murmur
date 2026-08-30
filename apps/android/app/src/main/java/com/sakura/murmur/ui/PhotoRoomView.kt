package com.sakura.murmur.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ArrowUpward
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.NorthWest
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sakura.murmur.PhotoRoomModel

/**
 * 当年今日's second half, ported from iOS `PhotoRoomView`: the photo settles
 * at the top of the screen and stays there while the person tells Murmur what
 * it is. The guess and the three openers are the way in; everything after
 * them is an ordinary exchange about one picture.
 *
 * `MurmurTranscriptView.kt` (the chat transcript, built by the parallel
 * chat-transcript task) is a separate surface, and iOS deliberately keeps
 * the room's bubble shape private to the transcript anyway ("one small
 * shape copied is cheaper than a shared surface") — so the bubble, typing
 * dots and composer pill here are the room's own small components.
 */
@Composable
fun PhotoRoomView(model: PhotoRoomModel, onClose: () -> Unit) {
    val colors = MurmurTheme.colors
    val lines by model.lines.collectAsState()
    val openers by model.openers.collectAsState()
    val phase by model.phase.collectAsState()
    val failure by model.failure.collectAsState()
    val photo by model.photo.collectAsState()
    val scrollState = rememberScrollState()

    // Opening the room is a view concern, exactly as on iOS (`.task`).
    LaunchedEffect(model) { model.open() }
    DisposableEffect(model) { onDispose { model.close() } }

    LaunchedEffect(lines.size, openers.size, phase) {
        scrollState.animateScrollTo(scrollState.maxValue)
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.paper)
            .imePadding(),
    ) {
        Column(modifier = Modifier.fillMaxSize()) {
            PhotoBand(photo, modifier = Modifier.padding(top = 64.dp, bottom = 16.dp))
            Column(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .verticalScroll(scrollState),
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .widthIn(max = 520.dp)
                        .align(Alignment.CenterHorizontally)
                        .padding(horizontal = MurmurSpacing.lg, vertical = MurmurSpacing.md),
                    verticalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(MurmurSpacing.md),
                ) {
                    lines.forEach { line -> RoomBubbleLine(line) }
                    if (model.isAwaitingReply) {
                        Row {
                            RoomTypingIndicator(Modifier.testTag("photo-room-typing"))
                            Spacer(Modifier.width(56.dp))
                        }
                    }
                    if (openers.isNotEmpty()) {
                        OpenersRow(openers, onPick = model::pick)
                    }
                    failure?.let { RoomFailureRow(model, it) }
                }
            }
            RoomComposer(model)
        }
        IconButton(
            onClick = onClose,
            modifier = Modifier
                .padding(start = MurmurSpacing.sm, top = 10.dp)
                .size(44.dp)
                .testTag("close-photo-room"),
        ) {
            Surface(
                shape = CircleShape,
                color = colors.raisedPaper,
                border = androidx.compose.foundation.BorderStroke(1.dp, colors.rule),
                modifier = Modifier.size(34.dp),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Icon(Icons.Outlined.Close, contentDescription = "离开这张照片", tint = colors.ink)
                }
            }
        }
    }
}

/** The fixed 220dp band rather than the photo's own aspect ratio: the room's
 *  job is the conversation under it, and a tall portrait shot left to itself
 *  takes the screen. */
@Composable
private fun PhotoBand(photo: android.graphics.Bitmap?, modifier: Modifier = Modifier) {
    val colors = MurmurTheme.colors
    Box(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = MurmurSpacing.lg)
            .height(220.dp)
            .clip(RoundedCornerShape(18.dp))
            .border(1.dp, colors.rule, RoundedCornerShape(18.dp))
            .background(colors.raisedPaper),
        contentAlignment = Alignment.Center,
    ) {
        val bitmap = photo
        if (bitmap != null) {
            Image(
                bitmap = bitmap.asImageBitmap(),
                contentDescription = "你带进来的那张照片",
                modifier = Modifier
                    .fillMaxSize()
                    .testTag("photo-room-photo"),
                contentScale = ContentScale.Crop,
            )
        }
    }
}

/** One line of the exchange. Outgoing hugs the trailing edge, Murmur the
 *  leading one; a minimum opposite spacer keeps short bubbles from crossing
 *  the whole width. */
@Composable
private fun RoomBubbleLine(line: PhotoRoomModel.Line) {
    val colors = MurmurTheme.colors
    val outgoing = line.author == PhotoRoomModel.LineAuthor.Mine
    Row(modifier = Modifier.fillMaxWidth()) {
        if (outgoing) Spacer(Modifier.width(56.dp))
        val shape = RoundedCornerShape(
            topStart = 18.dp,
            topEnd = 18.dp,
            bottomStart = if (outgoing) 18.dp else 5.dp,
            bottomEnd = if (outgoing) 5.dp else 18.dp,
        )
        Surface(
            shape = shape,
            color = if (outgoing) colors.outgoingBubble else colors.raisedPaper,
            border = if (outgoing) null else androidx.compose.foundation.BorderStroke(1.dp, colors.rule),
        ) {
            Text(
                text = line.text,
                color = if (outgoing) colors.paper else colors.ink,
                fontSize = 16.sp,
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
            )
        }
        if (!outgoing) Spacer(Modifier.width(56.dp))
    }
}

/** The three openers under the guess. One tap lifts an opener into the
 *  field; saying anything closes the doors behind it. */
@Composable
private fun OpenersRow(openers: List<String>, onPick: (String) -> Unit) {
    val colors = MurmurTheme.colors
    Column(
        modifier = Modifier.widthIn(max = 320.dp),
        verticalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(MurmurSpacing.sm),
    ) {
        openers.forEachIndexed { index, opener ->
            Surface(
                shape = RoundedCornerShape(12.dp),
                color = colors.raisedPaper,
                border = androidx.compose.foundation.BorderStroke(1.dp, colors.rule),
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 44.dp)
                    .clickable { onPick(opener) }
                    .testTag("opener-$index"),
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = opener,
                        color = colors.ink,
                        fontSize = 14.sp,
                        modifier = Modifier.weight(1f),
                        overflow = TextOverflow.Visible,
                    )
                    Icon(
                        Icons.Outlined.NorthWest,
                        contentDescription = null,
                        tint = colors.secondaryInk,
                        modifier = Modifier.size(11.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun RoomFailureRow(model: PhotoRoomModel, failure: com.sakura.murmur.MurmurFailure) {
    val colors = MurmurTheme.colors
    Column(
        modifier = Modifier.widthIn(max = 320.dp),
        verticalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(MurmurSpacing.sm),
    ) {
        Text(failure.message, color = colors.coral, fontSize = 12.sp)
        if (model.canRetryOpening && failure.retryable) {
            Button(
                onClick = model::open,
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(containerColor = colors.raisedPaper, contentColor = colors.ink),
                border = androidx.compose.foundation.BorderStroke(1.dp, colors.rule),
                modifier = Modifier
                    .heightIn(min = 44.dp)
                    .testTag("retry-photo-room"),
            ) {
                Text("再试一次", fontSize = 14.sp)
            }
        }
    }
}

/** Three quiet dots with an opacity pulse — the same motion the rest of the
 *  Android app allows. */
@Composable
private fun RoomTypingIndicator(modifier: Modifier = Modifier) {
    val colors = MurmurTheme.colors
    val transition = rememberInfiniteTransition(label = "room-typing")
    val alpha by transition.animateFloat(
        initialValue = 0.35f,
        targetValue = 0.9f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 600, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "room-typing-alpha",
    )
    Row(
        modifier = modifier
            .clip(RoundedCornerShape(18.dp))
            .background(colors.raisedPaper)
            .border(1.dp, colors.rule, RoundedCornerShape(18.dp))
            .padding(horizontal = MurmurSpacing.lg, vertical = MurmurSpacing.md),
        horizontalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(5.dp),
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

/** The pill the line is typed into. Send stays disabled until the field is
 *  live and has words in it — an unopened room does not take dictation. */
@Composable
private fun RoomComposer(model: PhotoRoomModel) {
    val colors = MurmurTheme.colors
    val draft by model.draft.collectAsState()
    val canSend = model.canSend
    Surface(
        shape = RoundedCornerShape(26.dp),
        color = colors.raisedPaper,
        border = androidx.compose.foundation.BorderStroke(1.dp, colors.rule),
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = MurmurSpacing.lg, vertical = MurmurSpacing.sm),
    ) {
        Row(
            modifier = Modifier.padding(start = 5.dp, end = 5.dp, top = 4.dp, bottom = 4.dp),
            verticalAlignment = Alignment.Bottom,
        ) {
            TextField(
                value = draft,
                onValueChange = { model.draft.value = it },
                modifier = Modifier
                    .weight(1f)
                    .testTag("photo-room-composer"),
                placeholder = { Text("跟它说说这张照片", color = colors.secondaryInk, fontSize = 16.sp) },
                maxLines = 4,
                shape = RoundedCornerShape(22.dp),
                colors = TextFieldDefaults.colors(
                    focusedContainerColor = colors.raisedPaper,
                    unfocusedContainerColor = colors.raisedPaper,
                    focusedIndicatorColor = androidx.compose.ui.graphics.Color.Transparent,
                    unfocusedIndicatorColor = androidx.compose.ui.graphics.Color.Transparent,
                ),
            )
            IconButton(
                onClick = model::send,
                enabled = canSend,
                modifier = Modifier
                    .size(44.dp)
                    .testTag("photo-room-send"),
            ) {
                Box(
                    modifier = Modifier
                        .size(36.dp)
                        .background(if (canSend) colors.ink else colors.rule, CircleShape),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        Icons.Outlined.ArrowUpward,
                        contentDescription = "说给 Murmur",
                        tint = if (canSend) colors.paper else colors.secondaryInk,
                        modifier = Modifier.size(16.dp),
                    )
                }
            }
        }
    }
}
