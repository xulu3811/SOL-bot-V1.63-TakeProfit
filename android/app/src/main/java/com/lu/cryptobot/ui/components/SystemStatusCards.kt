package com.lu.cryptobot.ui.components

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CleaningServices
import androidx.compose.material.icons.outlined.Dns
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.Hub
import androidx.compose.material.icons.outlined.Memory
import androidx.compose.material.icons.outlined.Security
import androidx.compose.material.icons.outlined.Terminal
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.lu.cryptobot.ui.theme.LocalBotColors
import com.lu.cryptobot.ui.theme.tnum
import org.json.JSONArray
import org.json.JSONObject

private enum class MiddlePanel { SERV, BOT }

@Composable
fun ServAndBotStatusSection(
    systemOverview: JSONObject?,
    opsBusy: Boolean,
    opsMessage: String?,
    onOptimizeMemory: () -> Unit,
    onRequestCleanCache: () -> Unit,
    onOpenLogs: () -> Unit,
    onDismissOpsMessage: () -> Unit,
    onExpandedChange: (Boolean) -> Unit = {},
) {
    val bot = LocalBotColors.current
    var expandedPanel by remember { mutableStateOf<MiddlePanel?>(null) }

    val server = systemOverview?.optJSONObject("server")
    val botModules = systemOverview?.optJSONObject("bot_modules")

    fun setPanel(next: MiddlePanel?) {
        expandedPanel = next
        onExpandedChange(next != null)
    }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .animateContentSize(),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        // Side-by-side compact cards
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            ServStatusCompactCard(
                server = server,
                expanded = expandedPanel == MiddlePanel.SERV,
                onClick = {
                    setPanel(if (expandedPanel == MiddlePanel.SERV) null else MiddlePanel.SERV)
                },
                modifier = Modifier.weight(1f),
            )
            BotStatusCompactCard(
                botModules = botModules,
                expanded = expandedPanel == MiddlePanel.BOT,
                onClick = {
                    setPanel(if (expandedPanel == MiddlePanel.BOT) null else MiddlePanel.BOT)
                },
                modifier = Modifier.weight(1f),
            )
        }

        // Toggleable drawer below the pair
        AnimatedVisibility(
            visible = expandedPanel != null,
            enter = expandVertically() + fadeIn(),
            exit = shrinkVertically() + fadeOut(),
        ) {
            when (expandedPanel) {
                MiddlePanel.SERV -> ServStatusDrawer(
                    server = server,
                    opsBusy = opsBusy,
                    opsMessage = opsMessage,
                    onOptimizeMemory = onOptimizeMemory,
                    onRequestCleanCache = onRequestCleanCache,
                    onOpenLogs = onOpenLogs,
                    onDismissOpsMessage = onDismissOpsMessage,
                    onCollapse = { setPanel(null) },
                )
                MiddlePanel.BOT -> BotStatusDrawer(
                    botModules = botModules,
                    onCollapse = { setPanel(null) },
                )
                null -> Unit
            }
        }
    }
}

