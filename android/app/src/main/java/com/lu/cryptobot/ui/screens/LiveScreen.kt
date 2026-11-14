package com.lu.cryptobot.ui.screens

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.BorderStroke
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Key
import androidx.compose.material.icons.outlined.Shield
import androidx.compose.material.icons.outlined.Sync
import androidx.compose.material.icons.outlined.WarningAmber
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.lu.cryptobot.ui.components.AptsTimelineCard
import com.lu.cryptobot.ui.components.BotFormatters
import com.lu.cryptobot.ui.components.BotSurfaceCard
import com.lu.cryptobot.ui.components.OrderListItem
import com.lu.cryptobot.ui.components.StatusBadge
import com.lu.cryptobot.ui.components.optCleanString
import com.lu.cryptobot.ui.theme.LocalBotColors
import com.lu.cryptobot.ui.theme.tnum
import org.json.JSONArray
import org.json.JSONObject

@Composable
fun LiveScreen(
    liveState: String,
    values: Map<String, JSONObject>,
    onRequestLiveToggle: (targetOn: Boolean) -> Unit,
    onOpenApiConfig: () -> Unit,
    onSyncBinance: () -> Unit,
    onRequestCustody: (position: JSONObject, enabling: Boolean) -> Unit,
    onInspectOrder: (order: JSONObject) -> Unit,
    modifier: Modifier = Modifier,
) {
    val bot = LocalBotColors.current
    val liveStatus = values["live/status"]
    val liveRisk = values["live/risk"]
    val livePositions = values["live/positions"]
    val liveOrders = values["live/orders"]
    val binanceAccount = values["binance/account"]

    val configured = binanceAccount?.optBoolean("configured", false) ?: false
    val fingerprint = binanceAccount.optCleanString("key_fingerprint")
    val readiness = liveStatus?.optJSONObject("readiness") ?: liveRisk?.optJSONObject("readiness")
    val spotEnabled = binanceAccount?.optBoolean(
        "orders_enabled",
        readiness?.optBoolean("orders_enabled", false) ?: false,
    ) ?: false

    val accountObj = livePositions?.optJSONObject("account") ?: binanceAccount?.optJSONObject("balances")
    val usdtObj = accountObj?.optJSONObject("USDT")
    val bnbObj = accountObj?.optJSONObject("BNB")
    val solObj = accountObj?.optJSONObject("SOL")
    val freeUsdt = readiness.optCleanString("free_usdt").ifBlank { usdtObj.optCleanString("free") }
    val blocker = readiness.optCleanString("blocker")
    val guardianCap = liveStatus.optCleanString("guardian_cap_usdt", "2000")
    val drawdownPctStr = liveRisk.optCleanString("drawdown_pct", "0.00")
    val hardLimitPctStr = liveRisk.optCleanString("hard_limit_pct", "5.00")
    val drawdownNum = drawdownPctStr.toFloatOrNull() ?: 0f
    val hardLimitNum = (hardLimitPctStr.toFloatOrNull() ?: 5f).coerceAtLeast(0.1f)

    val slotsArr = livePositions?.optJSONArray("slots")
    val manualArr = livePositions?.optJSONArray("manual")
    val botOrdersArr = liveOrders?.optJSONArray("bot_orders")
    val manualOrdersArr = liveOrders?.optJSONArray("manual_orders") ?: livePositions?.optJSONArray("manual_orders")

    var selectedTab by remember { mutableIntStateOf(0) }
    var riskExpanded by remember { mutableStateOf(false) }

    val (heroBg, heroBorder, statusText, statusFg) = when (liveState) {
        "LIVE_ON" -> Quad4(bot.gainSoft.copy(alpha = 0.55f), bot.gain.copy(alpha = 0.35f), "实盘自动运行中", bot.gain)
        "HARD_LOCK" -> Quad4(bot.lossSoft.copy(alpha = 0.65f), bot.loss.copy(alpha = 0.45f), "硬风控熔断锁定", bot.loss)
        "LIVE_OFF" -> Quad4(MaterialTheme.colorScheme.surfaceContainerLow, bot.hairline, "实盘暂停（安全只读）", MaterialTheme.colorScheme.onSurface)
        else -> Quad4(bot.warnSoft.copy(alpha = 0.45f), bot.hairline, "状态同步中", bot.warn)
    }

    val riskDanger = drawdownNum >= hardLimitNum * 0.7f
    val riskFg = if (riskDanger) bot.loss else bot.gain
    val riskBg = if (riskDanger) bot.lossSoft else bot.gainSoft
    val blockerMessage = BotFormatters.blockerLabel(blocker, freeUsdt)

    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        // 1. Account & Real Vault Assets + Embedded Compact Risk Guardrail Hero Card
        BotSurfaceCard(
            modifier = Modifier.animateContentSize(),
            containerColor = heroBg,
            borderColor = heroBorder,
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Text(
                            text = statusText,
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            color = statusFg,
                        )
                        StatusBadge(
                            text = if (!configured) "未绑定API" else if (spotEnabled) "现货可下单" else "仅只读",
                            containerColor = MaterialTheme.colorScheme.surface,
                            contentColor = if (!configured) bot.warn else if (spotEnabled) bot.gain else bot.warn,
                        )
                    }
                    Text(
                        text = buildString {
                            append("Guardian UID · ")
                            append(liveStatus?.optString("account_id", "SOL-live") ?: "SOL-live")
                            if (fingerprint.isNotBlank()) append(" · 尾号 ${fingerprint.takeLast(4)}")
                        },
                        style = MaterialTheme.typography.labelSmall.tnum(),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }

                Switch(
                    checked = liveState == "LIVE_ON",
                    onCheckedChange = { onRequestLiveToggle(it) },
                    colors = SwitchDefaults.colors(
                        checkedThumbColor = MaterialTheme.colorScheme.surface,
                        checkedTrackColor = bot.gain,
                    ),
                )
            }

            Spacer(Modifier.height(9.dp))

            // 3 Real Exchange Asset Cards (USDT / BNB / SOL)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                VaultAssetTile(
                    asset = "USDT",
                    free = BotFormatters.formatNumber(usdtObj?.optString("free"), 2),
                    locked = usdtObj?.optString("locked"),
                    sublabel = "可用流动资金",
                    accent = bot.gain,
                    modifier = Modifier.weight(1f),
                )
                VaultAssetTile(
                    asset = "BNB",
                    free = BotFormatters.formatPlainAmount(bnbObj?.optString("free"), 4),
                    locked = bnbObj?.optString("locked"),
                    sublabel = "手续费储备",
                    accent = bot.warn,
                    modifier = Modifier.weight(1f),
                )
                VaultAssetTile(
                    asset = "SOL",
                    free = BotFormatters.formatPlainAmount(solObj?.optString("free"), 4),
                    locked = solObj?.optString("locked"),
                    sublabel = "现货持仓",
                    accent = bot.info,
                    modifier = Modifier.weight(1f),
                )
            }

            if (blockerMessage != null) {
                Spacer(Modifier.height(8.dp))
                Surface(
                    shape = MaterialTheme.shapes.small,
                    color = if (blocker == "GUARDIAN_HARD_LOCK" || blocker == "SPOT_PERMISSION_OFF") bot.lossSoft else bot.warnSoft,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 7.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Icon(
                            Icons.Outlined.WarningAmber,
                            contentDescription = null,
                            tint = if (blocker == "GUARDIAN_HARD_LOCK" || blocker == "SPOT_PERMISSION_OFF") bot.loss else bot.warn,
                            modifier = Modifier.size(16.dp),
                        )
                        Text(
                            text = blockerMessage,
                            style = MaterialTheme.typography.labelSmall.tnum(),
                            color = if (blocker == "GUARDIAN_HARD_LOCK" || blocker == "SPOT_PERMISSION_OFF") bot.loss else bot.warn,
                            fontWeight = FontWeight.SemiBold,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }

            Spacer(Modifier.height(9.dp))

            // Compact Risk Guard Mini Icon Card + API & Sync Actions Row
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Surface(
                    modifier = Modifier
                        .weight(1f)
                        .clip(MaterialTheme.shapes.small)
                        .clickable { riskExpanded = !riskExpanded },
                    shape = MaterialTheme.shapes.small,
                    color = MaterialTheme.colorScheme.surface,
                    border = BorderStroke(1.dp, if (riskDanger) bot.loss.copy(alpha = 0.4f) else bot.hairline),
                ) {
                    Column(
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 6.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            Surface(
                                modifier = Modifier.size(24.dp),
                                shape = RoundedCornerShape(7.dp),
                                color = riskBg,
                            ) {
                                Box(contentAlignment = Alignment.Center) {
                                    Icon(
                                        Icons.Outlined.Shield,
                                        contentDescription = "日内风控",
                                        tint = riskFg,
                                        modifier = Modifier.size(14.dp),
                                    )
                                }
                            }
                            Column(modifier = Modifier.weight(1f)) {
                                Text(
                                    text = "风控 $drawdownPctStr%/$hardLimitPctStr%",
                                    style = MaterialTheme.typography.labelSmall.tnum(),
                                    fontWeight = FontWeight.Bold,
                                    color = riskFg,
                                    maxLines = 1,
                                    softWrap = false,
                                )
                                Text(
                                    text = "2槽 · $500–1000",
                                    fontSize = 10.sp,
                                    lineHeight = 12.sp,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    style = MaterialTheme.typography.labelSmall.tnum(),
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                        }
                        LinearProgressIndicator(
                            progress = { (drawdownNum / hardLimitNum).coerceIn(0f, 1f) },
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(3.dp)
                                .clip(RoundedCornerShape(999.dp)),
                            color = riskFg,
                            trackColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                        )
                    }
                }

                FilledTonalButton(
                    onClick = onOpenApiConfig,
                    shape = RoundedCornerShape(10.dp),
                    contentPadding = PaddingValues(horizontal = 10.dp, vertical = 0.dp),
                    modifier = Modifier.height(44.dp),
                ) {
                    Icon(Icons.Outlined.Key, null, modifier = Modifier.size(15.dp))
                    Spacer(Modifier.size(4.dp))
                    Text(
                        text = if (configured) "API" else "绑定",
                        style = MaterialTheme.typography.labelMedium,
                        maxLines = 1,
                        softWrap = false,
                    )
                }
                OutlinedButton(
                    onClick = onSyncBinance,
                    shape = RoundedCornerShape(10.dp),
                    contentPadding = PaddingValues(horizontal = 10.dp, vertical = 0.dp),
                    modifier = Modifier.height(44.dp),
                ) {
                    Icon(Icons.Outlined.Sync, null, modifier = Modifier.size(15.dp))
                    Spacer(Modifier.size(4.dp))
                    Text(
                        text = "同步",
                        style = MaterialTheme.typography.labelMedium,
                        maxLines = 1,
                        softWrap = false,
                    )
                }
            }

            if (riskExpanded) {
                Spacer(Modifier.height(6.dp))
                Surface(
                    shape = MaterialTheme.shapes.small,
                    color = MaterialTheme.colorScheme.surface.copy(alpha = 0.85f),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(
                        text = "日内回撤 $drawdownPctStr% / 熔断 $hardLimitPctStr% · 单笔 $500–$1000 动态定仓(不足$1000按可用全额·低于$500拒开) · 上限 2 个策略槽位 · 风控参考额度 $$guardianCap USDT(不含手动亏损)",
                        modifier = Modifier.padding(horizontal = 9.dp, vertical = 6.dp),
                        style = MaterialTheme.typography.labelSmall.tnum(),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        // 2. APTS v2 Ladder (Collapsed Drawer by Default)
        val firstManual = manualArr?.optJSONObject(0)
        val firstManualProt = firstManual?.optJSONObject("protection")
        val manualActive = firstManualProt?.optBoolean("active", false) == true || firstManual?.optBoolean("custody_on", false) == true
        val firstSlot = slotsArr?.optJSONObject(0)
        val aptsObj = firstManualProt?.optJSONObject("apts") ?: firstManual?.optJSONObject("apts") ?: firstSlot?.optJSONObject("apts")
        AptsTimelineCard(
            activeStage = firstManualProt?.optString("stage")?.ifBlank { null }
                ?: firstManual?.optString("stage")?.ifBlank { null }
                ?: firstSlot?.optString("stage"),
            isManualCustody = manualActive,
            apts = aptsObj,
        )

        // 4. Segmented Tabs: Positions / Bot Orders / Manual Trades
        val posCount = (slotsArr?.length() ?: 0) + (manualArr?.length() ?: 0)
        val botOrdCount = botOrdersArr?.length() ?: 0
        val manOrdCount = manualOrdersArr?.length() ?: 0
        Surface(
            shape = MaterialTheme.shapes.medium,
            color = MaterialTheme.colorScheme.surfaceContainer,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(4.dp),
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                listOf(
                    "持仓托管 ($posCount)",
                    "Bot委托 ($botOrdCount)",
                    "手动成交 ($manOrdCount)",
                ).forEachIndexed { idx, label ->
                    val selected = idx == selectedTab
                    Surface(
                        modifier = Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(10.dp))
                            .clickable { selectedTab = idx },
                        shape = RoundedCornerShape(10.dp),
                        color = if (selected) MaterialTheme.colorScheme.surface else MaterialTheme.colorScheme.surfaceContainer,
                    ) {
                        Box(modifier = Modifier.padding(vertical = 8.dp), contentAlignment = Alignment.Center) {
                            Text(
                                text = label,
                                style = MaterialTheme.typography.labelMedium.tnum(),
                                fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium,
                                color = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                }
            }
        }

        when (selectedTab) {
            0 -> LivePositionsSection(
                slots = slotsArr,
                manual = manualArr,
                onRequestCustody = onRequestCustody,
            )
            1 -> OrdersFeedSection(
                orders = botOrdersArr,
                isBot = true,
                emptyText = "暂无机器人自动下单记录，触发 T1/T2/R2 高分信号且可用 USDT ≥ $500 时将自动挂单。",
                onInspectOrder = onInspectOrder,
            )
            else -> OrdersFeedSection(
                orders = manualOrdersArr,
                isBot = false,
                emptyText = "暂无同步的手动币安成交流水。",
                onInspectOrder = onInspectOrder,
            )
        }
        Spacer(Modifier.height(12.dp))
    }
}

@Composable
private fun VaultAssetTile(
    asset: String,
    free: String,
    locked: String?,
    sublabel: String,
    accent: androidx.compose.ui.graphics.Color,
    modifier: Modifier = Modifier,
) {
    val hasLocked = (BotFormatters.parseBigDecimal(locked)?.toDouble() ?: 0.0) > 0.0
    Surface(
        modifier = modifier,
        shape = MaterialTheme.shapes.small,
        color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(1.dp, LocalBotColors.current.hairline),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 7.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            StatusBadge(
                text = asset,
                containerColor = accent.copy(alpha = 0.14f),
                contentColor = accent,
            )
            Text(
                text = free,
                style = MaterialTheme.typography.titleMedium.tnum(),
                fontWeight = FontWeight.Bold,
                maxLines = 1,
                softWrap = false,
            )
            Text(
                text = if (hasLocked) "冻结 ${BotFormatters.formatPlainAmount(locked, 4)}" else sublabel,
                style = MaterialTheme.typography.labelSmall.tnum(),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
private fun LivePositionsSection(
    slots: JSONArray?,
    manual: JSONArray?,
    onRequestCustody: (JSONObject, Boolean) -> Unit,
) {
    val bot = LocalBotColors.current
    val hasSlots = slots != null && slots.length() > 0
    val hasManual = manual != null && manual.length() > 0
    if (!hasSlots && !hasManual) {
        Surface(
            shape = MaterialTheme.shapes.medium,
            color = MaterialTheme.colorScheme.surfaceContainerLow,
            border = BorderStroke(1.dp, bot.hairline),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Row(
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 14.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Icon(Icons.Outlined.CheckCircle, null, tint = bot.gain, modifier = Modifier.size(20.dp))
                Column {
                    Text("当前暂无实盘持仓", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    Text("Guardian 持续对账中，满足条件后自动介入或保护手动仓位。", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        return
    }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (hasSlots) {
            for (i in 0 until slots!!.length()) {
                val slot = slots.optJSONObject(i) ?: continue
                val pnl = slot.optCleanString("pnl_usdt")
                val pct = slot.optCleanString("change_pct")
                val neg = BotFormatters.isNegative(pnl)
                BotSurfaceCard {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            StatusBadge("策略仓 #${slot.optInt("slot", i + 1)}", bot.infoSoft, bot.info)
                            StatusBadge(slot.optCleanString("strategy_id", "T1"), MaterialTheme.colorScheme.surfaceContainerHigh, MaterialTheme.colorScheme.onSurface)
                            StatusBadge(BotFormatters.stageLabel(slot.optCleanString("stage")), bot.gainSoft, bot.gain)
                        }
                        Text(
                            text = if (pnl.isNotBlank()) "${BotFormatters.formatSignedUsdt(pnl)}${if (pct.isNotBlank()) " ($pct)" else ""}" else "—",
                            style = MaterialTheme.typography.titleSmall.tnum(),
                            color = if (neg) bot.loss else bot.gain,
                            fontWeight = FontWeight.Bold,
                        )
                    }
                    Spacer(Modifier.height(8.dp))
                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(
                            text = "开仓 $${BotFormatters.formatCryptoPrice(slot.optCleanString("entry_price"))} · 现价 $${BotFormatters.formatCryptoPrice(slot.optCleanString("mark_price"))}",
                            style = MaterialTheme.typography.bodySmall.tnum(),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Text(
                            text = "${BotFormatters.formatPlainAmount(slot.optCleanString("quantity"))} SOL",
                            style = MaterialTheme.typography.bodySmall.tnum(),
                            fontWeight = FontWeight.SemiBold,
                        )
                    }
                    val stopP = slot.optCleanString("stop_price")
                    if (stopP.isNotBlank()) {
                        val slotStage = BotFormatters.stageLabel(slot.optCleanString("stage"))
                        val stopDistPct = slot.optCleanString("stop_distance", "0.010").toDoubleOrNull()?.let {
                            BotFormatters.formatNumber((it * 100.0).toString(), 1)
                        } ?: "1.0"
                        val (stopLabel, stopColor) = when (slotStage) {
                            "S1" -> "APTS 保本止损价 $${BotFormatters.formatCryptoPrice(stopP)} (S1 已锁利保本)" to bot.info
                            "S2" -> "APTS 追踪止盈价 $${BotFormatters.formatCryptoPrice(stopP)} (S2 动态追尾)" to bot.gain
                            "S3" -> "APTS 趋势止盈价 $${BotFormatters.formatCryptoPrice(stopP)} (S3 趋势平仓)" to bot.warn
                            else -> "APTS 初始止损价 $${BotFormatters.formatCryptoPrice(stopP)} (-$stopDistPct% 策略保护)" to bot.loss
                        }
                        Spacer(Modifier.height(4.dp))
                        Text(
                            text = stopLabel,
                            style = MaterialTheme.typography.labelSmall.tnum(),
                            color = stopColor,
                            fontWeight = FontWeight.SemiBold,
                        )
                    }
                }
            }
        }

        if (hasManual) {
            for (i in 0 until manual!!.length()) {
                val pos = manual.optJSONObject(i) ?: continue
                val prot = pos.optJSONObject("protection")
                val active = prot?.optBoolean("active", false) == true || pos.optBoolean("custody_on", false)
                val stopPriceStr = prot.optCleanString("stop_price").ifBlank { pos.optCleanString("stop_price") }
                val stageStr = prot.optCleanString("stage").ifBlank { pos.optCleanString("stage", "S0") }
                val anchorPriceStr = pos.optCleanString("custody_anchor_price").ifBlank { pos.optCleanString("entry_price") }
                val markPriceStr = pos.optCleanString("mark_price").ifBlank { anchorPriceStr }
                val pnl = pos.optCleanString("pnl_usdt")
                val pct = pos.optCleanString("change_pct")
                val neg = BotFormatters.isNegative(pnl)
                BotSurfaceCard {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            StatusBadge("手动 SOL 持仓", MaterialTheme.colorScheme.surfaceContainerHigh, MaterialTheme.colorScheme.onSurface)
                            if (active) {
                                StatusBadge("APTS 托管中", bot.gainSoft, bot.gain)
                            }
                        }
                        if (pnl.isNotBlank()) {
                            Text(
                                text = "${BotFormatters.formatSignedUsdt(pnl)}${if (pct.isNotBlank()) " ($pct)" else ""}",
                                style = MaterialTheme.typography.titleSmall.tnum(),
                                color = if (neg) bot.loss else bot.gain,
                                fontWeight = FontWeight.Bold,
                            )
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    Text(
                        text = if (active) {
                            "数量 ${BotFormatters.formatPlainAmount(pos.optCleanString("quantity"))} SOL · 托管起算价 $${BotFormatters.formatCryptoPrice(anchorPriceStr)}"
                        } else {
                            "数量 ${BotFormatters.formatPlainAmount(pos.optCleanString("quantity"))} SOL · 现价 $${BotFormatters.formatCryptoPrice(markPriceStr)} (点击托管起算止盈止损)"
                        },
                        style = MaterialTheme.typography.bodySmall.tnum(),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (active && stopPriceStr.isNotBlank()) {
                        Spacer(Modifier.height(4.dp))
                        Text(
                            text = "当前止损价 $${BotFormatters.formatCryptoPrice(stopPriceStr)} (${BotFormatters.stageLabel(stageStr)})",
                            style = MaterialTheme.typography.labelSmall.tnum(),
                            color = bot.gain,
                            fontWeight = FontWeight.SemiBold,
                        )
                    }
                    Spacer(Modifier.height(10.dp))
                    Button(
                        onClick = { onRequestCustody(pos, !active) },
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = if (active) bot.lossSoft else bot.info,
                            contentColor = if (active) bot.loss else MaterialTheme.colorScheme.onPrimary,
                        ),
                    ) {
                        Icon(Icons.Outlined.Shield, null, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.size(6.dp))
                        Text(if (active) "撤销 APTS 原生止损托管" else "开启 -0.8% APTS 移动止损托管")
                    }
                }
            }
        }
    }
}

@Composable
private fun OrdersFeedSection(
    orders: JSONArray?,
    isBot: Boolean,
    emptyText: String,
    onInspectOrder: (JSONObject) -> Unit,
) {
    if (orders == null || orders.length() == 0) {
        Surface(
            shape = MaterialTheme.shapes.medium,
            color = MaterialTheme.colorScheme.surfaceContainerLow,
            border = BorderStroke(1.dp, LocalBotColors.current.hairline),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(
                text = emptyText,
                modifier = Modifier.padding(16.dp),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        return
    }
    val cap = minOf(20, orders.length())
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        for (i in 0 until cap) {
            val row = orders.optJSONObject(i) ?: continue
            OrderListItem(order = row, isBotOrder = isBot, onClick = { onInspectOrder(row) })
        }
    }
}

private data class Quad4<A, B, C, D>(val a: A, val b: B, val c: C, val d: D)
