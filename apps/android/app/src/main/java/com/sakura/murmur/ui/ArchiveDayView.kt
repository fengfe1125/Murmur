package com.sakura.murmur.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ArrowUpward
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sakura.murmur.ArchiveDayModel
import com.sakura.murmur.MurmurMessage
import com.sakura.murmur.MurmurMessageAuthor
import java.io.File
import java.time.LocalDate
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Pinned Chinese date formatting, matching iOS `DateFormatter.murmur`
 *  (`zh_Hans_CN`, templates `MMMd` / `EEEE`): the copy is pinned, so the dates
 *  are too — no device-locale fallback. */
internal fun archiveDayTitle(day: LocalDate): String = "${day.monthValue}月${day.dayOfMonth}日"

private val WEEKDAY_NAMES = listOf("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

internal fun archiveDaySubtitle(day: LocalDate, photoCount: Int): String =
    "${WEEKDAY_NAMES[day.dayOfWeek.value - 1]} · 聊过 $photoCount 张"

/**
 * A day's rooms, with a composer that continues the latest photo room while
 * keeping the new exchange filed on this selected day. Ported from iOS
 * `ArchiveDayView`.
 */
@Composable
fun ArchiveDayView(model: ArchiveDayModel) {
    val colors = MurmurTheme.colors
    val rows by model.archive.rows.collectAsState()
    val dayRows = remember(rows) { model.archive.rows(model.day) }
    val phase by model.phase.collectAsState()
    val failure by model.failure.collectAsState()
    val photoCount = model.archive.photoCount(model.day)
    val scrollState = rememberScrollState()

    DisposableEffect(model) { onDispose { model.close() } }

    LaunchedEffect(dayRows.size, model.isAwaitingReply) {
        scrollState.animateScrollTo(scrollState.maxValue)
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.paper)
            // No PhotoBand-style top spacer here: the day title would land
            // under the status bar. The root clears the system bars instead
            // (consumed inertly when the tab's cover already cleared them).
            .safeDrawingPadding()
            .imePadding(),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = MurmurSpacing.sm),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(archiveDayTitle(model.day), fontSize = 17.sp, color = colors.ink)
            Text(archiveDaySubtitle(model.day, photoCount), fontSize = 11.sp, color = colors.secondaryInk)
        }
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
                    .padding(horizontal = MurmurSpacing.lg, vertical = MurmurSpacing.lg),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                dayRows.forEach { row -> ArchiveRowView(row, row.imageFile?.let(model.archive::imageFile)) }
                if (model.isAwaitingReply) {
                    Row {
                        ArchiveTypingIndicator(Modifier.testTag("archive-day-typing"))
                        Spacer(Modifier.width(56.dp))
                    }
                }
                failure?.let {
                    Row {
                        Text(it.message, color = colors.coral, fontSize = 12.sp, modifier = Modifier.widthIn(max = 320.dp))
                        Spacer(Modifier.width(40.dp))
                    }
                }
            }
        }
        ArchiveComposer(model, phaseCanSend = model.canSend)
    }
}

/** One row of an archived day: the photo it carried and the words under it,
 *  read-only — there is no resend here, because nothing is in flight. */
@Composable
private fun ArchiveRowView(row: MurmurMessage, imageFile: File?) {
    val colors = MurmurTheme.colors
    val outgoing = row.author == MurmurMessageAuthor.you
    Row(modifier = Modifier.fillMaxWidth()) {
        if (outgoing) Spacer(Modifier.width(40.dp))
        Column(
            horizontalAlignment = if (outgoing) Alignment.End else Alignment.Start,
            verticalArrangement = Arrangement.spacedBy(7.dp),
        ) {
            if (imageFile != null) ArchivePhoto(imageFile)
            if (row.text.isNotEmpty()) {
                Surface(
                    shape = RoundedCornerShape(18.dp),
                    color = if (outgoing) colors.outgoingBubble else colors.raisedPaper,
                    border = if (outgoing) null else androidx.compose.foundation.BorderStroke(1.dp, colors.rule),
                ) {
                    Text(
                        text = row.text,
                        color = if (outgoing) colors.paper else colors.ink,
                        fontSize = 16.sp,
                        modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                    )
                }
            }
        }
        if (!outgoing) Spacer(Modifier.width(40.dp))
    }
}

