package com.lu.cryptobot

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import javax.inject.Inject

@AndroidEntryPoint
class BotMessagingService : FirebaseMessagingService() {
    @Inject lateinit var repository: BotRepository
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onNewToken(token: String) {
        scope.launch {
            repository.settings.put("pending_fcm_token", token)
            runCatching { repository.registerToken(token) }
        }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val data = message.data
        val id = data["event_id"] ?: "fcm-${System.currentTimeMillis()}"
        val kind = data["kind"]?.trim()?.uppercase().orEmpty()
        val severity = data["severity"]?.trim()?.uppercase().orEmpty()
        val silentSecurity = kind == "SECURITY_ALERT" || severity == "SILENT" || id.startsWith("sec:")
        val title = (data["title"] ?: message.notification?.title ?: "SOL-bot 交易信号预警").take(80)
        val body = (data["message"] ?: message.notification?.body ?: "收到最新交易或策略信号，请立即查看").take(300)
        val route = data["route"]?.takeIf { it in setOf("markets", "strategy", "live", "shadow") } ?: "live"
        val urgent = !silentSecurity && severity != "NORMAL"
        val receivedAt = data["created_at"]?.toLongOrNull()?.takeIf { it > 0L } ?: System.currentTimeMillis()
        scope.launch {
            val record = AlertRecord(id, title, body, route, receivedAt)
            val inserted = repository.database.alerts().insert(record)
            repository.database.alerts().prune()
            if (inserted == -1L || silentSecurity) return@launch
            AlertNotifier.notify(this@BotMessagingService, record, urgent = urgent)
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}
