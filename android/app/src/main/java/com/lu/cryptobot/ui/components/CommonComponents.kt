package com.lu.cryptobot.ui.components

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ExpandLess
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.material.icons.outlined.Link
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.Shield
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.lu.cryptobot.BuildConfig
import com.lu.cryptobot.R
import com.lu.cryptobot.ui.theme.LocalBotColors
import com.lu.cryptobot.ui.theme.tnum
import org.json.JSONObject

@Composable
fun BotSurfaceCard(
    modifier: Modifier = Modifier,
    containerColor: Color = MaterialTheme.colorScheme.surface,
    borderColor: Color = LocalBotColors.current.hairline,
    onClick: (() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    val shape = MaterialTheme.shapes.large
    val mod = modifier
        .fillMaxWidth()
        .clip(shape)
        .let { if (onClick != null) it.clickable(onClick = onClick) else it }
    Surface(
        modifier = mod,
        shape = shape,
        color = containerColor,
        border = BorderStroke(1.dp, borderColor),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            content = content,
        )
    }
}

@Composable
fun StatusBadge(
    text: String,
    containerColor: Color,
    contentColor: Color,
    dotColor: Color? = null,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(999.dp),
        color = containerColor,
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 9.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(5.dp),
        ) {
            if (dotColor != null) {
                Box(
                    modifier = Modifier
                        .size(6.dp)
                        .clip(CircleShape)
                        .background(dotColor),
                )
            }
            Text(
                text = text,
                style = MaterialTheme.typography.labelSmall.tnum(),
                color = contentColor,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                softWrap = false,
            )
        }
    }
}

@Composable
fun SectionHeader(
    title: String,
    subtitle: String? = null,
    actionText: String? = null,
    onActionClick: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = title,
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurface,
                fontWeight = FontWeight.Bold,
            )
            if (!subtitle.isNullOrBlank()) {
                Text(
                    text = subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        if (actionText != null && onActionClick != null) {
            Text(
                text = actionText,
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier
                    .clip(RoundedCornerShape(8.dp))
                    .clickable(onClick = onActionClick)
                    .padding(horizontal = 8.dp, vertical = 4.dp),
            )
        }
    }
}

@Composable
fun MetricBentoTile(
    label: String,
    value: String,
    caption: String? = null,
    valueColor: Color = MaterialTheme.colorScheme.onSurface,
    containerColor: Color = MaterialTheme.colorScheme.surfaceContainerLow,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier,
        shape = MaterialTheme.shapes.small,
        color = containerColor,
        border = BorderStroke(1.dp, LocalBotColors.current.hairline),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(3.dp),
        ) {
            Text(
                text = label,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text = value,
                style = MaterialTheme.typography.titleMedium.tnum(),
                color = valueColor,
                fontWeight = FontWeight.Bold,
                maxLines = 1,
                softWrap = false,
            )
            if (!caption.isNullOrBlank()) {
                Text(
                    text = caption,
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
fun TopStatusBar(
    routeTitle: String,
    liveState: String,
    microReady: Boolean,
    unreadAlerts: Int,
    onOpenAlerts: () -> Unit,
    onOpenConnect: () -> Unit,
    onLiveBadgeClick: () -> Unit,
) {
    val bot = LocalBotColors.current
    val (badgeLabel, badgeBg, badgeFg, dotColor) = when (liveState) {
        "LIVE_ON" -> Quad("实盘 ON", bot.gainSoft, bot.gain, bot.gain)
        "HARD_LOCK" -> Quad("熔断 LOCK", bot.lossSoft, bot.loss, bot.loss)
        "LIVE_OFF" -> Quad("只读 OFF", MaterialTheme.colorScheme.surfaceContainerHigh, MaterialTheme.colorScheme.onSurfaceVariant, MaterialTheme.colorScheme.outline)
        else -> Quad("离线", bot.warnSoft, bot.warn, bot.warn)
    }

    Surface(
        color = MaterialTheme.colorScheme.surface,
        tonalElevation = 0.dp,
    ) {
        Column(modifier = Modifier.statusBarsPadding()) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(56.dp)
                    .padding(horizontal = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.weight(1f),
                ) {
                    Image(
                        painter = painterResource(id = R.drawable.app_mascot),
                        contentDescription = "SOL-bot",
                        modifier = Modifier
                            .size(28.dp)
                            .clip(RoundedCornerShape(8.dp)),
                    )
                    Column {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(5.dp),
                        ) {
                            Text(
                                text = routeTitle,
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.weight(1f, fill = false),
                            )
                            Surface(
                                shape = RoundedCornerShape(6.dp),
                                color = MaterialTheme.colorScheme.surfaceContainerHigh,
                            ) {
                                Text(
                                    text = "v${BuildConfig.VERSION_NAME}",
                                    modifier = Modifier.padding(horizontal = 5.dp, vertical = 1.dp),
                                    style = MaterialTheme.typography.labelSmall.tnum(),
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    fontWeight = FontWeight.SemiBold,
                                    maxLines = 1,
                                    softWrap = false,
                                )
                            }
                        }
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(6.dp)
                                    .clip(CircleShape)
                                    .background(if (microReady) bot.gain else MaterialTheme.colorScheme.outline),
                            )
                            Text(
                                text = if (microReady) "盘口实时流" else "K线同步",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1,
                            )
                        }
                    }
                }

                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    StatusBadge(
                        text = badgeLabel,
                        containerColor = badgeBg,
                        contentColor = badgeFg,
                        dotColor = dotColor,
                        modifier = Modifier
                            .clip(RoundedCornerShape(999.dp))
                            .clickable(onClick = onLiveBadgeClick),
                    )
                    IconButton(onClick = onOpenAlerts, modifier = Modifier.size(38.dp)) {
                        BadgedBox(
                            badge = {
                                if (unreadAlerts > 0) {
                                    Badge(containerColor = bot.loss) {
                                        Text(
                                            text = if (unreadAlerts > 9) "9+" else unreadAlerts.toString(),
                                            fontSize = 9.sp,
                                            color = Color.White,
                                        )
                                    }
                                }
                            },
                        ) {
                            Icon(
                                Icons.Outlined.Notifications,
                                contentDescription = "告警记录",
                                tint = MaterialTheme.colorScheme.onSurface,
                                modifier = Modifier.size(20.dp),
                            )
                        }
                    }
                    IconButton(onClick = onOpenConnect, modifier = Modifier.size(38.dp)) {
                        Icon(
                            Icons.Outlined.Link,
                            contentDescription = "节点配对",
                            tint = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.size(20.dp),
                        )
                    }
                }
            }
            HorizontalDivider(thickness = 1.dp, color = bot.hairline)
        }
    }
}

