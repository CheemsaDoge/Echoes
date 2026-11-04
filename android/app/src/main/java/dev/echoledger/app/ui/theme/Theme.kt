package dev.echoledger.app.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val MemoryDarkColorScheme = darkColorScheme(
    primary = PrimaryNeonGreen,
    onPrimary = Color(0xFF0A140B),
    primaryContainer = SurfaceCardGreen,
    onPrimaryContainer = TextPrimary,
    secondary = PrimaryBrightGreen,
    onSecondary = Color(0xFF0A140B),
    background = BgDark,
    onBackground = TextPrimary,
    surface = SurfaceDark,
    onSurface = TextPrimary,
    surfaceVariant = SurfaceCardElevated,
    onSurfaceVariant = TextSecondary,
    outline = DividerDark
)

@Composable
fun EchoesTheme(
    content: @Composable () -> Unit
) {
    MaterialTheme(
        colorScheme = MemoryDarkColorScheme,
        typography = Typography,
        content = content
    )
}
