# Sprint 6 Plan-Mode Prompt：Android Jetpack Compose (Material 3 + 3D 扁平化) 四大板块客户端与 Android 14 常驻保活预警

> **使用方式**：在 Sprint 5 后端全部就绪后，在 Antigravity + 4 Argon 的 **Plan Mode** 中打开 Android 工程目录输入本 Prompt。

---

## 【角色激活与任务目标】
你现在由 **项目总架构师**、**Android 高级客户端工程师** 与 **QA/独立审计工程师** 联合驱动。请在 **Plan Mode** 下完成 `Crypto-bot` 的 **Sprint 6：基于 Jetpack Compose (`minSdk 26`, `targetSdk 34`, Kotlin) + Google Material 3 + 3D 扁平化高颜值视觉风格的 Android 客户端**，完整实现四大核心板块、自定义 `X509TrustManager` 自签名 IP 证书指纹锁定通信、设备口令激活、手动订单托管交互、五维复盘报告阅读器以及适配 Android 14 (`specialUse`) 的后台常驻保活 + FCM 6 大类弹窗预警。

---

## 【待生成文件清单与 UI/UX 架构规范】

### 1. 工程配置与安全网络层 (`data/network/`)
- **`AndroidManifest.xml` 与 `build.gradle.kts`**：
  - `minSdk = 26`, `targetSdk = 34`，集成 Jetpack Compose Material 3、Retrofit2 + OkHttp3、Kotlin Coroutines + StateFlow、Firebase Messaging (FCM)、Security Crypto (`EncryptedSharedPreferences`)。
  - **【审计修复：适配 Android 14 (API 34) 前台常驻服务合规性】**：
    - 严禁使用会有 6 小时超时与后台启动限制的 `dataSync`！必须声明权限：`FOREGROUND_SERVICE`、`FOREGROUND_SERVICE_SPECIAL_USE`、`POST_NOTIFICATIONS`、`REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`、`WAKE_LOCK`。
    - 在 `<service android:name=".service.BotMonitorForegroundService" android:foregroundServiceType="specialUse">` 内部显式声明 `<property android:name="android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE" android:value="real_time_crypto_position_risk_monitoring_and_alert"/>`，并在启动服务前动态检查 Android 13+ `POST_NOTIFICATIONS` 权限。
- **`SecureHttpClient.kt`（【审计修复：自签名 IP 证书自定义 `X509TrustManager` 锁定与设备绑定】）**：
  - 支持用户在首次启动界面输入日本服务器公网 IP（如 `https://<IP>:8443`）、服务器自签名 ECDSA 证书公钥 SHA-256 指纹（`sha256/...`）以及一次性设备激活口令（`ACT-XXXX-...`）。
  - **彻底解决自签名证书 `CertPathValidatorException`**：构建自定义 `X509TrustManager` 与 `SSLContext`，在 `checkServerTrusted` 中直接提取服务端叶子证书的 `PublicKey` 计算 SHA-256 Base64 指纹并与用户录入的 Pinning Hash 进行常量时间比对（无需依赖系统根 CA 链），同时配置自定义 `HostnameVerifier` 严格比对请求的公网 IP 地址，100% 拦截中间人攻击。
  - 使用 Android `EncryptedSharedPreferences`（基于硬件 Android Keystore）安全存储绑定后的长效 JWT 令牌与证书指纹。

### 2. 视觉主题与 3D 扁平化组件库 (`ui/theme/` & `ui/components/`)
- **Material 3 + 3D 扁平化微拟物深色交易主题（`CryptoBot3DTheme.kt`）**：
  - 采用深空碳黑背景（`#0B0E14`）、多层光影高光悬浮卡片（带顶部 $1\text{ dp}$ 半透明亮边、底部柔和投影与微妙渐变，营造干净立体的 3D 扁平化质感）。
  - 语义色彩：多头脉冲翡翠绿（`#00E676`）、空头止损珊瑚红（`#FF5252`）、破量主升流光金（`#FFD740`）、影子模拟赛博紫（`#B388FF`）。

### 3. 四大核心业务板块实现 (`ui/screens/`)

#### 板块一：`MarketScreen.kt`【行情面板（SOL/USDT 微观扫描）】
- 顶部状态栏实时显示：`日本 2C2G 引擎状态：正常 | 宏观状态：宽幅震荡/多头趋势/极值抄底豁免 | 内存/Swap 健康度`。
- 3D 悬浮主卡：实时跳动 `SOL/USDT` 最新价、卡尔曼滤波平滑价格曲线与瞬时速度斜率指示器（$\hat{v}_t > 0$ 绿色向上箭头）、`Depth5` 五档买卖盘口厚度对比条。
- 周期指标矩阵卡：并排展示 `1m`、`5m`、`15m`、`1h`、`4h` 的 $\text{RSI}(6)$ 动态游标与 $300\text{ s}$ 滚动 $\text{RVOL}$ 放量柱状图（标记 $1.50\times$ 突破基准线）。