/** An archived photo, decoded off the main thread and constrained to the
 *  same 220dp box the room shows the living photo in. */
@Composable
private fun ArchivePhoto(file: File) {
    val colors = MurmurTheme.colors
    var bitmap by remember(file) { mutableStateOf<Bitmap?>(null) }
    LaunchedEffect(file) {
        bitmap = withContext(Dispatchers.IO) { decodeArchivePhoto(file) }
    }
    Box(
        modifier = Modifier
            .widthIn(max = 220.dp)
            .heightIn(max = 220.dp)
            .clip(RoundedCornerShape(16.dp))
            .border(1.dp, colors.rule, RoundedCornerShape(16.dp))
            .background(colors.raisedPaper),
    ) {
        val decoded = bitmap
        if (decoded != null) {
            Image(
                bitmap = decoded.asImageBitmap(),
                contentDescription = "这天聊过的照片",
                contentScale = ContentScale.Crop,
            )
        } else {
            Spacer(Modifier.size(180.dp, 140.dp))
        }
    }
}

/** Bounds-first decode with a power-of-two downsample: a day with a dozen
 *  photos must not hold a dozen full-resolution bitmaps for a 220dp box. */
private fun decodeArchivePhoto(file: File, maxLongEdge: Int = 900): Bitmap? {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeFile(file.absolutePath, bounds)
    if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
    var sample = 1
    while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= maxLongEdge) sample *= 2
    return BitmapFactory.decodeFile(file.absolutePath, BitmapFactory.Options().apply { inSampleSize = sample })
}

@Composable
private fun ArchiveTypingIndicator(modifier: Modifier = Modifier) {
    val colors = MurmurTheme.colors
    Row(
        modifier = modifier
            .clip(RoundedCornerShape(18.dp))
            .background(colors.raisedPaper)
            .border(1.dp, colors.rule, RoundedCornerShape(18.dp))
            .padding(horizontal = 14.dp),
        horizontalArrangement = Arrangement.spacedBy(5.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        repeat(3) {
            Box(Modifier.size(5.dp).background(colors.secondaryInk, CircleShape))
        }
    }
}

/** The 「接着这天说」 pill. Send stays disabled until the previous turn has
 *  fully landed — the day continues one moment at a time. */
@Composable
private fun ArchiveComposer(model: ArchiveDayModel, phaseCanSend: Boolean) {
    val colors = MurmurTheme.colors
    val draft by model.draft.collectAsState()
    val canSend = phaseCanSend && draft.trim().isNotEmpty()
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
                    .testTag("archive-day-composer"),
                placeholder = { Text("接着这天说", color = colors.secondaryInk, fontSize = 16.sp) },
                maxLines = 4,
                shape = RoundedCornerShape(22.dp),
                colors = TextFieldDefaults.colors(
                    focusedContainerColor = colors.raisedPaper,
                    unfocusedContainerColor = colors.raisedPaper,
                    focusedIndicatorColor = Color.Transparent,
                    unfocusedIndicatorColor = Color.Transparent,
                ),
            )
            IconButton(
                onClick = model::send,
                enabled = canSend,
                modifier = Modifier
                    .size(44.dp)
                    .testTag("archive-day-send"),
            ) {
                Box(
                    modifier = Modifier
                        .size(36.dp)
                        .background(if (canSend) colors.ink else colors.rule, CircleShape),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        Icons.Outlined.ArrowUpward,
                        contentDescription = "继续这天的对话",
                        tint = if (canSend) colors.paper else colors.secondaryInk,
                        modifier = Modifier.size(16.dp),
                    )
                }
            }
        }
    }
}
