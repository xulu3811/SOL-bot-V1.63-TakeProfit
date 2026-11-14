package com.lu.cryptobot

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.Ringtone
import android.media.RingtoneManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.VibrationAttributes
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.asSharedFlow

object AlertNotifier {
    const val CRITICAL_CHANNEL_ID = "solbot_critical_alarm_v2"
    const val UPDATES_CHANNEL_ID = "updates"

    private val _popupEvents = MutableSharedFlow<AlertRecord>(extraBufferCapacity = 16)
    val popupEvents = _popupEvents.asSharedFlow()

    @Volatile
    private var activeRingtone: Ringtone? = null
    private val mainHandler = Handler(Looper.getMainLooper())

    fun ensureChannels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        val soundUri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
            ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
        val audioAttrs = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ALARM)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build()

        val criticalChannel = NotificationChannel(
            CRITICAL_CHANNEL_ID,
            "交易与信号强预警（锁屏弹窗+响铃）",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = "每次策略信号触发、实盘成交、止盈止损时强制锁屏弹窗与响铃"
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            enableVibration(true)
            vibrationPattern = longArrayOf(0, 500, 200, 500, 200, 800)
            enableLights(true)
            runCatching { setBypassDnd(true) }
            if (soundUri != null) {
                setSound(soundUri, audioAttrs)
            }
        }
        manager.createNotificationChannel(criticalChannel)

        val updatesChannel = NotificationChannel(
            UPDATES_CHANNEL_ID,
            "一般状态同步",
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply {
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
        }
        manager.createNotificationChannel(updatesChannel)
    }

    fun notify(context: Context, record: AlertRecord, urgent: Boolean = true) {
        val appContext = context.applicationContext
        _popupEvents.tryEmit(record)
        ensureChannels(appContext)
        if (urgent) {
            wakeScreen(appContext)
            playAlarmAndVibrate(appContext)
        }
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(appContext, android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        val manager = appContext.getSystemService(NotificationManager::class.java) ?: return
        val title = record.title.ifBlank { "SOL-bot 交易信号预警" }
        val body = record.message.ifBlank { "收到最新策略信号或交易状态更新，请立即查看" }
        val route = record.route.ifBlank { "live" }
        val notifyId = record.eventId.hashCode()

        val launchIntent = Intent(appContext, MainActivity::class.java).apply {
            putExtra("route", route)
            putExtra("alert_event_id", record.eventId)
            putExtra("alert_title", title)
            putExtra("alert_body", body)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        }
        val clickPending = PendingIntent.getActivity(
            appContext,
            notifyId,
            launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val fullScreenPending = PendingIntent.getActivity(
            appContext,
            notifyId + 1,
            launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val channelId = if (urgent) CRITICAL_CHANNEL_ID else UPDATES_CHANNEL_ID
        val builder = NotificationCompat.Builder(appContext, channelId)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body).setBigContentTitle(title))
            .setPriority(if (urgent) NotificationCompat.PRIORITY_MAX else NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setDefaults(NotificationCompat.DEFAULT_ALL)
            .setVibrate(longArrayOf(0, 500, 200, 500, 200, 800))
            .setContentIntent(clickPending)
            .setAutoCancel(true)

        if (urgent) {
            builder.setFullScreenIntent(fullScreenPending, true)
        }

        runCatching { manager.notify(notifyId, builder.build()) }
    }

    fun triggerTestAlert(context: Context): AlertRecord {
        val now = System.currentTimeMillis()
        val testRecord = AlertRecord(
            eventId = "test-alarm-$now",
            title = "SOLUSDT · 强预警测试",
            message = "锁屏亮屏弹窗、闹钟通道铃声与震动测试已触发！后台监控常驻正常。",
            route = "live",
            receivedAt = now,
        )
        notify(context, testRecord, urgent = true)
        return testRecord
    }

    @Suppress("DEPRECATION")
    private fun wakeScreen(context: Context) {
        runCatching {
            val pm = context.getSystemService(PowerManager::class.java) ?: return
            if (!pm.isInteractive) {
                val wl = pm.newWakeLock(
                    PowerManager.SCREEN_BRIGHT_WAKE_LOCK or
                        PowerManager.ACQUIRE_CAUSES_WAKEUP or
                        PowerManager.ON_AFTER_RELEASE,
                    "SOLBot:AlertScreenWakeLock",
                )
                wl.acquire(10_000L)
            }
        }
    }

    @Suppress("DEPRECATION")
    private fun playAlarmAndVibrate(context: Context) {
        // 1. Trigger strong vibration waveform
        runCatching {
            val pattern = longArrayOf(0, 500, 200, 500, 200, 800)
            val vibrator: Vibrator? = if (Build.VERSION.SDK_INT >= 31) {
                context.getSystemService(VibratorManager::class.java)?.defaultVibrator
            } else {
                context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
            }
            if (vibrator?.hasVibrator() == true) {
                val effect = VibrationEffect.createWaveform(pattern, -1)
                if (Build.VERSION.SDK_INT >= 33) {
                    val attrs = VibrationAttributes.createForUsage(VibrationAttributes.USAGE_ALARM)
                    vibrator.vibrate(effect, attrs)
                } else {
                    val audioAttrs = AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ALARM)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build()
                    vibrator.vibrate(effect, audioAttrs)
                }
            }
        }

        // 2. Explicitly play audible ringtone on USAGE_ALARM channel for 3.5 seconds
        mainHandler.post {
            runCatching {
                activeRingtone?.let { prev ->
                    if (prev.isPlaying) prev.stop()
                }
                val uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
                    ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
                    ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
                if (uri != null) {
                    val ringtone = RingtoneManager.getRingtone(context, uri)
                    if (ringtone != null) {
                        if (Build.VERSION.SDK_INT >= 28) {
                            ringtone.audioAttributes = AudioAttributes.Builder()
                                .setUsage(AudioAttributes.USAGE_ALARM)
                                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                                .build()
                            ringtone.isLooping = false
                        }
                        activeRingtone = ringtone
                        ringtone.play()
                        mainHandler.postDelayed({
                            runCatching {
                                if (ringtone.isPlaying) ringtone.stop()
                            }
                        }, 3_500L)
                    }
                }
            }
        }
    }
}
