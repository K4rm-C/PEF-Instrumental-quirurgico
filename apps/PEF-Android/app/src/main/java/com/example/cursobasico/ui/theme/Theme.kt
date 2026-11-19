package com.example.cursobasico.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import com.example.cursobasico.preferences.ThemeMode

private fun materialScheme(colors: PefColors) = if (colors.isDark) {
    darkColorScheme(
        primary = colors.action,
        onPrimary = colors.onAction,
        background = colors.background,
        onBackground = colors.textPrimary,
        surface = colors.surface,
        onSurface = colors.textPrimary,
        surfaceContainer = colors.surface,
        onSurfaceVariant = colors.textSecondary,
        outline = colors.border,
        error = colors.red,
    )
} else {
    lightColorScheme(
        primary = colors.action,
        onPrimary = colors.onAction,
        background = colors.background,
        onBackground = colors.textPrimary,
        surface = colors.surface,
        onSurface = colors.textPrimary,
        surfaceContainer = colors.surface,
        onSurfaceVariant = colors.textSecondary,
        outline = colors.border,
        error = colors.red,
    )
}

/** PEF theme: light/dark/system from DataStore. no Material You dynamic colors; keep our palette */
@Composable
fun PefTheme(
    mode: ThemeMode = ThemeMode.SYSTEM,
    content: @Composable () -> Unit,
) {
    val dark = when (mode) {
        ThemeMode.SYSTEM -> isSystemInDarkTheme()
        ThemeMode.LIGHT -> false
        ThemeMode.DARK -> true
    }
    val colors = if (dark) DarkPefColors else LightPefColors
    CompositionLocalProvider(LocalPefColors provides colors) {
        MaterialTheme(
            colorScheme = materialScheme(colors),
            typography = Typography,
            content = content,
        )
    }
}

/** compat with Android Studio generated name */
@Composable
fun CursoBasicoTheme(content: @Composable () -> Unit) = PefTheme(content = content)