private data class Quad<A, B, C, D>(val a: A, val b: B, val c: C, val d: D)

@Composable
fun AptsTimelineCard(
    activeStage: String? = null,
    isManualCustody: Boolean = false,
    apts: JSONObject? = null,
    modifier: Modifier = Modifier,
) {
    val bot = LocalBotColors.current
    var expanded by remember { mutableStateOf(false) }
    val normalizedStage = BotFormatters.stageLabel(activeStage)
    val initialStopLabel = if (isManualCustody) "-0.8% 托管" else "-1.0% 策略"
    val stopPrice = apts.optCleanString("stop_price")
    val peakPrice = apts.optCleanString("peak_price")
    val hits = apts?.optInt("reversal_hits", 0) ?: 0
    val maxHits = apts?.optInt("max_reversal_hits", 2) ?: 2

    val steps = listOf(
        Triple("S0", initialStopLabel, bot.lossSoft to bot.loss),
        Triple("S1", "+0.8% 保本", bot.infoSoft to bot.info),
        Triple("S2", "+1.5% 追尾", bot.gainSoft to bot.gain),
        Triple("长持", "不限时持仓", bot.warnSoft to bot.warn),
    )

    val (stageBg, stageFg) = when (normalizedStage) {
        "S1" -> bot.infoSoft to bot.info
        "S2" -> bot.gainSoft to bot.gain
        "S3" -> bot.warnSoft to bot.warn
        else -> bot.lossSoft to bot.loss
    }

    BotSurfaceCard(
        modifier = modifier.animateContentSize(),
        onClick = { expanded = !expanded },
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(
                modifier = Modifier.weight(1f),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Icon(
                    Icons.Outlined.Shield,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.size(18.dp),
                )
                Column(modifier = Modifier.weight(1f)) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Text(
                            text = "APTS v2 阶梯止盈止损",
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.Bold,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        if (activeStage != null || apts != null) {
                            StatusBadge(normalizedStage, stageBg, stageFg)
                        }
                    }
                    Text(
                        text = if (stopPrice.isNotBlank()) {
                            "当前止损 $${BotFormatters.formatCryptoPrice(stopPrice)} · 策略 -1.0% / 托管 -0.8%"
                        } else {
                            "策略止损 -1.0% · 人工托管 -0.8% · 点击展开规则"
                        },
                        style = MaterialTheme.typography.labelSmall.tnum(),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            Icon(
                if (expanded) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore,
                contentDescription = if (expanded) "收起详情" else "展开详情",
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        if (expanded) {
            Spacer(Modifier.height(10.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                steps.forEach { (code, subtitle, palette) ->
                    val isCurrent = activeStage != null && (normalizedStage == code || (code == "长持" && normalizedStage == "S3"))
                    Surface(
                        modifier = Modifier
                            .weight(1f)
                            .let { if (isCurrent) it.border(1.5.dp, palette.second, RoundedCornerShape(10.dp)) else it },
                        shape = RoundedCornerShape(10.dp),
                        color = if (isCurrent) palette.first else MaterialTheme.colorScheme.surfaceContainerLow,
                    ) {
                        Column(
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 6.dp),
                            horizontalAlignment = Alignment.CenterHorizontally,
                        ) {
                            Text(
                                text = code,
                                style = MaterialTheme.typography.labelMedium.tnum(),
                                fontWeight = FontWeight.Bold,
                                color = palette.second,
                            )
                            Text(
                                text = subtitle,
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                }
            }

            if (apts != null && (stopPrice.isNotBlank() || peakPrice.isNotBlank())) {
                Spacer(Modifier.height(10.dp))
                Surface(
                    shape = RoundedCornerShape(10.dp),
                    color = MaterialTheme.colorScheme.surfaceContainerLow,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(
                            text = "当前止损 $${BotFormatters.formatCryptoPrice(stopPrice)}",
                            style = MaterialTheme.typography.labelSmall.tnum(),
                            color = bot.loss,
                            fontWeight = FontWeight.SemiBold,
                        )
                        Text(
                            text = "峰值 $${BotFormatters.formatCryptoPrice(peakPrice)}",
                            style = MaterialTheme.typography.labelSmall.tnum(),
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                        Text(
                            text = "回撤触发 $hits/$maxHits · 未止损持续持仓",
                            style = MaterialTheme.typography.labelSmall.tnum(),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            Spacer(Modifier.height(10.dp))
            HorizontalDivider(color = bot.hairline)
            Spacer(Modifier.height(8.dp))
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("• S0 初始保护：策略开仓 -1.0%（人工托管 -0.8%）交易所原生挂单。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("• S1 移动保本：浮盈达 +0.8% 后，止损抬升至成本上方保本位（覆盖手续费与滑点）。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("• S2 动态跟踪：浮盈 ≥ +1.5% 启动动态回撤保护，需连续 2-tick 确认防插针。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("• 持续持仓规则：已取消 12h 限时退场，未触发止损或趋势衰竭前将一直持仓。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
fun OrderListItem(
    order: JSONObject,
    isBotOrder: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val bot = LocalBotColors.current
    val side = order.optString("side", "BUY").uppercase()
    val isBuy = side == "BUY"
    val status = order.optString("status", "UNKNOWN")
    val strategyId = order.optString("strategy_id")
    val price = order.optString("price").ifBlank { order.optString("limit_price") }
    val qty = order.optString("executed_qty").ifBlank { order.optString("quantity") }
    val ts = order.optLong("filled_at", 0L).takeIf { it > 0L }
        ?: order.optLong("time", 0L).takeIf { it > 0L }
        ?: order.optLong("created_at", 0L)

    BotSurfaceCard(
        modifier = modifier,
        containerColor = MaterialTheme.colorScheme.surfaceContainerLow,
        onClick = onClick,
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
                StatusBadge(
                    text = if (isBuy) "买入" else "卖出",
                    containerColor = if (isBuy) bot.gainSoft else bot.lossSoft,
                    contentColor = if (isBuy) bot.gain else bot.loss,
                )
                Column {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Text(
                            text = order.optString("symbol", "SOLUSDT"),
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.Bold,
                        )
                        if (isBotOrder && strategyId.isNotBlank()) {
                            StatusBadge(
                                text = strategyId,
                                containerColor = bot.infoSoft,
                                contentColor = bot.info,
                            )
                        }
                    }
                    Text(
                        text = "价格 $${BotFormatters.formatCryptoPrice(price)} · 数量 ${BotFormatters.formatPlainAmount(qty)} SOL",
                        style = MaterialTheme.typography.bodySmall.tnum(),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            Column(horizontalAlignment = Alignment.End) {
                val (stBg, stFg) = when (status) {
                    "FILLED" -> bot.gainSoft to bot.gain
                    "CANCELED", "CANCELLED", "EXPIRED", "REJECTED" -> bot.lossSoft to bot.loss
                    else -> bot.warnSoft to bot.warn
                }
                StatusBadge(text = status, containerColor = stBg, contentColor = stFg)
                Spacer(Modifier.height(3.dp))
                Text(
                    text = BotFormatters.formatEpoch(ts),
                    style = MaterialTheme.typography.labelSmall.tnum(),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