@Composable
private fun ServStatusCompactCard(
    server: JSONObject?,
    expanded: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val bot = LocalBotColors.current
    val connected = server?.optBoolean("ops_agent_connected", false) == true
    val sec = server?.optJSONObject("security")
    val secStatus = sec?.optString("status", "OK").orEmpty()
    val secLabel = sec?.optString("status_label", "待同步").orEmpty().ifBlank { "待同步" }

    val (badgeBg, badgeFg, dotColor) = when {
        !connected -> Triple(MaterialTheme.colorScheme.surfaceContainerHigh, MaterialTheme.colorScheme.onSurfaceVariant, MaterialTheme.colorScheme.outline)
        secStatus == "ALERT" -> Triple(bot.lossSoft, bot.loss, bot.loss)
        secStatus == "WARN" -> Triple(bot.warnSoft, bot.warn, bot.warn)
        else -> Triple(bot.gainSoft, bot.gain, bot.gain)
    }

    val ramPct = server?.optDouble("ram_pct", Double.NaN)?.takeIf { !it.isNaN() }
    val cpuPct = server?.optDouble("cpu_pct", Double.NaN)?.takeIf { !it.isNaN() }
    val diskPct = server?.optDouble("disk_pct", Double.NaN)?.takeIf { !it.isNaN() }
    val sshCount = sec?.optInt("ssh_active_count", 0) ?: 0

    val line1 = if (connected && ramPct != null && cpuPct != null) {
        "RAM ${ramPct.toInt()}% · CPU ${cpuPct.toInt()}%"
    } else {
        "节点资源待同步"
    }
    val line2 = if (connected && diskPct != null) {
        "盘 ${diskPct.toInt()}% · SSH $sshCount"
    } else {
        "点击展开运维面板"
    }

    Surface(
        modifier = modifier
            .clip(MaterialTheme.shapes.large)
            .clickable(onClick = onClick),
        shape = MaterialTheme.shapes.large,
        color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(
            width = if (expanded) 1.5.dp else 1.dp,
            color = if (expanded) MaterialTheme.colorScheme.primary else bot.hairline,
        ),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(5.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(5.dp),
                ) {
                    Icon(
                        imageVector = Icons.Outlined.Dns,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.size(16.dp),
                    )
                    Text(
                        text = "Serv Status",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                        maxLines = 1,
                    )
                }
                Icon(
                    imageVector = if (expanded) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore,
                    contentDescription = if (expanded) "收起" else "展开",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(17.dp),
                )
            }

            StatusBadge(
                text = if (connected) secLabel else "等待代理",
                containerColor = badgeBg,
                contentColor = badgeFg,
                dotColor = dotColor,
            )

            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    text = line1,
                    style = MaterialTheme.typography.labelMedium.tnum(),
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = line2,
                    style = MaterialTheme.typography.labelSmall.tnum(),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

@Composable
private fun BotStatusCompactCard(
    botModules: JSONObject?,
    expanded: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val bot = LocalBotColors.current
    val healthy = botModules?.optBoolean("overall_healthy", false) == true
    val activeCount = botModules?.optInt("active_count", 0) ?: 0
    val totalCount = botModules?.optInt("total_count", 0) ?: 0
    val modeSummary = botModules?.optCleanString("mode_summary").orEmpty().ifBlank { "P3+R2 Shadow" }

    val (badgeText, badgeBg, badgeFg, dotColor) = when {
        botModules == null -> BadgeStyle("等待同步", MaterialTheme.colorScheme.surfaceContainerHigh, MaterialTheme.colorScheme.onSurfaceVariant, MaterialTheme.colorScheme.outline)
        totalCount > 0 && healthy -> BadgeStyle("$activeCount/$totalCount 在线", bot.gainSoft, bot.gain, bot.gain)
        totalCount > 0 -> BadgeStyle("$activeCount/$totalCount 异常", bot.warnSoft, bot.warn, bot.warn)
        healthy -> BadgeStyle("模块正常", bot.gainSoft, bot.gain, bot.gain)
        else -> BadgeStyle("局部降级", bot.warnSoft, bot.warn, bot.warn)
    }

    Surface(
        modifier = modifier
            .clip(MaterialTheme.shapes.large)
            .clickable(onClick = onClick),
        shape = MaterialTheme.shapes.large,
        color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(
            width = if (expanded) 1.5.dp else 1.dp,
            color = if (expanded) MaterialTheme.colorScheme.primary else bot.hairline,
        ),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(5.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(5.dp),
                ) {
                    Icon(
                        imageVector = Icons.Outlined.Hub,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.size(16.dp),
                    )
                    Text(
                        text = "Bot Status",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                        maxLines = 1,
                    )
                }
                Icon(
                    imageVector = if (expanded) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore,
                    contentDescription = if (expanded) "收起" else "展开",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(17.dp),
                )
            }

            StatusBadge(
                text = badgeText,
                containerColor = badgeBg,
                contentColor = badgeFg,
                dotColor = dotColor,
            )

            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    text = "行情·策略·风控·执行",
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = modeSummary,
                    style = MaterialTheme.typography.labelSmall.tnum(),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

@Composable
private fun ServStatusDrawer(
    server: JSONObject?,
    opsBusy: Boolean,
    opsMessage: String?,
    onOptimizeMemory: () -> Unit,
    onRequestCleanCache: () -> Unit,
    onOpenLogs: () -> Unit,
    onDismissOpsMessage: () -> Unit,
    onCollapse: () -> Unit,
) {
    val bot = LocalBotColors.current
    val connected = server?.optBoolean("ops_agent_connected", false) == true
    val uptimeText = server?.optCleanString("uptime_text").orEmpty().ifBlank { "—" }
    val cpuCores = server?.optInt("cpu_cores", 2) ?: 2
    val cpuPct = server?.optDouble("cpu_pct", Double.NaN)?.takeIf { !it.isNaN() }
    val load1m = server?.optDouble("load_1m", Double.NaN)?.takeIf { !it.isNaN() }
    val ramTotal = server?.optInt("ram_total_mb", 0) ?: 0
    val ramUsed = server?.optInt("ram_used_mb", 0) ?: 0
    val ramAvail = server?.optInt("ram_available_mb", 0) ?: 0
    val ramPct = server?.optDouble("ram_pct", Double.NaN)?.takeIf { !it.isNaN() }
    val buffCache = server?.optInt("buff_cache_mb", 0) ?: 0
    val swapUsed = server?.optInt("swap_used_mb", 0) ?: 0
    val swapTotal = server?.optInt("swap_total_mb", 0) ?: 0
    val diskUsed = server?.optDouble("disk_used_gb", Double.NaN)?.takeIf { !it.isNaN() }
    val diskTotal = server?.optDouble("disk_total_gb", Double.NaN)?.takeIf { !it.isNaN() }
    val diskPct = server?.optDouble("disk_pct", Double.NaN)?.takeIf { !it.isNaN() }
    val procsTotal = server?.optInt("processes_total", 0) ?: 0
    val svcActive = server?.optInt("services_active", 0) ?: 0
    val svcTotal = server?.optInt("services_total", 0) ?: 0
    val cleanableMb = server?.optDouble("cleanable_mb", 0.0) ?: 0.0

    val sec = server?.optJSONObject("security")
    val secStatus = sec?.optString("status", "OK").orEmpty()
    val secSummary = sec?.optCleanString("summary_text").orEmpty().ifBlank { "SSH/API 安全审计" }
    val secAlertCount = (sec?.optInt("ssh_failed_recent", 0) ?: 0) +
        (sec?.optInt("api_alerts_24h", 0) ?: 0) +
        (sec?.optInt("fail2ban_currently_banned", 0) ?: 0)

    BotSurfaceCard(borderColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.45f)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text("服务器实时性能与安全", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                Text(
                    text = "腾讯云日本节点 · 运行 $uptimeText",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            StatusBadge(
                text = "收起 ∧",
                containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier
                    .clip(RoundedCornerShape(999.dp))
                    .clickable(onClick = onCollapse),
            )
        }

        Spacer(Modifier.height(10.dp))

        if (!connected) {
            Surface(
                shape = MaterialTheme.shapes.small,
                color = bot.warnSoft,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    text = "本地运维代理 (solbot-ops-agent) 尚未连接，请确认服务已启动。",
                    style = MaterialTheme.typography.bodySmall,
                    color = bot.warn,
                    modifier = Modifier.padding(10.dp),
                )
            }
            Spacer(Modifier.height(10.dp))
        }

        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                MetricBentoTile(
                    label = "CPU 负载",
                    value = if (cpuPct != null) "${String.format("%.1f", cpuPct)}%" else "—",
                    caption = if (load1m != null) "Load ${String.format("%.2f", load1m)} · ${cpuCores}核" else "${cpuCores}核",
                    valueColor = when {
                        cpuPct != null && cpuPct >= 85.0 -> bot.loss
                        cpuPct != null && cpuPct >= 65.0 -> bot.warn
                        else -> MaterialTheme.colorScheme.onSurface
                    },
                    modifier = Modifier.weight(1f),
                )
                MetricBentoTile(
                    label = "RAM 内存",
                    value = if (ramPct != null) "${String.format("%.1f", ramPct)}%" else "—",
                    caption = if (ramTotal > 0) "$ramUsed/$ramTotal M · 剩${ramAvail}M" else "—",
                    valueColor = when {
                        ramPct != null && ramPct >= 85.0 -> bot.loss
                        ramPct != null && ramPct >= 70.0 -> bot.warn
                        else -> MaterialTheme.colorScheme.onSurface
                    },
                    modifier = Modifier.weight(1f),
                )
            }
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                MetricBentoTile(
                    label = "磁盘空间",
                    value = if (diskPct != null) "${String.format("%.0f", diskPct)}%" else "—",
                    caption = if (diskUsed != null && diskTotal != null) "${String.format("%.1f", diskUsed)} / ${String.format("%.0f", diskTotal)} GB" else "—",
                    valueColor = if (diskPct != null && diskPct >= 85.0) bot.loss else MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.weight(1f),
                )
                MetricBentoTile(
                    label = "系统进程",
                    value = if (procsTotal > 0) "$procsTotal 个" else "—",
                    caption = "核心 $svcActive/$svcTotal · Swap ${swapUsed}M",
                    modifier = Modifier.weight(1f),
                )
            }
        }

        Spacer(Modifier.height(8.dp))

        Text(
            text = "页缓存 ${buffCache} MB · Swap ${swapUsed}/${swapTotal} MB · 可清理冗余 ${String.format("%.1f", cleanableMb)} MB",
            style = MaterialTheme.typography.labelSmall.tnum(),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Spacer(Modifier.height(10.dp))

        // Security strip (matching Shadow R2 strip)
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(10.dp))
                .clickable(onClick = onOpenLogs),
            shape = RoundedCornerShape(10.dp),
            color = if (secStatus == "ALERT") bot.lossSoft.copy(alpha = 0.65f) else MaterialTheme.colorScheme.surfaceContainerLow,
            border = BorderStroke(1.dp, if (secStatus == "ALERT") bot.loss.copy(alpha = 0.35f) else bot.hairline),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 11.dp, vertical = 9.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    modifier = Modifier.weight(1f),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(7.dp),
                ) {
                    Icon(
                        imageVector = Icons.Outlined.Security,
                        contentDescription = null,
                        tint = when (secStatus) {
                            "ALERT" -> bot.loss
                            "WARN" -> bot.warn
                            else -> bot.gain
                        },
                        modifier = Modifier.size(16.dp),
                    )
                    Text(
                        text = if (secAlertCount > 0) "$secSummary（关注）" else secSummary,
                        style = MaterialTheme.typography.labelMedium.tnum(),
                        color = MaterialTheme.colorScheme.onSurface,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(3.dp),
                ) {
                    Icon(
                        imageVector = Icons.Outlined.Terminal,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.size(14.dp),
                    )
                    Text(
                        text = "查看日志 ›",
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
            }
        }

        if (!opsMessage.isNullOrBlank()) {
            Spacer(Modifier.height(8.dp))
            Surface(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(10.dp))
                    .clickable(onClick = onDismissOpsMessage),
                shape = RoundedCornerShape(10.dp),
                color = bot.infoSoft,
            ) {
                Text(
                    text = opsMessage,
                    style = MaterialTheme.typography.labelMedium,
                    color = bot.info,
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
                )
            }
        }

        Spacer(Modifier.height(10.dp))

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            FilledTonalButton(
                onClick = onOptimizeMemory,
                enabled = connected && !opsBusy,
                shape = RoundedCornerShape(12.dp),
                contentPadding = PaddingValues(horizontal = 10.dp, vertical = 10.dp),
                modifier = Modifier.weight(1f),
            ) {
                if (opsBusy) {
                    CircularProgressIndicator(modifier = Modifier.size(15.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(6.dp))
                } else {
                    Icon(Icons.Outlined.Memory, contentDescription = null, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(5.dp))
                }
                Text("一键优化内存", style = MaterialTheme.typography.labelLarge, maxLines = 1)
            }

            OutlinedButton(
                onClick = onRequestCleanCache,
                enabled = connected && !opsBusy,
                shape = RoundedCornerShape(12.dp),
                border = BorderStroke(1.dp, bot.hairline),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.onSurface),
                contentPadding = PaddingValues(horizontal = 10.dp, vertical = 10.dp),
                modifier = Modifier.weight(1f),
            ) {
                Icon(Icons.Outlined.CleaningServices, contentDescription = null, modifier = Modifier.size(16.dp))
                Spacer(Modifier.width(5.dp))
                Text("清理缓存垃圾", style = MaterialTheme.typography.labelLarge, maxLines = 1)
            }
        }
    }
}

