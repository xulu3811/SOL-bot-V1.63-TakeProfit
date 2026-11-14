package com.lu.cryptobot.ui.screens

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ReceiptLong
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.Science
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.lu.cryptobot.ui.components.BotFormatters
import com.lu.cryptobot.ui.components.BotSurfaceCard
import com.lu.cryptobot.ui.components.MetricBentoTile
import com.lu.cryptobot.ui.components.OrderListItem
import com.lu.cryptobot.ui.components.SectionHeader
import com.lu.cryptobot.ui.components.StatusBadge
import com.lu.cryptobot.ui.theme.LocalBotColors
import com.lu.cryptobot.ui.theme.tnum
import org.json.JSONObject

@Composable
fun ShadowScreen(
    values: Map<String, JSONObject>,
    onInspectOrder: (JSONObject) -> Unit,
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
    val maxPos = rules?.optInt("max_positions", 2) ?: 2
    val activePosCount = shadow?.optInt("active_positions", 0) ?: 0
    val feeLabel = shadow?.optString("fee_rate_label", "0.075%").orEmpty().ifBlank { "0.075%" }
    val bnbCap = shadow?.optString("bnb_cap_usdt", "20").orEmpty().ifBlank { "20" }
    val positionsArr = shadow?.optJSONArray("positions")
    val ordersArr = shadowOrders?.optJSONArray("items")

    val negPnl = BotFormatters.isNegative(pnl)
    var rulesExpanded by remember { mutableStateOf(false) }

    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
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
        // 1. Equity Hero Card with 2x2 Zero-Truncation Bento Grid
        shadow?.optJSONObject("r2")?.let { r2 ->
            val state = r2.optJSONObject("candidate")?.optString("state")
            val stage = when (state) {
                "WATCHING" -> "观察底部"
                "ARMED" -> "等待突破"
                "TRIGGERED" -> "入场意图待处理"
                "CONSUMED" -> "本次候选已处理"
                "EXPIRED" -> "候选已过期"
                "INVALIDATED" -> "候选已失效"
                else -> "等待超卖候选"
            }
            BotSurfaceCard {
                Text("R2 反弹 · 独立影子盘", style = MaterialTheme.typography.titleMedium)
                Text(if (r2.optBoolean("synced", false)) stage else "服务状态待恢复",
                    color = if (r2.optBoolean("synced", false)) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.error)
                r2.optJSONObject("shadow")?.let { account ->
                    Text("虚拟权益 $${BotFormatters.formatNumber(account.optString("last_equity"), 2)}")
                }
            }
        }
        BotSurfaceCard {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Icon(Icons.Outlined.Science, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(18.dp))
                    Text("Shadow-bot", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    StatusBadge("影子沙盒账本", bot.infoSoft, bot.info)
                }
                StatusBadge("与实盘同源行情", bot.gainSoft, bot.gain)
            }

            Spacer(Modifier.height(10.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.Bottom,
            ) {
                Text(
                    text = "$${BotFormatters.formatNumber(equity, 2)}",
                    style = MaterialTheme.typography.displaySmall.tnum(),
                    fontWeight = FontWeight.Bold,
                )
                StatusBadge(
                    text = "${BotFormatters.formatSignedUsdt(pnl)} (${BotFormatters.formatSignedPct(pnlPct)})",
                    containerColor = if (negPnl) bot.lossSoft else bot.gainSoft,
                    contentColor = if (negPnl) bot.loss else bot.gain,
                )
            }

            Spacer(Modifier.height(12.dp))

            // 2x2 Bento Grid guarantees zero ellipsis truncation at 360dp widths
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    MetricBentoTile(
                        label = "初始虚拟本金",
                        value = "$${BotFormatters.formatNumber(initialCap, 2)}",
                        modifier = Modifier.weight(1f),
                    )
                    MetricBentoTile(
                        label = "已结算胜率",
                        value = winRate,
                        modifier = Modifier.weight(1f),
                    )
                }
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    MetricBentoTile(
                        label = "累计虚拟成交",
                        value = "$totalTrades 笔",
                        modifier = Modifier.weight(1f),
                    )
                    MetricBentoTile(
                        label = "活跃虚拟仓位",
                        value = "$activePosCount / $maxPos 槽位",
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }

        // 2. Compact BNB Fee & Simulation Rules Strip
        BotSurfaceCard(
            modifier = Modifier.animateContentSize(),
            containerColor = MaterialTheme.colorScheme.surfaceContainerLow,
            onClick = { rulesExpanded = !rulesExpanded },
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.weight(1f),
                ) {
                    Icon(Icons.AutoMirrored.Outlined.ReceiptLong, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(18.dp))
                    Column {
                        Text("BNB $feeLabel 抵扣与沙盒参数", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                        Text(
                            text = "单笔 $500–$1000 · T1/T2/R2 · 未止损长持",
                            style = MaterialTheme.typography.labelSmall.tnum(),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                Icon(
                    if (rulesExpanded) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore,
                    contentDescription = "展开规则",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (rulesExpanded) {
                Spacer(Modifier.height(8.dp))
                HorizontalDivider(color = bot.hairline)
                Spacer(Modifier.height(8.dp))
                Text(
                    text = "• 模拟撮合使用真实盘口 Bid/Ask，扣除 $feeLabel BNB 等效手续费（储备上限 $${bnbCap} USDT，独立 SQLite 账本持久化）。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    text = "• 与实盘共享 T1/T2/R2 信号及 APTS v2 移动止损状态机（S0 初始保护 → S1 +1.0% 保本 → S2 +1.5%+ 双 tick 追踪，未触发止损一直持仓）。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
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
}
