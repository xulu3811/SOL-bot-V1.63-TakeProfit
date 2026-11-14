package com.lu.cryptobot.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Immutable
data class BotExtendedColors(
    val isDark: Boolean,
    val gain: Color,
    val gainSoft: Color,
    val loss: Color,
    val lossSoft: Color,
    val warn: Color,
    val warnSoft: Color,
    val info: Color,
    val infoSoft: Color,
    val purple: Color,
    val ema20: Color,
    val ema50: Color,
    val hairline: Color,
)

private val LightBotColors = BotExtendedColors(
    isDark = false,
    gain = Color(0xFF137333),
    gainSoft = Color(0xFFE6F4EA),
    loss = Color(0xFFC5221F),
    lossSoft = Color(0xFFFCE8E6),
    warn = Color(0xFFB06000),
    warnSoft = Color(0xFFFEF7E0),
    info = Color(0xFF1A73E8),
    infoSoft = Color(0xFFE8F0FE),
    purple = Color(0xFF8430CE),
    ema20 = Color(0xFFF9AB00),
    ema50 = Color(0xFF1A73E8),
    hairline = Color(0xFFDDE3EA),
)

private val DarkBotColors = BotExtendedColors(
    isDark = true,
    gain = Color(0xFF81C995),
    gainSoft = Color(0xFF103821),
    loss = Color(0xFFF28B82),
    lossSoft = Color(0xFF471615),
    warn = Color(0xFFFDD663),
    warnSoft = Color(0xFF3E2D0B),
    info = Color(0xFF8AB4F8),
    infoSoft = Color(0xFF153463),
    purple = Color(0xFFD7AEFB),
    ema20 = Color(0xFFFDD663),
    ema50 = Color(0xFF8AB4F8),
    hairline = Color(0xFF2B3442),
)

val LocalBotColors = staticCompositionLocalOf { LightBotColors }

private val LightColorScheme = lightColorScheme(
    primary = Color(0xFF1A73E8),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFD3E3FD),
    onPrimaryContainer = Color(0xFF041E49),
    secondary = Color(0xFF445E91),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFE8F0FE),
    onSecondaryContainer = Color(0xFF0B57D0),
    tertiary = Color(0xFF137333),
    onTertiary = Color.White,
    tertiaryContainer = Color(0xFFE6F4EA),
    onTertiaryContainer = Color(0xFF0D652D),
    error = Color(0xFFC5221F),
    onError = Color.White,
    errorContainer = Color(0xFFFCE8E6),
    onErrorContainer = Color(0xFFA50E0E),
    background = Color(0xFFF8FAFD),
    onBackground = Color(0xFF1F1F1F),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF1F1F1F),
    surfaceVariant = Color(0xFFECF1F8),
    onSurfaceVariant = Color(0xFF444746),
    surfaceContainerLowest = Color(0xFFFFFFFF),
    surfaceContainerLow = Color(0xFFF3F6FC),
    surfaceContainer = Color(0xFFEFF3F9),
    surfaceContainerHigh = Color(0xFFE4EBF5),
    surfaceContainerHighest = Color(0xFFDCE4F0),
    outline = Color(0xFF747775),
    outlineVariant = Color(0xFFDDE3EA),
)

private val DarkColorScheme = darkColorScheme(
    primary = Color(0xFF8AB4F8),
    onPrimary = Color(0xFF062E6F),
    primaryContainer = Color(0xFF174EA6),
    onPrimaryContainer = Color(0xFFD3E3FD),
    secondary = Color(0xFFA8C7FA),
    onSecondary = Color(0xFF062E6F),
    secondaryContainer = Color(0xFF1D3254),
    onSecondaryContainer = Color(0xFFD3E3FD),
    tertiary = Color(0xFF81C995),
    onTertiary = Color(0xFF0A3818),
    tertiaryContainer = Color(0xFF103821),
    onTertiaryContainer = Color(0xFFC4EED0),
    error = Color(0xFFF28B82),
    onError = Color(0xFF601410),
    errorContainer = Color(0xFF471615),
    onErrorContainer = Color(0xFFFAD2CF),
    background = Color(0xFF0E1116),
    onBackground = Color(0xFFE3E3E3),
    surface = Color(0xFF13171F),
    onSurface = Color(0xFFE3E3E3),
    surfaceVariant = Color(0xFF202734),
    onSurfaceVariant = Color(0xFFAAB4BE),
    surfaceContainerLowest = Color(0xFF0E1116),
    surfaceContainerLow = Color(0xFF171C26),
    surfaceContainer = Color(0xFF1D232F),
    surfaceContainerHigh = Color(0xFF242B39),
    surfaceContainerHighest = Color(0xFF2C3444),
    outline = Color(0xFF8E918F),
    outlineVariant = Color(0xFF2B3442),
)

private val BotShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(16.dp),
    large = RoundedCornerShape(20.dp),
    extraLarge = RoundedCornerShape(24.dp),
)

private val BotTypography = Typography(
    displaySmall = TextStyle(fontSize = 32.sp, lineHeight = 38.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.5).sp),
    headlineSmall = TextStyle(fontSize = 22.sp, lineHeight = 28.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.2).sp),
    titleLarge = TextStyle(fontSize = 18.sp, lineHeight = 24.sp, fontWeight = FontWeight.Bold),
    titleMedium = TextStyle(fontSize = 15.sp, lineHeight = 21.sp, fontWeight = FontWeight.SemiBold),
    titleSmall = TextStyle(fontSize = 13.sp, lineHeight = 18.sp, fontWeight = FontWeight.SemiBold),
    bodyLarge = TextStyle(fontSize = 15.sp, lineHeight = 22.sp),
    bodyMedium = TextStyle(fontSize = 13.sp, lineHeight = 19.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 17.sp),
    labelLarge = TextStyle(fontSize = 13.sp, lineHeight = 18.sp, fontWeight = FontWeight.SemiBold),
    labelMedium = TextStyle(fontSize = 11.5.sp, lineHeight = 16.sp, fontWeight = FontWeight.Medium),
    labelSmall = TextStyle(fontSize = 10.5.sp, lineHeight = 14.sp, fontWeight = FontWeight.Medium),
)

fun TextStyle.tnum(): TextStyle = copy(fontFeatureSettings = "tnum")

@Composable
fun CryptoBotTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val extended = if (darkTheme) DarkBotColors else LightBotColors
    CompositionLocalProvider(LocalBotColors provides extended) {
        MaterialTheme(
            colorScheme = if (darkTheme) DarkColorScheme else LightColorScheme,
            typography = BotTypography,
            shapes = BotShapes,
            content = content,
        )
    }
}
