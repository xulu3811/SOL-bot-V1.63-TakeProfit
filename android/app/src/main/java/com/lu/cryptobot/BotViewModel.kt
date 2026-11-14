package com.lu.cryptobot
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONObject
import javax.inject.Inject

data class ScreenState(
    val busy: Boolean = false,
    val live: String = "UNKNOWN",
    val error: String? = null,
    val values: Map<String, JSONObject> = emptyMap(),
    val interval: String = "15m",
    val alertsSeenAt: Long = 0L,
    val deviceId: String = "",
    val baseUrl: String = "https://43.163.192.120/",
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

    fun overlayMarket(current: JSONObject?, summary: JSONObject): JSONObject {
        val selectedTf = current?.optString("timeframe", "").orEmpty()
        val summaryTf = summary.optString("timeframe", "1m")
        val hasCurrentPrice = current != null && current.has("latest_price") && !current.isNull("latest_price")
        val skip = when {
            selectedTf.isNotEmpty() && selectedTf != summaryTf ->
                setOf("candles", "latest_price", "timeframe", "indicators", "ema_alignment", "ema_spread_pct")
            hasCurrentPrice ->
                setOf("candles", "latest_price", "timeframe", "indicators")
            else ->
                setOf("candles", "timeframe", "indicators")
        }
        return overlay(current, summary, skip)
    }

    fun mergeFrame(currentValues: Map<String, JSONObject>, frame: JSONObject): Map<String, JSONObject> {
        val map = currentValues.toMutableMap()
        frame.optJSONObject("status")?.let { map["live/status"] = JSONObject(it.toString()) }
        frame.optJSONObject("risk")?.let { map["live/risk"] = JSONObject(it.toString()) }
        frame.optJSONObject("positions")?.let { map["live/positions"] = JSONObject(it.toString()) }
        frame.optJSONObject("markets")?.let { summary -> map["markets"] = overlayMarket(map["markets"], summary) }
        frame.optJSONObject("strategies")?.let { summary -> map["strategies"] = overlay(map["strategies"], summary, setOf("items")) }
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
    private val mutable = MutableStateFlow(ScreenState())
    val state = mutable.asStateFlow()
    val alerts = repository.database.alerts().watch()

    init {
        viewModelScope.launch {
            syncSettingsSnapshot()
        }
        viewModelScope.launch { repository.frames.collect { applyFrame(it) } }
        execute {
            val ok = repository.ensureSession()
            syncSettingsSnapshot()
            if (ok) { registerPush(); BotConnectionService.start(app); refresh() }
        }
    }

    private suspend fun syncSettingsSnapshot() {
        val seen = repository.settings.get("alerts_seen_ms").toLongOrNull() ?: 0L
        val dev = repository.settings.get("device")
        val base = repository.settings.get("base").ifBlank { "https://43.163.192.120/" }
        mutable.value = mutable.value.copy(
            alertsSeenAt = maxOf(mutable.value.alertsSeenAt, seen),
            deviceId = dev,
            baseUrl = base,
        )
    }

    private fun applyFrame(frame: JSONObject) {
        val map = FrameSyncHelper.mergeFrame(mutable.value.values, frame)
        val live = map["live/status"]
        val shown = mutable.value.error
        val recovered = shown == "connection closed" || shown == "连接中断，请再拉一次刷新"
        mutable.value = mutable.value.copy(
            values = map,
            error = if (recovered) null else shown,
            live = RequestProtocol.authoritativeState(
                live?.optString("state"),
                live != null && live.optBoolean("available", live.has("state")),
            ),
        )
    }

    fun refreshCandles(interval: String) {
        mutable.value = mutable.value.copy(interval = interval)
        viewModelScope.launch {
            try {
                val markets = repository.get("markets?symbol=SOLUSDT&timeframe=$interval")
                if (mutable.value.interval == interval) {
                    val map = mutable.value.values.toMutableMap()
                    map["markets"] = markets
                    mutable.value = mutable.value.copy(values = map, interval = interval)
                }
            } catch (_: Exception) {
            }
        }
    }

    fun markAlertsRead(latestReceivedAt: Long) {
        val next = maxOf(mutable.value.alertsSeenAt, latestReceivedAt)
        mutable.value = mutable.value.copy(alertsSeenAt = next)
        viewModelScope.launch { repository.settings.put("alerts_seen_ms", next.toString()) }
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
        mutable.value = mutable.value.copy(interval = interval)
        execute {
            val map = mutable.value.values.toMutableMap()
            val errors = mutableListOf<String>()
            val currentInterval = mutable.value.interval
            for (path in listOf("status", "markets?symbol=SOLUSDT&timeframe=$currentInterval", "strategies?symbol=SOLUSDT", "live/status", "live/orders", "live/positions", "live/risk", "binance/account", "shadow/status", "shadow/orders")) {
                try {
                    val key = path.substringBefore('?')
                    val fetched = repository.get(path)
                    if (key == "markets" && mutable.value.interval != currentInterval) continue
                    map[key] = fetched
                }
                catch (error: Exception) { errors += (error.message ?: path).take(80) }
            }
            val live = map["live/status"]
            mutable.value = mutable.value.copy(
                values = map,
                live = RequestProtocol.authoritativeState(live?.optString("state"), live != null),
                error = errors.firstOrNull(),
            )
            if ("status" in map) runCatching { repository.syncAlerts() }
        }
    }
    private fun execute(block: suspend ()->Unit) { viewModelScope.launch {
        mutable.value=mutable.value.copy(busy=true,error=null)
        try { block() } catch (error: Exception) { mutable.value=mutable.value.copy(live="UNKNOWN",error=(error.message ?: error.javaClass.simpleName).take(180)) }
        finally { mutable.value=mutable.value.copy(busy=false) }
    } }
    fun connect(base: String,code: String,interval: String) { execute {
        repository.configure(base); if(code.isNotBlank()) repository.pair(code) else repository.reconnect()
        syncSettingsSnapshot()
        val value=repository.get("live/status");mutable.value=mutable.value.copy(live=RequestProtocol.authoritativeState(value.optString("state"),true),values=mutable.value.values+("live/status" to value))
        com.google.firebase.messaging.FirebaseMessaging.getInstance().token.addOnSuccessListener { token->viewModelScope.launch { runCatching { repository.registerToken(token) } } }
        BotConnectionService.start(app)
        refresh(interval)
    } }
    fun live(on: Boolean) { execute {
        val reply=repository.post(if(on) "live/request-enable" else "live/disable",if(on) JSONObject().put("risk_confirmed",true) else JSONObject())
        val snapshot=reply.optJSONObject("snapshot") ?: reply
        val code=if(!reply.optBoolean("accepted",false)) reply.optString("code",reply.optString("reason")) else ""
        mutable.value=mutable.value.copy(live=RequestProtocol.authoritativeState(snapshot.optString("state"),true),error=if(code.isEmpty()) null else binanceMessage(IllegalStateException(code)))
        val fresh=repository.get("live/status");mutable.value=mutable.value.copy(live=RequestProtocol.authoritativeState(fresh.optString("state"),true),values=mutable.value.values+("live/status" to fresh))
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
    private suspend fun binance(block: suspend () -> Unit) {
        try { block() } catch (error: Exception) { mutable.value = mutable.value.copy(error = binanceMessage(error)) }
    }
    private fun binanceMessage(error: Exception) = when (error.message) {
        "WITHDRAWALS_ENABLED" -> "请先在币安关闭这个 API 的提现权限"
        "READ_PERMISSION_REQUIRED" -> "这个 API 没有读取权限"
        "INVALID_KEY" -> "密钥格式不正确"
        "BINANCE_REJECTED" -> "币安没有接受这对密钥"
        "BINANCE_UNREACHABLE" -> "暂时连不上币安"
        "NOT_CONFIGURED" -> "还没有保存 API"
        "DENY_NOT_READY" -> "实盘开关没有打开。请确认币安现货交易权限已开启，并且提现保持关闭。"
        "USDT_BELOW_500", "USDT_BELOW_300" -> "可用 USDT 不足 500，这次没有买入。"
        "SLOTS_FULL" -> "已经有 2 个策略仓，不再开新仓。"
        "TRADE_PERMISSION_REQUIRED" -> "托管止损需要现货交易权限。请保持提现关闭，只打开现货交易后再试。"
        "INSUFFICIENT_FREE_SOL" -> "可用 SOL 不足，止损单没有提交。"
        "ENTRY_PRICE_MISSING" -> "找不到这批 SOL 的买入价，止损单没有提交。"
        "PROTECTION_REJECTED" -> "币安没有接受这张止损单。"
        "STOP_LOCKING_SOL" -> "止损已挂上，SOL 已被锁定。"
        else -> (error.message ?: "保存失败").take(180)
    }
    fun revoke() { execute { repository.revokeSelf();BotConnectionService.stop(app);mutable.value=ScreenState() } }
}
