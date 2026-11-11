package com.lu.cryptobot
import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import dagger.hilt.android.AndroidEntryPoint

@AndroidEntryPoint class MainActivity: FragmentActivity() {
    private val model: BotViewModel by viewModels()
    private val notificationPermission=registerForActivityResult(ActivityResultContracts.RequestPermission()) { }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if(Build.VERSION.SDK_INT>=33) notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        setContent { BotUi(model,intent.getStringExtra("route") ?: "markets",::authenticate) }
    }
    private fun authenticate(action: ()->Unit) {
        val prompt=BiometricPrompt(this,ContextCompat.getMainExecutor(this),object:BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) { action() }
        })
        prompt.authenticate(BiometricPrompt.PromptInfo.Builder().setTitle("验证设备身份").setSubtitle("使用系统生物识别或锁屏凭据确认操作")
            .setDeviceCredentialAllowed(true).build())
    }
}
