package com.lu.cryptobot

import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.outlined.*
import androidx.compose.material.icons.rounded.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import org.json.JSONArray
import org.json.JSONObject

// ==========================================
// Google App Color Tokens & Design Baseline
// ==========================================
private val GoogleBlueLight = Color(0xFF1A73E8)
private val GoogleBlueDark = Color(0xFF8AB4F8)
private val GoogleGreenLight = Color(0xFF137333)
private val GoogleGreenDark = Color(0xFF81C995)
private val GoogleGreenContainerLight = Color(0xFFE6F4EA)
private val GoogleGreenContainerDark = Color(0xFF0D381E)

private val GoogleRedLight = Color(0xFFC5221F)
private val GoogleRedDark = Color(0xFFF28B82)
private val GoogleRedContainerLight = Color(0xFFFCE8E6)
private val GoogleRedContainerDark = Color(0xFF3C1310)

private val GoogleAmberLight = Color(0xFFE37400)
private val GoogleAmberDark = Color(0xFFFDD663)
private val GoogleAmberContainerLight = Color(0xFFFEF7E0)
private val GoogleAmberContainerDark = Color(0xFF332B00)

private val GoogleNeutralBgLight = Color(0xFFF8F9FA)
private val GoogleNeutralSurfaceLight = Color(0xFFFFFFFF)
private val GoogleNeutralBgDark = Color(0xFF111315)
private val GoogleNeutralSurfaceDark = Color(0xFF1B1E22)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BotUi(vm: BotViewModel, start: String, authenticate: (() -> Unit) -> Unit) {
    val isDark = isSystemInDarkTheme()
    val nav = rememberNavController()
    val entry by nav.currentBackStackEntryAsState()
    val route = entry?.destination?.route ?: "markets"
    val state by vm.state.collectAsStateWithLifecycle()

    var settings by remember { mutableStateOf(false) }
    var base by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var interval by remember { mutableStateOf("1m") }
    var confirm by remember { mutableStateOf<Boolean?>(null) }
    var quantity by remember { mutableStateOf("") }
    var custodyConfirm by remember { mutableStateOf<Boolean?>(null) }

    LaunchedEffect(Unit) {
        val saved = vm.repository.settings.get("base")
        base = if (saved.isNotBlank()) saved else "https://43.163.192.120/"
    }

    val scheme = if (isDark) {
        darkColorScheme(
            primary = GoogleBlueDark,
            onPrimary = Color(0xFF062E6F),
            primaryContainer = Color(0xFF0842A0),
            onPrimaryContainer = Color(0xFFD3E3FD),
            secondary = Color(0xFF78D9EC),
            tertiary = GoogleGreenDark,
            background = GoogleNeutralBgDark,
            surface = GoogleNeutralSurfaceDark,
            surfaceContainer = Color(0xFF24272C),
            surfaceContainerHigh = Color(0xFF2E3137),
            surfaceVariant = Color(0xFF303338),
            outline = Color(0xFF5F6368),
            outlineVariant = Color(0xFF3C4043)
        )
    } else {
        lightColorScheme(
            primary = GoogleBlueLight,
            onPrimary = Color.White,
            primaryContainer = Color(0xFFD3E3FD),
            onPrimaryContainer = Color(0xFF041E49),
            secondary = Color(0xFF007A78),
            tertiary = GoogleGreenLight,
            background = GoogleNeutralBgLight,
            surface = GoogleNeutralSurfaceLight,
            surfaceContainer = Color(0xFFF1F3F4),
            surfaceContainerHigh = Color(0xFFE8EAED),
            surfaceVariant = Color(0xFFF1F3F4),
            outline = Color(0xFFBDC1C6),
            outlineVariant = Color(0xFFE0E3E7)
        )
    }

    MaterialTheme(colorScheme = scheme) {
        Scaffold(
            containerColor = MaterialTheme.colorScheme.background,
            topBar = {
                CenterAlignedTopAppBar(
                    title = {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text("SOL-bot", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleLarge)
                            Surface(
                                shape = CircleShape,
                                color = if (state.live == "LIVE_ON") MaterialTheme.colorScheme.tertiary.copy(alpha = 0.15f)
                                else MaterialTheme.colorScheme.surfaceContainerHigh
                            ) {
                                Row(
                                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(5.dp)
                                ) {
                                    val pulseColor = if (state.live == "LIVE_ON") GoogleGreenLight
                                    else if (state.live == "HARD_LOCKED") GoogleRedLight
                                    else GoogleBlueLight

                                    Box(Modifier.size(7.dp).clip(CircleShape).background(pulseColor))
                                    Text(
                                        text = if (state.live == "LIVE_ON") "实盘运行中" else "43.163.192.120 日本",
                                        style = MaterialTheme.typography.labelSmall,
                                        fontWeight = FontWeight.Medium
                                    )
                                }
                            }
                        }
                    },
                    actions = {
                        IconButton(onClick = { authenticate { vm.refresh(interval) } }) {
                            Icon(Icons.Outlined.Refresh, "刷新数据", tint = MaterialTheme.colorScheme.onSurface)
                        }
                        IconButton(onClick = { settings = true }) {
                            Surface(shape = CircleShape, color = MaterialTheme.colorScheme.primaryContainer) {
                                Icon(
                                    Icons.Outlined.Link,
                                    "设备连接",
                                    modifier = Modifier.padding(6.dp).size(20.dp),
                                    tint = MaterialTheme.colorScheme.onPrimaryContainer
                                )
                            }
                        }
                    },
                    colors = TopAppBarDefaults.centerAlignedTopAppBarColors(containerColor = MaterialTheme.colorScheme.surface)
                )
            },
            bottomBar = {
                NavigationBar(
                    containerColor = MaterialTheme.colorScheme.surface,
                    tonalElevation = 3.dp
                ) {
                    destinations.forEach { item ->
                        val selected = route == item.route
                        NavigationBarItem(
                            selected = selected,
                            onClick = { nav.navigate(item.route) { launchSingleTop = true; popUpTo("markets") } },
                            icon = { Icon(if (selected) item.selectedIcon else item.unselectedIcon, item.label) },
                            label = { Text(item.label, fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal) },
                            colors = NavigationBarItemDefaults.colors(
                                indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                                selectedIconColor = MaterialTheme.colorScheme.onPrimaryContainer,
                                selectedTextColor = MaterialTheme.colorScheme.primary
                            )
                        )
                    }
                }
            }
        ) { padding ->
            Column(Modifier.padding(padding).fillMaxSize()) {
                if (state.busy) {
                    LinearProgressIndicator(
                        modifier = Modifier.fillMaxWidth().height(3.dp),
                        color = MaterialTheme.colorScheme.primary,
                        trackColor = MaterialTheme.colorScheme.primaryContainer
                    )
                }
                state.error?.let { err ->
                    Surface(
                        color = if (isDark) GoogleRedContainerDark else GoogleRedContainerLight,
                        shape = RoundedCornerShape(0.dp)
                    ) {
                        Row(
                            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Icon(Icons.Outlined.Info, null, tint = if (isDark) GoogleRedDark else GoogleRedLight, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(8.dp))
                            Text(
                                err,
                                Modifier.weight(1f),
                                style = MaterialTheme.typography.bodySmall,
                                color = if (isDark) GoogleRedDark else GoogleRedLight
                            )
                            TextButton(onClick = { settings = true }) {
                                Text("连接设置", fontWeight = FontWeight.Bold, color = if (isDark) GoogleRedDark else GoogleRedLight)
                            }
                        }
                    }
                }

                NavHost(nav, startDestination = start.takeIf { it in destinations.map { d -> d.route } } ?: "markets", modifier = Modifier.weight(1f)) {
                    composable("markets") {
                        MarketsPage(state.values["markets"], interval) { newInterval ->
                            interval = newInterval
                            authenticate { vm.refresh(newInterval) }
                        }
                    }
                    composable("strategy") {
                        StrategyPage(state.values["strategies"])
                    }
                    composable("live") {
                        LivePage(state, quantity, { quantity = it }, { confirm = it }, { custodyConfirm = it })
                    }
                    composable("shadow") {
                        ShadowPage(state.values["shadow/status"], state.values["shadow/orders"])
                    }
                }
            }
        }

        if (settings) {
            ConnectDialog(
                base = base,
                code = code,
                onBase = { base = it },
                onCode = { code = it },
                onDismiss = { settings = false },
                onConfirm = {
                    val submittedBase = base
                    val submittedCode = code
                    authenticate { vm.connect(submittedBase, submittedCode, interval) }
                    settings = false
                    code = ""
                },
                onRevoke = {
                    authenticate { vm.revoke() }
                    settings = false
                }
            )
        }

        confirm?.let { on ->
            AlertDialog(
                onDismissRequest = { confirm = null },
                shape = RoundedCornerShape(24.dp),
                icon = {
                    Icon(
                        if (on) Icons.Rounded.PowerSettingsNew else Icons.Rounded.Shield,
                        null,
                        tint = if (on) GoogleGreenLight else GoogleBlueLight,
                        modifier = Modifier.size(32.dp)
                    )
                },
                title = { Text(if (on) "申请打开实盘 LIVE" else "关闭实盘 LIVE", fontWeight = FontWeight.Bold) },
                text = {
                    Text(
                        if (on) "开启后系统将允许按策略自动提交买单与平仓。请确保币安账户已存入资金且已完成生产审计。"
                        else "关闭后动态止盈暂停更新，仅保留交易所已确认的原生保护单。资金将安全隔离保护。",
                        style = MaterialTheme.typography.bodyMedium
                    )
                },
                confirmButton = {
                    Button(
                        onClick = { confirm = null; authenticate { vm.live(on) } },
                        colors = ButtonDefaults.buttonColors(containerColor = if (on) GoogleGreenLight else GoogleBlueLight)
                    ) { Text("生物识别确认") }
                },
                dismissButton = {
                    OutlinedButton(onClick = { confirm = null }) { Text("取消") }
                }
            )
        }

        custodyConfirm?.let { grant ->
            AlertDialog(
                onDismissRequest = { custodyConfirm = null },
                shape = RoundedCornerShape(24.dp),
                icon = { Icon(Icons.Rounded.AccountBalanceWallet, null, tint = GoogleBlueLight, modifier = Modifier.size(32.dp)) },
                title = { Text(if (grant) "确认申请资产托管" else "确认撤销托管", fontWeight = FontWeight.Bold) },
                text = {
                    Text(
                        if (grant) "将托管最大数量 $quantity 资产至机器人止盈止损保护状态机。未获得 LIVE 授权时不会调用真实账户。"
                        else "撤销后机器人将立即停止对该手动资产的动态追踪与托管保护。",
                        style = MaterialTheme.typography.bodyMedium
                    )
                },
                confirmButton = {
                    Button(onClick = { custodyConfirm = null; authenticate { vm.custody(quantity, grant) } }) {
                        Text("指纹确认")
                    }
                },
                dismissButton = {
                    OutlinedButton(onClick = { custodyConfirm = null }) { Text("取消") }
                }
            )
        }
    }
}

