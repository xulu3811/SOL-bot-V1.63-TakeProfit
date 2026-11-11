package com.lu.cryptobot
import android.content.Context
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import androidx.room.Room
import kotlinx.coroutines.Dispatchers
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
        return OkHttpClient.Builder().sslSocketFactory(ssl.socketFactory,trust).connectTimeout(10,TimeUnit.SECONDS).readTimeout(15,TimeUnit.SECONDS).callTimeout(20,TimeUnit.SECONDS).followRedirects(false).followSslRedirects(false).build()
    }
}
@Singleton class BotRepository @Inject constructor(val settings: Settings,val identity: DeviceIdentity,val database: BotDatabase,private val http: OkHttpClient) {
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
        if(!pairing && !noSession) require(session.isNotEmpty() && (expiresAt==0L || System.currentTimeMillis()<expiresAt)) { "设备连接已撤销，请重新绑定" }
        val headers=mutableMapOf("X-Device-Id" to device,"X-Timestamp" to time,"X-Nonce" to nonce,"X-Request-Id" to requestId,
            "X-Signature" to identity.sign(RequestProtocol.canonical(method,path,bytes,time,nonce,requestId)))
        if(!pairing && !noSession) headers["Authorization"]="Bearer $session"
        val response=if(body==null) api().get(path,headers) else api().post(path,headers,bytes.toRequestBody("application/json".toMediaType()))
        response.use { JSONObject(it.string()) }
    }
    suspend fun pair(code: String) {
        val result=request("/api/v1/app/devices/pair",JSONObject().put("device_passphrase",code).put("public_key_pem",identity.publicPem()).put("device_name",android.os.Build.MODEL.take(80)),pairing=true)
        settings.put("device",result.getString("device_id"));acceptSession(result)
    }
    private fun acceptSession(result: JSONObject) { session=result.getString("session_token");expiresAt=result.getLong("expires_at") }
    suspend fun reconnect() {
        val device=settings.get("device");require(device.isNotEmpty())
        val body=JSONObject().put("device_id",device)
        val response=api().post("/api/v1/app/session/challenge",emptyMap(),body.toString().toRequestBody("application/json".toMediaType()))
        val challenge=response.use { JSONObject(it.string()).getString("challenge") }
        acceptSession(request("/api/v1/app/session/open",JSONObject().put("device_id",device).put("challenge",challenge),noSession=true))
    }
    suspend fun renew() { acceptSession(request("/api/v1/app/session/renew",JSONObject())) }
    suspend fun get(path: String)=request("/api/v1/app/$path")
    suspend fun post(path: String,body: JSONObject=JSONObject())=request("/api/v1/app/$path",body)
    suspend fun revokeSelf() { post("devices/revoke",JSONObject().put("device_id",settings.get("device")));session="";expiresAt=0;settings.put("device","") }
    suspend fun registerToken(token: String) { post("notifications/register",JSONObject().put("token",token)) }
    suspend fun syncAlerts() {
        val value=get("alerts");val items=value.optJSONArray("items") ?: return
        for(i in 0 until items.length()) { val row=items.getJSONObject(i);database.alerts().insert(AlertRecord(row.optString("event_id"),row.optString("title","服务器提醒"),row.optString("message"),row.optString("route","live"),System.currentTimeMillis())) }
        database.alerts().prune()
    }
}
