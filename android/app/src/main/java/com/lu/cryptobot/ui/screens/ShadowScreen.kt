package com.lu.cryptobot.ui.screens

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ReceiptLong
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.RestartAlt
import androidx.compose.material.icons.outlined.Science
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.lu.cryptobot.ui.components.BotFormatters
import com.lu.cryptobot.ui.components.BotSurfaceCard
import com.lu.cryptobot.ui.components.OrderListItem
import com.lu.cryptobot.ui.components.SectionHeader
import com.lu.cryptobot.ui.components.StatusBadge
import com.lu.cryptobot.ui.components.optCleanString
import com.lu.cryptobot.ui.theme.LocalBotColors
import com.lu.cryptobot.ui.theme.tnum
import org.json.JSONObject

@Composable
fun ShadowScreen(
    values: Map<String, JSONObject>,
    onInspectOrder: (JSONObject) -> Unit,
    onOpenTradingConfig: () -> Unit = {},
    shadowResetBusy: Boolean = false,
    shadowResetMessage: String? = null,
    onResetShadowData: () -> Unit = {},
    onDismissResetMessage: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    val bot = LocalBotColors.current
    val shadow = values["shadow/status"]
    val shadowOrders = values["shadow/orders"]

    val equity = shadow?.optString("equity", "2000.00").orEmpty().ifBlank { "2000.00" }
    val initialCap = shadow?.optString("initial_capital", "2000.00").orEmpty().ifBlank { "2000.00" }
    val pnl = shadow?.optString("pnl", "+0.00").orEmpty().ifBlank { "+0.00" }
    val pnlPct = shadow?.optString("pnl_pct", "+0.00%").orEmpty().ifBlank { "+0.00%" }
    val winRate = shadow?.optString("win_rate", "0.0%").orEmpty().ifBlank { "0.0%" }
    val totalTrades = shadow?.optInt("total_trades", 0) ?: 0
    val rules = shadow?.optJSONObject("rules")
    val tradingConfig = values["trading/config"] ?: shadow?.optJSONObject("trading_config")
    val minOrderUsdt = tradingConfig.optCleanString("min_order_usdt")
        .ifBlank { rules.optCleanString("min_order_usdt", "500") }
    val maxOrderUsdt = tradingConfig.optCleanString("max_order_usdt")
        .ifBlank { rules.optCleanString("max_order_usdt", "1000") }
    val maxPos = tradingConfig?.optInt("max_slots", 0)?.takeIf { it > 0 }
        ?: rules?.optInt("max_positions", 2)?.takeIf { it > 0 } ?: 2
    val orderRangeText = BotFormatters.formatOrderRange(minOrderUsdt, maxOrderUsdt)
    val activePosCount = shadow?.optInt("active_positions", 0) ?: 0
    val feeLabel = shadow?.optString("fee_rate_label", "0.075%").orEmpty().ifBlank { "0.075%" }
    val bnbCap = shadow?.optString("bnb_cap_usdt", "20").orEmpty().ifBlank { "20" }
    val positionsArr = shadow?.optJSONArray("positions")
    val ordersArr = shadowOrders?.optJSONArray("items")

    val negPnl = BotFormatters.isNegative(pnl)
    var rulesExpanded by remember { mutableStateOf(false) }
    var showResetConfirm by remember { mutableStateOf(false) }

    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (!shadowResetMessage.isNullOrBlank()) {
            val isResetErr = shadowResetMessage.startsWith("重置失败")
            Surface(
                shape = MaterialTheme.shapes.medium,
                color = if (isResetErr) bot.lossSoft else bot.infoSoft,
                border = BorderStroke(1.dp, if (isResetErr) bot.loss.copy(alpha = 0.25f) else bot.info.copy(alpha = 0.25f)),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = shadowResetMessage,
                        style = MaterialTheme.typography.bodySmall,
                        color = if (isResetErr) bot.loss else bot.info,
                        modifier = Modifier.weight(1f).padding(end = 8.dp),
                    )
                    Text(
                        text = "知道了",
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.Bold,
                        color = if (isResetErr) bot.loss else bot.info,
                        modifier = Modifier.clickable { onDismissResetMessage() }.padding(4.dp),
                    )
                }
            }
        }
        if (shadow?.optBoolean("synced", false) != true) {
            val healthMessage = when (shadow?.optString("reason")) {
                "ENGINE_HEARTBEAT_STALE" -> "影子执行服务心跳已过期"
                "ENGINE_ERROR" -> "影子执行服务故障，请检查后台错误记录"
                "SHADOW_QUOTE_STALE" -> "影子账本行情已过期"
                "ENGINE_NOT_READY" -> "影子执行服务等待行情或执行条件恢复"
                else -> "影子服务状态尚未确认"
            }
            Text(healthMessage, color = MaterialTheme.colorScheme.error)
        }
        val r2Obj = shadow?.optJSONObject("r2")
        val r2Synced = r2Obj?.optBoolean("synced", false) == true
        val r2Stage = when (r2Obj?.optJSONObject("candidate")?.optString("state")) {
            "WATCHING" -> "观察底部"
            "ARMED" -> "等待突破"
            "TRIGGERED" -> "入场意图待处理"
            "CONSUMED" -> "本次候选已处理"
            "EXPIRED" -> "候选已过期"
            "INVALIDATED" -> "候选已失效"
            else -> "等待超卖候选"
        }
        val r2Equity = r2Obj?.optJSONObject("shadow")?.optString("last_equity").orEmpty()

        // 1. Unified Shadow-bot Card (R2 status strip + Equity 2x2 grid + Collapsible BNB/Rules drawer)
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .animateContentSize(),
            shape = MaterialTheme.shapes.large,
            color = MaterialTheme.colorScheme.surface,
            border = BorderStroke(1.dp, bot.hairline),
        ) {
            Column(modifier = Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Icon(Icons.Outlined.Science, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(17.dp))
                        Text("Shadow-bot", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        StatusBadge("影子沙盒", bot.infoSoft, bot.info)
                    }
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Surface(
                            modifier = Modifier
                                .clip(RoundedCornerShape(8.dp))
                                .clickable(enabled = !shadowResetBusy) { showResetConfirm = true },
                            shape = RoundedCornerShape(8.dp),
                            color = bot.lossSoft,
                        ) {
                            Row(
                                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(4.dp),
                            ) {
                                if (shadowResetBusy) {
                                    CircularProgressIndicator(
                                        modifier = Modifier.size(12.dp),
                                        strokeWidth = 1.6.dp,
                                        color = bot.loss,
                                    )
                                } else {
                                    Icon(Icons.Outlined.RestartAlt, null, tint = bot.loss, modifier = Modifier.size(13.dp))
                                }
                                Text(
                                    text = if (shadowResetBusy) "重置中" else "重置",
                                    style = MaterialTheme.typography.labelSmall,
                                    fontWeight = FontWeight.Bold,
                                    color = bot.loss,
                                )
                            }
                        }
                        Surface(
                            modifier = Modifier
                                .clip(RoundedCornerShape(8.dp))
                                .clickable { onOpenTradingConfig() },
                            shape = RoundedCornerShape(8.dp),
                            color = MaterialTheme.colorScheme.primaryContainer,
                        ) {
                            Row(
                                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(4.dp),
                            ) {
                                Icon(Icons.Outlined.Tune, null, tint = MaterialTheme.colorScheme.onPrimaryContainer, modifier = Modifier.size(13.dp))
                                Text(
                                    text = "配置",
                                    style = MaterialTheme.typography.labelSmall,
                                    fontWeight = FontWeight.Bold,
                                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                                )
                            }
                        }
                    }
                }

                Spacer(Modifier.height(6.dp))

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = "$${BotFormatters.formatNumber(equity, 2)}",
                        style = MaterialTheme.typography.headlineMedium.tnum(),
                        fontWeight = FontWeight.Bold,
                    )
                    StatusBadge(
                        text = "${BotFormatters.formatSignedUsdt(pnl)} (${BotFormatters.formatSignedPct(pnlPct)})",
                        containerColor = if (negPnl) bot.lossSoft else bot.gainSoft,
                        contentColor = if (negPnl) bot.loss else bot.gain,
                    )
                }

                Spacer(Modifier.height(8.dp))

                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        ShadowCompactMetricTile(
                            label = "初始本金",
                            value = "$${BotFormatters.formatNumber(initialCap, 2)}",
                            modifier = Modifier.weight(1f),
                        )
                        ShadowCompactMetricTile(
                            label = "已结胜率",
                            value = winRate,
                            modifier = Modifier.weight(1f),
                        )
                    }
                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        ShadowCompactMetricTile(
                            label = "累计成交",
                            value = "$totalTrades 笔",
                            modifier = Modifier.weight(1f),
                        )
                        ShadowCompactMetricTile(
                            label = "活跃仓位",
                            value = "$activePosCount / $maxPos 槽",
                            modifier = Modifier.weight(1f),
                        )
                    }
                }

                if (r2Obj != null) {
                    Spacer(Modifier.height(6.dp))
                    Surface(
                        shape = MaterialTheme.shapes.small,
                        color = MaterialTheme.colorScheme.surfaceContainerLow,
                        border = BorderStroke(1.dp, bot.hairline),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
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
                                        .background(if (r2Synced) bot.info else MaterialTheme.colorScheme.error),
                                )
                                Text(
                                    text = "R2 独立盘 · ${if (r2Synced) r2Stage else "服务状态待恢复"}",
                                    fontSize = 11.sp,
                                    lineHeight = 14.sp,
                                    color = if (r2Synced) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.error,
                                    fontWeight = FontWeight.Medium,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                            if (r2Equity.isNotBlank()) {
                                Text(
                                    text = "权益 $${BotFormatters.formatNumber(r2Equity, 2)}",
                                    fontSize = 11.sp,
                                    lineHeight = 14.sp,
                                    style = MaterialTheme.typography.labelSmall.tnum(),
                                    fontWeight = FontWeight.SemiBold,
                                    color = MaterialTheme.colorScheme.onSurface,
                                    maxLines = 1,
                                    softWrap = false,
                                )
                            }
                        }
                    }
                }

                Spacer(Modifier.height(6.dp))

                Surface(
                    shape = MaterialTheme.shapes.small,
                    color = MaterialTheme.colorScheme.surfaceContainerLow,
                    border = BorderStroke(1.dp, bot.hairline),
                    onClick = { rulesExpanded = !rulesExpanded },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(6.dp),
                                modifier = Modifier.weight(1f),
                            ) {
                                Icon(Icons.AutoMirrored.Outlined.ReceiptLong, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(15.dp))
                                Text(
                                    text = "BNB $feeLabel · 单笔 $orderRangeText · ${maxPos}槽 · 同步实盘",
                                    fontSize = 11.sp,
                                    lineHeight = 14.sp,
                                    style = MaterialTheme.typography.labelSmall.tnum(),
                                    fontWeight = FontWeight.Medium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                            Icon(
                                if (rulesExpanded) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore,
                                contentDescription = "展开规则",
                                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.size(16.dp),
                            )
                        }
                        if (rulesExpanded) {
                            Spacer(Modifier.height(6.dp))
                            HorizontalDivider(color = bot.hairline)
                            Spacer(Modifier.height(6.dp))
                            Text(
                                text = "• 当前同步规则：单笔开仓 $orderRangeText USDT，最多同时持仓 $maxPos 个策略槽位（与 Trading-bot 实盘配置同步）。",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurface,
                                fontWeight = FontWeight.Medium,
                            )
                            Spacer(Modifier.height(4.dp))
                            Text(
                                text = "• 模拟撮合使用真实盘口 Bid/Ask，扣除 $feeLabel BNB 等效手续费（储备上限 $${bnbCap} USDT，独立 SQLite 账本持久化）。",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Spacer(Modifier.height(4.dp))
                            Text(
                                text = "• 与实盘共享 T1/T2/R2 信号（跨策略 15m 冷却与槽位上限同步）及 APTS v2 移动止损状态机（S0 初始保护 → S1 +0.8% 保本 / +1.1% 动态锁盈 → S2 +1.2%+ 追尾与超买衰竭 0.40% 收紧，未触发止损一直持仓）。",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Spacer(Modifier.height(4.dp))
                            TextButton(
                                onClick = onOpenTradingConfig,
                                modifier = Modifier.align(Alignment.End),
                            ) {
                                Icon(Icons.Outlined.Tune, null, modifier = Modifier.size(15.dp))
                                Spacer(Modifier.size(4.dp))
                                Text("调整实盘与 Shadow 交易配置", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
                            }
                        }
                    }
                }
            }
        }

        // 3. Open Shadow Positions (Rich APTS + Unrealized Mark PnL)
        SectionHeader(
            title = "当前虚拟持仓",
            subtitle = if (activePosCount > 0) "按实时影子买一价估值" else "空仓巡检中",
        )

        if (positionsArr == null || positionsArr.length() == 0) {
            Surface(
                shape = MaterialTheme.shapes.medium,
                color = MaterialTheme.colorScheme.surfaceContainerLow,
                border = BorderStroke(1.dp, bot.hairline),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Icon(Icons.Outlined.CheckCircle, null, tint = bot.gain, modifier = Modifier.size(18.dp))
                    Text(
                        text = "当前暂无虚拟持仓，触发策略入场信号后将在此实时跟踪。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                for (i in 0 until positionsArr.length()) {
                    val pos = positionsArr.optJSONObject(i) ?: continue
                    val uPnl = pos.optString("unrealized_pnl")
                    val uPct = pos.optString("unrealized_pnl_pct")
                    val isNeg = BotFormatters.isNegative(uPnl)
                    val stage = BotFormatters.stageLabel(pos.optString("stage"))
                    val stopP = pos.optString("stop_price")
                    val hits = pos.optInt("reversal_hits", 0)
                    val maxHits = pos.optInt("max_reversal_hits", 2)
                    val stagMs = pos.optLong("stagnation_deadline_ms", 0L)

                    BotSurfaceCard {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                Text(pos.optString("symbol", "SOLUSDT"), style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                                StatusBadge(pos.optString("strategy_id", "T1"), bot.infoSoft, bot.info)
                                StatusBadge(stage, bot.gainSoft, bot.gain)
                            }
                            if (uPnl.isNotBlank()) {
                                Text(
                                    text = "${BotFormatters.formatSignedUsdt(uPnl)} ($uPct)",
                                    style = MaterialTheme.typography.titleSmall.tnum(),
                                    color = if (isNeg) bot.loss else bot.gain,
                                    fontWeight = FontWeight.Bold,
                                )
                            }
                        }
                        Spacer(Modifier.height(6.dp))
                        Text(
                            text = "开仓 $${BotFormatters.formatCryptoPrice(pos.optString("entry_price"))} · 现价 $${BotFormatters.formatCryptoPrice(pos.optString("mark_price"))} · ${BotFormatters.formatPlainAmount(pos.optString("quantity"))} SOL",
                            style = MaterialTheme.typography.bodySmall.tnum(),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.height(6.dp))
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                        ) {
                            Text(
                                text = if (stopP.isNotBlank()) "保护止损 $${BotFormatters.formatCryptoPrice(stopP)}" else "APTS S0 初始保护",
                                style = MaterialTheme.typography.labelSmall.tnum(),
                                color = bot.loss,
                                fontWeight = FontWeight.SemiBold,
                            )
                            Text(
                                text = "回撤触发 $hits/$maxHits · 未止损持续持仓",
                                style = MaterialTheme.typography.labelSmall.tnum(),
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        }

        // 4. Shadow Execution & Reconciliation Orders
        SectionHeader(
            title = "影子交易与对账订单",
            subtitle = if (ordersArr != null && ordersArr.length() > 0) "最近 ${ordersArr.length()} 条虚拟成交流水" else "沙盒执行记录",
        )

        if (ordersArr == null || ordersArr.length() == 0) {
            Surface(
                shape = MaterialTheme.shapes.medium,
                color = MaterialTheme.colorScheme.surfaceContainerLow,
                border = BorderStroke(1.dp, bot.hairline),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Row(
                    modifier = Modifier.padding(14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Icon(Icons.AutoMirrored.Outlined.ReceiptLong, null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(18.dp))
                    Text("尚未产生影子模拟委托记录", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        } else {
            val cap = minOf(20, ordersArr.length())
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                for (i in 0 until cap) {
                    val ord = ordersArr.optJSONObject(i) ?: continue
                    OrderListItem(order = ord, isBotOrder = true, onClick = { onInspectOrder(ord) })
                }
            }
        }
        Spacer(Modifier.height(12.dp))
    }

    if (showResetConfirm) {
        AlertDialog(
            onDismissRequest = { showResetConfirm = false },
            title = {
                Text("重置交易数据", fontWeight = FontWeight.Bold)
            },
            text = {
                Text(
                    text = "将清除 Shadow-bot 历史委托、模拟持仓、成交流水、保护状态及累计盈亏，并按当前仓位配置重新校准模拟本金（实盘账户、K 线与策略历史不受影响）。此操作不可撤销，确认继续？",
                    style = MaterialTheme.typography.bodySmall,
                )
            },
            confirmButton = {
                Button(
                    onClick = {
                        showResetConfirm = false
                        onResetShadowData()
                    },
                    colors = ButtonDefaults.buttonColors(
                        containerColor = bot.loss,
                        contentColor = MaterialTheme.colorScheme.onError,
                    ),
                ) {
                    Text("重置交易数据")
                }
            },
            dismissButton = {
                TextButton(onClick = { showResetConfirm = false }) {
                    Text("取消")
                }
            },
        )
    }
}

@Composable
private fun ShadowCompactMetricTile(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier,
        shape = MaterialTheme.shapes.small,
        color = MaterialTheme.colorScheme.surfaceContainerLow,
        border = BorderStroke(1.dp, LocalBotColors.current.hairline),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 9.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = label,
                fontSize = 11.sp,
                lineHeight = 14.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier
                    .weight(1f, fill = false)
                    .padding(end = 4.dp),
            )
            Text(
                text = value,
                style = MaterialTheme.typography.labelLarge.tnum(),
                fontSize = 12.5.sp,
                lineHeight = 16.sp,
                color = MaterialTheme.colorScheme.onSurface,
                fontWeight = FontWeight.Bold,
                maxLines = 1,
                softWrap = false,
            )
        }
    }
}
