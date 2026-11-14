package com.lu.cryptobot.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.TrendingUp
import androidx.compose.material.icons.outlined.CandlestickChart
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Link
import androidx.compose.material.icons.outlined.NotificationsActive
import androidx.compose.material.icons.outlined.Science
import androidx.compose.material.icons.outlined.Shield
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.lu.cryptobot.AlertNotifier
import com.lu.cryptobot.AlertRecord
import com.lu.cryptobot.BotViewModel
import com.lu.cryptobot.ui.components.TopStatusBar
import com.lu.cryptobot.ui.screens.LiveScreen
import com.lu.cryptobot.ui.screens.MarketsScreen
import com.lu.cryptobot.ui.screens.ShadowScreen
import com.lu.cryptobot.ui.screens.StrategyScreen
import com.lu.cryptobot.ui.sheets.AlertsInboxSheet
import com.lu.cryptobot.ui.sheets.BinanceApiSheet
import com.lu.cryptobot.ui.sheets.ConnectionSheet
import com.lu.cryptobot.ui.sheets.CustodyConfirmSheet
import com.lu.cryptobot.ui.sheets.LiveToggleConfirmSheet
import com.lu.cryptobot.ui.sheets.OrderDetailSheet
import com.lu.cryptobot.ui.theme.CryptoBotTheme
import com.lu.cryptobot.ui.theme.LocalBotColors
import org.json.JSONObject

private data class NavDestination(val route: String, val label: String, val title: String, val icon: ImageVector)

