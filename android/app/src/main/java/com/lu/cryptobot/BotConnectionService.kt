package com.lu.cryptobot

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.google.firebase.messaging.FirebaseMessaging
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import javax.inject.Inject

@AndroidEntryPoint
class BotConnectionService : Service() {
    @Inject lateinit var repository: BotRepository
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var loop: Job? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        AlertNotifier.ensureChannels(this)
        acquireKeepAliveLocks()
        BootReceiver.scheduleKeepAlive(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        AlertNotifier.ensureChannels(this)
        acquireKeepAliveLocks()
        BootReceiver.scheduleKeepAlive(this)
        val notice = ongoing("实时监控运行中 · 信号与交易锁屏响铃已开启")
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTICE_ID, notice, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTICE_ID, notice)
        }
        if (loop?.isActive != true) {
            loop = scope.launch {
                launch { repository.eventsLoop() }
                watch()
            }
        }
        return START_STICKY
    }

    private suspend fun watch() {
        var cursor = repository.settings.get("signal_cursor").toLongOrNull() ?: -1L
        var primed = cursor >= 0
        var tick = 0
        while (scope.isActive) {
            if (!repository.bound()) {
                releaseKeepAliveLocks()
                stopSelf()
                return
            }
            acquireKeepAliveLocks()
            runCatching {
                repository.ensureSession()
                if (tick % 6 == 0) {
                    ensureFcmRegistered()
                }
                tick++
                // 1. Sync server alert stream (covers T1/T2/R2 signals, live orders, fills, stops, shadow trades)
                runCatching { repository.syncAlerts() }

                // 2. Direct strategy decision check as secondary fast-path
                val value = repository.get("strategies?symbol=SOLUSDT")
                val allowed = FrameSyncHelper.allowedLiveStrategies(value)
                val items = value.optJSONArray("items")
                var newest = cursor
                val fresh = mutableListOf<JSONObject>()
                if (items != null) {
                    for (i in 0 until items.length()) {
                        val row = items.optJSONObject(i) ?: continue
                        val id = row.optLong("event_id", -1L)
                        if (id > newest) newest = id
                        if (primed && id > cursor && row.optBoolean("accepted", false) && row.optString("strategy_id") in allowed) {
                            fresh += row
                        }
                    }
                }
                if (!primed) primed = true
                if (newest >= 0) {
                    cursor = newest
                    repository.settings.put("signal_cursor", cursor.toString())
                }
                fresh.forEach { notifyBuy(it) }
            }
            delay(10_000L)
        }
    }

    private suspend fun ensureFcmRegistered() {
        val pending = repository.settings.get("pending_fcm_token")
        val registered = repository.settings.get("registered_fcm_token")
        if (pending.isNotBlank() && pending != registered) {
            runCatching { repository.registerToken(pending) }
        } else if (registered.isBlank()) {
            runCatching {
                FirebaseMessaging.getInstance().token.addOnSuccessListener { token ->
                    if (!token.isNullOrBlank()) {
                        scope.launch { runCatching { repository.registerToken(token) } }
                    }
                }
            }
        }
    }

    private suspend fun notifyBuy(row: JSONObject) {
        val symbol = row.optString("symbol", "SOLUSDT")
        val strategy = row.optString("strategy_id", "T1")
        val sigId = row.optString("signal_id", row.optString("event_id"))
        val eventId = "signal:$sigId"
        val title = "$symbol $strategy 买入信号"
        val body = "$symbol $strategy 策略买入信号已触发，请留意执行状态"
        val record = AlertRecord(eventId, title, body, "strategy", System.currentTimeMillis())
        val inserted = repository.database.alerts().insert(record)
        if (inserted != -1L) {
            AlertNotifier.notify(this, record, urgent = true)
        }
    }

    private fun ongoing(text: String): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(CHANNEL, "后台常驻监控连接", NotificationManager.IMPORTANCE_LOW).apply {
            description = "保持 SOL-bot 后台存活以实时接收信号和交易预警"
            setShowBadge(false)
        }
        manager.createNotificationChannel(channel)
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("SOL-bot 实时预警守护中")
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setSilent(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    @Suppress("DEPRECATION")
    private fun acquireKeepAliveLocks() {
        runCatching {
            val pm = getSystemService(PowerManager::class.java)
            if (pm != null) {
                val wl = wakeLock ?: pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "SOLBot:BackgroundWatchLock").apply {
                    setReferenceCounted(false)
                    wakeLock = this
                }
                if (!wl.isHeld) {
                    wl.acquire(15 * 60 * 1000L)
                }
            }
            val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            if (wm != null) {
                val mode = if (Build.VERSION.SDK_INT >= 29) WifiManager.WIFI_MODE_FULL_LOW_LATENCY else WifiManager.WIFI_MODE_FULL_HIGH_PERF
                val wfl = wifiLock ?: wm.createWifiLock(mode, "SOLBot:WifiWatchLock").apply {
                    setReferenceCounted(false)
                    wifiLock = this
                }
                if (!wfl.isHeld) {
                    wfl.acquire()
                }
            }
        }
    }

    private fun releaseKeepAliveLocks() {
        runCatching {
            wakeLock?.let { if (it.isHeld) it.release() }
            wifiLock?.let { if (it.isHeld) it.release() }
        }
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        BootReceiver.scheduleImmediateRestart(this)
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        loop?.cancel()
        scope.cancel()
        releaseKeepAliveLocks()
        BootReceiver.scheduleImmediateRestart(this)
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL = "connection"
        private const val NOTICE_ID = 42
        fun start(context: Context) {
            val appCtx = context.applicationContext
            val intent = Intent(appCtx, BotConnectionService::class.java)
            runCatching {
                if (Build.VERSION.SDK_INT >= 26) ContextCompat.startForegroundService(appCtx, intent)
                else appCtx.startService(intent)
            }
        }
        fun stop(context: Context) {
            context.applicationContext.stopService(Intent(context.applicationContext, BotConnectionService::class.java))
        }
    }
}
