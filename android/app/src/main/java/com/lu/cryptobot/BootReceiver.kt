package com.lu.cryptobot

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.SystemClock
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import javax.inject.Inject

@AndroidEntryPoint
class BootReceiver : BroadcastReceiver() {
    @Inject lateinit var repository: BotRepository

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        if (action !in ALLOWED_ACTIONS) return
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                if (repository.bound()) {
                    BotConnectionService.start(context)
                    scheduleKeepAlive(context)
                }
            } finally {
                pending.finish()
            }
        }
    }

    companion object {
        const val ACTION_KEEP_ALIVE = "com.lu.cryptobot.ACTION_KEEP_ALIVE"
        private val ALLOWED_ACTIONS = setOf(
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_LOCKED_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
            ACTION_KEEP_ALIVE,
        )

        fun scheduleKeepAlive(context: Context) {
            runCatching {
                val appCtx = context.applicationContext
                val alarm = appCtx.getSystemService(AlarmManager::class.java) ?: return
                val pi = PendingIntent.getBroadcast(
                    appCtx,
                    9001,
                    Intent(appCtx, BootReceiver::class.java).setAction(ACTION_KEEP_ALIVE),
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
                )
                val triggerAt = SystemClock.elapsedRealtime() + 5 * 60 * 1000L
                alarm.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAt, pi)
            }
        }

        fun scheduleImmediateRestart(context: Context) {
            runCatching {
                val appCtx = context.applicationContext
                val alarm = appCtx.getSystemService(AlarmManager::class.java) ?: return
                val pi = PendingIntent.getBroadcast(
                    appCtx,
                    9002,
                    Intent(appCtx, BootReceiver::class.java).setAction(ACTION_KEEP_ALIVE),
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
                )
                val triggerAt = SystemClock.elapsedRealtime() + 1_500L
                alarm.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAt, pi)
            }
        }
    }
}