#### 板块二：`StrategyDashboardScreen.kt`【策略面板（0%~100% 策略达成百分比 3D 仪表板）】
- **核心视觉中心**：使用 Compose `Canvas` 绘制高颜值 **3D 扁平化环形仪表盘（`StrategyGauge3D`）**，指针与数字实时平滑渲染当前策略达成百分比 $S_{\text{total}} \in [0\%, 100\%]$（$< 60\%$ 冷静蓝，$60\% \sim 74\%$ 蓄势琥珀黄，$\ge 75\%$ 触发翡翠绿脉冲光效）。
- **四维得分与三大补丁状态透视卡**：
  1. 宏观与波动过滤得分（$/25$）；
  2. 模式 A 超卖反弹 `15m/5m RSI(6)` 共振得分（$/35$）；
  3. **【补丁 3】** $\text{RVOL}$ 放量突破与主动买盘占比得分（$/25$，若触发 `< 1.25x 缩量诱多一票否决` 则显示醒目黄色护盾标志）；
  4. 盘口 OBI 承接力得分（$/15$）及当前数学模型预估分配仓位（$300 \sim 700\text{ USDT}$）；
  5. **【补丁 1 & 补丁 2 状态指示灯】**：实时展示“浮亏禁加第 2 仓锁”与“止损后重入双轨锁（需额外深跌 $\le -0.5\%$ 或冷却 $15\text{ m}$，含 $4\text{ h}$ 过期倒计时）”的激活/解锁状态。

#### 板块三：`CryptoBotLiveScreen.kt`【Crypto-bot 实盘面板（自主交易与手动单托管）】
- **顶部核心控制区**：
  - **【Crypto-bot 实盘 100% 自主接管总开关】**（带 3D 滑动质感与开启/优雅停机确认弹窗）；
  - 币安 API Key/Secret 安全录入弹窗与“交易参数自定义设置”入口（可调总金额 $2000\text{ USDT}$、单笔 $300 \sim 700\text{ USDT}$、止损 $-0.8\%$、止盈 $+1.0\% / +1.5\%$ 等）；
  - 账户资金与 **BNB 备用金状态徽章**（显示当前可用 BNB 折合 USDT 价值，及“每日首单 $< 20\text{ U}$ 自动按过滤器保护补买 BNB”状态）。
- **实盘持仓卡片列表（严格区分机器人自营单 vs 用户手动单）**：
  - **机器人自营订单卡片（Slot 1 / Slot 2）**：带 `CBOT AUTO` 翡翠徽章，实时展示买入均价、扣除手续费后真实可卖净持仓数量 `qty_net`、当前毛收益率 $r(t)$、预估净盈亏金额、历史最高收益率 $r_{\text{peak}}$，以及直观的 **三阶状态机进度条**：
    `[S0 初始风控 (-0.8%)] -> [S1 保底锁定 (+1.0%)] -> [S2-B 破量豁免 / S2-C 超买收割]`。
  - **用户非机器人手动订单卡片**：带 `MANUAL` 橙色徽章，右侧设有显眼的 **【自动止盈止损托管开关】**。
    - 当用户点击开启托管且该手动单当前浮亏已跌破 $-0.8\%$ 时，自动弹出 Material 3 双选对话框：**“该手动单当前浮亏已超 -0.8%：选项 1【立即按最优限价止损卖出】 / 选项 2【以当前现价重置为 0% 基准价开始托管】”**。

#### 板块四：`CryptoShadowScreen.kt`【Crypto-shadow 影子面板与五维周期复盘中心】
- **上半区（影子实时模拟交易）**：展示独立 $2000\text{ USDT}$ 虚拟账户净值曲线、滚动胜率、盈亏比、当前虚拟持仓卡片（基于 `Depth5` 真实盘口 + $0.075\%$ 费率撮合）。
- **下半区（$1\text{W} / 1\text{M} / 3\text{M} / 6\text{M} / 1\text{Y}$ 周期复盘报告与策略进化器）**：
  - 顶部 5 个周期切换 Chip（`1周` | `1月` | `3个月` | `6个月` | `1年`）；
  - 渲染复盘报告核心指标卡片（MAE 最大不利偏离、早产踏空率、假止损二次重入捕获率）与完整复盘正文；
  - 底部配有 **【一键应用本周期推荐优化参数】** 按钮，点击后将复盘寻优出的参数同步至后端引擎。

### 4. 后台常驻保活服务与 6 大类 FCM 预警弹窗 (`service/BotMonitorForegroundService.kt` & `BotFcmMessagingService.kt`)
- 实现适配 Android 14 `specialUse` 的前台常驻服务（带通知栏实时价格与策略达成度简报）+ WebSocket 双向心跳（每 $20\text{ s}$ 发送 Ping 帧）自动重连，并结合 `BotFcmMessagingService` 建立高优先级通知渠道（含震动与醒目弹窗），确保实盘成交、状态跃迁、手动单托管提醒以及**服务器故障/信号扫描服务异常** 100% 实时弹窗送达。

---

## 【Plan Mode 执行与 QA 验收要求】
1. 先输出 **Sprint 6 Android 客户端实现计划（Plan）** 与包结构树。
2. 逐文件生成完整 Kotlin Jetpack Compose 代码，确保无任何未实现占位符，且所有 UI 状态均通过 `StateFlow` 与后端 WebSocket / REST 接口响应式绑定。
3. 编写单元测试验证 `SecureHttpClient` 自定义 `X509TrustManager` 对自签名 ECDSA 证书 SHA-256 公钥指纹的正确放行与篡改拦截。