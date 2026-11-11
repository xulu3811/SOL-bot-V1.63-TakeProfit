# Role & Context
你是由 **8. Android 全栈工程师**、**11. 网络安全工程师** 与 **7. Python 后端工程师** 组成的移动端与全栈交付引擎。
请基于 **Phase 0 ~ Phase 3** 的后端核心，生成 **Phase 4：后端 FastAPI + WebSocket 接口层、FCM 推送服务，以及 Android Jetpack Compose (`minSdk 26`) Material 3 + 深空暗黑 3D 拟态金融风（Dark Glassmorphism + Clay 3D）客户端** 的完整代码。

---

## 1. 视觉设计与交互规范（Google Material 3 + 深空暗黑 3D 扁平化）
* **设计语言**：采用 **深空暗黑 3D 拟态金融风（Dark Glassmorphism + Clay 3D）**。
  * **色彩体系**：深邃太空灰/曜石黑底色 (`#0B0E14` $\sim$ `#131824`)；实盘 `Crypto-bot` 与多头激活态采用 **高饱和荧光翠绿 (`#00F59B`)**；影子 `Crypto-shadow` 采用 **电光幻影紫 (`#9D4EDD` $\sim$ `#C77DFF`)**；风险/止损与 RVOL 爆量预警采用 **熔岩琥珀橙 (`#FF6B35`)**。
  * **3D 扁平化立体质感**：所有核心卡片与按钮使用 Jetpack Compose 自定义多层 `graphicsLayer` 景深投影、顶部 $1\text{ dp}$ 高光折射描边（Specular Highlight Border）与微立体悬浮动效（点击时具备弹簧物理按压反馈 `Spring.DampingRatioMediumBouncy`）。

---

## 2. 四大核心板块功能规格（Bottom Navigation 4 Tabs）

### 板块 1：行情面板（Market Panel）
1. **SOL/USDT 3D 悬浮实时报价舱**：展示毫秒级最新价、$3\text{ s}$ 防插针平滑价 $\bar{P}_{3s}$、24h 涨跌幅、盘口买卖失衡度 $OBI$ 3D 双向张力条。
2. **四周期共振状态矩阵卡片**：以 3D 胶囊指示灯实时展示 `1H`（趋势方向）、`15m`（布林带 $\%B$ 空间）、`5m`（$\text{RSI}(6)$ 数值与时间补偿 $\text{RVOL}_{5m}$ 倍数）、`1m`（微观扳机）的实时状态。
3. **BNB 燃料舱状态条**：显示当前现货账户 `BNB` 折合美元市值，以及“低于 $\$3$ 自动补仓 $\$11.5$（上限 $\$20$）”守护状态。

### 板块 2：策略面板（策略达成 百分比 仪表板）
1. **核心视觉中心：3D 流体环状百分比仪表板（Canvas + Shader 渲染）**：
   * 实时动态展示当前策略总达成度 $S_{\text{total}} \in [0\%, 100\%]$，并在 $80\%$ 处绘制醒目的 **“自主开仓触发阈值刻度线”**。
   * 当 $S_{\text{total}} \ge 80\%$ 时，仪表盘外环呈现荧光绿脉冲呼吸光效，并同步显示当前数学模型推算的 **信号置信度 $C$** 与 **预计分配仓位金额（$\$300 \sim \$700$ 动态区间）**。
2. **四因子贡献度拆解 3D 柱状卡**：分别显示 `1H 趋势 (0~25%)`、`15m 结构 (0~25%)`、`RSI(6) 动量 (0~25%)`、`RVOL 放量 (0~25%)` 的实时得分与一票否决状态。
3. **止损后二次反弹猎手雷达（Rebound Watcher Card）**：当发生 $-0.8\%$ 止损后，实时显示距离“再跌 $-0.9\%$ 激活线”的价格进度与 $\text{RVOL} > 2.0$ 阳线反包监控状态。

### 板块 3：Crypto-bot（实盘自主交易面板）
1. **顶部核心控制舱：3D 机械感【LIVE 实盘授权总开关】**：
   * 遵循项目开发宪法：用户仅需打开此按钮，即 100% 授权 `Crypto-bot` 接入 Binance 现货账户自主决策交易；关闭时立即切断实盘开仓权限。
