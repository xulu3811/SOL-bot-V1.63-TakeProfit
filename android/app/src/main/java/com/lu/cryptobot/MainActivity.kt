package com.lu.cryptobot

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.WindowManager
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.lifecycleScope
import com.lu.cryptobot.ui.BotUi
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch

@AndroidEntryPoint
class MainActivity : FragmentActivity() {
    private val model: BotViewModel by viewModels()
    private var startRoute by mutableStateOf("markets")

    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        requestBatteryExemptionIfNeeded()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                    WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON,
            )
        }
        AlertNotifier.ensureChannels(this)
        intent?.getStringExtra("route")?.let { startRoute = it }

        if (Build.VERSION.SDK_INT >= 33) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            requestBatteryExemptionIfNeeded()
        }

        setContent { BotUi(model, startRoute) }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        intent.getStringExtra("route")?.let { startRoute = it }
    }

    override fun onResume() {
        super.onResume()
        lifecycleScope.launch {
            if (model.repository.bound()) {
                BotConnectionService.start(this@MainActivity)
            }
        }
    }

    @SuppressLint("BatteryLife")
    private fun requestBatteryExemptionIfNeeded() {
        runCatching {
            val pm = getSystemService(PowerManager::class.java) ?: return
            if (!pm.isIgnoringBatteryOptimizations(packageName)) {
                val intent = Intent(
                    Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:$packageName"),
                )
                startActivity(intent)
            }
        }
    }
}
