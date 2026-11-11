package solbot.control

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import kotlinx.coroutines.launch

/** Transport-independent server protocol. P2 provides no public control endpoint.
 * Future implementations must authenticate requests and verify the server.
 * The UI never receives an exchange secret or manufactures authorization.
 */
enum class LiveState { LIVE_OFF, LIVE_PENDING, LIVE_ON, PAUSED, HARD_LOCKED }

data class LiveServerSnapshot(
    val accountId: String,
    val symbol: String,
    val state: LiveState,
    val revision: Long,
    val liveReady: Boolean,
    val reason: String,
)

data class LiveControlReply(val accepted: Boolean, val code: String, val snapshot: LiveServerSnapshot)

interface LiveControlRepository {
    suspend fun fetchState(): LiveServerSnapshot
    suspend fun requestOn(): LiveControlReply
    suspend fun requestOff(): LiveControlReply
}

/** checked is derived exclusively from a confirmed server snapshot. */
@Composable
fun LiveControl(repository: LiveControlRepository, modifier: Modifier = Modifier) {
    var snapshot by remember(repository) { mutableStateOf<LiveServerSnapshot?>(null) }
    var busy by remember(repository) { mutableStateOf(true) }
    var error by remember(repository) { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(repository) {
        try {
            snapshot = repository.fetchState()
            error = null
        } catch (_: Exception) {
            snapshot = null
            error = "Server state unavailable; LIVE authorization is unknown."
        } finally {
            busy = false
        }
    }

    val current = snapshot
    Column(modifier = modifier, verticalArrangement = Arrangement.SpaceBetween) {
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("LIVE · ${current?.symbol ?: "Awaiting server"}", style = MaterialTheme.typography.titleMedium)
            Switch(
                checked = current?.state == LiveState.LIVE_ON,
                enabled = !busy && current != null && current.state != LiveState.HARD_LOCKED,
                onCheckedChange = { wantsOn ->
                    // No optimistic assignment to LIVE_ON, including while awaiting an ON reply.
                    busy = true
                    error = null
                    scope.launch {
                        try {
                            val reply = if (wantsOn) repository.requestOn() else repository.requestOff()
                            val previous = snapshot
                            if (previous == null || reply.snapshot.revision >= previous.revision) {
                                snapshot = reply.snapshot
                            }
                            if (!reply.accepted) error = reply.code
                        } catch (_: Exception) {
                            // A lost reply cannot prove either ON or OFF. Re-read authority.
                            snapshot = null
                            error = "Request outcome unknown; refresh server state before continuing."
                            try { snapshot = repository.fetchState() } catch (_: Exception) { }
                        } finally {
                            busy = false
                        }
                    }
                },
            )
        }
        Text(when {
            busy -> "Waiting for server confirmation"
            current == null -> "State unavailable"
            current.state == LiveState.HARD_LOCKED -> "Hard lock retained; this switch cannot clear it."
            !current.liveReady -> "Live trading is not ready: Guardian and protective orders require acceptance."
            else -> current.state.name
        })
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    }
}
