package com.lu.cryptobot
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.json.JSONObject
import javax.inject.Inject

private val ALL_TIMEFRAMES = listOf("1m", "5m", "15m", "1h", "4h")

data class ScreenState(
    val busy: Boolean = false,
    val live: String = "UNKNOWN",
    val error: String? = null,
    val values: Map<String, JSONObject> = emptyMap(),
    val interval: String = "5m",
    val alertsSeenAt: Long = 0L,
    val servAlertsSeenAt: Long = 0L,
    val deviceId: String = "",
    val baseUrl: String = "https://43.163.192.120/",
    val opsBusy: Boolean = false,
    val opsMessage: String? = null,
    val cleanPreview: JSONObject? = null,
    val systemLogs: JSONObject? = null,
    val logsBusy: Boolean = false,
    val shadowResetBusy: Boolean = false,
    val shadowResetMessage: String? = null,
)

internal object FrameSyncHelper {
    fun overlay(current: JSONObject?, summary: JSONObject, skip: Set<String>): JSONObject {
        val copy = JSONObject((current ?: JSONObject()).toString())
        val keys = summary.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            if (key in skip) continue
            copy.put(key, summary.get(key))
        }
        return copy
    }

    fun upsertLastCandle(candles: org.json.JSONArray?, lastCandle: JSONObject?): org.json.JSONArray? {
        if (candles == null || candles.length() == 0 || lastCandle == null) return candles
        val incomingOpen = lastCandle.optLong("open_time", -1L)
        if (incomingOpen <= 0L) return candles
        val out = org.json.JSONArray(candles.toString())
        val lastIdx = out.length() - 1
        val tail = out.optJSONObject(lastIdx)
        val tailOpen = tail?.optLong("open_time", -1L) ?: -1L
        when {
            tailOpen == incomingOpen -> out.put(lastIdx, JSONObject(lastCandle.toString()))
            tailOpen in 1 until incomingOpen -> {
                val trimmed = org.json.JSONArray()
                val start = if (out.length() >= 100) 1 else 0
                for (i in start until out.length()) {
                    trimmed.put(out.get(i))
                }
                trimmed.put(JSONObject(lastCandle.toString()))
                return trimmed
            }
        }
        return out
    }

    fun overlayMarket(current: JSONObject?, summary: JSONObject): JSONObject {
        val selectedTf = current?.optString("timeframe", "").orEmpty()
        val summaryTf = summary.optString("timeframe", "1m")
        if (selectedTf.isNotEmpty() && selectedTf != summaryTf) {
            val tfDoc = summary.optJSONObject("by_timeframe")?.optJSONObject(selectedTf)
            if (tfDoc != null) {
                val baseSkip = setOf("candles", "latest_price", "timeframe", "indicators", "ema_alignment", "ema_spread_pct", "by_timeframe", "last_candle")
                val merged = overlay(current, summary, baseSkip)
                val withTf = overlay(merged, tfDoc, setOf("last_candle"))
                upsertLastCandle(withTf.optJSONArray("candles"), tfDoc.optJSONObject("last_candle"))?.let {
                    withTf.put("candles", it)
                }
                return withTf
            }
            val skip = setOf("candles", "latest_price", "timeframe", "indicators", "ema_alignment", "ema_spread_pct", "by_timeframe", "last_candle")
            return overlay(current, summary, skip)
        }
        val skip = if (summary.has("candles")) {
            setOf("by_timeframe", "last_candle")
        } else {
            setOf("candles", "by_timeframe", "last_candle")
        }
        val merged = overlay(current, summary, skip)
        if (!summary.has("candles")) {
            val liveTip = summary.optJSONObject("last_candle")
                ?: summary.optJSONObject("by_timeframe")?.optJSONObject(summaryTf)?.optJSONObject("last_candle")
            upsertLastCandle(merged.optJSONArray("candles"), liveTip)?.let {
                merged.put("candles", it)
            }
        }
        return merged
    }

    fun mergeFrame(currentValues: Map<String, JSONObject>, frame: JSONObject): Map<String, JSONObject> {
        val map = currentValues.toMutableMap()
        frame.optJSONObject("status")?.let { map["live/status"] = JSONObject(it.toString()) }
        frame.optJSONObject("risk")?.let { map["live/risk"] = JSONObject(it.toString()) }
        frame.optJSONObject("positions")?.let { map["live/positions"] = JSONObject(it.toString()) }
        frame.optJSONObject("live_orders")?.let { if (it.length() > 0) map["live/orders"] = JSONObject(it.toString()) }
        frame.optJSONObject("binance_account")?.let { if (it.length() > 0) map["binance/account"] = JSONObject(it.toString()) }
        frame.optJSONObject("shadow_status")?.let { if (it.length() > 0) map["shadow/status"] = JSONObject(it.toString()) }
        frame.optJSONObject("shadow_orders")?.let { if (it.length() > 0) map["shadow/orders"] = JSONObject(it.toString()) }
        frame.optJSONObject("trading_config")?.let { if (it.length() > 0) map["trading/config"] = JSONObject(it.toString()) }
        frame.optJSONObject("system_overview")?.let { if (it.length() > 0) map["system/overview"] = JSONObject(it.toString()) }
        frame.optJSONObject("markets")?.let { summary -> map["markets"] = overlayMarket(map["markets"], summary) }
        frame.optJSONObject("strategies")?.let { summary ->
            val skip = if (summary.has("items")) emptySet() else setOf("items")
            map["strategies"] = overlay(map["strategies"], summary, skip)
        }
        return map
    }

    fun allowedLiveStrategies(strategies: JSONObject): Set<String> {
        val arr = strategies.optJSONArray("allowed_live_strategies") ?: return setOf("T1")
        val out = linkedSetOf<String>()
        for (i in 0 until arr.length()) {
            val sid = arr.optString(i).trim()
            if (sid.isNotEmpty()) out += sid
        }
        return if (out.isEmpty()) setOf("T1") else out
    }
}

