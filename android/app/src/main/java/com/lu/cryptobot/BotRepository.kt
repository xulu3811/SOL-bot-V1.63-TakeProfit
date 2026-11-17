package com.lu.cryptobot
import android.content.Context
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import androidx.room.Room
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import retrofit2.Retrofit
import retrofit2.http.*
import java.util.UUID
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

interface AppApi {
    @GET suspend fun get(@Url path: String,@HeaderMap headers: Map<String,String>): ResponseBody
    @POST suspend fun post(@Url path: String,@HeaderMap headers: Map<String,String>,@Body body: RequestBody): ResponseBody
}
@Module @InstallIn(SingletonComponent::class) object AppModule {
    @Provides @Singleton fun settings(@ApplicationContext context: Context)=Settings(context)
    @Provides @Singleton fun database(@ApplicationContext context: Context)=Room.databaseBuilder(context,BotDatabase::class.java,"alerts.db").build()
    @Provides @Singleton fun http(@ApplicationContext context: Context): OkHttpClient {
        val factory=javax.net.ssl.TrustManagerFactory.getInstance(javax.net.ssl.TrustManagerFactory.getDefaultAlgorithm())
        val store=java.security.KeyStore.getInstance(java.security.KeyStore.getDefaultType())
        store.load(null,null)
        context.resources.openRawResource(R.raw.solbot_ca).use { store.setCertificateEntry("solbot",java.security.cert.CertificateFactory.getInstance("X.509").generateCertificate(it)) }
        factory.init(store)
        val trust=factory.trustManagers.filterIsInstance<javax.net.ssl.X509TrustManager>().single()
        val ssl=javax.net.ssl.SSLContext.getInstance("TLS");ssl.init(null,arrayOf(trust),java.security.SecureRandom())
        return OkHttpClient.Builder().sslSocketFactory(ssl.socketFactory,trust).connectTimeout(10,TimeUnit.SECONDS).readTimeout(15,TimeUnit.SECONDS).callTimeout(20,TimeUnit.SECONDS).pingInterval(15,TimeUnit.SECONDS).retryOnConnectionFailure(false).followRedirects(false).followSslRedirects(false).build()
    }
}
@Singleton class BotRepository @Inject constructor(
    val settings: Settings,
    val identity: DeviceIdentity,
    val database: BotDatabase,
    private val http: OkHttpClient,
    @param:ApplicationContext private val appContext: Context,
) {
    val frames = MutableSharedFlow<JSONObject>(extraBufferCapacity = 32)
    val lastFrameAt = java.util.concurrent.atomic.AtomicLong(0L)
    private val alertScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val alertMutex = Mutex()
    @Volatile private var session: String=""
    @Volatile private var expiresAt: Long=0
    suspend fun configured()=settings.get("base").isNotEmpty()
    suspend fun configure(base: String) {
        val url=base.toHttpUrl();require(url.isHttps && url.username.isEmpty() && url.password.isEmpty() && url.encodedPath=="/") { "需要可信 HTTPS 根地址" }
        val old=settings.get("base"); if(old.isNotEmpty() && old!=url.toString()) { settings.put("device",""); session="";expiresAt=0 }
        settings.put("base",url.toString())
    }
    private suspend fun api(): AppApi {
        val base=settings.get("base");require(base.isNotEmpty()) { "请先设置已验证的 HTTPS 地址" }
        require(base.toHttpUrl().isHttps)
        return Retrofit.Builder().baseUrl(base).client(http).build().create(AppApi::class.java)
    }
    private suspend fun request(path: String,body: JSONObject?=null,pairing: Boolean=false,noSession: Boolean=false): JSONObject = withContext(Dispatchers.IO) {
        val bytes=body?.toString()?.toByteArray(Charsets.UTF_8) ?: byteArrayOf();val method=if(body==null) "GET" else "POST"
        val time=System.currentTimeMillis().toString();val nonce=UUID.randomUUID().toString();val requestId=UUID.randomUUID().toString()
        val device=if(pairing) "pairing" else settings.get("device")
        require(device.isNotEmpty()) { "设备尚未绑定" }
        if(!pairing && !noSession && session.isEmpty()) { session=settings.get("session");expiresAt=settings.get("session_expires").toLongOrNull() ?: 0L }
        if(!pairing && !noSession) require(session.isNotEmpty() && (expiresAt==0L || System.currentTimeMillis()<expiresAt)) { "设备连接已撤销，请重新绑定" }
        val headers=mutableMapOf("X-Device-Id" to device,"X-Timestamp" to time,"X-Nonce" to nonce,"X-Request-Id" to requestId,
            "X-Signature" to identity.sign(RequestProtocol.canonical(method,path,bytes,time,nonce,requestId)))
        if(!pairing && !noSession) headers["Authorization"]="Bearer $session"
        val response=try {
            if(body==null) api().get(path,headers) else api().post(path,headers,bytes.toRequestBody("application/json".toMediaType()))
        } catch (error: retrofit2.HttpException) {
            val detail=error.response()?.errorBody()?.string()?.let { runCatching { JSONObject(it).optString("error") }.getOrNull() }.orEmpty()
            throw IllegalStateException(if(detail.isNotEmpty()) detail else "HTTP ${error.code()}")
        }
        response.use { JSONObject(it.string()) }
    }
    suspend fun pair(code: String) {
        val result=request("/api/v1/app/devices/pair",JSONObject().put("device_passphrase",code).put("public_key_pem",identity.publicPem()).put("device_name",android.os.Build.MODEL.take(80)),pairing=true)
        settings.put("device",result.getString("device_id"));acceptSession(result)
    }
    private suspend fun acceptSession(result: JSONObject) {
        session=result.getString("session_token");expiresAt=result.getLong("expires_at")
        settings.put("session",session);settings.put("session_expires",expiresAt.toString())
    }
    suspend fun bound()=settings.get("device").isNotEmpty()
    suspend fun ensureSession(): Boolean {
        if(!bound()) return false
        if(settings.get("base").isEmpty()) configure("https://43.163.192.120/")
        if(session.isEmpty()) { session=settings.get("session");expiresAt=settings.get("session_expires").toLongOrNull() ?: 0L }
        return try { reconnect();true } catch (_: Exception) { session.isNotEmpty() && (expiresAt==0L || System.currentTimeMillis()<expiresAt) }
    }
    suspend fun reconnect() {
        val device=settings.get("device");require(device.isNotEmpty())
        val body=JSONObject().put("device_id",device)
        val response=api().post("/api/v1/app/session/challenge",emptyMap(),body.toString().toRequestBody("application/json".toMediaType()))
        val challenge=response.use { JSONObject(it.string()).getString("challenge") }
        acceptSession(request("/api/v1/app/session/open",JSONObject().put("device_id",device).put("challenge",challenge),noSession=true))
    }
    suspend fun renew() { acceptSession(request("/api/v1/app/session/renew",JSONObject())) }
    suspend fun get(path: String)=call("/api/v1/app/$path")
    suspend fun post(path: String,body: JSONObject=JSONObject())=call("/api/v1/app/$path",body)
    private suspend fun call(path: String,body: JSONObject?=null): JSONObject {
        try { return request(path,body) }
        catch (_: java.io.IOException) {
            try { return request(path,body) }
            catch (_: java.io.IOException) { throw IllegalStateException("连接中断，请再拉一次刷新") }
        }
    }
    suspend fun revokeSelf() { post("devices/revoke",JSONObject().put("device_id",settings.get("device")));session="";expiresAt=0;settings.put("device","");settings.put("session","");settings.put("session_expires","") }
    suspend fun registerToken(token: String) {
        if (token.isBlank()) return
        settings.put("pending_fcm_token", token)
        post("notifications/register", JSONObject().put("token", token))
        settings.put("registered_fcm_token", token)
    }
    suspend fun eventsLoop() {
        while (currentCoroutineContext().isActive) {
            if (session.isEmpty() && !ensureSession()) { delay(2_000); continue }
            val closed = CompletableDeferred<Unit>()
            val socket = openEvents(closed)
            try { closed.await() } catch (_: Exception) {}
            socket.cancel()
            delay(1_000)
        }
    }
    private suspend fun openEvents(closed: CompletableDeferred<Unit>): WebSocket {
        val path = "/api/v1/app/events"
        val bytes = ByteArray(0)
        val time = System.currentTimeMillis().toString()
        val nonce = UUID.randomUUID().toString()
        val requestId = UUID.randomUUID().toString()
        val device = settings.get("device")
        val request = Request.Builder().url(settings.get("base").trimEnd('/') + path)
            .header("X-Device-Id", device)
            .header("X-Timestamp", time)
            .header("X-Nonce", nonce)
            .header("X-Request-Id", requestId)
            .header("X-Signature", identity.sign(RequestProtocol.canonical("GET", path, bytes, time, nonce, requestId)))
            .header("Authorization", "Bearer $session")
            .build()
        return http.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                runCatching {
                    val frame = JSONObject(text)
                    lastFrameAt.set(System.currentTimeMillis())
                    frames.tryEmit(frame)
                    frame.optJSONObject("alerts")?.let { alertsObj ->
                        val items = alertsObj.optJSONArray("items")
                        if (items != null && items.length() > 0) {
                            alertScope.launch { runCatching { ingestAlerts(alertsObj) } }
                        }
                    }
                }
            }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { webSocket.close(code, null); if (!closed.isCompleted) closed.complete(Unit) }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) { if (!closed.isCompleted) closed.complete(Unit) }
        })
    }
    suspend fun syncAlerts() {
        val value = get("alerts?limit=100")
        ingestAlerts(value)
    }
    suspend fun ingestAlerts(value: JSONObject) = alertMutex.withLock {
        val items = value.optJSONArray("items") ?: return@withLock
        if (items.length() == 0) return@withLock
        val now = System.currentTimeMillis()
        val existingCount = runCatching { database.alerts().count() }.getOrDefault(0)
        val primed = settings.get("alert_notify_primed") == "1" || existingCount > 0
        for (i in 0 until items.length()) {
            val row = items.optJSONObject(i) ?: continue
            val eventId = row.optString("event_id").trim()
            if (eventId.isEmpty()) continue
            val record = buildAlertRecord(row, now)
            val inserted = database.alerts().insert(record)
            if (inserted != -1L) {
                val kind = row.optString("kind").trim().uppercase()
                val severity = row.optString("severity", "HIGH").trim().uppercase()
                val silentSecurity = kind == "SECURITY_ALERT" || severity == "SILENT" || eventId.startsWith("sec:")
                val createdAt = row.optLong("created_at", now).let { if (it > 0L) it else now }
                val ageMs = now - createdAt
                // Ring alarm only for non-security alerts when already primed (<=15 min) or fresh (<=2 min).
                val shouldNotify = !silentSecurity && ((primed && ageMs <= 900_000L) || ageMs <= 120_000L)
                if (shouldNotify) {
                    val urgent = severity != "NORMAL"
                    AlertNotifier.notify(appContext, record, urgent = urgent)
                }
            }
        }
        if (!primed) {
            settings.put("alert_notify_primed", "1")
        }
        database.alerts().prune()
    }
    private fun buildAlertRecord(row: JSONObject, receivedAt: Long): AlertRecord {
        val eventId = row.optString("event_id")
        val kind = row.optString("kind")
        val symbol = row.optString("symbol").ifBlank { "SOLUSDT" }
        val details = row.optJSONObject("details")
        val strat = details?.optString("strategy").orEmpty()
        val status = details?.optString("status").orEmpty()
        val prefix = if (strat.isNotBlank()) "$symbol $strat" else symbol

        val rawTitle = row.optString("title").trim()
        val title = when {
            rawTitle.isNotBlank() && strat.isNotBlank() && !rawTitle.contains(strat) -> "$prefix · $rawTitle"
            rawTitle.isNotBlank() -> rawTitle
            kind == "STRATEGY_SIGNAL" -> "$prefix 买入信号"
            else -> "$prefix · 系统提醒"
        }

        val rawMessage = row.optString("message").trim()
        val message = if (rawMessage.isNotBlank()) {
            rawMessage
        } else {
            when (kind) {
                "STRATEGY_SIGNAL" -> "$prefix 策略已触发买入信号，请留意执行状态"
                "BUY_FILL" -> "$prefix 实盘买单已成交，已挂原生止损保护"
                "LIVE_ORDER" -> {
                    val stText = when (status) {
                        "NEW" -> "已提交买入委托"
                        "PARTIALLY_FILLED" -> "买单部分成交"
                        "CANCELED" -> "委托已撤单"
                        "EXPIRED" -> "委托已过期未成交"
                        else -> "订单状态更新"
                    }
                    "$prefix 实盘$stText"
                }
                "APTS_EXIT" -> "$prefix 持仓已触发 APTS 止盈/止损平仓"
                "STOP_LOSS" -> "$prefix 止损保护单已挂载/更新"
                "SIMULATED_TRADE" -> "$prefix 影子沙盒模拟单已成交"
                "HARD_LOCK" -> "$symbol 触发 5% 日内风控硬锁，已暂停新开仓"
                "RECONCILIATION_FAILED" -> "$symbol 账户对账异常，请检查实盘持仓与挂单"
                else -> "$prefix 状态已更新，请查看详情"
            }
        }

        val rawRoute = row.optString("route").trim()
        val route = when {
            rawRoute in setOf("markets", "strategy", "live", "shadow") -> rawRoute
            kind == "STRATEGY_SIGNAL" -> "strategy"
            kind == "SIMULATED_TRADE" -> "shadow"
            else -> "live"
        }
        return AlertRecord(eventId, title, message, route, receivedAt)
    }
}
