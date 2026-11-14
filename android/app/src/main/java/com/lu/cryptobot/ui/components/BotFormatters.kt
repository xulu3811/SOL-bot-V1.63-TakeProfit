package com.lu.cryptobot.ui.components

import java.math.BigDecimal
import java.math.RoundingMode
import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import org.json.JSONObject

fun JSONObject?.optCleanString(key: String, fallback: String = ""): String {
    if (this == null || isNull(key)) return fallback
    val raw = optString(key, fallback)
    return if (raw.isBlank() || raw.equals("null", ignoreCase = true)) fallback else raw
}

object BotFormatters {
    private val usSymbols = DecimalFormatSymbols(Locale.US)
    private val timeFormatter = DateTimeFormatter.ofPattern("MM-dd HH:mm", Locale.US)
    private val clockFormatter = DateTimeFormatter.ofPattern("HH:mm", Locale.US)
    private val fullTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss", Locale.US)

    private val reasonMap = mapOf(
        "REGIME_NOT_UPTREND" to "市场未处于多头趋势",
        "HIGHER_TIMEFRAME_WEAK_TREND" to "4h/1h 趋势强度不足",
        "RSI_PULLBACK_NOT_RECOVERED" to "5m RSI(6) 尚未站上 48",
        "RVOL_BELOW_1_2" to "相对成交量 RVOL 低于 1.2x",
        "VWAP_OR_SUPPORT_NOT_RECLAIMED" to "未站稳 VWAP/支撑位",
        "ONE_MINUTE_BREAKOUT_UNCONFIRMED" to "1m 突破尚未确认",
        "ONE_MINUTE_CHASE_EXCEEDED" to "1m 短线偏离超 0.6% 防追高",
        "STRATEGY_COOLDOWN_ACTIVE" to "处于 60 分钟冷却保护期",
        "SCORE_BELOW_75" to "综合评分未达 75 门槛",
        "FOUR_HOUR_DOWNTREND" to "4h 处于下行趋势",
        "FOUR_HOUR_EMA_NOT_UP" to "4h EMA 尚未拐头向上",
        "ONE_HOUR_NOT_PULLED_BACK" to "1h 回踩结构未到位",
        "FIFTEEN_MINUTE_NOT_RECLAIMED" to "15m 均线尚未收复",
        "RVOL_15M_BELOW_1" to "15m 量比不足 1.0x",
        "RSI_TURN_NOT_CONFIRMED" to "5m RSI 尚未确认金叉拐头",
        "R1_REQUIRES_RANGE" to "R1 仅在震荡区间生效",
        "MACRO_DOWNTREND_GUARD" to "4h 空头保护已拦截 R1",
        "ACCELERATING_DECLINE" to "急跌加速中禁止接飞刀",
        "RSI_OVERSOLD_NOT_RECOVERED" to "RSI(6) 尚未脱离超卖区",
        "RVOL_BELOW_1_5" to "放量止跌量比不足 1.5x",
        "TAKER_BUY_NOT_RECOVERED" to "主动买盘占比尚未恢复",
        "FIVE_MINUTE_FLOOR_UNCONFIRMED" to "5m 底部支撑尚未确认",
        "WAITING_OVERSOLD_CANDIDATE" to "等待 5m 超卖候选触发",
        "WAITING_FOR_FLOOR" to "超卖候选已建档 · 等待 2 根 K 线筑底",
        "WAITING_FOR_RECOVERY_FLOOR_VWAP" to "等待底部不破及 RSI/VWAP 回升确认",
        "WAITING_FOR_BREAKOUT" to "结构已确认 · 等待 1m 突破前高触发",
        "CANDIDATE_LOW_BROKEN" to "跌破超卖候选最低价 · 候选作废",
        "CANDIDATE_EXPIRED" to "超卖候选超时未突破 · 候选作废",
        "INTENT_ACKNOWLEDGED" to "反弹突破已确认并提交执行",
        "NO_RECENT_R2_CANDIDATE" to "当前无活跃超卖候选 · 实时巡检中",
    )

