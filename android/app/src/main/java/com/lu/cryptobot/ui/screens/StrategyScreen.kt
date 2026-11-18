package com.lu.cryptobot.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.RadioButtonUnchecked
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
import androidx.compose.ui.unit.sp
import com.lu.cryptobot.FrameSyncHelper
import com.lu.cryptobot.ui.components.BotFormatters
import com.lu.cryptobot.ui.components.BotSurfaceCard
import com.lu.cryptobot.ui.components.MetricBentoTile
import com.lu.cryptobot.ui.components.SectionHeader
import com.lu.cryptobot.ui.components.StatusBadge
import com.lu.cryptobot.ui.theme.LocalBotColors
import com.lu.cryptobot.ui.theme.tnum
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.max

private data class FactorSpec(val key: String, val title: String, val subtitle: String, val maxWeight: Float)

private val FACTOR_SPECS = listOf(
    FactorSpec("trend", "顺势趋势", "多周期均线共振与复苏突破", 20f),
    FactorSpec("momentum", "动能回踩", "5m RSI(6) 企稳拐头", 25f),
    FactorSpec("participation", "量能爆发", "5m RVOL 相对放量强度", 20f),
    FactorSpec("structure", "结构支撑", "VWAP 收复与 1m 右侧确认", 20f),
    FactorSpec("execution", "微观执行", "波动上限与盘口深度价差", 15f),
)

private data class GateItemUi(
    val title: String,
    val passed: Boolean,
    val nearReady: Boolean,
    val progress: Float,
    val currentText: String,
    val targetText: String,
)

private fun optFiniteDouble(obj: JSONObject?, key: String): Double? {
    if (obj == null || !obj.has(key) || obj.isNull(key)) return null
    val v = obj.optDouble(key, Double.NaN)
    return if (!v.isNaN() && !v.isInfinite()) v else null
}

