package com.sakura.murmur.ui

import androidx.compose.foundation.border
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Error
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.RoundRect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Outline
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sakura.murmur.MurmurDeliveryState
import com.sakura.murmur.MurmurMessage
import com.sakura.murmur.MurmurMessageAuthor
import com.sakura.murmur.MurmurPhotoPreview
import com.sakura.murmur.MurmurSendFailure
import com.sakura.murmur.MurmurSessionModel
import com.sakura.murmur.TranscriptPhoto
import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId

/**
 * A rounded bubble with one squared-off corner on the speaker's side, the way
 * a tail reads without drawing an actual tail on every message — the
 * counterpart of `BubbleShape` in `MurmurTranscriptView.swift`.
 */
class RoomBubbleShape(private val isOutgoing: Boolean) : Shape {
    override fun createOutline(size: Size, layoutDirection: LayoutDirection, density: Density): Outline {
        val radius = CornerRadius(with(density) { 18.dp.toPx() })
        val tail = CornerRadius(with(density) { 5.dp.toPx() })
        return Outline.Rounded(
            RoundRect(
                rect = Rect(0f, 0f, size.width, size.height),
                topLeft = radius,
                topRight = radius,
                bottomLeft = if (isOutgoing) radius else tail,
                bottomRight = if (isOutgoing) tail else radius,
            ),
        )
    }
}

/** One row of the conversation.  The composable counterpart of `MessageRow`
 *  in `MurmurTranscriptView.swift`: photo and/or text bubble on the
 *  speaker's side, then a caption with the time and either the transport
 *  ticks or — when the send failed — why, in words. */
@Composable
private fun MessageRow(
    message: MurmurMessage,
    index: Int,
    imageFile: File?,
    sendFailure: MurmurSendFailure?,
    onOpenImage: (MurmurPhotoPreview) -> Unit,
    onAskResend: (MurmurMessage) -> Unit,
) {
    val colors = MurmurTheme.colors
    val isOutgoing = message.author == MurmurMessageAuthor.you
    Row(modifier = Modifier.fillMaxWidth()) {
        if (isOutgoing) Spacer(Modifier.width(if (sendFailure == null) 56.dp else 20.dp))
        Column(horizontalAlignment = if (isOutgoing) Alignment.End else Alignment.Start) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (isOutgoing && sendFailure != null && sendFailure.canResend) {
                    // The mark is a button exactly when pressing it would do
                    // something; the caption under the bubble says which of
                    // the two it is.
                    Icon(
                        Icons.Outlined.Error,
                        contentDescription = "重新发送",
                        tint = colors.coral,
                        modifier = Modifier
                            .size(44.dp)
                            .clickable(
                                interactionSource = remember { MutableInteractionSource() },
                                indication = null,
                            ) { onAskResend(message) }
                            .testTag("resend-moment"),
                    )
                }
                Column(
                    horizontalAlignment = if (isOutgoing) Alignment.End else Alignment.Start,
                    modifier = Modifier.testTag("murmur-message-$index"),
                ) {
                    if (imageFile != null) {
                        TranscriptPhoto(file = imageFile, onOpen = {
                            onOpenImage(MurmurPhotoPreview(id = message.id, file = imageFile))
                        })
                    }
                    if (message.text.isNotEmpty()) {
                        val bubbleShape = RoomBubbleShape(isOutgoing)
                        val bubbleModifier = if (isOutgoing) {
                            Modifier.background(colors.outgoingBubble, bubbleShape)
                        } else {
                            Modifier
                                .background(colors.raisedPaper, bubbleShape)
                                .border(1.dp, colors.rule, bubbleShape)
                        }
                        SelectionContainer {
                            Text(
                                text = message.text,
                                color = if (isOutgoing) colors.paper else colors.ink,
                                fontSize = 17.sp,
                                modifier = Modifier
                                    .then(bubbleModifier)
                                    .padding(horizontal = 14.dp, vertical = 10.dp),
                            )
                        }
                    }
                }
            }
            // The line under the bubble: the time, and then either the
            // transport ticks or — when the send failed — why, in words.
            Row(modifier = Modifier.padding(horizontal = 4.dp)) {
                if (sendFailure != null) {
                    Text(
                        text = "${formatTime(message.sentAt)}  ${caption(sendFailure)}",
                        color = colors.coral,
                        fontSize = 11.sp,
                    )
                } else {
                    Text(
                        text = formatTime(message.sentAt),
                        color = colors.secondaryInk.copy(alpha = 0.85f),
                        fontSize = 11.sp,
                    )
                    if (isOutgoing) {
                        Spacer(Modifier.width(5.dp))
                        DeliveryTicks(state = message.delivery)
                    }
                }
            }
        }
        if (!isOutgoing) Spacer(Modifier.width(56.dp))
    }
}

