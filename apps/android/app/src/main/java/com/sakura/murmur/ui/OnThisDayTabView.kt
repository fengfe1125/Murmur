package com.sakura.murmur.ui

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
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
import androidx.compose.material.icons.outlined.ArrowBack
import androidx.compose.material.icons.outlined.ArrowDownward
import androidx.compose.material.icons.outlined.ArrowUpward
import androidx.compose.material.icons.outlined.ChevronLeft
import androidx.compose.material.icons.outlined.ChevronRight
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.sakura.murmur.ArchiveDayModel
import com.sakura.murmur.MurmurArchive
import com.sakura.murmur.OnThisDayModel
import com.sakura.murmur.PhotoRoomModel
import java.time.LocalDate
import java.time.YearMonth

/**
 * 当年今日, one level up from the browser it used to be — ported from iOS
 * `OnThisDayTabView`. The calendar is the first thing: a deepened circle on
 * every day that carries a room, and pressing one opens that day's thread.
 * Under it is the way in to the browser itself, which is unchanged: up to
 * send, down for the next one.
 *
 * The building blocks arrive as constructor lambdas because their factories
 * (authenticated api, photo loader, recorder) live in the shell's session —
 *  the Android counterpart of iOS `model.makeArchiveDay(day:)` /
 * `model.makePhotoRoom(image:)`, kept as wiring the shell supplies:
 *  - `makeArchiveDay` builds the [ArchiveDayModel] one calendar day opens;
 *  - `makeRoom` turns a swiped-up bitmap into the [PhotoRoomModel] the flow
 *    view cross-fades into.
 */
@Composable
fun OnThisDayTabView(
    model: OnThisDayModel,
    archive: MurmurArchive,
    makeArchiveDay: (LocalDate) -> ArchiveDayModel,
    makeRoom: (Bitmap) -> PhotoRoomModel,
    /** While a cover (a calendar day or the browser) owns the whole screen the
     *  shell hides its tab bar — iOS `fullScreenCover` semantics. */
    onCoverChange: (Boolean) -> Unit = {},
) {
    val colors = MurmurTheme.colors
    var month by remember { mutableStateOf(YearMonth.now()) }
    var openDay by remember { mutableStateOf<LocalDate?>(null) }
    var showBrowser by remember { mutableStateOf(false) }
    val rows by archive.rows.collectAsState()
    val markedDays = remember(rows) { archive.daysWithRooms }

    // Covers own the whole screen (a calendar day, the browser); the shell
    // hides its tab bar for exactly as long as one is up.
    LaunchedEffect(showBrowser, openDay) {
        onCoverChange(showBrowser || openDay != null)
    }
    DisposableEffect(Unit) {
        onDispose { onCoverChange(false) }
    }

    LaunchedEffect(Unit) {
        archive.load()
        model.refreshAuthorization()
    }
    // Coming back from Settings may have flipped the door: re-read it on
    // resume, exactly as iOS re-reads on `.active`.
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) model.refreshAuthorization()
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    // The tab page and both of its covers (a calendar day, the browser) draw
    // under the same root, so the root is the one place that clears the system
    // bars; the insets are consumed here and covers do not pad again.
    Box(modifier = Modifier.fillMaxSize().background(colors.paper).safeDrawingPadding()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState()),
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .widthIn(max = 560.dp)
                    .align(Alignment.CenterHorizontally)
                    .padding(horizontal = MurmurSpacing.lg, vertical = MurmurSpacing.sm),
                verticalArrangement = Arrangement.spacedBy(20.dp),
            ) {
                MonthHeader(
                    month = month,
                    onStep = { step ->
                        val next = month.plusMonths(step.toLong())
                        // 下月 disabled past the current month: there is no
                        // archive there to look up yet.
                        if (step < 0 || !next.isAfter(YearMonth.now())) month = next
                    },
                )
                MurmurMonthView(
                    month = month,
                    markedDays = markedDays,
                    onSelect = { openDay = it },
                )
                OnThisDayEntryCard { showBrowser = true }
            }
        }

        // A calendar day pushes its thread over the tab. The day's composer
        // keeps the exchange filed on the selected day; closing drops back to
        // the calendar.
        openDay?.let { day ->
            Box(modifier = Modifier.fillMaxSize().background(colors.paper)) {
                ArchiveDayView(model = makeArchiveDay(day))
                IconButton(
                    onClick = { openDay = null },
                    modifier = Modifier
                        .align(Alignment.TopStart)
                        .padding(start = MurmurSpacing.sm, top = 10.dp)
                        .size(44.dp)
                        .testTag("archive-day-back"),
                ) {
                    Surface(
                        shape = CircleShape,
                        color = colors.raisedPaper,
                        border = androidx.compose.foundation.BorderStroke(1.dp, colors.rule),
                        modifier = Modifier.size(34.dp),
                    ) {
                        Box(contentAlignment = Alignment.Center) {
                            Icon(
                                Icons.Outlined.ArrowBack,
                                contentDescription = "返回当年今日",
                                tint = colors.ink,
                            )
                        }
                    }
                }
            }
        }

        // The browser keeps its cover: the send-off owns the whole screen,
        // and a tab bar under it would be a second thing to look at
        // mid-gesture. (The shell hides its tab bar while this is up, or
        // wraps this in its own cover — see the wiring notes in the handoff.)
        if (showBrowser) {
            Box(modifier = Modifier.fillMaxSize().background(colors.paper)) {
                OnThisDayFlowView(
                    model = model,
                    makeRoom = makeRoom,
                    onClose = { showBrowser = false },
                )
            }
        }
    }
}

