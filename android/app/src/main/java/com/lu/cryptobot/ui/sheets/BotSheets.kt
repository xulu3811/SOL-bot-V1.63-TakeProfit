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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Key
import androidx.compose.material.icons.outlined.NotificationsOff
import androidx.compose.material.icons.outlined.Security
import androidx.compose.material.icons.outlined.Sync
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material.icons.outlined.WarningAmber
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.lu.cryptobot.AlertRecord
import com.lu.cryptobot.ui.components.BotFormatters
import com.lu.cryptobot.ui.components.StatusBadge
import com.lu.cryptobot.ui.theme.LocalBotColors
import com.lu.cryptobot.ui.theme.tnum
import org.json.JSONObject

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConnectionSheet(
    initialUrl: String,
    deviceId: String,
    liveState: String,
    interval: String,
    microReady: Boolean,
    microAgeMs: Long?,
    onConnect: (url: String, code: String) -> Unit,
    onRevoke: () -> Unit,
    onDismiss: () -> Unit,
) {
    val bot = LocalBotColors.current
    var url by remember { mutableStateOf(initialUrl.ifBlank { "https://43.163.192.120/" }) }
    var code by remember { mutableStateOf("") }
    var confirmRevoke by remember { mutableStateOf(false) }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 20.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column {
                    Text("节点连接与设备安全", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Text("TLS 证书固定 · ECDSA 签名鉴权", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                StatusBadge(
                    text = if (deviceId.isNotBlank()) "已配对" else "未绑定",
                    containerColor = if (deviceId.isNotBlank()) bot.gainSoft else bot.warnSoft,
                    contentColor = if (deviceId.isNotBlank()) bot.gain else bot.warn,
                )
            }

            Surface(
                shape = MaterialTheme.shapes.small,
                color = MaterialTheme.colorScheme.surfaceContainerLow,
                border = BorderStroke(1.dp, bot.hairline),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(
                    modifier = Modifier.padding(12.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Text(
                        text = "设备 ID: ${deviceId.ifBlank { "尚未配对" }}",
                        style = MaterialTheme.typography.bodySmall.tnum(),
                        fontWeight = FontWeight.Medium,
                    )
                    Text(
                        text = "实盘状态: $liveState · 图表周期: $interval · 盘口流: ${if (microReady) "就绪 (${microAgeMs ?: 0}ms)" else "等待深度数据"}",
                        style = MaterialTheme.typography.labelSmall.tnum(),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            OutlinedTextField(
                value = url,
                onValueChange = { url = it },
                label = { Text("服务端 HTTPS 根地址") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
                shape = MaterialTheme.shapes.small,
            )

            OutlinedTextField(
                value = code,
                onValueChange = { code = it },
                label = { Text("配对口令 (重连节点可留空)") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                modifier = Modifier.fillMaxWidth(),
                shape = MaterialTheme.shapes.small,
            )

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Button(
                    onClick = {
                        onConnect(url.trim(), code.trim())
                        onDismiss()
                    },
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Text(if (code.isBlank()) "验证重连" else "配对并同步")
                }
                if (deviceId.isNotBlank()) {
                    OutlinedButton(
                        onClick = { confirmRevoke = !confirmRevoke },
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = bot.loss),
                    ) {
                        Text("注销设备")
                    }
                }
            }

            if (confirmRevoke) {
                Surface(
                    shape = MaterialTheme.shapes.small,
                    color = bot.lossSoft,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Row(
                        modifier = Modifier.padding(12.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            text = "确认撤销当前手机令牌并清空会话？",
                            style = MaterialTheme.typography.bodySmall,
                            color = bot.loss,
                            modifier = Modifier.weight(1f),
                        )
                        TextButton(
                            onClick = {
                                onRevoke()
                                onDismiss()
                            },
                        ) {
                            Text("确认注销", color = bot.loss, fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AlertsInboxSheet(
    alerts: List<AlertRecord>,
    onNavigateRoute: (String) -> Unit,
    onTestAlert: () -> Unit = {},
    onDismiss: () -> Unit,
) {
    val bot = LocalBotColors.current
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 20.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text("通知与风控告警", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Text("共 ${alerts.size} 条本地已同步记录 · 点击跳转对应模块", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                FilledTonalButton(
                    onClick = onTestAlert,
                    shape = RoundedCornerShape(10.dp),
                ) {
                    Text("测试弹窗铃声", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
                }
            }

            if (alerts.isEmpty()) {
                Surface(
                    shape = MaterialTheme.shapes.medium,
                    color = MaterialTheme.colorScheme.surfaceContainerLow,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Row(
                        modifier = Modifier.padding(20.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Icon(Icons.Outlined.NotificationsOff, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text("暂无告警或交易推送记录", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            } else {
                LazyColumn(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(max = 420.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    items(alerts, key = { it.eventId }) { alert ->
                        Surface(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clip(MaterialTheme.shapes.small)
                                .clickable {
                                    if (alert.route.isNotBlank()) onNavigateRoute(alert.route)
                                    onDismiss()
                                },
                            shape = MaterialTheme.shapes.small,
                            color = MaterialTheme.colorScheme.surfaceContainerLow,
                            border = BorderStroke(1.dp, bot.hairline),
                        ) {
                            Column(
                                modifier = Modifier.padding(12.dp),
                                verticalArrangement = Arrangement.spacedBy(4.dp),
                            ) {
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                ) {
                                    Text(
                                        text = alert.title.ifBlank { "系统通知" },
                                        style = MaterialTheme.typography.titleSmall,
                                        fontWeight = FontWeight.Bold,
                                        modifier = Modifier.weight(1f),
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                    Text(
                                        text = BotFormatters.formatEpoch(alert.receivedAt),
                                        style = MaterialTheme.typography.labelSmall.tnum(),
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                                if (alert.message.isNotBlank()) {
                                    Text(
                                        text = alert.message,
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            }
                        }
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BinanceApiSheet(
    account: JSONObject?,
    onSave: (key: String, secret: String) -> Unit,
    onSync: () -> Unit,
    onClear: () -> Unit,
    onDismiss: () -> Unit,
) {
    val bot = LocalBotColors.current
    val configured = account?.optBoolean("configured", false) ?: false
    val fingerprint = account?.optString("key_fingerprint").orEmpty()
    val ordersEnabled = account?.optBoolean("orders_enabled", false) ?: false
    val spotEnabled = account?.optBoolean("spot_trading_enabled", ordersEnabled) ?: false

    var apiKey by remember { mutableStateOf("") }
    var secret by remember { mutableStateOf("") }
    var confirmClear by remember { mutableStateOf(false) }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 20.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(Icons.Outlined.Key, null, tint = MaterialTheme.colorScheme.primary)
                Column {
                    Text("Binance 现货 API 配置", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Text("密钥经后端加密保险库保存 · 禁止提现权限", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }

            if (configured) {
                Surface(
                    shape = MaterialTheme.shapes.small,
                    color = MaterialTheme.colorScheme.surfaceContainerLow,
                    border = BorderStroke(1.dp, bot.hairline),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Row(
                        modifier = Modifier.padding(12.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column {
                            Text(
                                text = if (fingerprint.isNotBlank()) "已绑定尾号 ···${fingerprint.takeLast(4)}" else "已绑定 API 密钥",
                                style = MaterialTheme.typography.titleSmall.tnum(),
                                fontWeight = FontWeight.Bold,
                            )
                            Text(
                                text = if (spotEnabled) "权限校验通过：现货可下单 · 提现已关闭" else "权限校验：只读对账模式（未开启现货交易）",
                                style = MaterialTheme.typography.labelSmall,
                                color = if (spotEnabled) bot.gain else bot.warn,
                            )
                        }
                        FilledTonalButton(
                            onClick = {
                                onSync()
                                onDismiss()
                            },
                            shape = RoundedCornerShape(10.dp),
                        ) {
                            Icon(Icons.Outlined.Sync, null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.size(4.dp))
                            Text("对账")
                        }
                    }
                }
            }

            Surface(
                shape = MaterialTheme.shapes.small,
                color = bot.infoSoft,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    text = "安全要求：仅开启「允许读取」与「允许现货及杠杆交易」，严格保持「禁止提现」。",
                    modifier = Modifier.padding(10.dp),
                    style = MaterialTheme.typography.bodySmall,
                    color = bot.info,
                )
            }

            OutlinedTextField(
                value = apiKey,
                onValueChange = { apiKey = it },
                label = { Text("Binance API Key") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
                shape = MaterialTheme.shapes.small,
            )

            OutlinedTextField(
                value = secret,
                onValueChange = { secret = it },
                label = { Text("Secret Key") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                modifier = Modifier.fillMaxWidth(),
                shape = MaterialTheme.shapes.small,
            )

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Button(
                    onClick = {
                        onSave(apiKey.trim(), secret.trim())
                        onDismiss()
                    },
                    enabled = apiKey.isNotBlank() && secret.isNotBlank(),
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Text("校验并加密保存")
                }
                if (configured) {
                    OutlinedButton(
                        onClick = { confirmClear = !confirmClear },
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = bot.loss),
                    ) {
                        Text("清除")
                    }
                }
            }

            if (confirmClear) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("清除将关闭自动交易与托管挂单功能。", style = MaterialTheme.typography.bodySmall, color = bot.loss)
                    TextButton(
                        onClick = {
                            onClear()
                            onDismiss()
                        },
                    ) {
                        Text("确认清除", color = bot.loss, fontWeight = FontWeight.Bold)
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LiveToggleConfirmSheet(
    enabling: Boolean,
    ordersEnabled: Boolean,
    freeUsdt: String?,
    minOrderUsdt: String = "500",
    maxOrderUsdt: String = "1000",
    maxSlots: Int = 2,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    val bot = LocalBotColors.current
    val orderRangeText = BotFormatters.formatOrderRange(minOrderUsdt, maxOrderUsdt)
    val minCompact = BotFormatters.formatUsdtCompact(minOrderUsdt)
    val maxCompact = BotFormatters.formatUsdtCompact(maxOrderUsdt)
    val minEqualsMax = (BotFormatters.parseBigDecimal(minOrderUsdt)?.compareTo(BotFormatters.parseBigDecimal(maxOrderUsdt)) == 0)
    val orderRuleDesc = if (minEqualsMax) {
        "• 单笔限价买入固定 $$minCompact USDT（低于 $$minCompact 拒开），上限 $maxSlots 个策略仓，未触发止损一直持仓，仅策略交易计入日内 5.0% 亏损熔断。"
    } else {
        "• 单笔限价买入 $orderRangeText USDT（不足 $$maxCompact 按最大可用开仓，低于 $$minCompact 拒开），上限 $maxSlots 个策略仓，未触发止损一直持仓，仅策略交易计入日内 5.0% 亏损熔断。"
    }
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 20.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Icon(
                    if (enabling) Icons.Outlined.Security else Icons.Outlined.WarningAmber,
                    null,
                    tint = if (enabling) bot.gain else bot.warn,
                )
                Text(
                    text = if (enabling) "确认开启实盘自动交易" else "确认暂停实盘新开仓",
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold,
                )
            }

            Surface(
                shape = MaterialTheme.shapes.small,
                color = MaterialTheme.colorScheme.surfaceContainerLow,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(
                    modifier = Modifier.padding(12.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    if (enabling) {
                        Text("• 实盘默认授权 T1 顺势 / T2 波段 / R2 超卖反弹策略（R1 震荡默认禁用）。", style = MaterialTheme.typography.bodySmall)
                        Text(orderRuleDesc, style = MaterialTheme.typography.bodySmall)
                        Text(
                            text = "• API 现货权限: ${if (ordersEnabled) "已就绪" else "未开启"} · 当前可用 USDT: $${BotFormatters.formatNumber(freeUsdt, 2)}",
                            style = MaterialTheme.typography.bodySmall.tnum(),
                            color = if (ordersEnabled) bot.gain else bot.loss,
                            fontWeight = FontWeight.SemiBold,
                        )
                    } else {
                        Text("• 关闭实盘后，系统不会提交任何新的买入委托。", style = MaterialTheme.typography.bodySmall)
                        Text("• 已在交易所挂出的 APTS 止损保护单（含人工托管）不受影响，仍会持续保护持仓。", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                OutlinedButton(
                    onClick = onDismiss,
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Text("取消")
                }
                Button(
                    onClick = {
                        onConfirm()
                        onDismiss()
                    },
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(12.dp),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (enabling) bot.gain else bot.loss,
                    ),
                ) {
                    Text(if (enabling) "我已确认风险，开启" else "确认关闭")
                }
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CustodyConfirmSheet(
    position: JSONObject,
    enabling: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    val bot = LocalBotColors.current
    val qty = BotFormatters.formatPlainAmount(position.optString("quantity"))
    val liveRef = BotFormatters.formatCryptoPrice(
        position.optString("mark_price").ifBlank { position.optString("entry_price") }
    )
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 20.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text = if (enabling) "开启人工持仓 APTS 托管" else "撤销人工持仓 APTS 托管",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
            )
            Text(
                text = if (enabling) {
                    "系统将以点击托管时刻的实时市价（当前约 $$liveRef）为起算基准，为当前 $qty SOL 挂出交易所原生止损单：初始硬止损 -0.8%，随后随涨幅自动上移（+0.8% 保本 / +1.1% 动态锁盈 / +1.2%+ 追尾及超买衰竭收紧），不以历史买入价计算。"
                } else {
                    "撤销后，系统将撤回在 Binance 为人工 SOL 仓位挂出的原生止损委托，释放锁定的 SOL。"
                },
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                OutlinedButton(onClick = onDismiss, modifier = Modifier.weight(1f), shape = RoundedCornerShape(12.dp)) {
                    Text("返回")
                }
                Button(
                    onClick = {
                        onConfirm()
                        onDismiss()
                    },
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(12.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = if (enabling) bot.info else bot.loss),
                ) {
                    Text(if (enabling) "授权托管 (-0.8%)" else "撤销托管")
                }
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun OrderDetailSheet(
    order: JSONObject,
    onDismiss: () -> Unit,
) {
    val bot = LocalBotColors.current
    val apts = order.optJSONObject("apts")
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 20.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text("订单与执行明细", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            HorizontalDivider(color = bot.hairline)

            val rows = buildList {
                add("交易对" to order.optString("symbol", "SOLUSDT"))
                add("方向 / 状态" to "${order.optString("side", "—")} · ${order.optString("status", "—")}")
                if (order.optString("strategy_id").isNotBlank()) add("触发策略" to order.optString("strategy_id"))
                add("价格" to "$${BotFormatters.formatCryptoPrice(order.optString("price").ifBlank { order.optString("limit_price") })}")
                add("数量" to "${BotFormatters.formatPlainAmount(order.optString("executed_qty").ifBlank { order.optString("quantity") })} SOL")
                if (order.optString("quote").isNotBlank()) add("成交额" to "$${BotFormatters.formatNumber(order.optString("quote"), 2)}")
                if (order.optString("fee").isNotBlank()) add("手续费" to "${BotFormatters.formatPlainAmount(order.optString("fee"), 6)} BNB")
                if (order.optString("stop_price").isNotBlank()) add("预设止损价" to "$${BotFormatters.formatCryptoPrice(order.optString("stop_price"))}")
                val oid = order.optString("exchange_order_id").ifBlank { order.optString("order_id") }
                if (oid.isNotBlank()) add("订单号" to oid)
                if (order.optString("client_order_id").isNotBlank()) add("客户端 ID" to order.optString("client_order_id"))
                val ts = order.optLong("filled_at", 0L).takeIf { it > 0L }
                    ?: order.optLong("time", 0L).takeIf { it > 0L }
                    ?: order.optLong("created_at", 0L)
                add("时间" to BotFormatters.formatEpochFull(ts))
                if (apts != null) {
                    add("APTS 规则" to "初始止损 -${BotFormatters.formatNumber((apts.optString("initial_stop_pct", "0.010").toDoubleOrNull()?.times(100.0))?.toString(), 1)}% · 2-tick 确认 · 未止损持续持仓")
                }
            }

            rows.forEach { (k, v) ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 3.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(k, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(v, style = MaterialTheme.typography.bodySmall.tnum(), fontWeight = FontWeight.SemiBold)
                }
            }
            Spacer(Modifier.height(10.dp))
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TradingConfigSheet(
    currentMinOrderUsdt: String,
    currentMaxOrderUsdt: String,
    currentMaxSlots: Int,
    freeUsdt: String?,
    onSave: (minOrderUsdt: String, maxOrderUsdt: String, maxSlots: Int) -> Unit,
    onDismiss: () -> Unit,
) {
    val bot = LocalBotColors.current
    val initialMinBd = BotFormatters.parseBigDecimal(currentMinOrderUsdt) ?: java.math.BigDecimal("500")
    val initialMaxBd = BotFormatters.parseBigDecimal(currentMaxOrderUsdt) ?: java.math.BigDecimal("1000")
    val initialFixed = initialMinBd.compareTo(initialMaxBd) == 0

    var rangeMode by remember(currentMinOrderUsdt, currentMaxOrderUsdt) { mutableStateOf(!initialFixed) }
    var minAmount by remember(currentMinOrderUsdt) {
        mutableStateOf(initialMinBd.stripTrailingZeros().toPlainString())
    }
    var maxAmount by remember(currentMaxOrderUsdt) {
        mutableStateOf(initialMaxBd.stripTrailingZeros().toPlainString())
    }
    var maxSlots by remember(currentMaxSlots) { mutableIntStateOf(currentMaxSlots.coerceIn(1, 10)) }

    val parsedMin = BotFormatters.parseBigDecimal(minAmount)
    val parsedMax = if (rangeMode) BotFormatters.parseBigDecimal(maxAmount) else parsedMin
    val validationError = when {
        parsedMin == null -> "请输入有效的单笔开仓金额"
        parsedMin < java.math.BigDecimal("10") || parsedMin > java.math.BigDecimal("50000") -> "单笔最小金额需在 $10 – $50,000 USDT"
        parsedMax == null -> "请输入有效的最大开仓金额"
        parsedMax < parsedMin -> "最大金额不能低于最小金额"
        parsedMax > java.math.BigDecimal("50000") -> "单笔最大金额不能超过 $50,000 USDT"
        maxSlots !in 1..10 -> "持仓订单数量需在 1 – 10 槽之间"
        else -> null
    }

    val estExposure = (parsedMax ?: java.math.BigDecimal.ZERO).multiply(java.math.BigDecimal(maxSlots))

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surface,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 20.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(Icons.Outlined.Tune, null, tint = MaterialTheme.colorScheme.primary)
                    Column {
                        Text("交易仓位与金额配置", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                        Text("Trading-bot 实盘与 Shadow-bot 模拟盘同步生效", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                TextButton(
                    onClick = {
                        rangeMode = true
                        minAmount = "500"
                        maxAmount = "1000"
                        maxSlots = 2
                    },
                ) {
                    Text("默认", style = MaterialTheme.typography.labelMedium)
                }
            }

            Surface(
                shape = MaterialTheme.shapes.small,
                color = bot.infoSoft,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    text = buildString {
                        append("当前生效: $maxSlots 个持仓槽 · 单笔 ")
                        append(BotFormatters.formatOrderRange(currentMinOrderUsdt, currentMaxOrderUsdt))
                        append(" · 实盘可用 USDT $${BotFormatters.formatNumber(freeUsdt, 2)}")
                    },
                    modifier = Modifier.padding(10.dp),
                    style = MaterialTheme.typography.bodySmall.tnum(),
                    color = bot.info,
                    fontWeight = FontWeight.Medium,
                )
            }

            // Mode toggle: Fixed vs Dynamic Range
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Surface(
                    modifier = Modifier
                        .weight(1f)
                        .clip(RoundedCornerShape(10.dp))
                        .clickable {
                            rangeMode = false
                            if (maxAmount.isBlank() || parsedMax == null) maxAmount = minAmount
                        },
                    shape = RoundedCornerShape(10.dp),
                    color = if (!rangeMode) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceContainerLow,
                    border = BorderStroke(1.dp, if (!rangeMode) MaterialTheme.colorScheme.primary else bot.hairline),
                ) {
                    Text(
                        text = "固定单笔金额",
                        modifier = Modifier.padding(vertical = 8.dp, horizontal = 10.dp),
                        style = MaterialTheme.typography.labelLarge,
                        fontWeight = if (!rangeMode) FontWeight.Bold else FontWeight.Medium,
                        color = if (!rangeMode) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Surface(
                    modifier = Modifier
                        .weight(1f)
                        .clip(RoundedCornerShape(10.dp))
                        .clickable {
                            rangeMode = true
                            val curMin = BotFormatters.parseBigDecimal(minAmount)
                            val curMax = BotFormatters.parseBigDecimal(maxAmount)
                            if (curMin != null && (curMax == null || curMax < curMin)) {
                                maxAmount = minAmount
                            }
                        },
                    shape = RoundedCornerShape(10.dp),
                    color = if (rangeMode) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceContainerLow,
                    border = BorderStroke(1.dp, if (rangeMode) MaterialTheme.colorScheme.primary else bot.hairline),
                ) {
                    Text(
                        text = "评分动态区间",
                        modifier = Modifier.padding(vertical = 8.dp, horizontal = 10.dp),
                        style = MaterialTheme.typography.labelLarge,
                        fontWeight = if (rangeMode) FontWeight.Bold else FontWeight.Medium,
                        color = if (rangeMode) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // Quick amount presets
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                listOf("200", "500", "800", "1000", "1500").forEach { preset ->
                    val selected = !rangeMode && minAmount == preset
                    Surface(
                        modifier = Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(8.dp))
                            .clickable {
                                rangeMode = false
                                minAmount = preset
                                maxAmount = preset
                            },
                        shape = RoundedCornerShape(8.dp),
                        color = if (selected) MaterialTheme.colorScheme.primary.copy(alpha = 0.14f) else MaterialTheme.colorScheme.surfaceContainerLow,
                        border = BorderStroke(1.dp, if (selected) MaterialTheme.colorScheme.primary else bot.hairline),
                    ) {
                        Text(
                            text = "$$preset",
                            modifier = Modifier.padding(vertical = 6.dp),
                            style = MaterialTheme.typography.labelSmall.tnum(),
                            fontWeight = FontWeight.SemiBold,
                            color = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
                            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                        )
                    }
                }
            }

            if (!rangeMode) {
                OutlinedTextField(
                    value = minAmount,
                    onValueChange = {
                        minAmount = it
                        maxAmount = it
                    },
                    label = { Text("单笔开仓金额 (USDT)") },
                    suffix = { Text("USDT") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    modifier = Modifier.fillMaxWidth(),
                    shape = MaterialTheme.shapes.small,
                )
            } else {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    OutlinedTextField(
                        value = minAmount,
                        onValueChange = { minAmount = it },
                        label = { Text("单笔最小 (USDT)") },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                        modifier = Modifier.weight(1f),
                        shape = MaterialTheme.shapes.small,
                    )
                    OutlinedTextField(
                        value = maxAmount,
                        onValueChange = { maxAmount = it },
                        label = { Text("单笔最大 (USDT)") },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                        modifier = Modifier.weight(1f),
                        shape = MaterialTheme.shapes.small,
                    )
                }
            }

            // Max Open Position Slots Selector
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = "持仓订单数量上限 (策略槽位)",
                        style = MaterialTheme.typography.labelLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        text = "最多可占用 $${BotFormatters.formatNumber(estExposure.toPlainString(), 0)} USDT",
                        style = MaterialTheme.typography.labelSmall.tnum(),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    (1..5).forEach { slot ->
                        val selected = maxSlots == slot
                        Surface(
                            modifier = Modifier
                                .weight(1f)
                                .clip(RoundedCornerShape(8.dp))
                                .clickable { maxSlots = slot },
                            shape = RoundedCornerShape(8.dp),
                            color = if (selected) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceContainerLow,
                            border = BorderStroke(1.dp, if (selected) MaterialTheme.colorScheme.primary else bot.hairline),
                        ) {
                            Text(
                                text = "${slot}单",
                                modifier = Modifier.padding(vertical = 8.dp),
                                style = MaterialTheme.typography.labelMedium.tnum(),
                                fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium,
                                color = if (selected) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurface,
                                textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                            )
                        }
                    }
                    OutlinedButton(
                        onClick = { if (maxSlots > 1) maxSlots-- },
                        shape = RoundedCornerShape(8.dp),
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 10.dp, vertical = 6.dp),
                    ) {
                        Text("-")
                    }
                    OutlinedButton(
                        onClick = { if (maxSlots < 10) maxSlots++ },
                        shape = RoundedCornerShape(8.dp),
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 10.dp, vertical = 6.dp),
                    ) {
                        Text("+")
                    }
                }
            }

            if (validationError != null) {
                Text(
                    text = validationError,
                    style = MaterialTheme.typography.bodySmall,
                    color = bot.loss,
                )
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                OutlinedButton(
                    onClick = onDismiss,
                    modifier = Modifier.weight(0.36f),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Text("取消")
                }
                Button(
                    onClick = {
                        val finalMin = minAmount.trim()
                        val finalMax = if (rangeMode) maxAmount.trim() else finalMin
                        onSave(finalMin, finalMax, maxSlots)
                        onDismiss()
                    },
                    enabled = validationError == null,
                    modifier = Modifier.weight(0.64f),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Text("保存并同步实盘/模拟")
                }
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}