/** 「暂时没有连上 Murmur · 轻点重新发送」 — the reason keeps its own wording
 *  and loses only its full stop, so the offer reads as part of the same line. */
private fun caption(failure: MurmurSendFailure): String {
    val reason = failure.message.removeSuffix("。")
    return if (failure.canResend) "$reason · 轻点重新发送" else failure.message
}

/** The transport ticks: a clock while the send is in flight, then two
 *  overlapping checks — WhatsApp-style — the second sliding in once Murmur
 *  starts composing.  Nothing for a failed row: the mark beside the bubble
 *  and the caption under it say that already. */
@Composable
private fun DeliveryTicks(state: MurmurDeliveryState) {
    val colors = MurmurTheme.colors
    when (state) {
        MurmurDeliveryState.sending -> {
            val color = colors.secondaryInk.copy(alpha = 0.7f)
            Canvas(Modifier.size(width = 16.dp, height = 12.dp)) {
                val radius = 4.5.dp.toPx()
                val c = center.copy(x = center.x - 1.dp.toPx())
                drawCircle(color = color, radius = radius, center = c, style = Stroke(width = 1.dp.toPx()))
                drawLine(color = color, start = c, end = c.copy(y = c.y - radius * 0.55f), strokeWidth = 1.dp.toPx())
                drawLine(
                    color = color,
                    start = c,
                    end = c.copy(x = c.x + radius * 0.45f, y = c.y + radius * 0.15f),
                    strokeWidth = 1.dp.toPx(),
                )
            }
        }
        MurmurDeliveryState.failed -> Unit
        MurmurDeliveryState.sent, MurmurDeliveryState.answered -> {
            val tickColor = if (state == MurmurDeliveryState.answered) {
                colors.outgoingBubble
            } else {
                colors.secondaryInk.copy(alpha = 0.75f)
            }
            val secondAlpha = if (state == MurmurDeliveryState.answered) 1f else 0.35f
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("✓", color = tickColor, fontSize = 10.sp)
                Text("✓", color = tickColor, fontSize = 10.sp, modifier = Modifier.offset(x = (-4).dp).alpha(secondAlpha))
            }
        }
    }
}

/** 「2026年8月20日」 between two days of talk. */
@Composable
private fun DaySeparator(date: Instant) {
    val colors = MurmurTheme.colors
    val day = LocalDate.ofInstant(date, ZoneId.systemDefault())
    Text(
        text = "${day.year}年${day.monthValue}月${day.dayOfMonth}日",
        color = colors.secondaryInk,
        fontSize = 11.sp,
        modifier = Modifier
            .padding(vertical = 6.dp)
            .background(colors.raisedPaper, CircleShape)
            .border(1.dp, colors.rule, CircleShape)
            .padding(horizontal = 11.dp, vertical = 5.dp)
            .testTag("day-separator"),
    )
}

private fun formatTime(instant: Instant): String {
    val time = LocalTime.ofInstant(instant, ZoneId.systemDefault())
    return "%02d:%02d".format(time.hour, time.minute)
}

/** Three quiet dots — the counterpart of `TypingIndicator` in
 *  `MurmurTranscriptView.swift`; shown while a send is on the wire. */
@Composable
private fun TypingIndicator() {
    val colors = MurmurTheme.colors
    val bubbleShape = RoomBubbleShape(isOutgoing = false)
    Row(
        modifier = Modifier
            .background(colors.raisedPaper, bubbleShape)
            .border(1.dp, colors.rule, bubbleShape)
            .testTag("typing-indicator")
            .padding(horizontal = 15.dp, vertical = 13.dp),
        horizontalArrangement = Arrangement.spacedBy(5.dp),
    ) {
        repeat(3) {
            Box(
                Modifier
                    .size(7.dp)
                    .alpha(0.55f)
                    .background(colors.secondaryInk, CircleShape),
            )
        }
    }
}