private val DESTINATIONS = listOf(
    NavDestination("markets", "行情", "SOL / USDT", Icons.Outlined.CandlestickChart),
    NavDestination("strategy", "策略", "策略引擎", Icons.AutoMirrored.Outlined.TrendingUp),
    NavDestination("live", "实盘", "实盘资产", Icons.Outlined.Shield),
    NavDestination("shadow", "Shadow", "影子沙盒", Icons.Outlined.Science),
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BotUi(vm: BotViewModel, start: String = "markets") {
    val state by vm.state.collectAsState()
    val alerts by vm.alerts.collectAsState(initial = emptyList())

    var route by remember(start) {
        mutableStateOf(if (DESTINATIONS.any { it.route == start }) start else "markets")
    }
    var showConnect by remember { mutableStateOf(false) }
    var showAlerts by remember { mutableStateOf(false) }
    var showApiConfig by remember { mutableStateOf(false) }
    var pendingLiveToggle by remember { mutableStateOf<Boolean?>(null) }
    var pendingCustody by remember { mutableStateOf<Pair<JSONObject, Boolean>?>(null) }
    var inspectingOrder by remember { mutableStateOf<JSONObject?>(null) }
    var popupAlert by remember { mutableStateOf<AlertRecord?>(null) }

    LaunchedEffect(Unit) {
        AlertNotifier.popupEvents.collect { incoming ->
            popupAlert = incoming
        }
    }

    val deviceId = state.deviceId
    val baseUrl = state.baseUrl
    val unreadCount = remember(alerts, state.alertsSeenAt) {
        alerts.count { it.receivedAt > state.alertsSeenAt }
    }

    val micro = state.values["markets"]?.optJSONObject("micro")
    val microReady = micro?.optBoolean("ready", false) == true && micro.optBoolean("stale", false).not()
    val microAgeMs = if (micro != null && micro.has("age_ms")) micro.optLong("age_ms", -1L).takeIf { it >= 0L } else null

    val currentDest = DESTINATIONS.firstOrNull { it.route == route } ?: DESTINATIONS.first()

    CryptoBotTheme {
        val bot = LocalBotColors.current
        Scaffold(
            containerColor = MaterialTheme.colorScheme.background,
            topBar = {
                Column {
                    TopStatusBar(
                        routeTitle = currentDest.title,
                        liveState = state.live,
                        microReady = microReady,
                        unreadAlerts = unreadCount,
                        onRefresh = { vm.refresh(state.interval) },
                        onOpenAlerts = {
                            val newest = alerts.maxOfOrNull { it.receivedAt } ?: System.currentTimeMillis()
                            vm.markAlertsRead(newest)
                            showAlerts = true
                        },
                        onOpenConnect = { showConnect = true },
                        onLiveBadgeClick = { route = "live" },
                    )
                    if (state.busy) {
                        LinearProgressIndicator(
                            modifier = Modifier.fillMaxWidth(),
                            color = MaterialTheme.colorScheme.primary,
                            trackColor = MaterialTheme.colorScheme.primaryContainer,
                        )
                    }
                }
            },
            bottomBar = {
                Column {
                    HorizontalDivider(thickness = 1.dp, color = bot.hairline)
                    NavigationBar(
                        containerColor = MaterialTheme.colorScheme.surface,
                        tonalElevation = 0.dp,
                    ) {
                        DESTINATIONS.forEach { dest ->
                            NavigationBarItem(
                                selected = route == dest.route,
                                onClick = { route = dest.route },
                                icon = { Icon(dest.icon, contentDescription = dest.label) },
                                label = {
                                    Text(
                                        text = dest.label,
                                        style = MaterialTheme.typography.labelMedium,
                                        fontWeight = if (route == dest.route) FontWeight.Bold else FontWeight.Medium,
                                        maxLines = 1,
                                    )
                                },
                                colors = NavigationBarItemDefaults.colors(
                                    indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                                    selectedIconColor = MaterialTheme.colorScheme.onPrimaryContainer,
                                    selectedTextColor = MaterialTheme.colorScheme.primary,
                                    unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                                    unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                                ),
                            )
                        }
                    }
                }
            },
        ) { innerPadding ->
            PullToRefreshBox(
                isRefreshing = state.busy,
                onRefresh = { vm.refresh(state.interval) },
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding),
            ) {
                Column(modifier = Modifier.fillMaxSize()) {
                    if (!state.busy && deviceId.isBlank() && state.values.isEmpty()) {
                        Surface(
                            shape = RoundedCornerShape(14.dp),
                            color = MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.7f),
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(horizontal = 16.dp, vertical = 8.dp),
                        ) {
                            Row(
                                modifier = Modifier.padding(12.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.SpaceBetween,
                            ) {
                                Row(
                                    modifier = Modifier.weight(1f),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                                ) {
                                    Icon(Icons.Outlined.Link, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(18.dp))
                                    Text(
                                        text = "尚未连接交易服务器节点，请先绑定设备口令。",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onPrimaryContainer,
                                    )
                                }
                                Button(
                                    onClick = { showConnect = true },
                                    shape = RoundedCornerShape(10.dp),
                                ) {
                                    Text("去配对")
                                }
                            }
                        }
                    }

                    state.error?.let { err ->
                        Surface(
                            shape = RoundedCornerShape(12.dp),
                            color = bot.lossSoft,
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(horizontal = 16.dp, vertical = 6.dp),
                        ) {
                            Row(
                                modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                Icon(Icons.Outlined.ErrorOutline, null, tint = bot.loss, modifier = Modifier.size(16.dp))
                                Text(
                                    text = err,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = bot.loss,
                                    maxLines = 2,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                        }
                    }

                    Box(modifier = Modifier.weight(1f)) {
                        when (route) {
                            "markets" -> MarketsScreen(
                                values = state.values,
                                selectedTimeframe = state.interval,
                                onSelectTimeframe = { tf -> vm.refreshCandles(tf) },
                                modifier = Modifier.fillMaxSize(),
                            )
                            "strategy" -> StrategyScreen(
                                values = state.values,
                                modifier = Modifier.fillMaxSize(),
                            )
                            "live" -> LiveScreen(
                                liveState = state.live,
                                values = state.values,
                                onRequestLiveToggle = { targetOn -> pendingLiveToggle = targetOn },
                                onOpenApiConfig = { showApiConfig = true },
                                onSyncBinance = { vm.syncBinance() },
                                onRequestCustody = { pos, enabling -> pendingCustody = pos to enabling },
                                onInspectOrder = { ord -> inspectingOrder = ord },
                                modifier = Modifier.fillMaxSize(),
                            )
                            else -> ShadowScreen(
                                values = state.values,
                                onInspectOrder = { ord -> inspectingOrder = ord },
                                modifier = Modifier.fillMaxSize(),
                            )
                        }
                    }
                }
            }
        }

        if (showConnect) {
            ConnectionSheet(
                initialUrl = baseUrl,
                deviceId = deviceId,
                liveState = state.live,
                interval = state.interval,
                microReady = microReady,
                microAgeMs = microAgeMs,
                onConnect = { url, code -> vm.connect(url, code, state.interval) },
                onRevoke = { vm.revoke() },
                onDismiss = { showConnect = false },
            )
        }

        if (showAlerts) {
            AlertsInboxSheet(
                alerts = alerts,
                onNavigateRoute = { target ->
                    if (DESTINATIONS.any { it.route == target }) route = target
                },
                onTestAlert = { vm.triggerTestAlert() },
                onDismiss = { showAlerts = false },
            )
        }

        popupAlert?.let { alert ->
            AlertDialog(
                onDismissRequest = { popupAlert = null },
                icon = {
                    Icon(
                        Icons.Outlined.NotificationsActive,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.primary,
                    )
                },
                title = {
                    Text(
                        text = alert.title.ifBlank { "SOL-bot 交易信号预警" },
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                },
                text = {
                    Text(
                        text = alert.message.ifBlank { "收到最新策略或交易状态更新，请立即查看。" },
                        style = MaterialTheme.typography.bodyMedium,
                    )
                },
                confirmButton = {
                    Button(
                        onClick = {
                            val target = alert.route
                            if (DESTINATIONS.any { it.route == target }) route = target
                            vm.markAlertsRead(alert.receivedAt)
                            popupAlert = null
                        },
                    ) {
                        Text("立即查看")
                    }
                },
                dismissButton = {
                    TextButton(
                        onClick = {
                            vm.markAlertsRead(alert.receivedAt)
                            popupAlert = null
                        },
                    ) {
                        Text("知道了")
                    }
                },
            )
        }

        if (showApiConfig) {
            BinanceApiSheet(
                account = state.values["binance/account"],
                onSave = { k, s -> vm.saveBinance(k, s) },
                onSync = { vm.syncBinance() },
                onClear = { vm.clearBinance() },
                onDismiss = { showApiConfig = false },
            )
        }

        pendingLiveToggle?.let { enabling ->
            val binanceAcct = state.values["binance/account"]
            val liveStatus = state.values["live/status"]
            val readiness = liveStatus?.optJSONObject("readiness")
            val ordersEnabled = binanceAcct?.optBoolean(
                "orders_enabled",
                readiness?.optBoolean("orders_enabled", false) ?: false,
            ) ?: false
            val freeUsdt = readiness?.optString("free_usdt").orEmpty()
                .ifBlank { state.values["live/positions"]?.optJSONObject("account")?.optJSONObject("USDT")?.optString("free").orEmpty() }
            LiveToggleConfirmSheet(
                enabling = enabling,
                ordersEnabled = ordersEnabled,
                freeUsdt = freeUsdt,
                onConfirm = { vm.live(enabling) },
                onDismiss = { pendingLiveToggle = null },
            )
        }

        pendingCustody?.let { (pos, enabling) ->
            CustodyConfirmSheet(
                position = pos,
                enabling = enabling,
                onConfirm = { vm.custody(pos, enabling) },
                onDismiss = { pendingCustody = null },
            )
        }

        inspectingOrder?.let { ord ->
            OrderDetailSheet(
                order = ord,
                onDismiss = { inspectingOrder = null },
            )
        }
    }
}
