package com.lu.cryptobot
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.*
import javax.inject.Inject

@AndroidEntryPoint class BotMessagingService: FirebaseMessagingService() {
    @Inject lateinit var repository: BotRepository
    private val scope=CoroutineScope(SupervisorJob()+Dispatchers.IO)
    override fun onNewToken(token: String) { scope.launch { repository.settings.put("pending_fcm_token",token);runCatching { repository.registerToken(token) } } }
    override fun onMessageReceived(message: RemoteMessage) {
        val data=message.data;val id=data["event_id"] ?: return
        val title=data["title"]?.take(80) ?: "SOL-bot事件";val body=data["message"]?.take(300) ?: "请打开App同步服务器事件"
        val route=data["route"]?.takeIf { it in setOf("markets","strategy","live","shadow") } ?: "live"
        scope.launch {
            val inserted=repository.database.alerts().insert(AlertRecord(id,title,body,route,System.currentTimeMillis()))
            repository.database.alerts().prune();if(inserted==-1L)return@launch
            val urgent=data["severity"] in setOf("CRITICAL","HIGH","critical","high")
            val channel=if(urgent) "risk" else "events"
            val manager=getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(NotificationChannel(channel,if(urgent) "交易与风控紧急提醒" else "策略与模拟事件",if(urgent) NotificationManager.IMPORTANCE_HIGH else NotificationManager.IMPORTANCE_DEFAULT))
            if(Build.VERSION.SDK_INT>=33 && ContextCompat.checkSelfPermission(this@BotMessagingService,android.Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)return@launch
            val click=PendingIntent.getActivity(this@BotMessagingService,id.hashCode(),Intent(this@BotMessagingService,MainActivity::class.java).putExtra("route",route).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            manager.notify(id.hashCode(),NotificationCompat.Builder(this@BotMessagingService,channel).setSmallIcon(android.R.drawable.ic_dialog_info).setContentTitle(title).setContentText(body).setStyle(NotificationCompat.BigTextStyle().bigText(body)).setContentIntent(click).setAutoCancel(true).setVisibility(NotificationCompat.VISIBILITY_PRIVATE).build())
        }
    }
    override fun onDestroy() { scope.cancel();super.onDestroy() }
}