@Composable
private fun BotStatusDrawer(
    botModules: JSONObject?,
    onCollapse: () -> Unit,
) {
    val bot = LocalBotColors.current
    var showServicesDetail by remember { mutableStateOf(false) }

    val modeSummary = botModules?.optCleanString("mode_summary").orEmpty().ifBlank { "P3+R2 Shadow · LIVE_OFF" }
    val solActive = botModules?.optInt("sol_active_count", 0) ?: 0
    val solTotal = botModules?.optInt("sol_total_count", 0) ?: 0
    val suiActive = botModules?.optInt("sui_active_count", 0) ?: 0
    val suiTotal = botModules?.optInt("sui_total_count", 0) ?: 0
    val layers = botModules?.optJSONArray("layers")
    val services = botModules?.optJSONArray("services")

    BotSurfaceCard(borderColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.45f)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text("SOL-bot 模块化运行仪表盘", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                Text(
                    text = "$modeSummary · 只读可观测",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            StatusBadge(
                text = "收起 ∧",
                containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier
                    .clip(RoundedCornerShape(999.dp))
                    .clickable(onClick = onCollapse),
            )
        }

        Spacer(Modifier.height(10.dp))

        if (layers != null && layers.length() > 0) {
            val layerItems = (0 until layers.length()).mapNotNull { layers.optJSONObject(it) }
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                layerItems.chunked(2).forEach { pair ->
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        pair.forEach { layer ->
                            ModuleLayerTile(layer = layer, modifier = Modifier.weight(1f))
                        }
                        if (pair.size == 1) {
                            Spacer(Modifier.weight(1f))
                        }
                    }
                }
            }
        } else {
            Text(
                text = "正在等待交易模块状态同步…",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        Spacer(Modifier.height(10.dp))

        // Collapsible detail drawer for all 12 modules including SUI auxiliary research
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(10.dp))
                .clickable { showServicesDetail = !showServicesDetail },
            shape = RoundedCornerShape(10.dp),
            color = MaterialTheme.colorScheme.surfaceContainerLow,
            border = BorderStroke(1.dp, bot.hairline),
        ) {
            Column(modifier = Modifier.animateContentSize()) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 11.dp, vertical = 9.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = if (suiTotal > 0) {
                            "运行进程与 SUI 研究明细（SOL $solActive/$solTotal · SUI $suiActive/$suiTotal）"
                        } else {
                            "全链路运行进程明细（$solActive/$solTotal）"
                        },
                        style = MaterialTheme.typography.labelMedium.tnum(),
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier.weight(1f),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Icon(
                        imageVector = if (showServicesDetail) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(18.dp),
                    )
                }

                if (showServicesDetail && services != null && services.length() > 0) {
                    HorizontalDivider(color = bot.hairline)
                    ServicesGroupedList(services = services)
                }
            }
        }
    }
}

