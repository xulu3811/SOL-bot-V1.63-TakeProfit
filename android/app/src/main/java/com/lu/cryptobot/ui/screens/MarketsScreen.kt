package com.lu.cryptobot.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.lu.cryptobot.ui.charts.ProCandleChart
import com.lu.cryptobot.ui.components.BotFormatters
import com.lu.cryptobot.ui.components.BotSurfaceCard
import com.lu.cryptobot.ui.components.MetricBentoTile
import com.lu.cryptobot.ui.components.SectionHeader
import com.lu.cryptobot.ui.components.StatusBadge
import com.lu.cryptobot.ui.components.optCleanString
import com.lu.cryptobot.ui.theme.LocalBotColors
import com.lu.cryptobot.ui.theme.tnum
import org.json.JSONObject

private val TIMEFRAMES = listOf("1m", "5m", "15m", "1h", "4h")

@Composable
fun MarketsScreen(
    values: Map<String, JSONObject>,
    selectedTimeframe: String,
    onSelectTimeframe: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val bot = LocalBotColors.current
    val markets = values["markets"]
    val livePos = values["live/positions"]
    val shadowStatus = values["shadow/status"]

    val candleClosePrice = markets?.optCleanString("latest_price")
    val change24h = markets?.optCleanString("change_24h")
    val isDown = BotFormatters.isNegative(change24h)
    val priceColor = if (change24h.isNullOrBlank()) MaterialTheme.colorScheme.onSurface else if (isDown) bot.loss else bot.gain
    val high24h = markets?.optCleanString("high_24h")
    val low24h = markets?.optCleanString("low_24h")
    val volume24h = markets?.optCleanString("volume_24h")
    val volAsset = markets?.optCleanString("volume_24h_asset", "SOL").orEmpty().ifBlank { "SOL" }
    val priceSource = markets?.optCleanString("price_source", "LAST_CLOSED_CANDLE")
    val micro = markets?.optJSONObject("micro")
    val microReady = micro?.optBoolean("ready", false) == true && micro.optBoolean("stale", false).not()
    val latestPrice = if (microReady) micro?.optCleanString("mid").orEmpty().ifBlank { candleClosePrice.orEmpty() } else candleClosePrice

    val indicators = markets?.optJSONObject("indicators")
    val emaAlignment = markets?.optCleanString("ema_alignment")
    val emaSpreadPct = if (markets != null && markets.has("ema_spread_pct") && !markets.isNull("ema_spread_pct")) {
        markets.optDouble("ema_spread_pct", Double.NaN)
    } else Double.NaN

    // Resolve optional active APTS stop line for visual overlay on chart
    val activeStop = livePos?.optJSONArray("slots")?.optJSONObject(0)?.optCleanString("stop_price")?.toDoubleOrNull()
        ?: shadowStatus?.optJSONArray("positions")?.optJSONObject(0)?.optCleanString("stop_price")?.toDoubleOrNull()

    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        // 1. Compact Ticker & Real-time Orderbook Microstructure Card
        BotSurfaceCard {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text("SOL / USDT", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    StatusBadge(
                        text = "币安现货",
                        containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                        contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                StatusBadge(
                    text = if (microReady) "实时盘口" else if (priceSource == "LAST_CLOSED_CANDLE") "收盘锚定" else "行情同步",
                    containerColor = if (microReady) bot.gainSoft else MaterialTheme.colorScheme.surfaceContainerHigh,
                    contentColor = if (microReady) bot.gain else MaterialTheme.colorScheme.onSurfaceVariant,
                    dotColor = if (microReady) bot.gain else null,
                )
            }

            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.Bottom,
            ) {
                Text(
                    text = "$${BotFormatters.formatCryptoPrice(latestPrice)}",
                    style = MaterialTheme.typography.displaySmall.tnum(),
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                StatusBadge(
                    text = BotFormatters.formatSignedPct(change24h),
                    containerColor = if (isDown) bot.lossSoft else bot.gainSoft,
                    contentColor = priceColor,
                )
            }

            Spacer(Modifier.height(10.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                MiniStatChip("24h 最高", "$${BotFormatters.formatCryptoPrice(high24h)}", Modifier.weight(1f))
                MiniStatChip("24h 最低", "$${BotFormatters.formatCryptoPrice(low24h)}", Modifier.weight(1f))
                MiniStatChip("24h 量", BotFormatters.formatCompactVolume(volume24h, volAsset), Modifier.weight(1f))
            }

            Spacer(Modifier.height(10.dp))
            HorizontalDivider(color = bot.hairline)
            Spacer(Modifier.height(8.dp))

            if (micro != null && micro.optBoolean("ready", false)) {
                val bid = BotFormatters.formatCryptoPrice(micro.optCleanString("bid"))
                val ask = BotFormatters.formatCryptoPrice(micro.optCleanString("ask"))
                val spreadBps = BotFormatters.formatNumber(micro.optCleanString("spread_bps"), 2)
                val buyRatioPct = micro.optCleanString("buy_ratio").toDoubleOrNull()?.let { "${BotFormatters.formatNumber((it * 100).toString(), 0)}%" } ?: "—"
                val ageMs = micro.optLong("age_ms", -1L)
                val ageText = if (ageMs >= 0L) "${ageMs}ms" else "实时"
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = "买一 $bid / 卖一 $ask",
                        style = MaterialTheme.typography.labelSmall.tnum(),
                        color = MaterialTheme.colorScheme.onSurface,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        text = "点差 $spreadBps bps · 主动买 $buyRatioPct · $ageText",
                        style = MaterialTheme.typography.labelSmall.tnum(),
                        color = if (micro.optBoolean("stale", false)) bot.warn else MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            } else {
                Text(
                    text = "盘口深度流等待同步，当前使用最新已收盘 K 线估值",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

        // 2. 5-Timeframe Pill Switcher (1m / 5m / 15m / 1h / 4h)
        Surface(
            shape = MaterialTheme.shapes.medium,
            color = MaterialTheme.colorScheme.surfaceContainer,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(4.dp),
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                TIMEFRAMES.forEach { tf ->
                    val selected = tf == selectedTimeframe
                    Surface(
                        modifier = Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(10.dp))
                            .clickable { onSelectTimeframe(tf) },
                        shape = RoundedCornerShape(10.dp),
                        color = if (selected) MaterialTheme.colorScheme.surface else MaterialTheme.colorScheme.surfaceContainer,
                        shadowElevation = if (selected) 1.dp else 0.dp,
                    ) {
                        Box(
                            modifier = Modifier.padding(vertical = 8.dp),
                            contentAlignment = Alignment.Center,
                        ) {
                            Text(
                                text = tf,
                                style = MaterialTheme.typography.labelLarge.tnum(),
                                fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium,
                                color = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        }

        // 3. Interactive Candlestick Chart + Y/X Axis + Drag Crosshair
        BotSurfaceCard {
            ProCandleChart(
                candles = markets?.optJSONArray("candles"),
                timeframe = selectedTimeframe,
                stopPrice = activeStop,
            )
        }

        // 4. Core Technical Indicator Bento Grid (Dynamic Backend Values)
        SectionHeader(
            title = "核心技术指标矩阵",
            subtitle = "周期 $selectedTimeframe · 实时闭合K线计算",
        )

        val ema20Str = indicators?.optCleanString("ema20")
        val ema50Str = indicators?.optCleanString("ema50")
        val rsi6Str = indicators?.optCleanString("rsi6")
        val adxStr = indicators?.optCleanString("adx")
        val atrStr = indicators?.optCleanString("atr")
        val atrPctRaw = indicators?.optCleanString("atr_pct")
        val atrPctStr = if (!atrPctRaw.isNullOrBlank()) atrPctRaw else run {
            val atrVal = atrStr?.toDoubleOrNull()
            val refPrice = (candleClosePrice?.toDoubleOrNull() ?: latestPrice?.toDoubleOrNull())
            if (atrVal != null && refPrice != null && refPrice > 0.0) {
                (atrVal / refPrice * 100.0).toString()
            } else ""
        }
        val rvolStr = indicators?.optCleanString("rvol")
        val vwapStr = indicators?.optCleanString("vwap")

        val alignColor = when (emaAlignment) {
            "BULLISH" -> bot.gain
            "BEARISH" -> bot.loss
            else -> bot.warn
        }
        val spreadCaption = if (!emaSpreadPct.isNaN()) {
            "差值 ${BotFormatters.formatSignedPct(emaSpreadPct.toString())} · E20 $${BotFormatters.formatCryptoPrice(ema20Str)}"
        } else {
            "EMA20 $${BotFormatters.formatCryptoPrice(ema20Str)}"
        }

        val rsiNum = rsi6Str?.toDoubleOrNull()
        val rsiColor = when {
            rsiNum == null -> MaterialTheme.colorScheme.onSurface
            rsiNum >= 48.0 -> bot.gain
            rsiNum <= 30.0 -> bot.info
            else -> bot.warn
        }
        val adxNum = adxStr?.toDoubleOrNull()
        val rvolNum = rvolStr?.toDoubleOrNull()
        val vwapDistPct = run {
            val lp = latestPrice?.toDoubleOrNull()
            val vw = vwapStr?.toDoubleOrNull()
            if (lp != null && vw != null && vw > 0.0) {
                BotFormatters.formatSignedPct(((lp - vw) / vw * 100.0).toString())
            } else "—"
        }

        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                MetricBentoTile(
                    label = "EMA20 / EMA50",
                    value = BotFormatters.emaAlignmentLabel(emaAlignment),
                    caption = spreadCaption,
                    valueColor = alignColor,
                    modifier = Modifier.weight(1f),
                )
                MetricBentoTile(
                    label = "RSI(6) 动能",
                    value = BotFormatters.formatNumber(rsi6Str, 1),
                    caption = when {
                        rsiNum == null -> "等待足够K线"
                        rsiNum >= 48.0 -> "站上 48 企稳区"
                        rsiNum <= 30.0 -> "进入 30 超卖区"
                        else -> "未达 48 回升线"
                    },
                    valueColor = rsiColor,
                    modifier = Modifier.weight(1f),
                )
            }
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                MetricBentoTile(
                    label = "ADX(14) 趋势",
                    value = BotFormatters.formatNumber(adxStr, 1),
                    caption = if (adxNum != null && adxNum >= 23.0) "≥ 23 趋势确立" else "< 23 趋势偏弱",
                    valueColor = if (adxNum != null && adxNum >= 23.0) bot.gain else MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.weight(1f),
                )
                MetricBentoTile(
                    label = "ATR(14) 波动",
                    value = "$${BotFormatters.formatNumber(atrStr, 3)}",
                    caption = "波幅 ${BotFormatters.formatNumber(atrPctStr, 2)}%",
                    modifier = Modifier.weight(1f),
                )
            }
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                MetricBentoTile(
                    label = "RVOL 量比",
                    value = "${BotFormatters.formatNumber(rvolStr, 2)}x",
                    caption = if (rvolNum != null && rvolNum >= 1.2) "≥ 1.2x 放量确认" else "< 1.2x 量能不足",
                    valueColor = if (rvolNum != null && rvolNum >= 1.2) bot.gain else MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.weight(1f),
                )
                MetricBentoTile(
                    label = "VWAP 锚定",
                    value = "$${BotFormatters.formatCryptoPrice(vwapStr)}",
                    caption = "现价偏离 $vwapDistPct",
                    modifier = Modifier.weight(1f),
                )
            }
        }
        Spacer(Modifier.height(12.dp))
    }
}

@Composable
private fun MiniStatChip(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(10.dp),
        color = MaterialTheme.colorScheme.surfaceContainerLow,
        border = BorderStroke(1.dp, LocalBotColors.current.hairline),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 6.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Text(
                text = label,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
            )
            Text(
                text = value,
                style = MaterialTheme.typography.labelLarge.tnum(),
                fontWeight = FontWeight.Bold,
                maxLines = 1,
                softWrap = false,
            )
        }
    }
}