@HiltViewModel class BotViewModel @Inject constructor(
    val repository: BotRepository,
    @param:dagger.hilt.android.qualifiers.ApplicationContext private val app: android.content.Context,
): ViewModel() {
    private val initialInterval = repository.lastSelectedInterval
    private val initialValues = repository.getInitialCachedValues(initialInterval)
    private val mutable = MutableStateFlow(
        ScreenState(
            interval = initialInterval,
            values = initialValues,
            live = initialValues["live/status"]?.let {
                RequestProtocol.authoritativeState(it.optString("state"), it.optBoolean("available", it.has("state")))
            } ?: "UNKNOWN",
        )
    )
    val state = mutable.asStateFlow()
    val alerts = repository.database.alerts().watch()

    init {
        viewModelScope.launch {
            syncSettingsSnapshot()
        }
        viewModelScope.launch { repository.frames.collect { applyFrame(it) } }
        viewModelScope.launch {
            val ok = runCatching { repository.ensureSession() }.getOrDefault(false)
            syncSettingsSnapshot()
            if (ok) {
                val activeInterval = mutable.value.interval
                // 1. High-priority immediate K-line fetch so chart updates in ~100ms
                launch { fetchMarketsFast(activeInterval) }
                // 2. Pre-warm all other timeframes in parallel for 0ms timeframe switching
                launch { prewarmTimeframes(exclude = activeInterval) }
                // 3. Background service + remaining state sync
                registerPush()
                BotConnectionService.start(app)
                syncAllSilently(activeInterval)
            }
            startRealtimeLoop()
        }
    }

    private suspend fun fetchMarketsFast(interval: String) {
        try {
            val markets = repository.get("markets?symbol=SOLUSDT&timeframe=$interval")
            repository.putCachedMarket(interval, markets)
            mutable.update { curr ->
                if (curr.interval != interval) curr
                else {
                    val nextValues = curr.values + ("markets" to markets)
                    repository.putCachedValues(nextValues)
                    curr.copy(values = nextValues)
                }
            }
        } catch (_: Exception) {
        }
    }

    private suspend fun prewarmTimeframes(exclude: String) = coroutineScope {
        ALL_TIMEFRAMES.filter { it != exclude && repository.getCachedMarket(it) == null }.map { tf ->
            async {
                runCatching {
                    val doc = repository.get("markets?symbol=SOLUSDT&timeframe=$tf")
                    repository.putCachedMarket(tf, doc)
                    mutable.update { curr ->
                        if (curr.interval == tf) {
                            curr.copy(values = curr.values + ("markets" to doc))
                        } else curr
                    }
                }
            }
        }.awaitAll()
    }

    private fun startRealtimeLoop() {
        viewModelScope.launch {
            while (true) {
                kotlinx.coroutines.delay(2_000L)
                if (!runCatching { repository.bound() }.getOrDefault(false)) continue
                val currentInterval = mutable.value.interval
                val currentMarkets = mutable.value.values["markets"]
                val candlesArr = currentMarkets?.optJSONArray("candles")
                val needsCandles = candlesArr == null || candlesArr.length() == 0 || currentMarkets.optString("timeframe") != currentInterval
                val wsSilentMs = System.currentTimeMillis() - repository.lastFrameAt.get()
                if (needsCandles) {
                    fetchMarketsFast(currentInterval)
                }
                if (wsSilentMs > 2_800L) {
                    syncAllSilently(currentInterval)
                }
            }
        }
    }

    private suspend fun syncAllSilently(interval: String = mutable.value.interval) = coroutineScope {
        // Fetch markets & strategies immediately in parallel first
        val fastJobs = listOf(
            async {
                runCatching {
                    val fetched = repository.get("markets?symbol=SOLUSDT&timeframe=$interval")
                    repository.putCachedMarket(interval, fetched)
                    mutable.update { curr ->
                        if (curr.interval == interval) {
                            curr.copy(values = curr.values + ("markets" to fetched))
                        } else curr
                    }
                }
            },
            async {
                runCatching {
                    val fetched = repository.get("strategies?symbol=SOLUSDT")
                    mutable.update { curr -> curr.copy(values = curr.values + ("strategies" to fetched)) }
                }
            },
        )
        fastJobs.awaitAll()

        // Fetch remaining endpoints without clobbering concurrent market/timeframe updates
        val otherPaths = listOf(
            "status",
            "live/status",
            "live/orders",
            "live/positions",
            "live/risk",
            "binance/account",
            "trading/config",
            "shadow/status",
            "shadow/orders",
            "system/overview",
        )
        val updates = mutableMapOf<String, JSONObject>()
        for (path in otherPaths) {
            try {
                val key = path.substringBefore('?')
                updates[key] = repository.get(path)
            } catch (_: Exception) {
            }
        }
        if (updates.isNotEmpty()) {
            mutable.update { curr ->
                val merged = curr.values.toMutableMap()
                for ((k, v) in updates) {
                    merged[k] = v
                }
                val live = merged["live/status"]
                val shown = curr.error
                val recovered = shown == "connection closed" || shown == "连接中断，请再拉一次刷新"
                repository.putCachedValues(merged)
                curr.copy(
                    values = merged,
                    live = if (live != null) RequestProtocol.authoritativeState(live.optString("state"), true) else curr.live,
                    error = if (recovered) null else shown,
                )
            }
        }
        if ("status" in updates) runCatching { repository.syncAlerts() }
    }

    private suspend fun syncSettingsSnapshot() {
        val seen = repository.settings.get("alerts_seen_ms").toLongOrNull() ?: 0L
        val servSeen = repository.settings.get("serv_alerts_seen_ms").toLongOrNull() ?: 0L
        val dev = repository.settings.get("device")
        val base = repository.settings.get("base").ifBlank { "https://43.163.192.120/" }
        mutable.update { curr ->
            curr.copy(
                alertsSeenAt = maxOf(curr.alertsSeenAt, seen),
                servAlertsSeenAt = maxOf(curr.servAlertsSeenAt, servSeen),
                deviceId = dev,
                baseUrl = base,
            )
        }
    }

    private fun applyFrame(frame: JSONObject) {
        mutable.update { curr ->
            val seededValues = if (curr.values["markets"]?.optJSONArray("candles")?.length()?.let { it > 0 } == true &&
                curr.values["markets"]?.optString("timeframe") == curr.interval
            ) {
                curr.values
            } else {
                val cachedTf = repository.getCachedMarket(curr.interval)
                when {
                    cachedTf != null -> curr.values + ("markets" to cachedTf)
                    curr.values["markets"] == null -> curr.values + ("markets" to JSONObject().put("timeframe", curr.interval))
                    else -> curr.values
                }
            }
            val map = FrameSyncHelper.mergeFrame(seededValues, frame)
            repository.putCachedValues(map)
            val live = map["live/status"]
            val shown = curr.error
            val recovered = shown == "connection closed" || shown == "连接中断，请再拉一次刷新"
            curr.copy(
                values = map,
                error = if (recovered) null else shown,
                live = RequestProtocol.authoritativeState(
                    live?.optString("state"),
                    live != null && live.optBoolean("available", live.has("state")),
                ),
            )
        }
    }

    fun refreshCandles(interval: String) {
        repository.saveIntervalPreference(interval)
        val cached = repository.getCachedMarket(interval)
        mutable.update { curr ->
            val nextValues = if (cached != null) {
                // Preserve latest live micro/24h ticker stats when swapping cached timeframe candles
                val currentMarket = curr.values["markets"]
                val mergedCached = if (currentMarket != null) {
                    val copy = JSONObject(cached.toString())
                    for (k in listOf("micro", "change_24h", "high_24h", "low_24h", "volume_24h", "volume_24h_asset")) {
                        if (currentMarket.has(k)) copy.put(k, currentMarket.get(k))
                    }
                    copy
                } else cached
                curr.values + ("markets" to mergedCached)
            } else curr.values
            curr.copy(interval = interval, values = nextValues)
        }
        viewModelScope.launch {
            fetchMarketsFast(interval)
        }
    }

    fun markAlertsRead(latestReceivedAt: Long) {
        val next = maxOf(mutable.value.alertsSeenAt, latestReceivedAt)
        mutable.update { it.copy(alertsSeenAt = next) }
        viewModelScope.launch { repository.settings.put("alerts_seen_ms", next.toString()) }
    }

    fun markServAlertsRead(latestNoticeAt: Long) {
        val next = if (latestNoticeAt > 0L) {
            maxOf(mutable.value.servAlertsSeenAt, latestNoticeAt)
        } else {
            maxOf(mutable.value.servAlertsSeenAt, System.currentTimeMillis())
        }
        mutable.update { it.copy(servAlertsSeenAt = next) }
        viewModelScope.launch { repository.settings.put("serv_alerts_seen_ms", next.toString()) }
    }

    fun triggerTestAlert() {
        val testRec = AlertNotifier.triggerTestAlert(app)
        viewModelScope.launch {
            repository.database.alerts().insert(testRec)
        }
    }

    private fun registerPush() {
        com.google.firebase.messaging.FirebaseMessaging.getInstance().token.addOnSuccessListener { token ->
            viewModelScope.launch { runCatching { repository.registerToken(token) } }
        }
    }

    fun refresh(interval: String = mutable.value.interval) = run {
        repository.saveIntervalPreference(interval)
        mutable.update { it.copy(interval = interval) }
        viewModelScope.launch {
            fetchMarketsFast(interval)
            syncAllSilently(interval)
        }
    }
    private fun execute(block: suspend ()->Unit) { viewModelScope.launch {
        mutable.update { it.copy(busy = true, error = null) }
        try { block() } catch (error: Exception) { mutable.update { it.copy(live = "UNKNOWN", error = (error.message ?: error.javaClass.simpleName).take(180)) } }
        finally { mutable.update { it.copy(busy = false) } }
    } }
    fun connect(base: String,code: String,interval: String) { execute {
        repository.configure(base); if(code.isNotBlank()) repository.pair(code) else repository.reconnect()
        syncSettingsSnapshot()
        val value=repository.get("live/status");mutable.update { it.copy(live=RequestProtocol.authoritativeState(value.optString("state"),true),values=it.values+("live/status" to value)) }
        com.google.firebase.messaging.FirebaseMessaging.getInstance().token.addOnSuccessListener { token->viewModelScope.launch { runCatching { repository.registerToken(token) } } }
        BotConnectionService.start(app)
        refresh(interval)
    } }
    fun live(on: Boolean) { execute {
        val reply=repository.post(if(on) "live/request-enable" else "live/disable",if(on) JSONObject().put("risk_confirmed",true) else JSONObject())
        val snapshot=reply.optJSONObject("snapshot") ?: reply
        val code=if(!reply.optBoolean("accepted",false)) reply.optString("code",reply.optString("reason")) else ""
        mutable.update { it.copy(live=RequestProtocol.authoritativeState(snapshot.optString("state"),true),error=if(code.isEmpty()) null else binanceMessage(IllegalStateException(code))) }
        val fresh=repository.get("live/status");mutable.update { it.copy(live=RequestProtocol.authoritativeState(fresh.optString("state"),true),values=it.values+("live/status" to fresh)) }
    } }
    fun custody(position: JSONObject, on: Boolean) { execute { binance {
        val intent = position.optString("intent_id")
        require(intent.isNotEmpty()) { "持仓没有可托管的编号" }
        if (on) {
            repository.post("custody/grant", JSONObject()
                .put("asset", position.optString("asset", "SOL"))
                .put("quantity", position.optString("quantity"))
                .put("position_id", intent)
                .put("expires_at", System.currentTimeMillis() + 86400000L)
                .put("risk_confirmed", true))
        } else {
            val authorization = position.optString("authorization_id")
            repository.post("custody/revoke", if (authorization.isNotEmpty()) JSONObject().put("authorization_id", authorization) else JSONObject().put("position_id", intent))
        }
        refresh()
    } } }
    fun saveBinance(apiKey: String, secret: String) { execute { binance { repository.post("binance/account", JSONObject().put("api_key", apiKey).put("secret", secret)); refresh() } } }
    fun syncBinance() { execute { binance { repository.post("binance/sync"); refresh() } } }
    fun clearBinance() { execute { binance { repository.post("binance/clear"); refresh() } } }
    fun saveTradingConfig(minOrderUsdt: String, maxOrderUsdt: String, maxSlots: Int) { execute { binance {
        val reply = repository.post(
            "trading/config",
            JSONObject()
                .put("min_order_usdt", minOrderUsdt.trim())
                .put("max_order_usdt", maxOrderUsdt.trim().ifBlank { minOrderUsdt.trim() })
                .put("max_slots", maxSlots)
        )
        mutable.update { it.copy(values = it.values + ("trading/config" to reply)) }
        refresh()
    } } }
    private suspend fun binance(block: suspend () -> Unit) {
        try { block() } catch (error: Exception) { mutable.update { it.copy(error = binanceMessage(error)) } }
    }
    private fun binanceMessage(error: Exception) = when (error.message) {
        "WITHDRAWALS_ENABLED" -> "请先在币安关闭这个 API 的提现权限"
        "READ_PERMISSION_REQUIRED" -> "这个 API 没有读取权限"
        "INVALID_KEY" -> "密钥格式不正确"
        "BINANCE_REJECTED" -> "币安没有接受这对密钥"
        "BINANCE_UNREACHABLE" -> "暂时连不上币安"
        "NOT_CONFIGURED" -> "还没有保存 API"
        "DENY_NOT_READY" -> "实盘开关没有打开。请确认币安现货交易权限已开启，并且提现保持关闭。"
        "USDT_BELOW_500", "USDT_BELOW_300", "USDT_BELOW_MIN" -> "可用 USDT 不足配置的最小开仓金额，这次没有买入。"
        "SLOTS_FULL" -> "已达到最大策略持仓槽位上限，不再开新仓。"
        "INVALID_ORDER_USDT", "INVALID_TRADING_CONFIG" -> "单笔开仓金额需在 $10–$50,000 USDT 之间，且最大值不低于最小值。"
        "INVALID_MAX_SLOTS" -> "持仓订单数量需在 1–10 槽之间。"
        "TRADE_PERMISSION_REQUIRED" -> "托管止损需要现货交易权限。请保持提现关闭，只打开现货交易后再试。"
        "INSUFFICIENT_FREE_SOL" -> "可用 SOL 不足，止损单没有提交。"
        "ENTRY_PRICE_MISSING" -> "找不到这批 SOL 的买入价，止损单没有提交。"
        "PROTECTION_REJECTED" -> "币安没有接受这张止损单。"
        "STOP_LOCKING_SOL" -> "止损已挂上，SOL 已被锁定。"
        else -> (error.message ?: "保存失败").take(180)
    }
    fun refreshSystemOverview() {
        viewModelScope.launch {
            runCatching { repository.get("system/overview") }.onSuccess { doc ->
                mutable.update { it.copy(values = it.values + ("system/overview" to doc)) }
            }
        }
    }

    fun optimizeServerMemory() {
        viewModelScope.launch {
            mutable.update { it.copy(opsBusy = true, opsMessage = null) }
            try {
                val res = repository.post("system/optimize-memory", JSONObject())
                val msg = res.optString("message").ifBlank {
                    if (res.optBoolean("ok", false)) "内存优化完成" else "内存优化未执行"
                }
                mutable.update { it.copy(opsMessage = msg) }
                refreshSystemOverview()
            } catch (error: Exception) {
                mutable.update { it.copy(opsMessage = "内存优化失败：${(error.message ?: "网络异常").take(80)}") }
            } finally {
                mutable.update { it.copy(opsBusy = false) }
            }
        }
    }

    fun previewCleanServerCache() {
        viewModelScope.launch {
            mutable.update { it.copy(opsBusy = true, opsMessage = null) }
            try {
                val res = repository.post("system/clean-cache", JSONObject().put("dry_run", true))
                mutable.update { it.copy(cleanPreview = res) }
            } catch (error: Exception) {
                mutable.update { it.copy(opsMessage = "扫描缓存失败：${(error.message ?: "网络异常").take(80)}") }
            } finally {
                mutable.update { it.copy(opsBusy = false) }
            }
        }
    }

    fun executeCleanServerCache() {
        viewModelScope.launch {
            mutable.update { it.copy(opsBusy = true, cleanPreview = null, opsMessage = null) }
            try {
                val res = repository.post("system/clean-cache", JSONObject().put("dry_run", false))
                val freedMb = res.optDouble("total_freed_mb", 0.0)
                val diskAfter = res.optDouble("disk_after_pct", 0.0)
                val msg = if (res.optBoolean("ok", false)) {
                    "清理完成：释放 ${String.format("%.1f", freedMb)} MB（磁盘 ${String.format("%.1f", diskAfter)}%）"
                } else {
                    res.optString("message", "清理未执行")
                }
                mutable.update { it.copy(opsMessage = msg) }
                refreshSystemOverview()
            } catch (error: Exception) {
                mutable.update { it.copy(opsMessage = "清理失败：${(error.message ?: "网络异常").take(80)}") }
            } finally {
                mutable.update { it.copy(opsBusy = false) }
            }
        }
    }

    fun dismissCleanPreview() {
        mutable.update { it.copy(cleanPreview = null) }
    }

    fun clearOpsMessage() {
        mutable.update { it.copy(opsMessage = null) }
    }

    fun resetShadowTradingData() {
        viewModelScope.launch {
            mutable.update { it.copy(shadowResetBusy = true, shadowResetMessage = null) }
            try {
                val res = repository.post("shadow/reset", JSONObject().put("confirm", true))
                val updatedValues = mutable.value.values.toMutableMap()
                runCatching { repository.get("shadow/status") }.onSuccess { updatedValues["shadow/status"] = it }
                runCatching { repository.get("shadow/orders") }.onSuccess { updatedValues["shadow/orders"] = it }
                mutable.update {
                    it.copy(
                        values = updatedValues,
                        shadowResetMessage = res.optString("message", "Shadow 模拟盘交易数据已重置"),
                    )
                }
                refresh()
            } catch (error: Exception) {
                mutable.update {
                    it.copy(
                        shadowResetMessage = "重置失败：${(error.message ?: "网络异常").take(80)}"
                    )
                }
            } finally {
                mutable.update { it.copy(shadowResetBusy = false) }
            }
        }
    }

    fun dismissShadowResetMessage() {
        mutable.update { it.copy(shadowResetMessage = null) }
    }

    fun loadSystemLogs(source: String = "auth", onlyAlerts: Boolean = false) {
        viewModelScope.launch {
            mutable.update { it.copy(logsBusy = true) }
            try {
                val res = repository.get("system/logs?source=$source&only_alerts=$onlyAlerts&limit=80")
                mutable.update { it.copy(systemLogs = res) }
            } catch (error: Exception) {
                mutable.update {
                    it.copy(
                        systemLogs = JSONObject()
                            .put("ok", false)
                            .put("source", source)
                            .put("error", (error.message ?: "读取日志失败").take(100))
                    )
                }
            } finally {
                mutable.update { it.copy(logsBusy = false) }
            }
        }
    }

    fun revoke() { execute { repository.revokeSelf();BotConnectionService.stop(app);mutable.value=ScreenState() } }
}