    private val blockerMap = mapOf(
        "LIVE_DISABLED" to "实盘自动下单开关当前关闭（系统处于安全只读模式）",
        "GUARDIAN_HARD_LOCK" to "触发日内 5% 硬风控熔断保护，自动买入已锁定",
        "API_KEY_MISSING" to "尚未绑定 Binance API 密钥，无法查询余额或自动交易",
        "SPOT_PERMISSION_OFF" to "当前 API 未开启「现货与杠杆交易」权限，仅支持只读对账",
        "INSUFFICIENT_FREE_USDT" to "可用 USDT 低于单笔下限 $500.00，当前不会自动开新仓",
        "MAX_SLOTS_REACHED" to "机器人已满 2 槽位持仓上限，暂停开立新仓",
    )

    private val engineGates = mapOf(
        "T1" to listOf(
            "regime_uptrend" to "多头环境",
            "trend_4h_above_ema20" to "4h>EMA20",
            "adx_1h_ge_23" to "1h ADX≥23",
            "rsi_recover_above_48" to "RSI(6)>48",
            "rvol_ge_1_2" to "RVOL≥1.2x",
            "vwap_reclaim" to "站上VWAP",
            "breakout_1m" to "1m突破",
            "chase_1m_ok" to "1m不追高",
            "cooldown_ok" to "60m冷却就绪",
        ),
        "T2" to listOf(
            "trend_4h_up" to "4h多头",
            "pullback_1h" to "1h回踩",
            "reclaim_15m" to "15m收复",
            "rvol_15m_ok" to "15m量比",
            "rsi_5m_turn" to "RSI拐头",
            "breakout_1m" to "1m突破",
        ),
        "R1" to listOf(
            "regime_range" to "震荡区间",
            "macro_4h_guard" to "4h非急跌",
            "not_accelerating_drop" to "无加速下杀",
            "rsi_oversold_recover" to "超卖回升",
            "rvol_ge_1_5" to "RVOL≥1.5x",
            "taker_buy_ok" to "买盘恢复",
            "floor_5m" to "5m企稳",
            "breakout_1m" to "1m确认",
        ),
        "R2" to listOf(
            "oversold_event" to "超卖触发",
            "floor_held" to "底部不破",
            "rsi_turn" to "RSI拐头",
            "vwap_or_climax" to "量价确认",
            "breakout_1m" to "1m突破",
        ),
    )

    fun gatesFor(strategyId: String): List<Pair<String, String>> =
        engineGates[strategyId] ?: engineGates.getValue("T1")

    fun reasonLabel(code: String): String = reasonMap[code] ?: code

    fun blockerLabel(code: String?, freeUsdt: String?): String? {
        if (code.isNullOrBlank() || code.equals("null", ignoreCase = true)) return null
        if (code == "INSUFFICIENT_FREE_USDT" && !freeUsdt.isNullOrBlank()) {
            return "可用 USDT $${formatNumber(freeUsdt, 2)} 低于单笔下限 $500.00，新买入已暂停"
        }
        return blockerMap[code] ?: code
    }

    fun emaAlignmentLabel(code: String?): String = when (code?.uppercase(Locale.US)) {
        "BULLISH" -> "多头排列"
        "BEARISH" -> "空头排列"
        "CONVERGING" -> "均线收敛"
        else -> "等待均线"
    }

    fun regimeLabel(code: String?): String = when (code?.uppercase(Locale.US)) {
        "UPTREND" -> "多头趋势"
        "DOWNTREND" -> "空头承压"
        "RANGE" -> "区间震荡"
        else -> code?.takeIf { it.isNotBlank() } ?: "分析中"
    }

    fun stageLabel(code: String?): String = when {
        code == null || code.isBlank() -> "S0"
        code.startsWith("S0") -> "S0"
        code.startsWith("S1") -> "S1"
        code.startsWith("S2") -> "S2"
        code.startsWith("S3") -> "S3"
        else -> code
    }

    fun formatCryptoPrice(raw: String?): String {
        val bd = parseBigDecimal(raw) ?: return "—"
        val abs = bd.abs()
        val pattern = when {
            abs >= BigDecimal("1000") -> "#,##0.00"
            abs >= BigDecimal("1") -> "#,##0.00##"
            else -> "0.0000##"
        }
        return DecimalFormat(pattern, usSymbols).apply {
            roundingMode = RoundingMode.HALF_UP
        }.format(bd)
    }

