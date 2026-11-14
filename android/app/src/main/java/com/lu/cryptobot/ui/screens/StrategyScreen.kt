package com.lu.cryptobot.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Cancel
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
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
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.lu.cryptobot.FrameSyncHelper
import com.lu.cryptobot.ui.components.BotFormatters
import com.lu.cryptobot.ui.components.BotSurfaceCard
import com.lu.cryptobot.ui.components.MetricBentoTile
import com.lu.cryptobot.ui.components.SectionHeader
import com.lu.cryptobot.ui.components.StatusBadge
import com.lu.cryptobot.ui.theme.LocalBotColors
import com.lu.cryptobot.ui.theme.tnum
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.max

private data class FactorSpec(val key: String, val title: String, val subtitle: String, val maxWeight: Float)

private val FACTOR_SPECS = listOf(
    FactorSpec("trend", "顺势趋势", "4h/1h 均线共振与方向过滤", 20f),
    FactorSpec("momentum", "动能回踩", "5m RSI(6) 企稳回升", 25f),
    FactorSpec("participation", "量能爆发", "5m RVOL 相对放量强度", 20f),
    FactorSpec("structure", "结构支撑", "VWAP 与支撑区间确认", 15f),
    FactorSpec("execution", "微观执行", "1m 突破与盘口防追高", 20f),
)

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun StrategyScreen(
    values: Map<String, JSONObject>,
    modifier: Modifier = Modifier,
) {
    val bot = LocalBotColors.current
    val strategies = values["strategies"]
    val defaultLiveSid = strategies?.optString("live_strategy_id", "T1").orEmpty().ifBlank { "T1" }
    val allowedLive = remember(strategies) {
        FrameSyncHelper.allowedLiveStrategies(strategies ?: JSONObject())
    }

    var selectedSid by remember(defaultLiveSid) { mutableStateOf(defaultLiveSid) }
    val enginesObj = strategies?.optJSONObject("engines")
    val activeEngine = enginesObj?.optJSONObject(selectedSid)
        ?: strategies?.optJSONObject("latest")
        ?: JSONObject()

    val score = activeEngine.optDouble("score", strategies?.optDouble("score", 0.0) ?: 0.0)
        .let { if (it.isNaN()) 0.0 else it }
    val threshold = activeEngine.optDouble("threshold", 75.0)
    val accepted = activeEngine.optBoolean("accepted", false)
    val priority = activeEngine.optString("priority", "REJECT")
    val regime = activeEngine.optString("regime")
    val reasonsArr = activeEngine.optJSONArray("reasons")
    val gatesObj = activeEngine.optJSONObject("gates")
    val factors = activeEngine.optJSONObject("score_factors") ?: strategies?.optJSONObject("score_factors")
    val stopDist = activeEngine.optString("stop_distance")
    val cooldownMs = activeEngine.optLong("cooldown_remaining_ms", 0L)
    val htf = activeEngine.optJSONObject("htf_snapshot")
    val indicators5m = strategies?.optJSONObject("indicators_5m")
    val itemsArr = strategies?.optJSONArray("items")

    val scoreAccent = when {
        accepted || score >= threshold -> bot.gain
        score >= 55.0 -> bot.warn
        else -> MaterialTheme.colorScheme.primary
    }

    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        // 1. 4-Engine Segmented Switcher (T1 / T2 / R2 / R1)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            listOf(
                Triple("T1", "顺势回调", if ("T1" in allowedLive) "主实盘" else "影子"),
                Triple("T2", "波段回踩", if ("T2" in allowedLive) "实盘" else "仅影子"),
                Triple("R2", "超卖反弹", if ("R2" in allowedLive) "实盘" else "影子"),
                Triple("R1", "均值反转", if (strategies?.optBoolean("r1_live_allowed", false) == true) "实盘" else "4h保护"),
            ).forEach { (sid, name, tag) ->
                val isSelected = sid == selectedSid
                val isLiveAllowed = sid in allowedLive
                val engScore = enginesObj?.optJSONObject(sid)?.optDouble("score", Double.NaN)
                Surface(
                    modifier = Modifier
                        .weight(1f)
                        .clip(MaterialTheme.shapes.small)
                        .clickable { selectedSid = sid },
                    shape = MaterialTheme.shapes.small,
                    color = if (isSelected) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface,
                    border = BorderStroke(
                        width = if (isSelected) 1.5.dp else 1.dp,
                        color = if (isSelected) MaterialTheme.colorScheme.primary else bot.hairline,
                    ),
                ) {
                    Column(
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
                        verticalArrangement = Arrangement.spacedBy(3.dp),
                    ) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                text = sid,
                                style = MaterialTheme.typography.titleSmall,
                                fontWeight = FontWeight.Bold,
                                color = if (isSelected) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurface,
                            )
                            Text(
                                text = tag,
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.SemiBold,
                                color = if (isLiveAllowed) bot.gain else MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Text(
                            text = if (engScore != null && !engScore.isNaN()) "$name · ${BotFormatters.formatNumber(engScore.toString(), 1)}" else name,
                            style = MaterialTheme.typography.labelSmall.tnum(),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
        }

        // 2. Compact Horizontal Score Card (~132dp height vs giant empty ring!)
        BotSurfaceCard {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                Box(
                    modifier = Modifier.size(82.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    val trackColor = MaterialTheme.colorScheme.surfaceContainerHigh
                    val sweep = ((score / 100.0).toFloat().coerceIn(0f, 1f)) * 360f
                    Canvas(modifier = Modifier.size(82.dp)) {
                        drawArc(
                            color = trackColor,
                            startAngle = -90f,
                            sweepAngle = 360f,
                            useCenter = false,
                            style = Stroke(width = 8.dp.toPx(), cap = StrokeCap.Round),
                        )
                        drawArc(
                            color = scoreAccent,
                            startAngle = -90f,
                            sweepAngle = sweep,
                            useCenter = false,
                            style = Stroke(width = 8.dp.toPx(), cap = StrokeCap.Round),
                        )
                    }
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            text = BotFormatters.formatNumber(score.toString(), 1, grouping = false),
                            style = MaterialTheme.typography.titleLarge.tnum(),
                            fontWeight = FontWeight.Bold,
                        )
                        Text(
                            text = "/ 100",
                            style = MaterialTheme.typography.labelSmall.tnum(),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }

                Column(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        val (regBg, regFg) = when (regime) {
                            "UPTREND" -> bot.gainSoft to bot.gain
                            "DOWNTREND" -> bot.lossSoft to bot.loss
                            else -> bot.infoSoft to bot.info
                        }
                        StatusBadge(BotFormatters.regimeLabel(regime), regBg, regFg)
                        StatusBadge(
                            text = if (accepted) "触发买入 $priority" else if (score >= 55) "接近阈值" else "观望拦截",
                            containerColor = if (accepted) bot.gainSoft else bot.warnSoft,
                            contentColor = if (accepted) bot.gain else bot.warn,
                        )
                        if (stopDist.isNotBlank()) {
                            val pct = stopDist.toDoubleOrNull()?.let { "-${BotFormatters.formatNumber((it * 100).toString(), 1)}%" } ?: stopDist
                            StatusBadge("止损 $pct", MaterialTheme.colorScheme.surfaceContainerHigh, MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }

                    val gap = max(0.0, threshold - score)
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(
                            text = if (gap <= 0.0) "已达到买入门槛 ≥75" else "距门槛差 ${BotFormatters.formatNumber(gap.toString(), 1)} 分",
                            style = MaterialTheme.typography.labelMedium.tnum(),
                            fontWeight = FontWeight.SemiBold,
                        )
                        Text(
                            text = "阈值 ${threshold.toInt()}",
                            style = MaterialTheme.typography.labelSmall.tnum(),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    LinearProgressIndicator(
                        progress = { (score / threshold).toFloat().coerceIn(0f, 1f) },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(7.dp)
                            .clip(RoundedCornerShape(999.dp)),
                        color = scoreAccent,
                        trackColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                    )

                    Text(
                        text = if (cooldownMs > 0L) "策略冷却剩余 ${BotFormatters.formatCooldown(cooldownMs)} · 评分仅表示条件匹配度" else "条件匹配评分 · 非未来收益概率",
                        style = MaterialTheme.typography.labelSmall.tnum(),
                        color = if (cooldownMs > 0L) bot.warn else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        // 3. Blocking Reasons & Entry Gate Checklist
        BotSurfaceCard {
            SectionHeader(
                title = "$selectedSid 入场门槛与拦截原因",
                subtitle = if (selectedSid in allowedLive) "当前实盘主策略 · 每分钟重评估" else "仅影子回测评估 · 不直接触发实盘买单",
            )
            Spacer(Modifier.height(10.dp))

            val gatesList = BotFormatters.gatesFor(selectedSid)
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                gatesList.forEach { (key, label) ->
                    val passed = gatesObj?.optBoolean(key, false) == true
                    Surface(
                        shape = RoundedCornerShape(999.dp),
                        color = if (passed) bot.gainSoft else bot.lossSoft,
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 9.dp, vertical = 4.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                        ) {
                            Icon(
                                if (passed) Icons.Outlined.CheckCircle else Icons.Outlined.Cancel,
                                contentDescription = null,
                                tint = if (passed) bot.gain else bot.loss,
                                modifier = Modifier.size(13.dp),
                            )
                            Text(
                                text = label,
                                style = MaterialTheme.typography.labelSmall,
                                color = if (passed) bot.gain else bot.loss,
                                fontWeight = FontWeight.SemiBold,
                            )
                        }
                    }
                }
            }

            if (reasonsArr != null && reasonsArr.length() > 0) {
                Spacer(Modifier.height(10.dp))
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    for (i in 0 until reasonsArr.length()) {
                        val code = reasonsArr.optString(i)
                        if (code.isBlank()) continue
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            Icon(
                                Icons.Outlined.Schedule,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.size(13.dp),
                            )
                            Text(
                                text = BotFormatters.reasonLabel(code),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }

            if (htf != null) {
                val adx1h = if (htf.has("adx_1h") && !htf.isNull("adx_1h")) BotFormatters.formatNumber(htf.optDouble("adx_1h").toString(), 1) else "—"
                val e20_4h = if (htf.has("ema20_4h") && !htf.isNull("ema20_4h")) BotFormatters.formatCryptoPrice(htf.optDouble("ema20_4h").toString()) else "—"
                val e50_4h = if (htf.has("ema50_4h") && !htf.isNull("ema50_4h")) BotFormatters.formatCryptoPrice(htf.optDouble("ema50_4h").toString()) else "—"
                Spacer(Modifier.height(10.dp))
                Surface(
                    shape = RoundedCornerShape(10.dp),
                    color = MaterialTheme.colorScheme.surfaceContainerLow,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(
                        text = "多周期基准：1h ADX $adx1h · 4h EMA20 $$e20_4h / EMA50 $$e50_4h",
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 7.dp),
                        style = MaterialTheme.typography.labelSmall.tnum(),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        // 4. 5-Factor Weighted Breakdown
        BotSurfaceCard {
            SectionHeader(
                title = "五维决策因子权重",
                subtitle = "总分 ${BotFormatters.formatNumber(score.toString(), 1)} / 100",
            )
            Spacer(Modifier.height(10.dp))

            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                FACTOR_SPECS.forEach { spec ->
                    val valScore = factors?.optDouble(spec.key, 0.0)?.let { if (it.isNaN()) 0.0 else it } ?: 0.0
                    val ratio = (valScore.toFloat() / spec.maxWeight).coerceIn(0f, 1f)
                    val color = when {
                        ratio >= 0.7f -> bot.gain
                        ratio >= 0.4f -> bot.info
                        else -> MaterialTheme.colorScheme.outline
                    }
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(6.dp),
                            ) {
                                Text(spec.title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
                                Text(spec.subtitle, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            Text(
                                text = "${BotFormatters.formatNumber(valScore.toString(), 1, grouping = false)} / ${spec.maxWeight.toInt()}",
                                style = MaterialTheme.typography.labelMedium.tnum(),
                                color = color,
                                fontWeight = FontWeight.Bold,
                            )
                        }
                        LinearProgressIndicator(
                            progress = { ratio },
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(6.dp)
                                .clip(RoundedCornerShape(999.dp)),
                            color = color,
                            trackColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                        )
                    }
                }
            }
        }

        // 5. 5m Closed-Bar Features Strip
        if (indicators5m != null && indicators5m.length() > 0) {
            SectionHeader(title = "5m 策略主周期快照", subtitle = "收盘K线同步校验")
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                MetricBentoTile("5m RSI(6)", BotFormatters.formatNumber(indicators5m.optString("rsi6"), 1), modifier = Modifier.weight(1f))
                MetricBentoTile("5m RVOL", "${BotFormatters.formatNumber(indicators5m.optString("rvol"), 2)}x", modifier = Modifier.weight(1f))
                MetricBentoTile("5m ADX", BotFormatters.formatNumber(indicators5m.optString("adx"), 1), modifier = Modifier.weight(1f))
            }
        }

        // 6. Recent Signal Decisions Feed
        if (itemsArr != null && itemsArr.length() > 0) {
            SectionHeader(title = "近期引擎扫描记录")
            val cap = minOf(6, itemsArr.length())
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                for (i in 0 until cap) {
                    val row = itemsArr.optJSONObject(i) ?: continue
                    val sid = row.optString("strategy_id", "T1")
                    val rowScore = row.optDouble("score", 0.0)
                    val rowAccepted = row.optBoolean("accepted", false)
                    val rowRegime = BotFormatters.regimeLabel(row.optString("regime"))
                    val ts = row.optLong("signal_time", row.optLong("time", 0L))
                    Surface(
                        shape = MaterialTheme.shapes.small,
                        color = MaterialTheme.colorScheme.surfaceContainerLow,
                        border = BorderStroke(1.dp, bot.hairline),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                StatusBadge(sid, if (sid in allowedLive) bot.infoSoft else MaterialTheme.colorScheme.surfaceContainerHigh, if (sid in allowedLive) bot.info else MaterialTheme.colorScheme.onSurfaceVariant)
                                Column {
                                    Text(
                                        text = "$rowRegime · ${BotFormatters.formatNumber(rowScore.toString(), 1)} 分",
                                        style = MaterialTheme.typography.titleSmall.tnum(),
                                        fontWeight = FontWeight.SemiBold,
                                    )
                                    Text(
                                        text = BotFormatters.formatEpoch(ts),
                                        style = MaterialTheme.typography.labelSmall.tnum(),
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            }
                            StatusBadge(
                                text = if (rowAccepted) "已采纳" else "拦截",
                                containerColor = if (rowAccepted) bot.gainSoft else MaterialTheme.colorScheme.surfaceContainerHigh,
                                contentColor = if (rowAccepted) bot.gain else MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        }
        Spacer(Modifier.height(12.dp))
    }
}
