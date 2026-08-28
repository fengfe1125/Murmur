package com.sakura.murmur.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Design tokens, transcribed 1:1 from the UIColor definitions in
 * MurmurApp/MurmurChatView.swift (design.md is the locked visual system).
 * All components must read colours/spacing from here — never invent their own.
 */
class MurmurColors(
    val paper: Color,
    val raisedPaper: Color,
    val ink: Color,
    val secondaryInk: Color,
    val rule: Color,
    val olive: Color,
    val coral: Color,
    val outgoingBubble: Color,
    val isLight: Boolean,
)

val LightMurmurColors = MurmurColors(
    paper = Color(0.96f, 0.95f, 0.91f),
    raisedPaper = Color(0.99f, 0.98f, 0.95f),
    ink = Color(0.12f, 0.12f, 0.10f),
    secondaryInk = Color(0.38f, 0.38f, 0.33f),
    rule = Color(0.81f, 0.80f, 0.73f),
    olive = Color(0.32f, 0.37f, 0.18f),
    coral = Color(0.76f, 0.27f, 0.20f),
    outgoingBubble = Color(0.42f, 0.48f, 0.25f),
    isLight = true,
)

val DarkMurmurColors = MurmurColors(
    paper = Color(0.08f, 0.08f, 0.07f),
    raisedPaper = Color(0.12f, 0.12f, 0.10f),
    ink = Color(0.92f, 0.91f, 0.86f),
    secondaryInk = Color(0.62f, 0.62f, 0.56f),
    rule = Color(0.24f, 0.24f, 0.20f),
    olive = Color(0.66f, 0.70f, 0.47f),
    coral = Color(0.93f, 0.53f, 0.43f),
    outgoingBubble = Color(0.26f, 0.33f, 0.18f),
    isLight = false,
)

val LocalMurmurColors = staticCompositionLocalOf<MurmurColors> { LightMurmurColors }

/** 4pt spacing ladder (design.md). */
object MurmurSpacing {
    val xs = 4.dp
    val sm = 8.dp
    val md = 12.dp
    val lg = 16.dp
    val xl = 24.dp
    val xxl = 32.dp
    val xxxl = 40.dp
    val huge = 64.dp
}

private val MurmurTypography = Typography(
    // Wordmark / headings map New York (serif) → FontFamily.Serif; bold only.
    headlineMedium = androidx.compose.ui.text.TextStyle(
        fontFamily = FontFamily.Serif,
        fontWeight = FontWeight.Bold,
        fontSize = 28.sp,
    ),
    titleLarge = androidx.compose.ui.text.TextStyle(
        fontFamily = FontFamily.Serif,
        fontWeight = FontWeight.Bold,
        fontSize = 22.sp,
    ),
)

object MurmurTheme {
    val colors: MurmurColors
        @Composable get() = LocalMurmurColors.current
}

@Composable
fun MurmurTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val colors = if (darkTheme) DarkMurmurColors else LightMurmurColors
    val materialScheme = if (darkTheme) {
        darkColorScheme(
            background = colors.paper,
            surface = colors.raisedPaper,
            onBackground = colors.ink,
            onSurface = colors.ink,
            primary = colors.olive,
            onPrimary = colors.paper,
            error = colors.coral,
            outline = colors.rule,
        )
    } else {
        lightColorScheme(
            background = colors.paper,
            surface = colors.raisedPaper,
            onBackground = colors.ink,
            onSurface = colors.ink,
            primary = colors.olive,
            onPrimary = colors.paper,
            error = colors.coral,
            outline = colors.rule,
        )
    }
    CompositionLocalProvider(LocalMurmurColors provides colors) {
        MaterialTheme(
            colorScheme = materialScheme,
            typography = MurmurTypography,
            content = content,
        )
    }
}