private fun buildEngineGateItems(
    sid: String,
    regime: String,
    gatesObj: JSONObject?,
    htf: JSONObject?,
    ind5: JSONObject?,
    cooldownMs: Long,
    r2Stage: String,
): List<GateItemUi> {
    fun gate(k: String): Boolean = gatesObj?.optBoolean(k, false) == true

    val close5 = optFiniteDouble(ind5, "close")
    val rsi5 = optFiniteDouble(ind5, "rsi6")
    val rvol5 = optFiniteDouble(ind5, "rvol")
    val vwap5 = optFiniteDouble(ind5, "vwap")
    val adx1h = optFiniteDouble(htf, "adx_1h")
    val e20_15m = optFiniteDouble(htf, "ema20_15m")
    val e50_15m = optFiniteDouble(htf, "ema50_15m")
    val rvol15m = optFiniteDouble(htf, "rvol_15m")
    val e20_4h = optFiniteDouble(htf, "ema20_4h")

    val closeText = close5?.let { "$${BotFormatters.formatCryptoPrice(it.toString())}" } ?: "—"
    val vwapText = vwap5?.let { "$${BotFormatters.formatCryptoPrice(it.toString())}" } ?: "—"
    val rsiText = rsi5?.let { BotFormatters.formatNumber(it.toString(), 1, grouping = false) } ?: "—"
    val rvolText = rvol5?.let { "${BotFormatters.formatNumber(it.toString(), 2, grouping = false)}x" } ?: "—"

    return when (sid) {
        "T1" -> {
            val gRegime = gate("regime_uptrend")
            val gHtf = gate("trend_4h_above_ema20") && gate("adx_1h_ge_23")
            val trendPassed = gRegime && gHtf
            val m15Bull = e20_15m != null && e50_15m != null && e20_15m > e50_15m
            val trendProgress = when {
                trendPassed -> 1f
                gRegime || gHtf -> 0.7f
                m15Bull -> 0.5f
                else -> 0.2f
            }
            val trendCur = when {
                trendPassed -> "多头共振已就绪"
                m15Bull && gate("vwap_reclaim") -> if (adx1h != null) "15m多头 · 1h ADX ${BotFormatters.formatNumber(adx1h.toString(), 1)}" else "15m多头 · 待1h/4h确认"
                m15Bull -> "15m多头 · 待5m收复"
                adx1h != null -> "1h ADX ${BotFormatters.formatNumber(adx1h.toString(), 1)}"
                else -> BotFormatters.regimeLabel(regime)
            }

            val rsiPassed = gate("rsi_recover_above_48")
            val rsiProg = if (rsiPassed) 1f else ((rsi5 ?: 0.0) / 48.0).toFloat().coerceIn(0.1f, 0.92f)

            val rvolPassed = gate("rvol_ge_1_2")
            val rvolProg = if (rvolPassed) 1f else ((rvol5 ?: 0.0) / 1.2).toFloat().coerceIn(0.1f, 0.92f)

            val vwapPassed = gate("vwap_reclaim")
            val vwapDiffPct = if (close5 != null && vwap5 != null && vwap5 > 0) (close5 - vwap5) / vwap5 * 100.0 else null
            val vwapProg = when {
                vwapPassed -> 1f
                vwapDiffPct != null && vwapDiffPct >= -0.35 -> 0.75f
                else -> 0.3f
            }

            val b1mPassed = gate("breakout_1m")
            val chaseOk = gate("chase_1m_ok")
            val cdOk = gate("cooldown_ok") && cooldownMs <= 0L
            val guardPassed = chaseOk && cdOk

            listOf(
                GateItemUi(
                    title = "① 多周期趋势",
                    passed = trendPassed,
                    nearReady = !trendPassed && trendProgress >= 0.5f,
                    progress = trendProgress,
                    currentText = trendCur,
                    targetText = "目标: 4h/1h多头或复苏共振",
                ),
                GateItemUi(
                    title = "② 5m 动能恢复",
                    passed = rsiPassed,
                    nearReady = !rsiPassed && (rsi5 ?: 0.0) >= 42.0,
                    progress = rsiProg,
                    currentText = "RSI(6) $rsiText",
                    targetText = "目标: 回踩后拐头 > 48.0",
                ),
                GateItemUi(
                    title = "③ 5m 放量参与",
                    passed = rvolPassed,
                    nearReady = !rvolPassed && (rvol5 ?: 0.0) >= 0.95,
                    progress = rvolProg,
                    currentText = "RVOL $rvolText",
                    targetText = "目标: 量比 ≥ 1.20x",
                ),
                GateItemUi(
                    title = "④ 5m 站上 VWAP",
                    passed = vwapPassed,
                    nearReady = !vwapPassed && (vwapDiffPct ?: -1.0) >= -0.25,
                    progress = vwapProg,
                    currentText = "现价 $closeText",
                    targetText = "VWAP $vwapText",
                ),
                GateItemUi(
                    title = "⑤ 1m 右侧突破",
                    passed = b1mPassed,
                    nearReady = !b1mPassed && vwapPassed && rsiPassed,
                    progress = if (b1mPassed) 1f else if (vwapPassed) 0.65f else 0.25f,
                    currentText = if (b1mPassed) "已确认抬底破高" else "等待 1m 阳线突破",
                    targetText = "目标: 3根1m抬底+破前高",
                ),
                GateItemUi(
                    title = "⑥ 防追高与冷却",
                    passed = guardPassed,
                    nearReady = !guardPassed && (chaseOk || cdOk),
                    progress = if (guardPassed) 1f else 0.5f,
                    currentText = if (!cdOk) "冷却 ${BotFormatters.formatCooldown(cooldownMs)}" else if (chaseOk) "偏离安全 · 冷却就绪" else "短线偏离 >0.6%",
                    targetText = "目标: 偏离≤0.6% · 无冷却",
                ),
            )
        }
        "T2" -> {
            val g4h = gate("trend_4h_up")
            val gPull = gate("pullback_1h")
            val gRec15 = gate("reclaim_15m")
            val gRvol15 = gate("rvol_15m_ok")
            val gTurn = gate("rsi_5m_turn")
            val gB1m = gate("breakout_1m")
            val e20_15Text = e20_15m?.let { "$${BotFormatters.formatCryptoPrice(it.toString())}" } ?: "—"
            val rvol15Text = rvol15m?.let { "${BotFormatters.formatNumber(it.toString(), 2, grouping = false)}x" } ?: rvolText

            listOf(
                GateItemUi(
                    title = "① 4h/15m 趋势",
                    passed = g4h,
                    nearReady = !g4h && (e20_15m != null && e50_15m != null && e20_15m > e50_15m),
                    progress = if (g4h) 1f else if (e20_15m != null && e50_15m != null && e20_15m > e50_15m) 0.65f else 0.25f,
                    currentText = if (g4h) "多头方向已确认" else "15m EMA20 $e20_15Text",
                    targetText = "目标: 4h金叉或复苏多头",
                ),
                GateItemUi(
                    title = "② 波段回踩到位",
                    passed = gPull,
                    nearReady = !gPull,
                    progress = if (gPull) 1f else 0.45f,
                    currentText = if (gPull) "已完成均线回踩" else "等待回踩均线支撑",
                    targetText = "目标: 1h/15m 回踩 EMA20",
                ),
                GateItemUi(
                    title = "③ 15m 均线收复",
                    passed = gRec15,
                    nearReady = !gRec15 && close5 != null && e20_15m != null && close5 >= e20_15m * 0.998,
                    progress = if (gRec15) 1f else 0.5f,
                    currentText = "现价 $closeText",
                    targetText = "15m EMA20 $e20_15Text",
                ),
                GateItemUi(
                    title = "④ 15m/5m 量比",
                    passed = gRvol15,
                    nearReady = !gRvol15 && ((rvol15m ?: 0.0) >= 0.85 || (rvol5 ?: 0.0) >= 1.0),
                    progress = if (gRvol15) 1f else ((rvol15m ?: rvol5 ?: 0.0) / 1.0).toFloat().coerceIn(0.15f, 0.9f),
                    currentText = "量比 $rvol15Text",
                    targetText = "目标: 15m RVOL ≥ 1.0x",
                ),
                GateItemUi(
                    title = "⑤ 5m RSI 拐头",
                    passed = gTurn,
                    nearReady = !gTurn && (rsi5 ?: 50.0) <= 45.0,
                    progress = if (gTurn) 1f else if ((rsi5 ?: 50.0) < 42.0) 0.7f else 0.35f,
                    currentText = "5m RSI $rsiText",
                    targetText = "目标: <40 后拐头向上",
                ),
                GateItemUi(
                    title = "⑥ 1m 右侧突破",
                    passed = gB1m,
                    nearReady = !gB1m && gRec15 && gTurn,
                    progress = if (gB1m) 1f else if (gRec15) 0.65f else 0.25f,
                    currentText = if (gB1m) "已确认 1m 突破" else "等待 1m 突破前高",
                    targetText = "目标: 3根1m抬底+破高",
                ),
            )
        }
        "R2" -> {
            val gOver = gate("oversold_event")
            val gFloor = gate("floor_held")
            val gTurn = gate("rsi_turn")
            val gVwap = gate("vwap_or_climax")
            val gB1m = gate("breakout_1m")
            val stageText = when (r2Stage) {
                "WATCHING" -> "监控筑底中"
                "ARMED" -> "已上膛待突破"
                "TRIGGERED" -> "已触发买入"
                else -> "巡检等待超卖"
            }
            listOf(
                GateItemUi(
                    title = "① 5m 超卖触发",
                    passed = gOver,
                    nearReady = !gOver && (rsi5 ?: 50.0) <= 26.0,
                    progress = if (gOver) 1f else ((40.0 - ((rsi5 ?: 40.0).coerceIn(20.0, 40.0))) / 20.0).toFloat().coerceIn(0.15f, 0.9f),
                    currentText = "5m RSI $rsiText",
                    targetText = "目标: RSI(6) ≤ 20.0",
                ),
                GateItemUi(
                    title = "② 底部支撑不破",
                    passed = gFloor,
                    nearReady = gOver && !gFloor,
                    progress = if (gFloor) 1f else if (gOver) 0.6f else 0.15f,
                    currentText = if (gFloor) "连续K线守住前低" else stageText,
                    targetText = "目标: 2根5m不破候选底",
                ),
                GateItemUi(
                    title = "③ RSI 脱离超卖",
                    passed = gTurn,
                    nearReady = gOver && !gTurn && (rsi5 ?: 0.0) >= 38.0,
                    progress = if (gTurn) 1f else if (gOver) ((rsi5 ?: 20.0) / 48.0).toFloat().coerceIn(0.2f, 0.9f) else 0.15f,
                    currentText = "5m RSI $rsiText",
                    targetText = "目标: 回升突破 48 或衰竭拐头",
                ),
                GateItemUi(
                    title = "④ VWAP / 放量确认",
                    passed = gVwap,
                    nearReady = gTurn && !gVwap,
                    progress = if (gVwap) 1f else if (gTurn) 0.7f else 0.2f,
                    currentText = "现价 $closeText · 量比 $rvolText",
                    targetText = "目标: 站上 VWAP $vwapText",
                ),
                GateItemUi(
                    title = "⑤ 1m 突破开火",
                    passed = gB1m,
                    nearReady = gVwap && !gB1m,
                    progress = if (gB1m) 1f else if (gVwap) 0.8f else 0.15f,
                    currentText = if (gB1m) "已触发反弹买单" else "状态: $stageText",
                    targetText = "目标: 1m 阳线突破前高",
                ),
                GateItemUi(
                    title = "⑥ 状态机阶段",
                    passed = r2Stage in setOf("ARMED", "TRIGGERED"),
                    nearReady = r2Stage == "WATCHING",
                    progress = when (r2Stage) {
                        "TRIGGERED" -> 1f
                        "ARMED" -> 0.85f
                        "WATCHING" -> 0.5f
                        else -> 0.2f
                    },
                    currentText = "阶段: $r2Stage",
                    targetText = "IDLE→WATCHING→ARMED",
                ),
            )
        }
        else -> {
            val gRange = gate("regime_range")
            val gMacro = gate("macro_4h_guard")
            val gOver = gate("rsi_oversold_recover")
            val gRvol = gate("rvol_ge_1_5")
            val gFloor = gate("floor_5m") && gate("taker_buy_ok")
            val gB1m = gate("breakout_1m")
            val e50_4hText = optFiniteDouble(htf, "ema50_4h")?.let { "$${BotFormatters.formatCryptoPrice(it.toString())}" } ?: "—"
            listOf(
                GateItemUi("① 震荡区间环境", gRange, false, if (gRange) 1f else 0.3f, BotFormatters.regimeLabel(regime), "目标: RANGE 震荡区间"),
                GateItemUi("② 4h 宏观保护", gMacro, false, if (gMacro) 1f else 0.25f, "现价 $closeText", "4h EMA50 $e50_4hText"),
                GateItemUi("③ 超卖回升 >28", gOver, false, if (gOver) 1f else 0.3f, "5m RSI $rsiText", "目标: ≤20 后回升 >28"),
                GateItemUi("④ 放量 ≥1.5x", gRvol, false, if (gRvol) 1f else ((rvol5 ?: 0.0) / 1.5).toFloat().coerceIn(0.15f, 0.9f), "RVOL $rvolText", "目标: 量比 ≥ 1.50x"),
                GateItemUi("⑤ 5m 企稳与买盘", gFloor, false, if (gFloor) 1f else 0.35f, if (gFloor) "买盘恢复+底抬升" else "等待买盘占比 ≥50%", "目标: 低点不破+买盘恢复"),
                GateItemUi("⑥ 1m 突破确认", gB1m, false, if (gB1m) 1f else 0.25f, if (gB1m) "已确认突破" else "等待 1m 突破", "目标: 仅作研究观察"),
            )
        }
    }
}

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
    val gatesObj = activeEngine.optJSONObject("gates")
    val factors = activeEngine.optJSONObject("score_factors") ?: strategies?.optJSONObject("score_factors")
    val stopDist = activeEngine.optString("stop_distance")
    val cooldownMs = activeEngine.optLong("cooldown_remaining_ms", 0L)
    val r2Stage = activeEngine.optString("r2_stage", "IDLE")
    val htf = activeEngine.optJSONObject("htf_snapshot")
    val indicators5m = strategies?.optJSONObject("indicators_5m")
    val itemsArr = strategies?.optJSONArray("items")

    val gateItems = remember(selectedSid, regime, gatesObj, htf, indicators5m, cooldownMs, r2Stage) {
        buildEngineGateItems(selectedSid, regime, gatesObj, htf, indicators5m, cooldownMs, r2Stage)
    }
    val passedCount = gateItems.count { it.passed }
    val totalGates = gateItems.size

    val scoreAccent = when {
        accepted || score >= threshold -> bot.gain
        score >= 55.0 -> bot.warn
        else -> MaterialTheme.colorScheme.primary
    }

    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(11.dp),
    ) {
        // 1. 4-Engine Segmented Switcher (T1 / T2 / R2 / R1)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            listOf(
                Triple("T1", "顺势回调", if ("T1" in allowedLive) "主实盘" else "影子"),
                Triple("T2", "波段回踩", if ("T2" in allowedLive) "实盘" else "仅影子"),
                Triple("R2", "超卖反弹", if ("R2" in allowedLive) "实盘" else "影子"),
                Triple("R1", "均值反转", if (strategies?.optBoolean("r1_live_allowed", false) == true) "实盘" else "4h保护"),
            ).forEach { (sid, name, tag) ->
                val isSelected = sid == selectedSid
                val isLiveAllowed = sid in allowedLive
                val engObj = enginesObj?.optJSONObject(sid)
                val engScore = engObj?.optDouble("score", Double.NaN)
                val engAccepted = engObj?.optBoolean("accepted", false) == true
                val barRatio = if (engScore != null && !engScore.isNaN()) (engScore / 75.0).toFloat().coerceIn(0.08f, 1f) else 0.08f
                val barColor = when {
                    engAccepted || (engScore != null && !engScore.isNaN() && engScore >= 75.0) -> bot.gain
                    engScore != null && !engScore.isNaN() && engScore >= 55.0 -> bot.warn
                    isSelected -> MaterialTheme.colorScheme.primary
                    else -> MaterialTheme.colorScheme.outlineVariant
                }
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
                        modifier = Modifier.padding(horizontal = 7.dp, vertical = 7.dp),
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
                                style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                                fontWeight = FontWeight.SemiBold,
                                color = if (isLiveAllowed) bot.gain else MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1,
                            )
                        }
                        Text(
                            text = if (engScore != null && !engScore.isNaN()) "$name ${BotFormatters.formatNumber(engScore.toString(), 1, grouping = false)}" else name,
                            style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp).tnum(),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        LinearProgressIndicator(
                            progress = { barRatio },
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(3.dp)
                                .clip(RoundedCornerShape(999.dp)),
                            color = barColor,
                            trackColor = MaterialTheme.colorScheme.surfaceContainerHighest,
                        )
                    }
                }
            }
        }

        // 2. Unified Score & Gate Light Overview Card
        BotSurfaceCard(contentPadding = PaddingValues(horizontal = 14.dp, vertical = 13.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                Box(
                    modifier = Modifier.size(76.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    val trackColor = MaterialTheme.colorScheme.surfaceContainerHigh
                    val sweep = ((score / 100.0).toFloat().coerceIn(0f, 1f)) * 360f
                    Canvas(modifier = Modifier.size(76.dp)) {
                        drawArc(
                            color = trackColor,
                            startAngle = -90f,
                            sweepAngle = 360f,
                            useCenter = false,
                            style = Stroke(width = 7.dp.toPx(), cap = StrokeCap.Round),
                        )
                        drawArc(
                            color = scoreAccent,
                            startAngle = -90f,
                            sweepAngle = sweep,
                            useCenter = false,
                            style = Stroke(width = 7.dp.toPx(), cap = StrokeCap.Round),
                        )
                    }
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            text = BotFormatters.formatNumber(score.toString(), 1, grouping = false),
                            style = MaterialTheme.typography.titleLarge.tnum(),
                            fontWeight = FontWeight.Bold,
                        )
                        Text(
                            text = "阈值 ${threshold.toInt()}",
                            style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp).tnum(),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }

                Column(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(5.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        val (regBg, regFg) = when (regime) {
                            "UPTREND" -> bot.gainSoft to bot.gain
                            "DOWNTREND" -> bot.lossSoft to bot.loss
                            else -> bot.infoSoft to bot.info
                        }
                        StatusBadge(BotFormatters.regimeLabel(regime), regBg, regFg)
                        StatusBadge(
                            text = if (accepted) "触发买入 $priority" else if (score >= 55) "接近阈值" else "巡检等待",
                            containerColor = if (accepted) bot.gainSoft else if (score >= 55) bot.warnSoft else MaterialTheme.colorScheme.surfaceContainerHigh,
                            contentColor = if (accepted) bot.gain else if (score >= 55) bot.warn else MaterialTheme.colorScheme.onSurfaceVariant,
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
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            text = "绿灯进度 $passedCount / $totalGates 项",
                            style = MaterialTheme.typography.labelMedium.tnum(),
                            fontWeight = FontWeight.Bold,
                            color = if (passedCount == totalGates) bot.gain else MaterialTheme.colorScheme.onSurface,
                        )
                        Text(
                            text = if (gap <= 0.0) "评分已达标 ≥${threshold.toInt()}" else "距门槛差 ${BotFormatters.formatNumber(gap.toString(), 1)} 分",
                            style = MaterialTheme.typography.labelSmall.tnum(),
                            color = if (gap <= 0.0) bot.gain else MaterialTheme.colorScheme.onSurfaceVariant,
                            fontWeight = FontWeight.SemiBold,
                        )
                    }

                    // Segmented 6-Gate Light Strip
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        gateItems.forEach { item ->
                            val segColor = when {
                                item.passed -> bot.gain
                                item.nearReady -> bot.warn
                                else -> MaterialTheme.colorScheme.surfaceContainerHighest
                            }
                            Box(
                                modifier = Modifier
                                    .weight(1f)
                                    .height(6.dp)
                                    .clip(RoundedCornerShape(999.dp))
                                    .background(segColor),
                            )
                        }
                    }

                    Text(
                        text = if (cooldownMs > 0L) {
                            "冷却保护剩余 ${BotFormatters.formatCooldown(cooldownMs)} · 全部绿灯点亮且≥75分自动买入"
                        } else {
                            "6 项核心门槛全部亮绿灯且评分 ≥ 75 分时触发买入"
                        },
                        style = MaterialTheme.typography.labelSmall.copy(fontSize = 11.sp).tnum(),
                        color = if (cooldownMs > 0L) bot.warn else MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }

        // 3. Structured 2-Column Gate Checklist & Live Indicator Progress Grid
        BotSurfaceCard(contentPadding = PaddingValues(horizontal = 13.dp, vertical = 12.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text(
                        text = "$selectedSid 入场门槛与达成进度",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                }
                StatusBadge(
                    text = if (passedCount == totalGates) "✓ $passedCount/$totalGates 全部点亮" else "已亮绿灯 $passedCount / $totalGates",
                    containerColor = if (passedCount == totalGates) bot.gainSoft else if (passedCount >= 3) bot.infoSoft else MaterialTheme.colorScheme.surfaceContainerHigh,
                    contentColor = if (passedCount == totalGates) bot.gain else if (passedCount >= 3) bot.info else MaterialTheme.colorScheme.onSurfaceVariant,
                    dotColor = if (passedCount > 0) bot.gain else null,
                )
            }
            Spacer(Modifier.height(9.dp))

            Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
                gateItems.chunked(2).forEach { pair ->
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(7.dp),
                    ) {
                        pair.forEach { item ->
                            val cardBg = when {
                                item.passed -> bot.gainSoft.copy(alpha = 0.38f)
                                item.nearReady -> bot.warnSoft.copy(alpha = 0.28f)
                                else -> MaterialTheme.colorScheme.surfaceContainerLow
                            }
                            val cardBorder = when {
                                item.passed -> bot.gain.copy(alpha = 0.55f)
                                item.nearReady -> bot.warn.copy(alpha = 0.45f)
                                else -> bot.hairline
                            }
                            val accentColor = when {
                                item.passed -> bot.gain
                                item.nearReady -> bot.warn
                                else -> MaterialTheme.colorScheme.outline
                            }
                            val stateText = when {
                                item.passed -> "已亮灯"
                                item.nearReady -> "接近"
                                else -> "待达成"
                            }

                            Surface(
                                modifier = Modifier.weight(1f),
                                shape = RoundedCornerShape(10.dp),
                                color = cardBg,
                                border = BorderStroke(1.dp, cardBorder),
                            ) {
                                Column(
                                    modifier = Modifier.padding(horizontal = 9.dp, vertical = 7.dp),
                                    verticalArrangement = Arrangement.spacedBy(4.dp),
                                ) {
                                    Row(
                                        modifier = Modifier.fillMaxWidth(),
                                        horizontalArrangement = Arrangement.SpaceBetween,
                                        verticalAlignment = Alignment.CenterVertically,
                                    ) {
                                        Row(
                                            verticalAlignment = Alignment.CenterVertically,
                                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                                            modifier = Modifier.weight(1f),
                                        ) {
                                            Icon(
                                                imageVector = if (item.passed) Icons.Outlined.CheckCircle else if (item.nearReady) Icons.Outlined.Schedule else Icons.Outlined.RadioButtonUnchecked,
                                                contentDescription = null,
                                                tint = accentColor,
                                                modifier = Modifier.size(13.dp),
                                            )
                                            Text(
                                                text = item.title,
                                                style = MaterialTheme.typography.labelMedium,
                                                fontWeight = FontWeight.Bold,
                                                color = MaterialTheme.colorScheme.onSurface,
                                                maxLines = 1,
                                                overflow = TextOverflow.Ellipsis,
                                            )
                                        }
                                        Text(
                                            text = stateText,
                                            style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                                            fontWeight = FontWeight.Bold,
                                            color = accentColor,
                                        )
                                    }

                                    Text(
                                        text = item.currentText,
                                        style = MaterialTheme.typography.bodySmall.tnum(),
                                        fontWeight = FontWeight.SemiBold,
                                        color = if (item.passed) bot.gain else MaterialTheme.colorScheme.onSurface,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )

                                    LinearProgressIndicator(
                                        progress = { item.progress.coerceIn(0f, 1f) },
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .height(4.dp)
                                            .clip(RoundedCornerShape(999.dp)),
                                        color = accentColor,
                                        trackColor = MaterialTheme.colorScheme.surfaceContainerHighest,
                                    )

                                    Text(
                                        text = item.targetText,
                                        style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp).tnum(),
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                }
                            }
                        }
                        if (pair.size == 1) {
                            Spacer(Modifier.weight(1f))
                        }
                    }
                }
            }

            if (htf != null) {
                val adx1h = optFiniteDouble(htf, "adx_1h")?.let { BotFormatters.formatNumber(it.toString(), 1) } ?: "—"
                val e20_15m = optFiniteDouble(htf, "ema20_15m")?.let { BotFormatters.formatCryptoPrice(it.toString()) } ?: "—"
                val e20_4h = optFiniteDouble(htf, "ema20_4h")?.let { BotFormatters.formatCryptoPrice(it.toString()) } ?: "—"
                val e50_4h = optFiniteDouble(htf, "ema50_4h")?.let { BotFormatters.formatCryptoPrice(it.toString()) } ?: "—"
                Spacer(Modifier.height(7.dp))
                Surface(
                    shape = RoundedCornerShape(8.dp),
                    color = MaterialTheme.colorScheme.surfaceContainerLow,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            text = "多周期基准：15m EMA20 $$e20_15m · 1h ADX $adx1h · 4h $$e20_4h / $$e50_4h",
                            style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.5.sp).tnum(),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
        }

        // 4. 5-Factor Weighted Breakdown
        BotSurfaceCard(contentPadding = PaddingValues(horizontal = 14.dp, vertical = 13.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "五维决策因子权重",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
                StatusBadge(
                    text = "总分 ${BotFormatters.formatNumber(score.toString(), 1, grouping = false)} / 100",
                    containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                    contentColor = scoreAccent,
                )
            }
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
                    Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(6.dp),
                                modifier = Modifier.weight(1f),
                            ) {
                                Text(spec.title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
                                Text(
                                    spec.subtitle,
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
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

        // 5. Below-the-fold Scroll Area: 5m Closed-Bar Snapshot
        if (indicators5m != null && indicators5m.length() > 0) {
            BotSurfaceCard(contentPadding = PaddingValues(horizontal = 14.dp, vertical = 12.dp)) {
                SectionHeader(title = "5m 策略主周期快照", subtitle = "收盘K线同步校验")
                Spacer(Modifier.height(8.dp))
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    MetricBentoTile("5m RSI(6)", BotFormatters.formatNumber(indicators5m.optString("rsi6"), 1), modifier = Modifier.weight(1f))
                    MetricBentoTile("5m RVOL", "${BotFormatters.formatNumber(indicators5m.optString("rvol"), 2)}x", modifier = Modifier.weight(1f))
                    MetricBentoTile("5m ADX", BotFormatters.formatNumber(indicators5m.optString("adx"), 1), modifier = Modifier.weight(1f))
                }
            }
        }

        // 6. Below-the-fold Scroll Area: Recent Signal Decisions Feed
        if (itemsArr != null && itemsArr.length() > 0) {
            val cap = minOf(6, itemsArr.length())
            BotSurfaceCard(contentPadding = PaddingValues(horizontal = 14.dp, vertical = 12.dp)) {
                SectionHeader(title = "近期引擎扫描记录", subtitle = "最近 $cap 条决策审计")
                Spacer(Modifier.height(8.dp))
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
                                modifier = Modifier.padding(horizontal = 12.dp, vertical = 9.dp),
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
        }
        Spacer(Modifier.height(8.dp))
    }
}
