package com.lu.cryptobot
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONObject
import javax.inject.Inject

data class ScreenState(val busy: Boolean=false,val live: String="UNKNOWN",val error: String?=null,val values: Map<String,JSONObject> = emptyMap())
@HiltViewModel class BotViewModel @Inject constructor(val repository: BotRepository): ViewModel() {
    private val mutable=MutableStateFlow(ScreenState());val state=mutable.asStateFlow()
    val alerts=repository.database.alerts().watch()
    fun refresh(interval: String="1m") = run {
        execute {
            val map=linkedMapOf<String,JSONObject>()
            for(path in listOf("status","markets?symbol=SOLUSDT&timeframe=$interval","strategies?symbol=SOLUSDT","live/status","live/orders","live/positions","live/risk","shadow/status","shadow/orders")) map[path.substringBefore('?')]=repository.get(path)
            mutable.value=ScreenState(values=map,live=RequestProtocol.authoritativeState(map["live/status"]?.optString("state"),true))
            repository.syncAlerts()
        }
    }
    private fun execute(block: suspend ()->Unit) { viewModelScope.launch {
        mutable.value=mutable.value.copy(busy=true,error=null)
        try { block() } catch (error: Exception) { mutable.value=mutable.value.copy(live="UNKNOWN",error=(error.message ?: error.javaClass.simpleName).take(180)) }
        finally { mutable.value=mutable.value.copy(busy=false) }
    } }
    fun connect(base: String,code: String,interval: String) { execute {
        repository.configure(base); if(code.isNotBlank()) repository.pair(code) else repository.reconnect()
        val value=repository.get("live/status");mutable.value=mutable.value.copy(live=RequestProtocol.authoritativeState(value.optString("state"),true),values=mutable.value.values+("live/status" to value))
        com.google.firebase.messaging.FirebaseMessaging.getInstance().token.addOnSuccessListener { token->viewModelScope.launch { runCatching { repository.registerToken(token) } } }
        refresh(interval)
    } }
    fun live(on: Boolean) { execute {
        val reply=repository.post(if(on) "live/request-enable" else "live/disable",if(on) JSONObject().put("risk_confirmed",true) else JSONObject())
        val snapshot=reply.optJSONObject("snapshot") ?: reply
        mutable.value=mutable.value.copy(live=RequestProtocol.authoritativeState(snapshot.optString("state"),true),error=if(!reply.optBoolean("accepted",false)) reply.optString("code",reply.optString("reason")) else null)
        val fresh=repository.get("live/status");mutable.value=mutable.value.copy(live=RequestProtocol.authoritativeState(fresh.optString("state"),true),values=mutable.value.values+("live/status" to fresh))
    } }
    fun custody(quantity: String,grant: Boolean) { execute { repository.post(if(grant) "custody/grant" else "custody/revoke",if(grant) JSONObject().put("asset","SUI").put("quantity",quantity).put("expires_at",System.currentTimeMillis()+86400000).put("risk_confirmed",true) else JSONObject().put("authorization_id",quantity)) } }
    fun revoke() { execute { repository.revokeSelf();mutable.value=ScreenState() } }
}
