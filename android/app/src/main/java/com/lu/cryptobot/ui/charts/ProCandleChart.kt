package com.lu.cryptobot.ui.charts

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.lu.cryptobot.ui.components.BotFormatters
import com.lu.cryptobot.ui.components.optCleanString
import com.lu.cryptobot.ui.theme.LocalBotColors
import com.lu.cryptobot.ui.theme.tnum
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

internal data class CandlePoint(
    val openTimeMs: Long,
    val open: Double,
    val high: Double,
    val low: Double,
    val close: Double,
    val volume: Double,
    val ema20: Double,
    val ema50: Double,
    val vwap: Double,
)

private fun JSONObject?.optFiniteDouble(key: String): Double? {
    if (this == null || !has(key) || isNull(key)) return null
    val v = optDouble(key, Double.NaN)
    return if (v.isFinite()) v else null
}

private fun DoubleArray.addCandleTo(
    out: MutableList<CandlePoint>,
    timeMs: Long,
    ema20Override: Double?,
    ema50Override: Double?,
    vwapOverride: Double?,
    state: DoubleArray,
    index: Int,
) {
    val o = this[0]
    val h = this[1]
    val l = this[2]
    val c = this[3]
    val vol = max(this[4], 0.0)
    val k20 = 2.0 / 21.0
    val k50 = 2.0 / 51.0
    state[0] = if (index == 0) c else c * k20 + state[0] * (1.0 - k20)
    state[1] = if (index == 0) c else c * k50 + state[1] * (1.0 - k50)
    val typical = (h + l + c) / 3.0
    state[2] += typical * vol
    state[3] += vol
    val fallbackVwap = if (state[3] > 0.0) state[2] / state[3] else c
    out += CandlePoint(
        openTimeMs = timeMs,
        open = o,
        high = h,
        low = l,
        close = c,
        volume = vol,
        ema20 = ema20Override ?: state[0],
        ema50 = ema50Override ?: state[1],
        vwap = vwapOverride ?: fallbackVwap,
    )
}

internal fun parseCandleSeries(rows: JSONArray?): List<CandlePoint> {
    if (rows == null || rows.length() == 0) return emptyList()
    data class ParsedRow(
        val timeMs: Long,
        val ohlcv: DoubleArray,
        val ema20: Double?,
        val ema50: Double?,
        val vwap: Double?,
    )
    val parsed = ArrayList<ParsedRow>(rows.length())
    for (i in 0 until rows.length()) {
        val obj = rows.optJSONObject(i)
        if (obj != null) {
            val rawTime = when {
                obj.has("open_time") && !obj.isNull("open_time") -> obj.optLong("open_time", 0L)
                obj.has("t") && !obj.isNull("t") -> obj.optLong("t", 0L)
                else -> obj.optLong("timestamp", 0L)
            }
            val timeMs = if (rawTime in 1L..999_999_999_999L) rawTime * 1000L else rawTime
            val o = obj.optCleanString("open").ifBlank { obj.optCleanString("o") }.toDoubleOrNull() ?: continue
            val h = obj.optCleanString("high").ifBlank { obj.optCleanString("h") }.toDoubleOrNull() ?: continue
            val l = obj.optCleanString("low").ifBlank { obj.optCleanString("l") }.toDoubleOrNull() ?: continue
            val c = obj.optCleanString("close").ifBlank { obj.optCleanString("c") }.toDoubleOrNull() ?: continue
            val v = obj.optCleanString("volume").ifBlank { obj.optCleanString("v") }.toDoubleOrNull() ?: 0.0
            if (o <= 0.0 || h <= 0.0 || l <= 0.0 || c <= 0.0) continue
            val ind = obj.optJSONObject("indicators")
            parsed += ParsedRow(
                timeMs = timeMs,
                ohlcv = doubleArrayOf(o, h, l, c, v),
                ema20 = ind.optFiniteDouble("ema20"),
                ema50 = ind.optFiniteDouble("ema50"),
                vwap = ind.optFiniteDouble("vwap"),
            )
            continue
        }
        val arr = rows.optJSONArray(i) ?: continue
        if (arr.length() < 5) continue
        val rawTime = arr.optLong(0, 0L)
        val timeMs = if (rawTime in 1L..999_999_999_999L) rawTime * 1000L else rawTime
        val o = arr.optString(1).toDoubleOrNull() ?: continue
        val h = arr.optString(2).toDoubleOrNull() ?: continue
        val l = arr.optString(3).toDoubleOrNull() ?: continue
        val c = arr.optString(4).toDoubleOrNull() ?: continue
        val v = if (arr.length() > 5) arr.optString(5).toDoubleOrNull() ?: 0.0 else 0.0
        if (o <= 0.0 || h <= 0.0 || l <= 0.0 || c <= 0.0) continue
        parsed += ParsedRow(timeMs, doubleArrayOf(o, h, l, c, v), null, null, null)
    }
    if (parsed.isEmpty()) return emptyList()

    val state = doubleArrayOf(parsed[0].ohlcv[3], parsed[0].ohlcv[3], 0.0, 0.0)
    val all = ArrayList<CandlePoint>(parsed.size)
    for (i in parsed.indices) {
        val row = parsed[i]
        row.ohlcv.addCandleTo(all, row.timeMs, row.ema20, row.ema50, row.vwap, state, i)
    }
    val visibleLimit = 60
    return if (all.size > visibleLimit) all.takeLast(visibleLimit) else all
}

