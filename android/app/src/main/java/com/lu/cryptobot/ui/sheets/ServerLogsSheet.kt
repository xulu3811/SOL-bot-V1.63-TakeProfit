package com.lu.cryptobot.ui.sheets

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CleaningServices
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.lu.cryptobot.ui.components.StatusBadge
import com.lu.cryptobot.ui.components.optCleanString
import com.lu.cryptobot.ui.theme.LocalBotColors
import com.lu.cryptobot.ui.theme.tnum
import org.json.JSONObject

private val LOG_TABS = listOf(
    "auth" to "SSH/登录",
    "fail2ban" to "Fail2Ban",
    "api" to "API审计",
    "health" to "系统健康",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ServerLogsSheet(
    logs: JSONObject?,
    busy: Boolean,
    onFetchLogs: (source: String, onlyAlerts: Boolean) -> Unit,
    onDismiss: () -> Unit,
) {
    val bot = LocalBotColors.current
    var selectedSource by remember { mutableStateOf("auth") }
    var onlyAlerts by remember { mutableStateOf(false) }

    LaunchedEffect(selectedSource, onlyAlerts) {
        onFetchLogs(selectedSource, onlyAlerts)
    }

    val entriesArr = logs?.optJSONArray("entries")
    val entries = remember(logs) {
        buildList {
            if (entriesArr != null) {
                for (i in 0 until entriesArr.length()) {
                    entriesArr.optJSONObject(i)?.let { add(it) }
                }
            }
        }
    }
    val alertCount = logs?.optInt("alert_count", 0) ?: 0
    val errText = logs?.optCleanString("error").orEmpty()

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 16.dp, vertical = 6.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column {
                    Text("服务器安全与运行日志", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Text(
                        text = "只读审计 · 敏感字段已自动脱敏",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    StatusBadge(
                        text = if (alertCount > 0) "异常 $alertCount" else "无异常",
                        containerColor = if (alertCount > 0) bot.lossSoft else bot.gainSoft,
                        contentColor = if (alertCount > 0) bot.loss else bot.gain,
                    )
                    IconButton(onClick = { onFetchLogs(selectedSource, onlyAlerts) }) {
                        Icon(Icons.Outlined.Refresh, contentDescription = "刷新日志")
                    }
                }
            }

            // Segmented source selector
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                LOG_TABS.forEach { (key, label) ->
                    val active = selectedSource == key
                    Surface(
                        modifier = Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(10.dp))
                            .clickable { selectedSource = key },
                        shape = RoundedCornerShape(10.dp),
                        color = if (active) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceContainerLow,
                        border = BorderStroke(1.dp, if (active) MaterialTheme.colorScheme.primary else bot.hairline),
                    ) {
                        Text(
                            text = label,
                            style = MaterialTheme.typography.labelMedium,
                            fontWeight = if (active) FontWeight.Bold else FontWeight.Medium,
                            color = if (active) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(vertical = 8.dp).fillMaxWidth(),
                            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                            maxLines = 1,
                        )
                    }
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                FilterChip(
                    selected = onlyAlerts,
                    onClick = { onlyAlerts = !onlyAlerts },
                    label = { Text("仅看异常/拦截", style = MaterialTheme.typography.labelMedium) },
                )
                Text(
                    text = "显示最近 ${entries.size} 条",
                    style = MaterialTheme.typography.labelSmall.tnum(),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            if (busy) {
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            }

            if (errText.isNotBlank()) {
                Surface(
                    shape = MaterialTheme.shapes.small,
                    color = bot.lossSoft,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(
                        text = "日志读取失败：$errText",
                        style = MaterialTheme.typography.bodySmall,
                        color = bot.loss,
                        modifier = Modifier.padding(10.dp),
                    )
                }
            }

            Surface(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 200.dp, max = 380.dp),
                shape = MaterialTheme.shapes.medium,
                color = MaterialTheme.colorScheme.surfaceContainerLow,
                border = BorderStroke(1.dp, bot.hairline),
            ) {
                if (entries.isEmpty() && !busy) {
                    Column(
                        modifier = Modifier.fillMaxWidth().padding(24.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text(
                            text = if (onlyAlerts) "当前筛选条件下暂无异常日志" else "暂无日志记录",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                } else {
                    LazyColumn(
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        items(entries) { entry ->
                            val lvl = entry.optCleanString("level")
                            val txt = entry.optCleanString("text")
                            val color = when (lvl) {
                                "alert" -> bot.loss
                                "warn" -> bot.warn
                                else -> MaterialTheme.colorScheme.onSurface
                            }
                            Text(
                                text = txt,
                                fontFamily = FontFamily.Monospace,
                                fontSize = 11.sp,
                                lineHeight = 15.sp,
                                color = color,
                            )
                            HorizontalDivider(color = bot.hairline.copy(alpha = 0.55f))
                        }
                    }
                }
            }
            Spacer(Modifier.height(6.dp))
        }
    }
}

@Composable
fun CleanCacheConfirmDialog(
    preview: JSONObject,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    val bot = LocalBotColors.current
    val itemsArr = preview.optJSONArray("items")
    val totalMb = preview.optDouble("total_freed_mb", 0.0)
    val diskPct = preview.optDouble("disk_before_pct", 0.0)

    AlertDialog(
        onDismissRequest = onDismiss,
        icon = {
            Icon(
                imageVector = Icons.Outlined.CleaningServices,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
            )
        },
        title = {
            Text("确认清理服务器缓存与冗余？", fontWeight = FontWeight.Bold)
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    text = "仅执行白名单安全清理，绝不触碰运行中的交易数据库、密钥或服务进程。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Surface(
                    shape = MaterialTheme.shapes.small,
                    color = MaterialTheme.colorScheme.surfaceContainerLow,
                    border = BorderStroke(1.dp, bot.hairline),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(
                        modifier = Modifier.padding(10.dp),
                        verticalArrangement = Arrangement.spacedBy(5.dp),
                    ) {
                        if (itemsArr != null) {
                            for (i in 0 until itemsArr.length()) {
                                val row = itemsArr.optJSONObject(i) ?: continue
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                ) {
                                    Text(
                                        text = row.optCleanString("label"),
                                        style = MaterialTheme.typography.labelMedium,
                                        color = MaterialTheme.colorScheme.onSurface,
                                    )
                                    Text(
                                        text = "${String.format("%.1f", row.optDouble("mb", 0.0))} MB",
                                        style = MaterialTheme.typography.labelMedium.tnum(),
                                        fontWeight = FontWeight.Bold,
                                        color = MaterialTheme.colorScheme.primary,
                                    )
                                }
                            }
                        }
                        HorizontalDivider(color = bot.hairline)
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                        ) {
                            Text("预计共释放（当前盘 ${String.format("%.1f", diskPct)}%）", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
                            Text(
                                text = "${String.format("%.1f", totalMb)} MB",
                                style = MaterialTheme.typography.labelMedium.tnum(),
                                fontWeight = FontWeight.Bold,
                                color = bot.gain,
                            )
                        }
                    }
                }
            }
        },
        confirmButton = {
            Button(onClick = onConfirm) {
                Text("确认清理")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("取消")
            }
        },
    )
}