@Composable
private fun MonthHeader(month: YearMonth, onStep: (Int) -> Unit) {
    val colors = MurmurTheme.colors
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("当年今日", fontSize = 22.sp, color = colors.ink)
            Text("${month.year}年${month.monthValue}月", fontSize = 11.sp, color = colors.secondaryInk)
        }
        IconButton(onClick = { onStep(-1) }, modifier = Modifier.testTag("archive-prev-month")) {
            Icon(Icons.Outlined.ChevronLeft, contentDescription = "上个月", tint = colors.ink)
        }
        IconButton(
            onClick = { onStep(1) },
            enabled = month != YearMonth.now(),
            modifier = Modifier.testTag("archive-next-month"),
        ) {
            Icon(
                Icons.Outlined.ChevronRight,
                contentDescription = "下个月",
                tint = if (month == YearMonth.now()) colors.rule else colors.ink,
            )
        }
    }
}

/**
 * The door into the browser. Deliberately the largest thing under the
 * calendar: the calendar is where you look something up, this is where the
 * feature actually happens. The subtitle is deliberately not a count — the
 * album is only read once the cover is up, and a number that has to be
 * corrected a second later is worse than none.
 */
@Composable
private fun OnThisDayEntryCard(onClick: () -> Unit) {
    val colors = MurmurTheme.colors
    Surface(
        shape = RoundedCornerShape(22.dp),
        color = colors.raisedPaper,
        border = androidx.compose.foundation.BorderStroke(1.dp, colors.rule),
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(22.dp))
            .clickable(onClick = onClick)
            .testTag("onthisday-entry"),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 18.dp, vertical = 20.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            ParticlePreview()
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("当年今日", fontSize = 18.sp, color = colors.ink)
                Text("翻翻同一天的旧照片", fontSize = 12.sp, color = colors.secondaryInk)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                EntryHint(Icons.Outlined.ArrowUpward, "发给 Murmur")
                EntryHint(Icons.Outlined.ArrowDownward, "换下一张")
            }
        }
    }
}

@Composable
private fun EntryHint(icon: ImageVector, text: String) {
    val colors = MurmurTheme.colors
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
        Icon(icon, contentDescription = null, tint = colors.olive, modifier = Modifier.size(11.dp))
        Text(text, fontSize = 11.sp, color = colors.secondaryInk)
    }
}

/** A photo caught mid-dissolve. Not the shader — this is a still of what the
 *  gesture does, so the door looks like the room behind it. The little
 *  squares are the same seeded LCG the iOS `ParticlePreview` draws. */
@Composable
private fun ParticlePreview() {
    val colors = MurmurTheme.colors
    Box(modifier = Modifier.width(132.dp).height(132.dp), contentAlignment = Alignment.CenterEnd) {
        Box(
            modifier = Modifier
                .size(132.dp)
                .clip(RoundedCornerShape(18.dp))
                .background(
                    Brush.linearGradient(
                        colors = listOf(colors.olive.copy(alpha = 0.55f), colors.ink.copy(alpha = 0.55f)),
                    ),
                )
                .border(1.dp, colors.rule, RoundedCornerShape(18.dp)),
        )
        Canvas(modifier = Modifier.size(92.dp, 132.dp)) {
            var seed = 7L
            fun next(): Double {
                seed = seed * 6_364_136_223_846_793_005L + 1_442_695_040_888_963_407L
                return (seed ushr 33).toDouble() / (1L shl 31).toDouble()
            }
            val radius = CornerRadius(1.dp.toPx())
            repeat(26) {
                val side = (3.0 + next() * 4.0).toFloat()
                val x = (size.width - 54.0 + next() * 54.0).toFloat()
                val y = (next() * size.height).toFloat()
                drawRoundRect(
                    color = colors.olive.copy(alpha = (0.2 + next() * 0.6).toFloat()),
                    topLeft = Offset(x, y),
                    size = Size(side, side),
                    cornerRadius = radius,
                )
            }
        }
    }
}
