package com.sakura.murmur.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.LocalDate
import java.time.YearMonth

/**
 * One month laid out in rows of seven, with `null` for the days either side
 * of it. Murmur is not localised, so the grid is pinned: Monday first,
 * whatever phone this is — the Android counterpart of iOS `Calendar.murmur`
 * (`firstWeekday = 2`). Pure `java.time` math, kept off the composable so a
 * JVM test can hold the layout contract.
 */
internal object MurmurMonthGrid {
    /** Pinned Chinese weekday header, Monday first — the copy is pinned, so
     *  the calendar follows the copy rather than the device locale. */
    val WEEKDAY_LABELS = listOf("一", "二", "三", "四", "五", "六", "日")

    fun weeks(month: YearMonth): List<List<LocalDate?>> {
        val lead = month.atDay(1).dayOfWeek.value - 1
        val cells = mutableListOf<LocalDate?>()
        repeat(lead) { cells.add(null) }
        for (day in 1..month.lengthOfMonth()) cells.add(month.atDay(day))
        while (cells.size % 7 != 0) cells.add(null)
        return cells.chunked(7)
    }
}

/**
 * One month, with a deepened circle on every day that has a room behind it.
 * The circle is the affordance: nothing else on the grid is pressable, so a
 * day that carries one is the only thing that answers. Ported from iOS
 * `MurmurMonthView`.
 */
@Composable
fun MurmurMonthView(
    month: YearMonth,
    markedDays: Set<LocalDate>,
    today: LocalDate = LocalDate.now(),
    onSelect: (LocalDate) -> Unit,
) {
    val colors = MurmurTheme.colors
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(22.dp))
            .background(colors.raisedPaper)
            .border(1.dp, colors.rule, RoundedCornerShape(22.dp))
            .padding(horizontal = 14.dp, vertical = 14.dp),
    ) {
        Row(modifier = Modifier.fillMaxWidth().padding(bottom = 2.dp)) {
            MurmurMonthGrid.WEEKDAY_LABELS.forEach { symbol ->
                Text(
                    text = symbol,
                    color = colors.secondaryInk,
                    fontSize = 11.sp,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.weight(1f),
                )
            }
        }
        MurmurMonthGrid.weeks(month).forEach { week ->
            Row(modifier = Modifier.fillMaxWidth()) {
                week.forEach { day -> MonthCell(day, day in markedDays, day == today, onSelect, Modifier.weight(1f)) }
            }
        }
    }
}

@Composable
private fun MonthCell(
    day: LocalDate?,
    marked: Boolean,
    isToday: Boolean,
    onSelect: (LocalDate) -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = MurmurTheme.colors
    if (day == null) {
        Box(modifier = modifier.aspectRatio(1f))
        return
    }
    val accent: Color = colors.olive
    Box(
        modifier = modifier.aspectRatio(1f),
        contentAlignment = Alignment.Center,
    ) {
        Box(
            modifier = Modifier
                .size(34.dp)
                .clip(CircleShape)
                .background(if (marked) accent else Color.Transparent)
                .then(
                    // Today is a ring and nothing else, so "has a record" and
                    // "is today" stay two different marks rather than one
                    // ambiguous one — the two marks never merge.
                    if (isToday) Modifier.border(1.5.dp, accent, CircleShape) else Modifier,
                )
                .clickable(enabled = marked) { onSelect(day) }
                .testTag("archive-day-$day"),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = "${day.dayOfMonth}",
                color = when {
                    marked -> colors.paper
                    else -> colors.secondaryInk
                },
                fontSize = 14.sp,
                textAlign = TextAlign.Center,
            )
        }
    }
}