@Composable
private fun ModuleLayerTile(
    layer: JSONObject,
    modifier: Modifier = Modifier,
) {
    val bot = LocalBotColors.current
    val healthy = layer.optBoolean("healthy", false)
    val title = layer.optCleanString("title")
    val subtitle = layer.optCleanString("subtitle")
    val statusText = layer.optCleanString("status_text")
    val metricText = layer.optCleanString("metric_text")
    val subText = layer.optCleanString("sub_text")

    val dotColor = if (healthy) bot.gain else bot.warn
    val badgeBg = if (healthy) bot.gainSoft else bot.warnSoft
    val badgeFg = if (healthy) bot.gain else bot.warn

    Surface(
        modifier = modifier,
        shape = MaterialTheme.shapes.small,
        color = MaterialTheme.colorScheme.surfaceContainerLow,
        border = BorderStroke(1.dp, bot.hairline),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 9.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(5.dp),
                ) {
                    Box(
                        modifier = Modifier
                            .size(7.dp)
                            .clip(CircleShape)
                            .background(dotColor),
                    )
                    Text(
                        text = title,
                        style = MaterialTheme.typography.labelLarge,
                        fontWeight = FontWeight.Bold,
                        maxLines = 1,
                    )
                }
                Surface(
                    shape = RoundedCornerShape(999.dp),
                    color = badgeBg,
                ) {
                    Text(
                        text = statusText,
                        style = MaterialTheme.typography.labelSmall,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = badgeFg,
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                        maxLines = 1,
                    )
                }
            }
            Text(
                text = metricText.ifBlank { subtitle },
                style = MaterialTheme.typography.titleSmall.tnum(),
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurface,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text = subText,
                style = MaterialTheme.typography.labelSmall.tnum(),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
private fun ServicesGroupedList(services: JSONArray) {
    val bot = LocalBotColors.current
    val grouped = linkedMapOf<String, MutableList<JSONObject>>()
    for (i in 0 until services.length()) {
        val svc = services.optJSONObject(i) ?: continue
        val gLabel = svc.optCleanString("group_label").ifBlank { "其他模块" }
        grouped.getOrPut(gLabel) { mutableListOf() }.add(svc)
    }

    Column(
        modifier = Modifier.padding(horizontal = 11.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        grouped.forEach { (groupLabel, list) ->
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    text = groupLabel,
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    color = if (groupLabel.contains("SUI")) bot.info else MaterialTheme.colorScheme.onSurfaceVariant,
                )
                list.forEach { svc ->
                    val active = svc.optBoolean("active", false)
                    val label = svc.optCleanString("label").ifBlank { svc.optCleanString("unit") }
                    val detail = svc.optCleanString("detail")
                    val memMb = svc.optDouble("memory_mb", Double.NaN).takeIf { !it.isNaN() }
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Row(
                            modifier = Modifier.weight(1f),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(6.dp)
                                    .clip(CircleShape)
                                    .background(if (active) bot.gain else bot.loss),
                            )
                            Column(modifier = Modifier.weight(1f)) {
                                Text(
                                    text = label,
                                    style = MaterialTheme.typography.labelMedium,
                                    fontWeight = FontWeight.SemiBold,
                                    color = MaterialTheme.colorScheme.onSurface,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                                if (detail.isNotBlank()) {
                                    Text(
                                        text = detail,
                                        style = MaterialTheme.typography.labelSmall.tnum(),
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                }
                            }
                        }
                        Text(
                            text = if (memMb != null) "${String.format("%.0f", memMb)} MB" else if (active) "在线" else "离线",
                            style = MaterialTheme.typography.labelSmall.tnum(),
                            color = if (active) MaterialTheme.colorScheme.onSurfaceVariant else bot.loss,
                        )
                    }
                }
            }
        }
    }
}

private data class BadgeStyle(
    val first: String,
    val second: Color,
    val third: Color,
    val fourth: Color,
)
