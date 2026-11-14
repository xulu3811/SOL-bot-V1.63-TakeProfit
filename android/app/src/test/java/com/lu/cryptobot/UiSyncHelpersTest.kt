package com.lu.cryptobot

import com.lu.cryptobot.ui.charts.parseCandleSeries
import com.lu.cryptobot.ui.components.BotFormatters
import com.lu.cryptobot.ui.components.optCleanString
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UiSyncHelpersTest {
    @Test
    fun wsOneMinuteSummaryDoesNotOverwriteFifteenMinuteEmaAlignmentOrCandles() {
        val current = JSONObject()
            .put("timeframe", "15m")
            .put("latest_price", "80.40")
            .put("ema_alignment", "BEARISH")
            .put("ema_spread_pct", -0.31)
            .put("candles", JSONArray().put(JSONArray().put(1000L).put("80").put("81").put("79").put("80.4")))
        val wsSummary = JSONObject()
            .put("timeframe", "1m")
            .put("latest_price", "80.42")
            .put("ema_alignment", "BULLISH")
            .put("ema_spread_pct", 0.12)
            .put("change_24h", "-3.50%")
            .put("micro", JSONObject().put("ready", true).put("bid", "80.41"))

        val merged = FrameSyncHelper.overlayMarket(current, wsSummary)
        assertEquals("15m", merged.getString("timeframe"))
        assertEquals("BEARISH", merged.getString("ema_alignment"))
        assertEquals(-0.31, merged.getDouble("ema_spread_pct"), 1e-6)
        assertEquals("-3.50%", merged.getString("change_24h"))
        assertEquals("80.41", merged.getJSONObject("micro").getString("bid"))
        assertEquals(1, merged.getJSONArray("candles").length())
    }

    @Test
    fun wsOneMinuteSummaryUpdatesEmaAlignmentWhenOnOneMinuteChart() {
        val current = JSONObject()
            .put("timeframe", "1m")
            .put("ema_alignment", "CONVERGING")
        val wsSummary = JSONObject()
            .put("timeframe", "1m")
            .put("ema_alignment", "BULLISH")
            .put("ema_spread_pct", 0.15)

        val merged = FrameSyncHelper.overlayMarket(current, wsSummary)
        assertEquals("BULLISH", merged.getString("ema_alignment"))
        assertEquals(0.15, merged.getDouble("ema_spread_pct"), 1e-6)
    }

    @Test
    fun mergeFrameOverlaysRiskAndRespectsAllowedLiveStrategies() {
        val frame = JSONObject()
            .put("risk", JSONObject().put("drawdown_pct", "0.25").put("hard_limit_pct", "5.00"))
        val updated = FrameSyncHelper.mergeFrame(emptyMap(), frame)
        assertEquals("0.25", updated["live/risk"]?.getString("drawdown_pct"))

        val defaultAllowed = FrameSyncHelper.allowedLiveStrategies(JSONObject())
        assertEquals(setOf("T1"), defaultAllowed)

        val explicit = FrameSyncHelper.allowedLiveStrategies(
            JSONObject().put("allowed_live_strategies", JSONArray().put("T1").put("R1")),
        )
        assertEquals(setOf("T1", "R1"), explicit)
    }

    @Test
    fun formattersNormalizeLongDecimalsAlignmentsAndBlockers() {
        assertEquals("1.24", BotFormatters.formatNumber("1.2438584489085142", 2))
        assertEquals("2,000.00", BotFormatters.formatNumber("2000.00", 2))
        assertEquals("+$4.00", BotFormatters.formatSignedUsdt("4"))
        assertEquals("-$3.50", BotFormatters.formatSignedUsdt("-3.5"))
        assertEquals("空头排列", BotFormatters.emaAlignmentLabel("BEARISH"))
        assertEquals("多头排列", BotFormatters.emaAlignmentLabel("BULLISH"))
        val msg = BotFormatters.blockerLabel("INSUFFICIENT_FREE_USDT", "1.61").orEmpty()
        assertTrue(msg.contains("1.61") && msg.contains("300.00"))
        assertEquals("4h/1h 趋势强度不足", BotFormatters.reasonLabel("HIGHER_TIMEFRAME_WEAK_TREND"))
        assertEquals("", JSONObject().put("blocker", JSONObject.NULL).optCleanString("blocker"))
        assertEquals("", JSONObject().put("blocker", "null").optCleanString("blocker"))
        assertNull(BotFormatters.blockerLabel("null", "2182.27"))
        assertEquals("3.34M SOL", BotFormatters.formatCompactVolume("3.34M", "SOL"))
        assertEquals("3.34M SOL", BotFormatters.formatCompactVolume("3340000", "SOL"))
        assertEquals("850.12K SOL", BotFormatters.formatCompactVolume("850.12K", "SOL"))
        assertEquals("—", BotFormatters.formatCompactVolume("null", "SOL"))
    }

    @Test
    fun parseCandleSeriesHandlesBackendObjectsAndLegacyArrays() {
        val objectRows = JSONArray()
            .put(
                JSONObject()
                    .put("open_time", 1794580000000L)
                    .put("open", "80.10")
                    .put("high", "80.50")
                    .put("low", "79.90")
                    .put("close", "80.03")
                    .put("volume", "12345.67")
                    .put(
                        "indicators",
                        JSONObject()
                            .put("ema20", 80.25)
                            .put("ema50", JSONObject.NULL)
                            .put("vwap", 80.12),
                    ),
            )
            .put(
                JSONObject()
                    .put("open_time", 1794580900000L)
                    .put("open", "80.03")
                    .put("high", "80.20")
                    .put("low", "79.80")
                    .put("close", "79.95")
                    .put("volume", "9876.54")
                    .put(
                        "indicators",
                        JSONObject()
                            .put("ema20", 80.20)
                            .put("ema50", 80.45)
                            .put("vwap", 80.10),
                    ),
            )
        val objBars = parseCandleSeries(objectRows)
        assertEquals(2, objBars.size)
        assertEquals(1794580000000L, objBars[0].openTimeMs)
        assertEquals(80.03, objBars[0].close, 1e-6)
        assertEquals(80.25, objBars[0].ema20, 1e-6)
        assertEquals(80.03, objBars[0].ema50, 1e-6)
        assertEquals(80.45, objBars[1].ema50, 1e-6)
        assertEquals(80.10, objBars[1].vwap, 1e-6)

        val legacyRows = JSONArray()
            .put(JSONArray().put(1794580000000L).put("80.0").put("81.0").put("79.5").put("80.4").put("1000"))
        val legacyBars = parseCandleSeries(legacyRows)
        assertEquals(1, legacyBars.size)
        assertEquals(80.4, legacyBars[0].close, 1e-6)
    }
}