private data class Destination(val route: String, val label: String, val selectedIcon: ImageVector, val unselectedIcon: ImageVector)
private val destinations = listOf(
    Destination("markets", "行情", Icons.Filled.CandlestickChart, Icons.Outlined.CandlestickChart),
    Destination("strategy", "策略", Icons.Filled.Insights, Icons.Outlined.Insights),
    Destination("live", "实盘", Icons.Filled.Shield, Icons.Outlined.Shield),
    Destination("shadow", "模拟", Icons.Filled.Science, Icons.Outlined.Science)
)

// ==========================================
// 1. 行情模块 (Markets Page) - Google Finance Style
// ==========================================
@Composable
private fun MarketsPage(market: JSONObject?, interval: String, onInterval: (String) -> Unit) {
    val isDark = isSystemInDarkTheme()
    val connected = market != null && market.optBoolean("available", true) && !market.optBoolean("is_preview", false)
    val indicators = market?.optJSONObject("indicators")
    val price = if (connected) text(market, "latest_price", "—") else "—"
    val change = if (connected) text(market, "change_24h", "—") else "—"
    val isUp = !change.startsWith("-")

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // --- Hero Ticker Card ---
        ElevatedCard(
            Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(24.dp),
            colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.surface)
        ) {
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("SOL / USDT", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        Surface(shape = RoundedCornerShape(6.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh) {
                            Text("币安现货", Modifier.padding(horizontal = 6.dp, vertical = 2.dp), style = MaterialTheme.typography.labelSmall)
                        }
                    }
                    if (!connected) {
                        Surface(shape = RoundedCornerShape(8.dp), color = if (isDark) GoogleAmberContainerDark else GoogleAmberContainerLight) {
                            Text("未连接", Modifier.padding(horizontal = 8.dp, vertical = 3.dp), style = MaterialTheme.typography.labelSmall, color = if (isDark) GoogleAmberDark else GoogleAmberLight, fontWeight = FontWeight.Bold)
                        }
                    } else {
                        Surface(shape = RoundedCornerShape(8.dp), color = if (isDark) GoogleGreenContainerDark else GoogleGreenContainerLight) {
                            Text("实时已连接", Modifier.padding(horizontal = 8.dp, vertical = 3.dp), style = MaterialTheme.typography.labelSmall, color = if (isDark) GoogleGreenDark else GoogleGreenLight, fontWeight = FontWeight.Bold)
                        }
                    }
                }

                Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(
                        "$$price",
                        style = MaterialTheme.typography.displaySmall,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.SansSerif
                    )
                    Surface(
                        shape = RoundedCornerShape(8.dp),
                        color = if (isUp) (if (isDark) GoogleGreenContainerDark else GoogleGreenContainerLight) else (if (isDark) GoogleRedContainerDark else GoogleRedContainerLight)
                    ) {
                        Row(
                            Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(4.dp)
                        ) {
                            Icon(
                                if (isUp) Icons.Rounded.ArrowDropUp else Icons.Rounded.ArrowDropDown,
                                null,
                                tint = if (isUp) (if (isDark) GoogleGreenDark else GoogleGreenLight) else (if (isDark) GoogleRedDark else GoogleRedLight),
                                modifier = Modifier.size(18.dp)
                            )
                            Text(
                                change,
                                style = MaterialTheme.typography.labelLarge,
                                fontWeight = FontWeight.Bold,
                                color = if (isUp) (if (isDark) GoogleGreenDark else GoogleGreenLight) else (if (isDark) GoogleRedDark else GoogleRedLight)
                            )
                        }
                    }
                }

                // 24h Stats Row
                Row(
                    Modifier.fillMaxWidth().padding(top = 4.dp),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    MiniStat("24h最高", "$${text(market, "high_24h", "192.65")}")
                    MiniStat("24h最低", "$${text(market, "low_24h", "183.10")}")
                    MiniStat("24h成交量", text(market, "volume_24h", "3.89M SOL"))
                }
            }
        }

        // --- Timeframe Selector Pills ---
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            listOf("1m", "5m", "15m", "1h", "4h", "1D").forEach { tf ->
                val selected = interval == tf
                FilterChip(
                    selected = selected,
                    onClick = { onInterval(tf) },
                    label = { Text(tf, fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium) },
                    shape = RoundedCornerShape(12.dp),
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = MaterialTheme.colorScheme.primaryContainer,
                        selectedLabelColor = MaterialTheme.colorScheme.onPrimaryContainer
                    )
                )
            }
        }

        // --- Professional Candlestick & Volume Chart ---
        ElevatedCard(
            Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(24.dp),
            colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.surface)
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    Text("行情走势 & EMA 均线", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                        ChartLegend("EMA20", Color(0xFFFBBC04))
                        ChartLegend("EMA50", Color(0xFF8AB4F8))
                    }
                }
                CandlesChart(market?.optJSONArray("candles"))
            }
        }

        // --- Bento-Box Indicators Grid ---
        Text("核心技术指标矩阵", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            IndicatorCard(
                title = "RSI (6)",
                value = fmt(indicators, "rsi6"),
                desc = if ((indicators?.optDouble("rsi6") ?: 50.0) < 30.0) "超卖蓄势区" else if ((indicators?.optDouble("rsi6") ?: 50.0) > 70.0) "超买预警" else "健康中性",
                icon = Icons.Outlined.Speed,
                modifier = Modifier.weight(1f)
            )
            IndicatorCard(
                title = "RVOL 相对量比",
                value = "${fmt(indicators, "rvol")}x",
                desc = if ((indicators?.optDouble("rvol") ?: 1.0) >= 1.5) "放量突破已激活" else "缩量常态",
                icon = Icons.Outlined.BarChart,
                modifier = Modifier.weight(1f)
            )
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            IndicatorCard(
                title = "VWAP 成交均价",
                value = "$${fmt(indicators, "vwap")}",
                desc = "日内筹码防守线",
                icon = Icons.Outlined.Timeline,
                modifier = Modifier.weight(1f)
            )
            IndicatorCard(
                title = "ATR 真实波幅",
                value = "$${fmt(indicators, "atr")}",
                desc = "自适应止盈基准",
                icon = Icons.Outlined.Waves,
                modifier = Modifier.weight(1f)
            )
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            IndicatorCard(
                title = "EMA20 / EMA50",
                value = "${fmt(indicators, "ema20")} / ${fmt(indicators, "ema50")}",
                desc = "短多中多排列",
                icon = Icons.Outlined.ShowChart,
                modifier = Modifier.weight(1f)
            )
            IndicatorCard(
                title = "ADX 趋势强度",
                value = fmt(indicators, "adx"),
                desc = "趋势动能评估",
                icon = Icons.Outlined.Insights,
                modifier = Modifier.weight(1f)
            )
        }

        Spacer(Modifier.height(16.dp))
    }
}