2. **Crypto-bot 专属战绩看板**：展示机器人自主交易（`count_in_bot_stats = 1`）的累计净利（USDT & $\%$）、胜率、总交易笔数、可用 USDT 余额。
3. **双区持仓管理列表（严格区分机器人单与用户手动单）**：
   * **区域 A：Crypto-bot 机器人自主持仓卡片（最多 2 个）**：显示买入均价、动态分配金额、当前毛利率、最高浮盈记录、移动止盈激活状态（$+1.5\%$ 激活标志 & RVOL 大绿柱放宽保护标志）、距离 $-0.8\%$ 止损线空间。
   * **区域 B：非机器人交易订单（用户手动操作）与【一键托管】卡片**：
     * 自动展示用户在币安手动买入的 `SOL` 订单（并提供“登记存量 SOL 持仓均价”入口）。
     * 每张手动订单卡片配备独立的 **【托管按钮（Hosting Switch）】**：打开后即授权 `Crypto-bot` 使用机器人的移动止盈与止损策略代为平仓获利，卡片带“托管中 · 不计入 Bot 战绩”专属角标。
4. **右上角⚙️：自定义交易参数配置抽屉（默认关闭）**：
   * 提供总开关（默认 OFF，使用系统 $2000\text{ U}$ / $\$300\sim\$700$ / $+1.0\%$ / $+1.5\%$ / $-0.5\%$ / $-0.8\%$ 默认配置）。
   * 打开后支持用户自定义修改：**交易总金额、持仓金额、单笔交易金额上限 $W_{\max}$（数学模型自动按 $[0.45 W_{\max}, W_{\max}]$ 缩放）、止盈参数、止损参数**，修改后实盘与影子同步生效。
5. **右上角🔐：安全与设备激活中心**：输入设备激活口令（Device Passphrase）绑定手机，以及加密录入/更新 Binance API Key 与 Secret。

### 板块 4：Crypto-bot 影子（Crypto-shadow 模拟交易面板）
* 采用电光紫 3D 视觉主题，展示独立 **$2000\text{ USDT}$ 虚拟资金账户** 的实时净值、虚拟持仓卡片（与实盘同构）、历史模拟交割流水，以及实盘 vs 影子策略表现对比分析曲线。

---

## 3. Android 后台常驻保活与 FCM 强弹窗推送架构
1. **`SolBotForegroundService`（前台常驻守护服务）**：
   * 适配 Android 8.0+ (`minSdk 26` $\sim$ `targetSdk 34`)，使用 `startForeground()` 挂载常驻状态栏通知（实时显示 `SOL` 最新价、LIVE 开关状态与当前持仓浮盈），申请 `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` 豁免电池优化，确保进程常驻手机后台不被系统杀死。
2. **Firebase Cloud Messaging (FCM) 高优通知渠道 (`IMPORTANCE_HIGH`)**：
   * 后端集成 `firebase-admin` SDK，使用 FCM **Data + Notification 双载荷 (`priority="high"`)** 推送以下 5 大场景，确保锁屏与后台状态下立即亮屏弹窗并发出定制提示音：
     1. 实盘/影子开仓成交提醒（含买入均价、动态仓位金额、置信度评分）；
     2. 止盈/止损平仓战报（含净利/亏损及触发原因：常规移动止盈 / RVOL 豁免止盈 / $-0.8\%$ 止损 / 二次反弹抄底）；
     3. 用户手动托管订单止盈/止损完成提醒；
     4. `BNB` 余额低于 $\$3$ 自动买入 $\$11.5$ 完成通知；
     5. 异常风控告警（USDT 余额不足、API 异常或网络重连）。

---

## 4. 待生成交付物清单
1. `backend/api/server.py` & `backend/notify/fcm_pusher.py`：FastAPI REST + WebSocket 实时状态广播服务与 Firebase Admin 推送模块。
2. `android/app/build.gradle.kts` & `android/app/src/main/AndroidManifest.xml`：配置 `minSdk = 26`、Jetpack Compose Material 3、Retrofit2、OkHttp3、Kotlin Coroutines、Firebase Messaging 及前台服务权限。
3. `android/app/src/main/java/com/solbot/ui/theme/Theme3D.kt`：实现深空暗黑配色与 3D 扁平化悬浮卡片修饰符 `Modifier.clay3DCard()`。
4. `android/app/src/main/java/com/solbot/ui/screens/`：完整实现四大面板（`MarketScreen.kt`、`StrategyGaugeScreen.kt`、`CryptoBotLiveScreen.kt`、`CryptoShadowScreen.kt`）及自定义参数弹窗、手动单托管卡片组件。
5. `android/app/src/main/java/com/solbot/service/SolBotMessagingService.kt` & `SolBotForegroundService.kt`：实现后台常驻保活与 FCM 强弹窗通知。