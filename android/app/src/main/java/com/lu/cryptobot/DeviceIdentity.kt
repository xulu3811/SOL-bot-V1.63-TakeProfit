package com.lu.cryptobot

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import javax.inject.Inject
import javax.inject.Singleton

object RequestProtocol {
    fun hash(body: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(body).joinToString("") { "%02x".format(it) }
    fun canonical(method: String, path: String, body: ByteArray, timestamp: String, nonce: String, requestId: String) = listOf(method.uppercase(),path,hash(body),timestamp,nonce,requestId).joinToString("\n")
    fun authoritativeState(server: String?, reachable: Boolean): String = if (!reachable || server !in setOf("LIVE_OFF","LIVE_PENDING","LIVE_ON","PAUSED","HARD_LOCKED")) "UNKNOWN" else server!!
}

@Singleton class DeviceIdentity @Inject constructor() {
    private val alias = "solbot-device-v2"
    private fun store() = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    fun ensure() {
        if (store().containsAlias(alias)) return
        val generator=KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC,"AndroidKeyStore")
        generator.initialize(KeyGenParameterSpec.Builder(alias,KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY)
            .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1")).setDigests(KeyProperties.DIGEST_SHA256)
            .setUserAuthenticationRequired(false).build())
        generator.generateKeyPair()
    }
    fun publicPem(): String { ensure(); return "-----BEGIN PUBLIC KEY-----\n"+Base64.encodeToString(store().getCertificate(alias).publicKey.encoded,Base64.NO_WRAP)+"\n-----END PUBLIC KEY-----" }
    fun sign(value: String): String {
        ensure(); val signature=Signature.getInstance("SHA256withECDSA")
        signature.initSign(store().getKey(alias,null) as java.security.PrivateKey); signature.update(value.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(signature.sign(),Base64.NO_WRAP)
    }
}