/** The opening line, when there is nothing to scroll back to yet. */
@Composable
private fun EmptyTranscript(modifier: Modifier = Modifier) {
    val colors = MurmurTheme.colors
    Column(
        modifier = modifier
            .fillMaxSize()
            .testTag("empty-transcript"),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            "发来眼前的一刻。",
            style = MaterialTheme.typography.titleLarge,
            color = colors.ink,
        )
        Spacer(Modifier.height(12.dp))
        Text("可以是一张照片，也可以只说一句。", color = colors.secondaryInk, fontSize = 15.sp)
    }
}

/**
 * The scrollback the person reads — the counterpart of
 * `MurmurTranscriptView.swift`.  A reversed lazy column pins the newest line
 * against the composer through resizes (the keyboard changing the room's
 * height is the case that matters), and day separators hang above the oldest
 * line of each day.
 *
 * Failed rows carry their verdict under the bubble; pressing the mark asks
 * before sending again, the way the iOS question card does — the question
 * itself is Material chrome (an explicit 取消 button) rather than the pinned
 * paper card, which has no Compose equivalent.
 */
@Composable
fun MurmurTranscriptView(
    state: MurmurSessionModel.UiState,
    showsTyping: Boolean,
    imageFile: (String) -> File,
    onOpenImage: (MurmurPhotoPreview) -> Unit,
    onResend: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val messages = state.messages
    var resendQuestion by remember { mutableStateOf<MurmurMessage?>(null) }

    if (messages.isEmpty() && !showsTyping) {
        EmptyTranscript(modifier)
        return
    }

    // Newest first: item 0 rests against the composer and the list grows
    // upward, so no scroll bookkeeping is needed to keep it there.
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        reverseLayout = true,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (showsTyping) {
            item(key = "typing") {
                Row {
                    TypingIndicator()
                    Spacer(Modifier.width(56.dp))
                }
            }
        }
        items(
            count = messages.size,
            key = { i -> messages[messages.size - 1 - i].id },
        ) { i ->
            val index = messages.size - 1 - i
            val message = messages[index]
            // The separator hangs above the oldest line of a day; in reversed
            // layout that line is the one whose older neighbour belongs to a
            // different day (index + 1 is always the older row).
            val older = messages.getOrNull(index + 1)
            if (older == null || dayOf(older.sentAt) != dayOf(message.sentAt)) {
                DaySeparator(message.sentAt)
            }
            MessageRow(
                message = message,
                index = index,
                imageFile = message.imageFile?.let(imageFile),
                sendFailure = sendFailureFor(state, message),
                onOpenImage = onOpenImage,
                onAskResend = { resendQuestion = it },
            )
        }
    }

    resendQuestion?.let { question ->
        val failure = sendFailureFor(state, question) ?: return@let
        MurmurConfirmDialog(
            title = "重新发送这一条？",
            message = failure.message,
            confirmTitle = "重新发送",
            destructive = false,
            onConfirm = {
                resendQuestion = null
                onResend(question.id)
            },
            onDismiss = { resendQuestion = null },
        )
    }
}

/** One rule for the mark: a row of the person's own that ended in `.failed`
 *  always carries one, and never carries ticks.  The model's reason is used
 *  when it has one; a row read back from disk kept the verdict but not the
 *  wording, and says only that much — but it is still pressable, because the
 *  transcript holds everything the send needs. */
private fun sendFailureFor(state: MurmurSessionModel.UiState, message: MurmurMessage): MurmurSendFailure? {
    if (message.author != MurmurMessageAuthor.you || message.delivery != MurmurDeliveryState.failed) return null
    return state.sendFailures[message.id]
        ?: MurmurSendFailure.interrupted(canResend = message.text.isNotEmpty() || message.imageFile != null)
}

private fun dayOf(instant: Instant): LocalDate = LocalDate.ofInstant(instant, ZoneId.systemDefault())