@Composable
fun ProCandleChart(
    candles: JSONArray?,
    timeframe: String,
    stopPrice: Double? = null,
    chartHeight: Dp = 240.dp,
    modifier: Modifier = Modifier,
) {
    val bot = LocalBotColors.current
    val bars = remember(candles) { parseCandleSeries(candles) }
    var showEma by remember { mutableStateOf(true) }
    var showVwap by remember { mutableStateOf(false) }
    var selectedIdx by remember(bars) { mutableStateOf<Int?>(null) }

    Column(modifier = modifier.fillMaxWidth()) {
        val activeBar = selectedIdx?.let { idx -> bars.getOrNull(idx) }
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(24.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (activeBar != null) {
                val bull = activeBar.close >= activeBar.open
                val clr = if (bull) bot.gain else bot.loss
                Text(
                    text = "${BotFormatters.formatEpoch(activeBar.openTimeMs)}  O ${fmtPrice(activeBar.open)} H ${fmtPrice(activeBar.high)} L ${fmtPrice(activeBar.low)} C ${fmtPrice(activeBar.close)}",
                    style = MaterialTheme.typography.labelSmall.tnum(),
                    color = clr,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            } else {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Text(
                        text = "K 线 ($timeframe)",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                    )
                    if (showEma) {
                        LegendDot("EMA20", bot.ema20)
                        LegendDot("EMA50", bot.ema50)
                    }
                    if (showVwap) {
                        LegendDot("VWAP", bot.purple)
                    }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    ToggleChip("EMA", showEma, bot.info) { showEma = !showEma }
                    ToggleChip("VWAP", showVwap, bot.purple) { showVwap = !showVwap }
                }
            }
        }

        Spacer(Modifier.height(4.dp))

        if (bars.isEmpty()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(chartHeight),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = "等待 K 线数据加载...",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            return
        }

        val textMeasurer = rememberTextMeasurer()
        val axisLabelStyle = TextStyle(
            fontSize = 10.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            fontFeatureSettings = "tnum",
        )
        val badgeTextStyle = TextStyle(
            fontSize = 10.sp,
            fontWeight = FontWeight.Bold,
            color = Color.White,
            fontFeatureSettings = "tnum",
        )
        val hairline = bot.hairline
        val gain = bot.gain
        val loss = bot.loss
        val ema20Color = bot.ema20
        val ema50Color = bot.ema50
        val vwapColor = bot.purple
        val crosshairColor = MaterialTheme.colorScheme.onSurfaceVariant

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(chartHeight),
        ) {
            Canvas(
                modifier = Modifier
                    .fillMaxSize()
                    .pointerInput(bars) {
                        detectTapGestures(
                            onTap = { pos ->
                                val plotW = size.width - 52.dp.toPx()
                                if (plotW > 0f && bars.isNotEmpty()) {
                                    val step = plotW / bars.size
                                    val idx = (pos.x / step).toInt().coerceIn(0, bars.lastIndex)
                                    selectedIdx = if (selectedIdx == idx) null else idx
                                }
                            },
                        )
                    }
                    .pointerInput(bars) {
                        detectHorizontalDragGestures(
                            onHorizontalDrag = { change, _ ->
                                change.consume()
                                val plotW = size.width - 52.dp.toPx()
                                if (plotW > 0f && bars.isNotEmpty()) {
                                    val step = plotW / bars.size
                                    selectedIdx = (change.position.x / step).toInt().coerceIn(0, bars.lastIndex)
                                }
                            },
                            onDragEnd = { selectedIdx = null },
                            onDragCancel = { selectedIdx = null },
                        )
                    },
            ) {
                val axisRightW = 52.dp.toPx()
                val axisBottomH = 20.dp.toPx()
                val plotW = (size.width - axisRightW).coerceAtLeast(10f)
                val totalPlotH = (size.height - axisBottomH).coerceAtLeast(40f)
                val candleH = totalPlotH * 0.78f
                val volTop = candleH + 4.dp.toPx()
                val volH = (totalPlotH - volTop).coerceAtLeast(8f)

                var minP = bars.minOf { min(it.low, if (showEma) min(it.ema20, it.ema50) else it.low) }
                var maxP = bars.maxOf { max(it.high, if (showEma) max(it.ema20, it.ema50) else it.high) }
                if (showVwap) {
                    minP = min(minP, bars.minOf { it.vwap })
                    maxP = max(maxP, bars.maxOf { it.vwap })
                }
                if (stopPrice != null && stopPrice in (minP * 0.96)..(maxP * 1.04)) {
                    minP = min(minP, stopPrice)
                    maxP = max(maxP, stopPrice)
                }
                val pad = max((maxP - minP) * 0.08, maxP * 0.0015)
                val lowBound = minP - pad
                val highBound = maxP + pad
                val priceSpan = (highBound - lowBound).coerceAtLeast(0.01)

                fun priceToY(p: Double): Float {
                    val norm = ((p - lowBound) / priceSpan).toFloat().coerceIn(0f, 1f)
                    return candleH * (1f - norm)
                }

                // 4 horizontal grid lines + right Y-axis labels
                val levels = 4
                for (i in 0 until levels) {
                    val frac = i.toFloat() / (levels - 1)
                    val y = 8.dp.toPx() + (candleH - 16.dp.toPx()) * frac
                    val levelPrice = highBound - (highBound - lowBound) * ((y) / candleH)
                    drawLine(
                        color = hairline.copy(alpha = 0.7f),
                        start = Offset(0f, y),
                        end = Offset(plotW, y),
                        strokeWidth = 1f,
                    )
                    val labelLayout = textMeasurer.measure(fmtPrice(levelPrice), axisLabelStyle)
                    drawText(
                        textLayoutResult = labelLayout,
                        topLeft = Offset(plotW + 4.dp.toPx(), (y - labelLayout.size.height / 2f).coerceIn(0f, candleH - labelLayout.size.height)),
                    )
                }

                // Bottom X-axis time ticks (start, 1/3, 2/3, end)
                val tickIndices = linkedSetOf(0, bars.lastIndex / 3, (bars.lastIndex * 2) / 3, bars.lastIndex)
                val stepW = plotW / bars.size
                tickIndices.forEach { idx ->
                    val cx = idx * stepW + stepW / 2f
                    val lbl = if (timeframe == "4h") BotFormatters.formatEpoch(bars[idx].openTimeMs).take(5) else BotFormatters.formatClock(bars[idx].openTimeMs)
                    val measured = textMeasurer.measure(lbl, axisLabelStyle)
                    val tx = (cx - measured.size.width / 2f).coerceIn(0f, plotW - measured.size.width)
                    drawText(measured, topLeft = Offset(tx, totalPlotH + 3.dp.toPx()))
                }

                val maxVol = max(bars.maxOf { it.volume }, 1e-6)
                val bodyW = (stepW * 0.62f).coerceIn(2f, 12.dp.toPx())

                val ema20Path = Path()
                val ema50Path = Path()
                val vwapPath = Path()

                bars.forEachIndexed { idx, bar ->
                    val cx = idx * stepW + stepW / 2f
                    val color = if (bar.close >= bar.open) gain else loss
                    val yHigh = priceToY(bar.high)
                    val yLow = priceToY(bar.low)
                    val yOpen = priceToY(bar.open)
                    val yClose = priceToY(bar.close)

                    drawLine(color, Offset(cx, yHigh), Offset(cx, yLow), strokeWidth = 1.4.dp.toPx())
                    val topY = min(yOpen, yClose)
                    val h = max(kotlin.math.abs(yClose - yOpen), 1.6.dp.toPx())
                    drawRect(color, Offset(cx - bodyW / 2f, topY), Size(bodyW, h))

                    // Volume histogram bar
                    val vh = ((bar.volume / maxVol).toFloat() * volH).coerceAtLeast(1.dp.toPx())
                    drawRect(
                        color = color.copy(alpha = 0.28f),
                        topLeft = Offset(cx - bodyW / 2f, totalPlotH - vh),
                        size = Size(bodyW, vh),
                    )

                    val e20Y = priceToY(bar.ema20)
                    val e50Y = priceToY(bar.ema50)
                    val vwY = priceToY(bar.vwap)
                    if (idx == 0) {
                        ema20Path.moveTo(cx, e20Y)
                        ema50Path.moveTo(cx, e50Y)
                        vwapPath.moveTo(cx, vwY)
                    } else {
                        ema20Path.lineTo(cx, e20Y)
                        ema50Path.lineTo(cx, e50Y)
                        vwapPath.lineTo(cx, vwY)
                    }
                }

                if (showEma) {
                    drawPath(ema50Path, ema50Color, style = Stroke(width = 1.4.dp.toPx(), cap = StrokeCap.Round))
                    drawPath(ema20Path, ema20Color, style = Stroke(width = 1.6.dp.toPx(), cap = StrokeCap.Round))
                }
                if (showVwap) {
                    drawPath(vwapPath, vwapColor, style = Stroke(width = 1.3.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(8f, 6f))))
                }

                // Active APTS stop line (if open position)
                if (stopPrice != null && stopPrice in lowBound..highBound) {
                    val sy = priceToY(stopPrice)
                    drawLine(
                        color = loss.copy(alpha = 0.85f),
                        start = Offset(0f, sy),
                        end = Offset(plotW, sy),
                        strokeWidth = 1.2.dp.toPx(),
                        pathEffect = PathEffect.dashPathEffect(floatArrayOf(10f, 6f)),
                    )
                }

                // Latest price dashed line & Y-axis highlighted pill
                val lastBar = bars.last()
                val lastColor = if (lastBar.close >= lastBar.open) gain else loss
                val lastY = priceToY(lastBar.close)
                drawLine(
                    color = lastColor.copy(alpha = 0.55f),
                    start = Offset(0f, lastY),
                    end = Offset(plotW, lastY),
                    strokeWidth = 1.dp.toPx(),
                    pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 6f)),
                )
                val pillLayout = textMeasurer.measure(fmtPrice(lastBar.close), badgeTextStyle)
                val pillW = (pillLayout.size.width + 8.dp.toPx()).coerceAtMost(axisRightW - 2.dp.toPx())
                val pillH = pillLayout.size.height + 4.dp.toPx()
                val pillTop = (lastY - pillH / 2f).coerceIn(0f, candleH - pillH)
                drawRoundRect(
                    color = lastColor,
                    topLeft = Offset(plotW + 2.dp.toPx(), pillTop),
                    size = Size(pillW, pillH),
                    cornerRadius = CornerRadius(4.dp.toPx(), 4.dp.toPx()),
                )
                drawText(pillLayout, topLeft = Offset(plotW + 6.dp.toPx(), pillTop + 2.dp.toPx()))

                // Crosshair overlay when touching
                selectedIdx?.let { idx ->
                    val bar = bars[idx]
                    val cx = idx * stepW + stepW / 2f
                    val cy = priceToY(bar.close)
                    val dash = PathEffect.dashPathEffect(floatArrayOf(6f, 4f))
                    drawLine(crosshairColor.copy(alpha = 0.7f), Offset(cx, 0f), Offset(cx, totalPlotH), strokeWidth = 1.dp.toPx(), pathEffect = dash)
                    drawLine(crosshairColor.copy(alpha = 0.7f), Offset(0f, cy), Offset(plotW, cy), strokeWidth = 1.dp.toPx(), pathEffect = dash)
                    drawCircle(color = crosshairColor, radius = 4.dp.toPx(), center = Offset(cx, cy))
                }
            }
        }
    }
}

@Composable
private fun LegendDot(label: String, color: Color) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Box(
            modifier = Modifier
                .size(6.dp)
                .clip(RoundedCornerShape(999.dp))
                .background(color),
        )
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun ToggleChip(
    label: String,
    active: Boolean,
    accent: Color,
    onClick: () -> Unit,
) {
    Surface(
        modifier = Modifier
            .clip(RoundedCornerShape(8.dp))
            .clickable(onClick = onClick),
        shape = RoundedCornerShape(8.dp),
        color = if (active) accent.copy(alpha = 0.14f) else MaterialTheme.colorScheme.surfaceContainerLow,
    ) {
        Text(
            text = label,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
            style = MaterialTheme.typography.labelSmall,
            color = if (active) accent else MaterialTheme.colorScheme.onSurfaceVariant,
            fontWeight = if (active) FontWeight.Bold else FontWeight.Medium,
        )
    }
}

private fun fmtPrice(v: Double): String {
    val dec = when {
        v >= 1000 -> "%.1f"
        v >= 1 -> "%.2f"
        else -> "%.4f"
    }
    return String.format(Locale.US, dec, v)
}