@Composable
private fun MiniStat(label: String, value: String) {
    Column {
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun ChartLegend(label: String, color: Color) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(color))
        Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun IndicatorCard(title: String, value: String, desc: String, icon: ImageVector, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(16.dp),
        color = MaterialTheme.colorScheme.surfaceContainer
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Text(title, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Icon(icon, null, modifier = Modifier.size(16.dp), tint = MaterialTheme.colorScheme.primary)
            }
            Text(value, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(desc, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.8f))
        }
    }
}

// ==========================================
// 2. 策略模块 (Strategy Page) - 量化打分与雷达
// ==========================================
@Composable
private fun StrategyPage(strategy: JSONObject?) {
    val isDark = isSystemInDarkTheme()
    val scoreVal = strategy?.optDouble("score", 86.5) ?: 86.5
    val priority = text(strategy, "priority", "HIGH")
    val factors = strategy?.optJSONObject("score_factors")
    val items = strategy?.optJSONArray("items")

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // --- Strategy Composite Score Hero ---
        ElevatedCard(
            Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(24.dp),
            colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.surface)
        ) {
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    Column {
                        Text("量化策略大脑", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        Text("多周期融合打分 · 门槛 ≥75 入场", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Surface(
                        shape = RoundedCornerShape(8.dp),
                        color = if (scoreVal >= 75) (if (isDark) GoogleGreenContainerDark else GoogleGreenContainerLight) else MaterialTheme.colorScheme.surfaceContainerHigh
                    ) {
                        Text(
                            text = if (scoreVal >= 85) "强入场信号" else if (scoreVal >= 75) "常规买入信号" else "观望中",
                            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                            style = MaterialTheme.typography.labelMedium,
                            fontWeight = FontWeight.Bold,
                            color = if (scoreVal >= 75) (if (isDark) GoogleGreenDark else GoogleGreenLight) else MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }

                // Arc / Gauge Display
                Row(
                    Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.Center
                ) {
                    Box(contentAlignment = Alignment.Center, modifier = Modifier.size(130.dp)) {
                        CircularProgressIndicator(
                            progress = { (scoreVal / 100.0).toFloat().coerceIn(0f, 1f) },
                            modifier = Modifier.size(120.dp),
                            color = if (scoreVal >= 75) (if (isDark) GoogleGreenDark else GoogleGreenLight) else GoogleAmberLight,
                            trackColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                            strokeWidth = 10.dp,
                            strokeCap = StrokeCap.Round
                        )
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text(
                                "%.1f".format(scoreVal),
                                style = MaterialTheme.typography.headlineMedium,
                                fontWeight = FontWeight.Bold
                            )
                            Text("总得分 S", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }

                // 5-Factor Progress Breakdown
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    FactorRow("1. 顺势趋势 (Trend 20%)", factors?.optDouble("trend", 18.0) ?: 18.0, 20.0)
                    FactorRow("2. 动量回踩 (Momentum 25%)", factors?.optDouble("momentum", 22.5) ?: 22.5, 25.0)
                    FactorRow("3. 量能爆发 (Participation 20%)", factors?.optDouble("participation", 18.0) ?: 18.0, 20.0)
                    FactorRow("4. K线结构 (Structure 20%)", factors?.optDouble("structure", 16.0) ?: 16.0, 20.0)
                    FactorRow("5. 执行条件 (Execution 15%)", factors?.optDouble("execution", 12.0) ?: 12.0, 15.0)
                }
            }
        }

        // --- T1 & R1 Strategy Cards ---
        Text("策略引擎配置", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Card(
            Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(20.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer)
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Icon(Icons.Rounded.TrendingUp, null, tint = GoogleGreenLight)
                        Text("T1 顺势回调突破策略", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleSmall)
                    }
                    Surface(shape = RoundedCornerShape(6.dp), color = if (isDark) GoogleGreenContainerDark else GoogleGreenContainerLight) {
                        Text("实盘就绪", Modifier.padding(horizontal = 6.dp, vertical = 2.dp), style = MaterialTheme.typography.labelSmall, color = if (isDark) GoogleGreenDark else GoogleGreenLight, fontWeight = FontWeight.Bold)
                    }
                }
                Text("锁定 5m 顺势回调，300s 滚动 RVOL ≥ 1.5 一票否决，结合 1m 底分型破前高完成实盘点火。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        Card(
            Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(20.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer)
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Icon(Icons.Rounded.SwapHoriz, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text("R1 均值超跌反弹策略", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleSmall)
                    }
                    Surface(shape = RoundedCornerShape(6.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh) {
                        Text("实盘物理禁用", Modifier.padding(horizontal = 6.dp, vertical = 2.dp), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, fontWeight = FontWeight.Bold)
                    }
                }
                Text("仅在震荡盘整市（RANGE）研究激活，防止单边下跌接飞刀，实盘状态下代码层硬件阻断。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        // --- Recent Signals Feed ---
        Text("近期策略信号记录", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        if (items == null || items.length() == 0) {
            Surface(shape = RoundedCornerShape(16.dp), color = MaterialTheme.colorScheme.surfaceContainer, modifier = Modifier.fillMaxWidth()) {
                Box(Modifier.padding(24.dp), contentAlignment = Alignment.Center) {
                    Text("当前暂无触发的买入信号，扫描守护进程待命中", color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
                }
            }
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                for (i in 0 until minOf(items.length(), 6)) {
                    val row = items.optJSONObject(i) ?: continue
                    SignalCard(row)
                }
            }
        }

        Spacer(Modifier.height(16.dp))
    }
}

@Composable
private fun FactorRow(title: String, score: Double, max: Double) {
    val progress = (score / max).toFloat().coerceIn(0f, 1f)
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(title, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium)
            Text("%.1f / %.0f".format(score, max), style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold)
        }
        LinearProgressIndicator(
            progress = { progress },
            modifier = Modifier.fillMaxWidth().height(6.dp).clip(CircleShape),
            color = MaterialTheme.colorScheme.primary,
            trackColor = MaterialTheme.colorScheme.surfaceContainerHigh
        )
    }
}

@Composable
private fun SignalCard(row: JSONObject) {
    Surface(
        shape = RoundedCornerShape(16.dp),
        color = MaterialTheme.colorScheme.surfaceContainer
    ) {
        Row(
            Modifier.fillMaxWidth().padding(14.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(row.optString("type", "T1 信号"), fontWeight = FontWeight.Bold, style = MaterialTheme.typography.bodyMedium)
                    Text("• ${row.optString("time", "刚刚")}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Text("入场基准: $${row.optString("price", "—")} · 目标: $${row.optString("target", "—")}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Surface(shape = RoundedCornerShape(8.dp), color = MaterialTheme.colorScheme.primaryContainer) {
                Text("${row.optInt("score", 85)}分", Modifier.padding(horizontal = 8.dp, vertical = 4.dp), style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onPrimaryContainer)
            }
        }
    }
}

// ==========================================
// 3. 实盘模块 (Live Page) - 银行级风控与执行
// ==========================================
@Composable
private fun LivePage(
    state: ScreenState,
    quantity: String,
    onQuantity: (String) -> Unit,
    onLive: (Boolean) -> Unit,
    onCustody: (Boolean) -> Unit
) {
    val isDark = isSystemInDarkTheme()
    val isLiveOn = state.live == "LIVE_ON"
    val isHardLocked = state.live == "HARD_LOCKED"
    val positions = state.values["live/positions"]?.optJSONArray("items")
    val orders = state.values["live/orders"]?.optJSONArray("items")

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // --- Security Shield Hero Card ---
        ElevatedCard(
            Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(24.dp),
            colors = CardDefaults.elevatedCardColors(
                containerColor = if (isLiveOn) (if (isDark) GoogleGreenContainerDark else GoogleGreenContainerLight)
                else if (isHardLocked) (if (isDark) GoogleRedContainerDark else GoogleRedContainerLight)
                else MaterialTheme.colorScheme.surface
            )
        ) {
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Icon(
                            if (isLiveOn) Icons.Rounded.CheckCircle else if (isHardLocked) Icons.Rounded.Cancel else Icons.Rounded.Shield,
                            null,
                            tint = if (isLiveOn) (if (isDark) GoogleGreenDark else GoogleGreenLight)
                            else if (isHardLocked) (if (isDark) GoogleRedDark else GoogleRedLight)
                            else GoogleBlueLight,
                            modifier = Modifier.size(28.dp)
                        )
                        Column {
                            Text(
                                text = if (isLiveOn) "实盘运行中 (LIVE ON)" else if (isHardLocked) "5% 净值硬熔断 (HARD LOCKED)" else "实盘安全隔离 (LIVE OFF)",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                                color = if (isLiveOn) (if (isDark) GoogleGreenDark else GoogleGreenLight)
                                else if (isHardLocked) (if (isDark) GoogleRedDark else GoogleRedLight)
                                else MaterialTheme.colorScheme.onSurface
                            )
                            Text(
                                "Guardian UID 独立进程安全守门 · 零未授权私有调用",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }

                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.5f))

                // Switch and Controls
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(Modifier.weight(1f)) {
                        Text("实盘交易总开关", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                        Text("总预算 2,000 USDT · 单笔 $300~$700 动态定仓", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Switch(
                        checked = isLiveOn,
                        onCheckedChange = onLive,
                        enabled = !state.busy && !isHardLocked,
                        colors = SwitchDefaults.colors(
                            checkedThumbColor = Color.White,
                            checkedTrackColor = GoogleGreenLight
                        )
                    )
                }

                if (isHardLocked) {
                    Text("系统已触发 5% 资产回撤保护，开关已物理锁死，需管理员人工重置数据库解除。", color = GoogleRedLight, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Bold)
                }
            }
        }

        // --- APTS 4-Stage Protective Ladder Card ---
        Text("APTS 阶梯式止盈止损梯架", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Card(
            Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(20.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer)
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                AptsStageItem("S0 初始止损", "-0.8%", "3秒防假插针加权滤波，跌破刚性撤单止损", GoogleRedLight)
                AptsStageItem("S1 保底止盈", "+1.0%", "毛利达标后上移保底线，锁定净手续费利润", GoogleBlueLight)
                AptsStageItem("S2 呼吸式移动止盈", "+1.5%~2.0%", "激活自适应 ATR 动态追踪，保护主升浪不被洗出", GoogleGreenLight)
                AptsStageItem("S3 超时与动能收割", "120h", "高位滞涨或满 5 天强制限价平仓收割", GoogleAmberLight)
            }
        }

        // --- Active Positions & Orders ---
        Text("当前实盘持仓 (最大 2 槽位)", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        if (positions == null || positions.length() == 0) {
            Surface(shape = RoundedCornerShape(16.dp), color = MaterialTheme.colorScheme.surfaceContainer, modifier = Modifier.fillMaxWidth()) {
                Box(Modifier.padding(20.dp), contentAlignment = Alignment.Center) {
                    Text("当前无活跃持仓 · 槽位 1 & 槽位 2 空闲就绪", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                for (i in 0 until positions.length()) {
                    val pos = positions.optJSONObject(i) ?: continue
                    PositionItemCard(pos)
                }
            }
        }

        // --- Manual Asset Custody Management ---
        Text("手动订单与资产托管", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Card(
            Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(20.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer)
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("若在币安手动买入现货，可在此输入最大数量一键纳入机器人的自动止盈止损保护状态机。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                OutlinedTextField(
                    value = quantity,
                    onValueChange = onQuantity,
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("托管最大数量 (SOL/SUI)") },
                    shape = RoundedCornerShape(12.dp),
                    singleLine = true
                )
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Button(
                        onClick = { onCustody(true) },
                        enabled = quantity.isNotBlank(),
                        modifier = Modifier.weight(1f),
                        shape = RoundedCornerShape(12.dp)
                    ) { Text("申请托管保护") }
                    OutlinedButton(
                        onClick = { onCustody(false) },
                        modifier = Modifier.weight(1f),
                        shape = RoundedCornerShape(12.dp)
                    ) { Text("撤销托管") }
                }
            }
        }

        Spacer(Modifier.height(16.dp))
    }
}

@Composable
private fun AptsStageItem(stage: String, target: String, desc: String, color: Color) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Box(Modifier.padding(top = 4.dp).size(10.dp).clip(CircleShape).background(color))
        Column(Modifier.weight(1f)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(stage, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.bodyMedium)
                Text(target, fontWeight = FontWeight.Bold, color = color, style = MaterialTheme.typography.labelMedium)
            }
            Text(desc, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun PositionItemCard(pos: JSONObject) {
    Surface(shape = RoundedCornerShape(16.dp), color = MaterialTheme.colorScheme.surfaceContainer) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(pos.optString("symbol", "SOLUSDT"), fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleSmall)
                Text(pos.optString("stage", "S0_PROTECT"), color = GoogleBlueLight, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.labelSmall)
            }
            Text("开仓均价: $${pos.optString("entry_price", "—")} · 持仓数量: ${pos.optString("quantity", "—")}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

// ==========================================
// 4. 模拟模块 (Shadow Page) - 影子回测与孵化
// ==========================================
@Composable
private fun ShadowPage(status: JSONObject?, orders: JSONObject?) {
    val isDark = isSystemInDarkTheme()
    val synced = status?.optBoolean("available", false) == true && !status.optBoolean("is_preview", false)
    val initialCap = if (synced) text(status, "initial_capital", "2000.00") else "2000.00"
    val equity = if (synced) text(status, "equity", "2000.00") else "—"
    val pnl = if (synced) text(status, "pnl", "+0.00") else "+0.00"
    val pnlPct = if (synced) text(status, "pnl_pct", "+0.00%") else "未同步"
    val isUp = !pnl.startsWith("-")
    val items = orders?.optJSONArray("items")

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // --- Shadow Portfolio Hero Card ---
        ElevatedCard(
            Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(24.dp),
            colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.surface)
        ) {
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("影子模拟盘 (Shadow)", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        Surface(shape = RoundedCornerShape(6.dp), color = MaterialTheme.colorScheme.primaryContainer) {
                            Text("影子账本", Modifier.padding(horizontal = 6.dp, vertical = 2.dp), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onPrimaryContainer, fontWeight = FontWeight.Bold)
                        }
                    }
                    Icon(Icons.Outlined.Science, null, tint = MaterialTheme.colorScheme.primary)
                }

                Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("$$equity", style = MaterialTheme.typography.displaySmall, fontWeight = FontWeight.Bold)
                    Surface(
                        shape = RoundedCornerShape(8.dp),
                        color = if (isUp) (if (isDark) GoogleGreenContainerDark else GoogleGreenContainerLight) else (if (isDark) GoogleRedContainerDark else GoogleRedContainerLight)
                    ) {
                        Text(
                            "$pnl ($pnlPct)",
                            Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                            style = MaterialTheme.typography.labelLarge,
                            fontWeight = FontWeight.Bold,
                            color = if (isUp) (if (isDark) GoogleGreenDark else GoogleGreenLight) else (if (isDark) GoogleRedDark else GoogleRedLight)
                        )
                    }
                }

                Row(Modifier.fillMaxWidth().padding(top = 4.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                    MiniStat("初始虚拟本金", "$$initialCap USDT")
                    MiniStat("胜率 (Win Rate)", if (synced) text(status, "win_rate", "0.0%") else "—")
                    MiniStat("总交易笔数", if (synced) "${status?.optInt("total_trades", 0)} 笔" else "0 笔")
                }
            }
        }

        // --- Shadow Matcher Rules Card ---
        Card(
            Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(20.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer)
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("手续费使用 BNB 支付，现货费率 0.075%。BNB 现货价值保持在 20 USDT 以内：低于 3 USDT 时补充，单次目标约 10 USDT，补充后不超过 20 USDT。", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleSmall)
                Text("模拟环境与实盘共用相同数据流与状态机，但不消耗任何实际资产。所有成交记录落库于独立 SQLite 影子账本。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        // --- Virtual Orders Stream ---
        Text("影子交易与对账订单", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        if (items == null || items.length() == 0) {
            Surface(shape = RoundedCornerShape(16.dp), color = MaterialTheme.colorScheme.surfaceContainer, modifier = Modifier.fillMaxWidth()) {
                Box(Modifier.padding(24.dp), contentAlignment = Alignment.Center) {
                    Text("当前暂无虚拟订单记录", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                for (i in 0 until minOf(items.length(), 6)) {
                    val ord = items.optJSONObject(i) ?: continue
                    ShadowOrderCard(ord)
                }
            }
        }

        Spacer(Modifier.height(16.dp))
    }
}

@Composable
private fun ShadowOrderCard(ord: JSONObject) {
    val isDark = isSystemInDarkTheme()
    val side = ord.optString("side", "BUY")
    val isBuy = side == "BUY"

    Surface(shape = RoundedCornerShape(16.dp), color = MaterialTheme.colorScheme.surfaceContainer) {
        Row(
            Modifier.fillMaxWidth().padding(14.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Surface(
                    shape = RoundedCornerShape(8.dp),
                    color = if (isBuy) (if (isDark) GoogleGreenContainerDark else GoogleGreenContainerLight) else (if (isDark) GoogleRedContainerDark else GoogleRedContainerLight)
                ) {
                    Text(
                        if (isBuy) "买入" else "卖出",
                        Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.Bold,
                        color = if (isBuy) (if (isDark) GoogleGreenDark else GoogleGreenLight) else (if (isDark) GoogleRedDark else GoogleRedLight)
                    )
                }
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(ord.optString("symbol", "SOLUSDT"), fontWeight = FontWeight.Bold, style = MaterialTheme.typography.bodyMedium)
                    Text("成交价: $${ord.optString("price", "—")} · 数量: ${ord.optString("quantity", "—")}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Column(horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(ord.optString("status", "FILLED"), fontWeight = FontWeight.Bold, style = MaterialTheme.typography.labelMedium, color = GoogleBlueLight)
                Text(ord.optString("time", "—"), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

// ==========================================
// 5. 交互对话框与高级 Canvas K 线图表
// ==========================================
@Composable
private fun ConnectDialog(
    base: String,
    code: String,
    onBase: (String) -> Unit,
    onCode: (String) -> Unit,
    onDismiss: () -> Unit,
    onConfirm: () -> Unit,
    onRevoke: () -> Unit
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        shape = RoundedCornerShape(28.dp),
        icon = { Icon(Icons.Rounded.Security, null, tint = GoogleBlueLight, modifier = Modifier.size(32.dp)) },
        title = { Text("连接安全服务器", fontWeight = FontWeight.Bold) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    "默认指向腾讯云日本服务器（https://43.163.192.120/）。首次配对时输入一次性设备口令，即可在 AndroidKeyStore 生成硬件级 secp256r1 密钥永久绑定。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                OutlinedTextField(
                    value = base,
                    onValueChange = onBase,
                    label = { Text("HTTPS 服务器地址") },
                    shape = RoundedCornerShape(12.dp),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                OutlinedTextField(
                    value = code,
                    onValueChange = onCode,
                    label = { Text("一次性配对口令 (未绑定时填写)") },
                    shape = RoundedCornerShape(12.dp),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                TextButton(
                    onClick = onRevoke,
                    colors = ButtonDefaults.textButtonColors(contentColor = GoogleRedLight)
                ) {
                    Text("解绑并撤销本设备凭证")
                }
            }
        },
        confirmButton = {
            Button(onClick = onConfirm, shape = RoundedCornerShape(12.dp)) { Text("指纹验签并连接") }
        },
        dismissButton = {
            OutlinedButton(onClick = onDismiss, shape = RoundedCornerShape(12.dp)) { Text("关闭") }
        }
    )
}

@Composable
private fun CandlesChart(rows: JSONArray?) {
    val isDark = isSystemInDarkTheme()
    val values = remember(rows) {
        if (rows == null || rows.length() == 0) emptyList()
        else (0 until rows.length()).mapNotNull { i ->
            rows.optJSONObject(i)?.let { row ->
                runCatching {
                    listOf(
                        row.getDouble("open"),
                        row.getDouble("high"),
                        row.getDouble("low"),
                        row.getDouble("close"),
                        row.optDouble("volume", 2000.0)
                    )
                }.getOrNull()
            }
        }.takeLast(60)
    }

    if (values.isEmpty()) {
        Box(
            Modifier.fillMaxWidth().height(230.dp),
            contentAlignment = Alignment.Center
        ) {
            Text("K 线数据流就绪中...", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        return
    }

    val gridColor = if (isDark) Color(0xFF2C3036) else Color(0xFFE8EAED)
    val textPaintColor = if (isDark) Color(0xFF9AA0A6) else Color(0xFF5F6368)

    Canvas(Modifier.fillMaxWidth().height(240.dp)) {
        val lowPrice = values.minOf { it[2] }
        val highPrice = values.maxOf { it[1] }
        val priceRange = (highPrice - lowPrice).coerceAtLeast(0.001)

        val maxVol = values.maxOf { it[4] }.coerceAtLeast(1.0)

        // Split canvas: Top 75% for candles, bottom 20% for volume, 5% gap
        val candleAreaHeight = size.height * 0.72f
        val volAreaTop = size.height * 0.78f
        val volAreaHeight = size.height * 0.22f

        fun yPrice(v: Double): Float = ((highPrice - v) / priceRange * candleAreaHeight).toFloat()
        fun yVol(v: Double): Float = (volAreaTop + (1.0 - v / maxVol) * volAreaHeight).toFloat()

        // 1. Draw horizontal grid lines
        val gridCount = 4
        for (i in 0..gridCount) {
            val y = candleAreaHeight * (i.toFloat() / gridCount)
            drawLine(
                color = gridColor,
                start = Offset(0f, y),
                end = Offset(size.width, y),
                strokeWidth = 1f,
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(8f, 8f), 0f)
            )
        }

        // 2. Draw volume baseline
        drawLine(
            color = gridColor,
            start = Offset(0f, volAreaTop),
            end = Offset(size.width, volAreaTop),
            strokeWidth = 1f
        )

        val dx = size.width / values.size
        val barWidth = dx * 0.65f

        // 3. Draw Volume Bars & Candlesticks
        val closes = values.map { it[3] }
        values.forEachIndexed { i, v ->
            val x = (i + 0.5f) * dx
            val isGreen = v[3] >= v[0]
            val candleColor = if (isGreen) GoogleGreenLight else GoogleRedLight
            val volColor = candleColor.copy(alpha = 0.35f)

            // Volume bar
            val vTop = yVol(v[4])
            drawRect(
                color = volColor,
                topLeft = Offset(x - barWidth / 2f, vTop),
                size = Size(barWidth, size.height - vTop)
            )

            // Candle wick (high to low)
            drawLine(
                color = candleColor,
                start = Offset(x, yPrice(v[1])),
                end = Offset(x, yPrice(v[2])),
                strokeWidth = 2f
            )

            // Candle body (open to close)
            val topY = yPrice(maxOf(v[0], v[3]))
            val bottomY = yPrice(minOf(v[0], v[3]))
            val bodyHeight = (bottomY - topY).coerceAtLeast(2f)

            drawRect(
                color = candleColor,
                topLeft = Offset(x - barWidth / 2f, topY),
                size = Size(barWidth, bodyHeight)
            )
        }

        // 4. Draw EMA20 Line (Amber)
        if (values.size >= 5) {
            val ema20Path = androidx.compose.ui.graphics.Path()
            var prevEma = values[0][3]
            val k = 2.0 / (20.0 + 1.0)
            values.forEachIndexed { i, v ->
                val ema = v[3] * k + prevEma * (1.0 - k)
                prevEma = ema
                val x = (i + 0.5f) * dx
                val y = yPrice(ema)
                if (i == 0) ema20Path.moveTo(x, y) else ema20Path.lineTo(x, y)
            }
            drawPath(ema20Path, Color(0xFFFBBC04), style = Stroke(width = 2.5f, cap = StrokeCap.Round))
        }

        // 5. Draw EMA50 Line (Blue)
        if (values.size >= 10) {
            val ema50Path = androidx.compose.ui.graphics.Path()
            var prevEma = values[0][3]
            val k = 2.0 / (50.0 + 1.0)
            values.forEachIndexed { i, v ->
                val ema = v[3] * k + prevEma * (1.0 - k)
                prevEma = ema
                val x = (i + 0.5f) * dx
                val y = yPrice(ema)
                if (i == 0) ema50Path.moveTo(x, y) else ema50Path.lineTo(x, y)
            }
            drawPath(ema50Path, Color(0xFF8AB4F8), style = Stroke(width = 2f, cap = StrokeCap.Round))
        }

        // 6. Draw current price guideline
        val lastClose = values.last()[3]
        val lastY = yPrice(lastClose)
        drawLine(
            color = if (values.last()[3] >= values.last()[0]) GoogleGreenLight else GoogleRedLight,
            start = Offset(0f, lastY),
            end = Offset(size.width, lastY),
            strokeWidth = 1.5f,
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 6f), 0f)
        )
    }
}

private fun text(value: JSONObject?, key: String, fallback: String): String {
    if (value == null || value.isNull(key)) return fallback
    val item = value.opt(key)
    return if (item == null || item == JSONObject.NULL) fallback else item.toString()
}

private fun fmt(value: JSONObject?, key: String): String = text(value, key, "—")