    fun formatNumber(raw: String?, decimals: Int = 2, grouping: Boolean = true): String {
        val bd = parseBigDecimal(raw) ?: return "—"
        val frac = if (decimals > 0) "." + "0".repeat(decimals) else ""
        val prefix = if (grouping) "#,##0" else "##0"
        return DecimalFormat(prefix + frac, usSymbols).apply {
            roundingMode = RoundingMode.HALF_UP
        }.format(bd)
    }

    fun formatPlainAmount(raw: String?, maxDecimals: Int = 4): String {
        val bd = parseBigDecimal(raw) ?: return "—"
        val scaled = bd.setScale(maxDecimals, RoundingMode.HALF_UP).stripTrailingZeros()
        return if (scaled.compareTo(BigDecimal.ZERO) == 0) "0" else scaled.toPlainString()
    }

    fun formatSignedUsdt(raw: String?): String {
        val bd = parseBigDecimal(raw) ?: return "—"
        val sign = if (bd >= BigDecimal.ZERO) "+" else "-"
        return "$sign$${formatNumber(bd.abs().toPlainString(), 2)}"
    }

    fun formatSignedPct(raw: String?): String {
        if (raw.isNullOrBlank()) return "—"
        val clean = raw.trim().removeSuffix("%")
        val bd = parseBigDecimal(clean) ?: return raw
        val sign = if (bd >= BigDecimal.ZERO) "+" else "-"
        return "$sign${formatNumber(bd.abs().toPlainString(), 2, grouping = false)}%"
    }

    fun formatCompactVolume(raw: String?, asset: String = ""): String {
        val clean = raw?.trim().orEmpty()
        if (clean.isEmpty() || clean.equals("null", ignoreCase = true) || clean == "—") return "—"
        val suffixMatch = Regex("""^([+-]?\d+(?:\.\d+)?)\s*([KMBkmb])$""").matchEntire(clean)
        val core = if (suffixMatch != null) {
            val num = suffixMatch.groupValues[1].toBigDecimalOrNull()?.let {
                it.setScale(2, RoundingMode.HALF_UP).toPlainString()
            } ?: suffixMatch.groupValues[1]
            "$num${suffixMatch.groupValues[2].uppercase(Locale.US)}"
        } else {
            val bd = parseBigDecimal(clean) ?: return "—"
            val v = bd.toDouble()
            when {
                v >= 1_000_000_000 -> String.format(Locale.US, "%.2fB", v / 1_000_000_000.0)
                v >= 1_000_000 -> String.format(Locale.US, "%.2fM", v / 1_000_000.0)
                v >= 1_000 -> String.format(Locale.US, "%.2fK", v / 1_000.0)
                else -> String.format(Locale.US, "%.2f", v)
            }
        }
        return if (asset.isNotBlank() && !core.endsWith(asset, ignoreCase = true)) "$core $asset" else core
    }

    fun formatEpoch(ms: Long?): String {
        if (ms == null || ms <= 0L) return "—"
        return timeFormatter.format(Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()))
    }

    fun formatEpochFull(ms: Long?): String {
        if (ms == null || ms <= 0L) return "—"
        return fullTimeFormatter.format(Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()))
    }

    fun formatClock(ms: Long?): String {
        if (ms == null || ms <= 0L) return "—"
        return clockFormatter.format(Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()))
    }

    fun formatCooldown(ms: Long): String {
        if (ms <= 0L) return "就绪"
        val totalSec = ms / 1000L
        val mins = totalSec / 60L
        val secs = totalSec % 60L
        return if (mins > 0) "${mins}分${secs}秒" else "${secs}秒"
    }

    fun parseBigDecimal(raw: String?): BigDecimal? {
        if (raw.isNullOrBlank() || raw == "null" || raw == "—") return null
        return runCatching { BigDecimal(raw.trim().replace(",", "")) }.getOrNull()
    }

    fun isNegative(raw: String?): Boolean {
        val clean = raw?.trim()?.removeSuffix("%")?.replace("$", "") ?: return false
        val bd = parseBigDecimal(clean) ?: return clean.startsWith("-")
        return bd < BigDecimal.ZERO
    }
}
